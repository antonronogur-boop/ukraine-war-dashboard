"""
oryx_export.py  –  Oryx adatok JSON exportja a dashboardhoz (v2 — öngyógyító kategorizálás)

Futtatás: python oryx_export.py
Kimenet:  oryx_data.json

Tartalmaz:
  - Kategória szintű heti kumulatív és delta adatok (RUS + UKR)
  - Típus szintű top 15 lista kategóriánként (melyik tankmodell veszett el legtöbbet)
  - Heti trend a top típusokhoz (tankok, repülők, légvédelem)
  - MoD/Oryx arány számításhoz szükséges adatok

MI VÁLTOZOTT A v2-BEN (2026-11):
  1. Az Oryx 2026 nyarán átnevezte és bővítette a kategóriáit. A régi
     CATEGORY_MAP ezeket nem ismerte, ezért pl. a vontatott tüzérség a
     "támogató eszköz", a műszaki járművek a "parancsnoki" kategóriába
     csúsztak, az új nevek (Rocket and Missile Artillery, Trucks and similar
     vehicles, Naval Ships, Radars And Communications Equipment ...) pedig
     egyáltalán nem kerültek be. → A térkép bővült, a keresés név-normalizált.
  2. A régi scraper (v2) hiányzó horgonyok miatt 2026-06/08-tól NEM írta be a
     "Tanks" (és több más) fejlécet a heti lapokra, így a harckocsi-sor 0 lett,
     más típusok pedig rossz kategória alá kerültek. Ezek a lapok már nem
     generálhatók újra (az Oryx csak az aktuális állapotot mutatja), ezért az
     export ÖNGYÓGYÍTÓ: a legteljesebb (legtöbb fejlécet tartalmazó, legfrissebb)
     lapból típus→kategória referencia-térképet épít, és MINDEN lapon ezzel
     sorolja be a típusokat. A fejléc csak a referenciában nem szereplő (új)
     típusoknál számít. Így a teljes idősor egységes kategorizálást kap, a
     sérült hetek is helyreállnak.
  3. Minden nem térképezett fejléc WARNING-ot ír → a GitHub Actions naplóban
     azonnal látszik, ha az Oryx újra új kategóriát vezet be.
"""

import os, re, json, logging, openpyxl
from collections import Counter
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)

# ── Kategória mapping (Oryx <h3> cím → dashboard-kategória) ──────────────────
# A kulcsokat normalizálva (kisbetű, egyszeres szóköz) hasonlítjuk össze.
CATEGORY_MAP = {
    # Harckocsi
    'Tanks': 'tanks',
    # Páncélozott harcjárművek (minden páncélozott, nem-harckocsi platform)
    'Armoured Fighting Vehicles': 'afv',
    'Infantry Fighting Vehicles': 'afv',
    'Armoured Personnel Carriers': 'afv',
    'Mine-Resistant Ambush Protected (MRAP) Vehicles': 'afv',
    'Infantry Mobility Vehicles': 'afv',                              # új (2026)
    # Tüzérség — a MoD "tüzérségi rendszer" számhoz hasonlóan vontatott + önjáró
    'Towed Artillery': 'artillery',                                   # eddig hiányzott!
    'Self-Propelled Artillery': 'artillery',
    # Sorozatvető / rakétatüzérség
    'Multiple Rocket Launchers': 'mlrs',
    'Rocket and Missile Artillery': 'mlrs',                           # átnevezve (2026)
    # Légvédelem
    'Anti-Aircraft Guns': 'airdef',
    'Self-Propelled Anti-Aircraft Guns': 'airdef',
    'Surface-To-Air Missile Systems': 'airdef',
    # Repülő / helikopter / UAV
    'Aircraft': 'planes',
    'Helicopters': 'helicopters',
    'Unmanned Combat Aerial Vehicles': 'uav',
    'Reconnaissance Unmanned Aerial Vehicles': 'uav',                 # korábbi ukrán lapokon
    'Unmanned Ground Vehicles': 'support',                            # korábbi ukrán lapokon (nincs saját kategória)
    # Hajó
    'Naval Ships and Submarines': 'naval',
    'Naval Ships': 'naval',                                           # ukrán oldal neve
    # Logisztikai jármű
    'Trucks, Vehicles, and Jeeps': 'vehicles',
    'Trucks and similar vehicles': 'vehicles',                        # átnevezve (2026)
    # Elektronika / vezetés
    'Command Posts And Communications Stations': 'electronics',
    'Radars': 'electronics',
    'Radars And Communications Equipment': 'electronics',             # ukrán oldal neve
    'Jammers And Deception Systems': 'electronics',
    # Páncéltörő
    'Self-Propelled Anti-Tank Missile Systems': 'antitank',
    # Támogató / műszaki
    'Artillery Support Vehicles And Equipment': 'support',
    'Artillery and Missile Support Vehicles And Equipment': 'support', # átnevezve (2026)
    'Engineering Vehicles And Equipment': 'support',                  # eddig hiányzott!
}

