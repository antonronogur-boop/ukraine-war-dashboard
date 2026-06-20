"""
oryx_export.py  –  Oryx adatok JSON exportja a dashboardhoz

Futtatás: python oryx_export.py
Kimenet:  oryx_data.json

Tartalmaz:
  - Kategória szintű heti kumulatív és delta adatok (RUS + UKR)
  - Típus szintű top 15 lista kategóriánként (melyik tankmodell veszett el legtöbbet)
  - Heti trend a top típusokhoz (tankok, repülők, légvédelem)
  - MoD/Oryx arány számításhoz szükséges adatok
"""

import os, re, json, openpyxl
from datetime import datetime

# ── Kategória mapping ────────────────────────────────────────────────
CATEGORY_MAP = {
    'Tanks': 'tanks',
    'Armoured Fighting Vehicles': 'afv',
    'Infantry Fighting Vehicles': 'afv',
    'Armoured Personnel Carriers': 'afv',
    'Mine-Resistant Ambush Protected (MRAP) Vehicles': 'afv',
    'Self-Propelled Artillery': 'artillery',
    'Multiple Rocket Launchers': 'mlrs',
    'Anti-Aircraft Guns': 'airdef',
    'Self-Propelled Anti-Aircraft Guns': 'airdef',
    'Surface-To-Air Missile Systems': 'airdef',
    'Aircraft': 'planes',
    'Helicopters': 'helicopters',
    'Unmanned Combat Aerial Vehicles': 'uav',
    'Naval Ships and Submarines': 'naval',
    'Trucks, Vehicles, and Jeeps': 'vehicles',
    'Command Posts And Communications Stations': 'electronics',
    'Radars': 'electronics',
    'Jammers And Deception Systems': 'electronics',
    'Self-Propelled Anti-Tank Missile Systems': 'antitank',
    'Artillery Support Vehicles And Equipment': 'support',
}

DASHBOARD_CATS = ['tanks', 'afv', 'artillery', 'mlrs', 'airdef',
                  'planes', 'helicopters', 'uav', 'naval',
                  'vehicles', 'electronics', 'antitank', 'support']

CAT_LABELS = {
    'tanks': 'Harckocsi', 'afv': 'Páncélozott harcjármű',
    'artillery': 'Tüzérség', 'mlrs': 'MLRS', 'airdef': 'Légvédelem',
    'planes': 'Repülő', 'helicopters': 'Helikopter', 'uav': 'UAV drón',
    'naval': 'Hajó / tengeralattjáró', 'vehicles': 'Jármű / teherautó',
    'electronics': 'Elektr. / parancsnoki', 'antitank': 'Páncéltörő rendszer',
    'support': 'Támogató eszköz',
}

# Típusbontást csak ezekre a kategóriákra gyűjtünk (a legérdekesebbek)
TYPE_BREAKDOWN_CATS = ['tanks', 'planes', 'helicopters', 'airdef', 'artillery']
TOP_N = 15  # top N típus kategóriánként