# Kulcsszavas tartalék, ha egy fejléc nincs a fenti listában (új Oryx-kategória)
KEYWORD_FALLBACK = [
    (r'\btank', 'tanks'),
    (r'anti-tank|atgm', 'antitank'),
    (r'anti-aircraft|surface-to-air|air defen[cs]e', 'airdef'),
    (r'artillery support|engineering|support', 'support'),
    (r'rocket|mrl|missile artillery', 'mlrs'),
    (r'artillery|howitzer|mortar', 'artillery'),
    (r'fighting vehicle|personnel carrier|mrap|mobility vehicle|armou?red', 'afv'),
    (r'helicopter', 'helicopters'),
    (r'aircraft|aeroplane|jet', 'planes'),
    (r'unmanned|uav|drone', 'uav'),
    (r'naval|ship|boat|submarine', 'naval'),
    (r'truck|vehicle|jeep', 'vehicles'),
    (r'radar|command|communication|jammer|electronic', 'electronics'),
]

DASHBOARD_CATS = ['tanks', 'afv', 'artillery', 'mlrs', 'airdef',
                  'planes', 'helicopters', 'uav', 'naval',
                  'vehicles', 'electronics', 'antitank', 'support']

CAT_LABELS = {
    'tanks': 'Harckocsi', 'afv': 'Páncélozott harcjármű',
    'artillery': 'Tüzérség', 'mlrs': 'Sorozatvető / rakétatüzérség', 'airdef': 'Légvédelem',
    'planes': 'Repülő', 'helicopters': 'Helikopter', 'uav': 'UAV drón',
    'naval': 'Hajó / tengeralattjáró', 'vehicles': 'Jármű / teherautó',
    'electronics': 'Elektr. / parancsnoki', 'antitank': 'Páncéltörő rendszer',
    'support': 'Támogató / műszaki eszköz',
}

# Típusbontást csak ezekre a kategóriákra gyűjtünk (a legérdekesebbek)
TYPE_BREAKDOWN_CATS = ['tanks', 'planes', 'helicopters', 'airdef', 'artillery']
TOP_N = 15  # top N típus kategóriánként

_norm_map = {re.sub(r'\s+', ' ', k.strip().lower()): v for k, v in CATEGORY_MAP.items()}
_unmapped_seen = set()


def map_heading(heading: str):
    """Oryx fejléc → dashboard-kategória (None ha nem azonosítható)."""
    key = re.sub(r'\s+', ' ', str(heading).strip().lower())
    if key in _norm_map:
        return _norm_map[key]
    for pat, cat in KEYWORD_FALLBACK:
        if re.search(pat, key):
            if heading not in _unmapped_seen:
                _unmapped_seen.add(heading)
                log.warning("Ismeretlen Oryx-fejléc: '%s' → kulcsszó alapján '%s'. "
                            "Vedd fel a CATEGORY_MAP-be!", heading, cat)
            return cat
    if heading not in _unmapped_seen:
        _unmapped_seen.add(heading)
        log.warning("Ismeretlen Oryx-fejléc: '%s' → NEM térképezhető, a sorai kimaradnak!", heading)
    return None


def read_sheets(path):
    """Beolvassa az xlsx dátum-lapjait.

    Visszaad: [(sheet_name, rows)] ahol rows = [(type_name, header_cat, d, c, dam, a, total)]
    és header_cat = a lapon ELŐTTE álló kategória-fejlécből levezetett kategória (vagy None).
    """
    wb = openpyxl.load_workbook(path, read_only=True)
    date_sheets = sorted([s for s in wb.sheetnames if re.match(r'\d{4}-\d{2}-\d{2}', s)])
    sheets = []
    for sheet_name in date_sheets:
        ws = wb[sheet_name]
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            continue
        parsed, current_cat, n_headers = [], None, 0
        for row in rows[1:]:
            if not row or not row[0]:
                continue
            type_name = str(row[0]).strip()
            if row[1] is None and row[2] is None:          # kategória-fejléc sor
                current_cat = map_heading(type_name)
                n_headers += 1
                continue
            d = int(row[1] or 0); c = int(row[2] or 0); dam = int(row[3] or 0)
            a = int(row[4] or 0); total = int(row[5] or 0)
            parsed.append((type_name, current_cat, d, c, dam, a, total))
        sheets.append((sheet_name, parsed, n_headers))
    wb.close()
    return sheets


def build_reference_map(sheets):
    """Típus → kategória referencia-térkép.

    Alap: a LEGFRISSEBB olyan lap, amely (közel) teljes fejléckészlettel
    rendelkezik — legalább a valaha látott maximális fejlécszám 85%-a. Így a
    sérült (kevés fejlécű) hetek sosem lehetnek referenciák, de egy újabb,
    kicsit eltérő Oryx-szerkezet (pl. megszűnt alkategória) igen.
    Kiegészítés: a referenciában nem szereplő típusoknál a legfrissebb olyan
    lap, ahol a típus ismert kategóriájú fejléc alatt állt.
    """
    if not sheets:
        return {}, None
    max_headers = max(s[2] for s in sheets)
    threshold = max_headers * 0.85
    best = max((s for s in sheets if s[2] >= threshold), key=lambda s: s[0])   # legfrissebb teljes lap
    ref_name = best[0]
    ref = {t: cat for t, cat, *_ in best[1] if cat}
    for sheet_name, parsed, _ in reversed(sheets):          # legfrissebbtől visszafelé
        for t, cat, *_ in parsed:
            if cat and t not in ref:
                ref[t] = cat
    log.info("Referencia-lap: %s (%d fejléc, %d típus a térképben)", ref_name, best[2], len(ref))
    return ref, ref_name


def parse_file(path):
    """Beolvassa az xlsx-et. Visszaad: (week_dates, weekly_cat_data, type_data, info)"""
    sheets = read_sheets(path)
    ref_map, ref_name = build_reference_map(sheets)

    week_dates, weekly_data = [], []
    type_data = {cat: {} for cat in TYPE_BREAKDOWN_CATS}
    reclassified = Counter()
    unknown_types = set()

    for week_idx, (sheet_name, parsed, _) in enumerate(sheets):
        week_dates.append(sheet_name)
        week_totals = {cat: {'destroyed': 0, 'captured': 0, 'damaged': 0, 'abandoned': 0, 'total': 0}
                       for cat in DASHBOARD_CATS}
        for type_name, header_cat, d, c, dam, a, total in parsed:
            cat = ref_map.get(type_name) or header_cat
            if cat is None:
                unknown_types.add(type_name)
                continue
            if header_cat != cat:
                reclassified[(sheet_name, header_cat, cat)] += 1
            wt = week_totals[cat]
            wt['destroyed'] += d; wt['captured'] += c; wt['damaged'] += dam
            wt['abandoned'] += a; wt['total'] += total
            if cat in TYPE_BREAKDOWN_CATS and total > 0:
                type_data[cat].setdefault(type_name, {})[week_idx] = total
        weekly_data.append(week_totals)

    n_fixed_sheets = len({s for s, _, _ in reclassified})
    if n_fixed_sheets:
        log.info("Öngyógyítás: %d lapon %d sor került a referencia-térkép szerinti kategóriába.",
                 n_fixed_sheets, sum(reclassified.values()))
    if unknown_types:
        log.warning("%d típus kimaradt (sem fejléc, sem referencia): pl. %s",
                    len(unknown_types), sorted(unknown_types)[:5])
    info = {'reference_sheet': ref_name, 'reclassified_rows': sum(reclassified.values()),
            'unknown_types': len(unknown_types)}
    return week_dates, weekly_data, type_data, info


def build_type_breakdown(type_data, dates):
    """
    Típusbontás: minden kategóriára top N típus.
    Visszaad: {cat: [{type, total, weekly: [0,0,2,...]}, ...]}
    """
    result = {}
    n_weeks = len(dates)

    for cat, types in type_data.items():
        ranked = []
        for type_name, week_vals in types.items():
            last_val = week_vals.get(max(week_vals.keys()), 0) if week_vals else 0
            ranked.append((type_name, last_val, week_vals))

        ranked.sort(key=lambda x: x[1], reverse=True)
        top = ranked[:TOP_N]

        result[cat] = []
        for type_name, total, week_vals in top:
            weekly = [week_vals.get(i, None) for i in range(n_weeks)]
            last_known = 0
            filled = []
            for v in weekly:
                if v is not None:
                    last_known = v
                filled.append(last_known)
            result[cat].append({'type': type_name, 'total': total, 'weekly': filled})

    return result