def parse_file(path):
    """Beolvassa az xlsx-et. Visszaad: (week_dates, weekly_cat_data, type_data)"""
    wb = openpyxl.load_workbook(path, read_only=True)
    date_sheets = sorted([s for s in wb.sheetnames if re.match(r'\d{4}-\d{2}-\d{2}', s)])

    week_dates, weekly_data = [], []
    # type_data: {cat: {type_name: {week_idx: total, ...}}}
    type_data = {cat: {} for cat in TYPE_BREAKDOWN_CATS}

    for week_idx, sheet_name in enumerate(date_sheets):
        ws = wb[sheet_name]
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            continue

        week_dates.append(sheet_name)
        week_totals = {cat: {'destroyed': 0, 'captured': 0, 'damaged': 0, 'abandoned': 0, 'total': 0}
                       for cat in DASHBOARD_CATS}

        current_cat = None
        for row in rows[1:]:
            if not row[0]:
                continue
            type_name = str(row[0]).strip()

            if row[1] is None and row[2] is None:
                current_cat = CATEGORY_MAP.get(type_name)
                continue
            if current_cat is None:
                continue

            d = int(row[1] or 0)
            c = int(row[2] or 0)
            dam = int(row[3] or 0)
            a = int(row[4] or 0)
            total = int(row[5] or 0)

            week_totals[current_cat]['destroyed'] += d
            week_totals[current_cat]['captured'] += c
            week_totals[current_cat]['damaged'] += dam
            week_totals[current_cat]['abandoned'] += a
            week_totals[current_cat]['total'] += total

            # Típusbontás gyűjtése
            if current_cat in TYPE_BREAKDOWN_CATS and total > 0:
                if type_name not in type_data[current_cat]:
                    type_data[current_cat][type_name] = {}
                type_data[current_cat][type_name][week_idx] = total

        weekly_data.append(week_totals)

    wb.close()
    return week_dates, weekly_data, type_data


def build_type_breakdown(type_data, dates):
    """
    Típusbontás: minden kategóriára top N típus.
    Visszaad: {cat: [{type, total, weekly: [0,0,2,...]}, ...]}
    """
    result = {}
    n_weeks = len(dates)

    for cat, types in type_data.items():
        # Utolsó héten mért összesített értékek alapján rangsorolunk
        ranked = []
        for type_name, week_vals in types.items():
            # Utolsó ismert érték = kumulatív összesítés
            last_val = week_vals.get(max(week_vals.keys()), 0) if week_vals else 0
            ranked.append((type_name, last_val, week_vals))

        ranked.sort(key=lambda x: x[1], reverse=True)
        top = ranked[:TOP_N]

        result[cat] = []
        for type_name, total, week_vals in top:
            # Heti értéksorozat (0 ahol nincs adat)
            weekly = [week_vals.get(i, None) for i in range(n_weeks)]
            # Forward fill: ha None, az előző értéket visszük tovább
            last_known = 0
            filled = []
            for v in weekly:
                if v is not None:
                    last_known = v
                filled.append(last_known)
            result[cat].append({
                'type': type_name,
                'total': total,
                'weekly': filled
            })

    return result


def build_output(dates, rus_data, ukr_data, rus_types, ukr_types):
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
    rus_dates, rus_data, rus_types_raw = parse_file(rus_path)
    print(f'  {len(rus_dates)} hét')

    print('Ukrán adatok beolvasása...')
    ukr_dates, ukr_data, ukr_types_raw = parse_file(ukr_path)
    print(f'  {len(ukr_dates)} hét')

    common = sorted(set(rus_dates) & set(ukr_dates))
    ri = {d: i for i, d in enumerate(rus_dates)}
    ui = {d: i for i, d in enumerate(ukr_dates)}
    rc = [rus_data[ri[d]] for d in common]
    uc = [ukr_data[ui[d]] for d in common]

    print(f'Közös hetek: {len(common)}')

    print('Típusbontás generálása...')
    rus_types = build_type_breakdown(rus_types_raw, common)
    ukr_types = build_type_breakdown(ukr_types_raw, common)

    output = build_output(common, rc, uc, rus_types, ukr_types)

    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    size_kb = os.path.getsize(out_path) // 1024
    print(f'\nMentve: {out_path} ({size_kb} KB)')

    print('\n=== Orosz összesítő (utolsó hét) ===')
    for cat, label in CAT_LABELS.items():
        t = output['russia']['totals'][cat].get('total', 0)
        if t > 0:
            print(f'  {label}: {t}')

    print('\n=== Top 5 orosz harckocsi típus (Oryx) ===')
    for entry in output['russia']['type_breakdown'].get('tanks', [])[:5]:
        print(f"  {entry['type']}: {entry['total']}")


if __name__ == '__main__':
    main()