def build_output(dates, rus_data, ukr_data, rus_types, ukr_types, rus_info, ukr_info):
    rus_weekly, ukr_weekly = [], []

    for i, (date, rd, ud) in enumerate(zip(dates, rus_data, ukr_data)):
        re_ = {'date': date, 'week': i + 1}
        ue_ = {'date': date, 'week': i + 1}
        for cat in DASHBOARD_CATS:
            re_[cat] = rd[cat]['total']
            ue_[cat] = ud[cat]['total']
        rus_weekly.append(re_)
        ukr_weekly.append(ue_)

    last_rus = rus_data[-1] if rus_data else {}
    last_ukr = ukr_data[-1] if ukr_data else {}

    rus_totals = {cat: last_rus.get(cat, {}) for cat in DASHBOARD_CATS}
    ukr_totals = {cat: last_ukr.get(cat, {}) for cat in DASHBOARD_CATS}

    rus_deltas, ukr_deltas = [], []
    for i in range(1, len(rus_weekly)):
        rd_ = {'date': dates[i], 'week': i + 1}
        ud_ = {'date': dates[i], 'week': i + 1}
        for cat in DASHBOARD_CATS:
            rd_[cat] = max(0, rus_weekly[i][cat] - rus_weekly[i-1][cat])
            ud_[cat] = max(0, ukr_weekly[i][cat] - ukr_weekly[i-1][cat])
        rus_deltas.append(rd_)
        ukr_deltas.append(ud_)

    return {
        'meta': {
            'source': 'Oryx (oryxspioenkop.com)',
            'source_type': 'Vizuálisan dokumentált, fotóval igazolt veszteségek',
            'note': 'Csak képi bizonyítékkal igazolt veszteségek. A valós veszteségek magasabbak.',
            'first_week': dates[0] if dates else None,
            'last_week': dates[-1] if dates else None,
            'total_weeks': len(dates),
            'categories': CAT_LABELS,
            'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
            'export_version': 2,
            'classification': {'russia': rus_info, 'ukraine': ukr_info},
        },
        'russia': {
            'totals': rus_totals,
            'weekly_cumulative': rus_weekly,
            'weekly_delta': rus_deltas,
            'type_breakdown': rus_types,
        },
        'ukraine': {
            'totals': ukr_totals,
            'weekly_cumulative': ukr_weekly,
            'weekly_delta': ukr_deltas,
            'type_breakdown': ukr_types,
        }
    }


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))

    data_dir = os.path.join(script_dir, 'data')
    if not os.path.exists(os.path.join(data_dir, 'xyz_RUS.xlsx')):
        data_dir = script_dir

    rus_path = os.path.join(data_dir, 'xyz_RUS.xlsx')
    ukr_path = os.path.join(data_dir, 'xyz_UKR.xlsx')
    out_path = os.path.join(script_dir, 'oryx_data.json')

    print('Orosz adatok beolvasása...')
    rus_dates, rus_data, rus_types_raw, rus_info = parse_file(rus_path)
    print(f'  {len(rus_dates)} hét')

    print('Ukrán adatok beolvasása...')
    ukr_dates, ukr_data, ukr_types_raw, ukr_info = parse_file(ukr_path)
    print(f'  {len(ukr_dates)} hét')

    common = sorted(set(rus_dates) & set(ukr_dates))
    ri = {d: i for i, d in enumerate(rus_dates)}
    ui = {d: i for i, d in enumerate(ukr_dates)}
    rc = [rus_data[ri[d]] for d in common]
    uc = [ukr_data[ui[d]] for d in common]

    print(f'Közös hetek: {len(common)}')

    # A típusbontás heti indexei a saját fájl heteire mutatnak → átindexelés a közös hetekre
    def reindex(types_raw, own_dates):
        own_idx = {d: i for i, d in enumerate(own_dates)}
        out = {}
        for cat, types in types_raw.items():
            out[cat] = {}
            for t, wk in types.items():
                out[cat][t] = {ci: wk[own_idx[d]] for ci, d in enumerate(common) if own_idx[d] in wk}
        return out

    print('Típusbontás generálása...')
    rus_types = build_type_breakdown(reindex(rus_types_raw, rus_dates), common)
    ukr_types = build_type_breakdown(reindex(ukr_types_raw, ukr_dates), common)

    output = build_output(common, rc, uc, rus_types, ukr_types, rus_info, ukr_info)

    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    size_kb = os.path.getsize(out_path) // 1024
    print(f'\nMentve: {out_path} ({size_kb} KB)')

    print('\n=== Orosz összesítő (utolsó hét) ===')
    for cat, label in CAT_LABELS.items():
        t = output['russia']['totals'][cat].get('total', 0)
        if t > 0:
            print(f'  {label}: {t}')

    print('\n=== Ukrán összesítő (utolsó hét) ===')
    for cat, label in CAT_LABELS.items():
        t = output['ukraine']['totals'][cat].get('total', 0)
        if t > 0:
            print(f'  {label}: {t}')

    print('\n=== Top 5 orosz harckocsi típus (Oryx) ===')
    for entry in output['russia']['type_breakdown'].get('tanks', [])[:5]:
        print(f"  {entry['type']}: {entry['total']}")

    # Józanság-ellenőrzés: ha a harckocsi-összeg 0, valami nagyon elromlott
    if output['russia']['totals']['tanks'].get('total', 0) == 0:
        print('\n!!! HIBA: az orosz harckocsi-összeg 0 — a kategorizálás elromlott.')
        raise SystemExit(1)


if __name__ == '__main__':
    main()
