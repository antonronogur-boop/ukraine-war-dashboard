"""
scraper_RUS.py  –  Orosz Oryx adatok scraper (v3 — fejléc-alapú kategorizálás)

Használat:
    python scraper_RUS.py                    # default: data/xyz_RUS.xlsx
    python scraper_RUS.py data/xyz_RUS.xlsx  # explicit elérési út
    python scraper_RUS.py data/xyz_RUS.xlsx --html mentett_oldal.html   # offline teszt

MIÉRT KELLETT ÚJRAÍRNI (2026-11):
    A v2 a kategória-fejléceket NEM az Oryx oldal <h3> címeiből vette, hanem
    egy beégetett "első típus" horgony alapján szúrta be (pl. Tanks → "T-54").
    Amikor az Oryx 2026 nyarán átnevezte/átrendezte a listáit (T-54 → T-54-3M,
    új "Infantry Mobility Vehicles", "Engineering Vehicles", "Towed Artillery",
    "Rocket and Missile Artillery" kategóriák stb.), a horgonyok eltűntek:
      - a "Tanks" fejléc nem került be → az oryx_export.py 0 harckocsit számolt
        (2026-08-05-től az orosz, 2026-06-04-től az ukrán oldalon),
      - a parancsnoki járművek a MRAP alá, a radarok a légvédelem alá,
        az ukrán teherautók az UAV alá csúsztak.

MIT CSINÁL A v3:
    - A kategóriát az Oryx saját <h3> címéből olvassa:
          "Tanks (4454, of which destroyed: 3359, damaged: 165, ...)"
      → fejléc sor: Type="Tanks", számoszlopok üresek (None) — a formátum
        változatlan, az analysis_losses.py / collector.py / oryx_export.py
        ugyanúgy felismeri.
    - Minden típus darabszámát az Oryx saját <summary> sorából veszi
      ("2 T-54-3M:" → 2), ez az Oryx hivatalos száma.
    - A státusz-bontást (destroyed/captured/damaged/abandoned) a bejegyzések
      "(1, destroyed)", "(12 and 13, captured)" linkszövegeiből számolja,
      a kombinált státuszoknál ("damaged and abandoned") az UTOLSÓ állapot
      számít — ez egyezik az Oryx saját összesítésével (ellenőrizve: 20/23
      kategória pontos egyezés, a többinél ±1–3 db eltérés).
    - A kategória-összegeket a futás végén összeveti az <h3> címben közölt
      hivatalos összeggel és WARNING-ot ír, ha eltérés van → a GitHub Actions
      naplóban azonnal látszik, ha az Oryx újra szerkezetet vált.

Kimeneti formátum (változatlan):
    oszlopok: Type, Destroyed, Captured, Damaged, Abandoned, Sum_Loss
    kategória-fejléc sor: Type = kategórianév, a többi oszlop None
"""

import sys, os, re, logging
import pandas as pd
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_with_backoff import fetch  # noqa: E402
from collections import OrderedDict
from openpyxl import load_workbook, Workbook
from openpyxl.utils.dataframe import dataframe_to_rows
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)

URL = "https://www.oryxspioenkop.com/2022/02/attack-on-europe-documenting-equipment.html"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_FILE = os.path.normpath(os.path.join(SCRIPT_DIR, '..', 'data', 'xyz_RUS.xlsx'))

STATUS_KEYS = ('destroyed', 'damaged', 'abandoned', 'captured')
COLUMNS = ['Type', 'Destroyed', 'Captured', 'Damaged', 'Abandoned', 'Sum_Loss']

# Oryx kategória-cím: "Tanks (4454, of which destroyed: 3359, damaged: 165, ...)"
# Az első két <h3> ("Russia - 24251, of which: ...", "Losses of Armoured Combat
# Vehicles [...] - 14197, ...") NEM illeszkedik erre a mintára → kihagyjuk.
HEADER_RE = re.compile(r'^(.*?)\s*\((\d+),\s*of which(.*)\)\s*$')

# Oryx bejegyzés-link szövege: "(1, destroyed)", "(12 and 13, captured)",
# "(1, 2 and 3, damaged and abandoned)", ritkán "(116 and 117 destroyed)".
LINK_RE = re.compile(r'^\((.*)\)$')
IDS_RE = re.compile(r'^\s*((?:\d+\s*(?:,|and)\s*)*\d+)\s*[, ]\s*(.*)$')


def classify_status(status_text: str):
    """Egy bejegyzés státusz-szövegéből az Oryx-féle kategória.

    Kombinált státusznál ("damaged and abandoned", "captured and later
    destroyed") az utolsóként említett állapot számít — így egyezik az Oryx
    <h3>-ban közölt bontással. "sunk" / "scuttled" / "beyond economical
    repair" / "stripped" = destroyed.
    """
    s = status_text.lower()
    s = s.replace('damagd', 'damaged').replace('damage and', 'damaged and')
    if re.search(r'\b(sunk|scuttled|beyond economical repair|stripped)\b', s):
        return 'destroyed'
    found = sorted((s.find(k), k) for k in STATUS_KEYS if k in s)
    return found[-1][1] if found else None


def parse_entry_link(text: str):
    """'(12 and 13, captured)' → (2, 'captured'); None ha nem bejegyzés."""
    m = LINK_RE.match(text.strip())
    if not m:
        return None
    inner = m.group(1)
    mm = IDS_RE.match(inner)
    if mm:
        n = len(re.findall(r'\d+', mm.group(1)))
        status = mm.group(2)
    else:
        n, status = 1, inner
    return n, classify_status(status)


def parse_type_summary(text: str):
    """' 2 T-54-3M:' → (2, 'T-54-3M'); None ha nem típus-sor."""
    name = text.split(':')[0].strip()
    m = re.match(r'^(\d+)\s+(.*)$', name)
    if not m:
        return None
    return int(m.group(1)), m.group(2).strip()


def parse_page(html: str) -> pd.DataFrame:
    soup = BeautifulSoup(html, 'html.parser')
    main = soup.find('div', class_='post-body entry-content')
    if not main:
        log.error("Nem található post-body. Változott az Oryx oldal struktúrája?")
        return None

    # categories: OrderedDict[kategória] = {'declared': {...}, 'types': OrderedDict[típus] = számok}
    categories = OrderedDict()
    cur_cat = None
    type_index = {}   # típusnév → kategória (duplikátum-kezeléshez)

    for el in main.descendants:
        name = getattr(el, 'name', None)
        if name == 'h3':
            title = el.get_text(' ', strip=True)
            m = HEADER_RE.match(title)
            if not m:
                cur_cat = None          # összesítő cím (pl. "Russia - 24251, ...") → nem kategória
                continue
            cur_cat = m.group(1).strip()
            declared = {k: int(v) for k, v in
                        re.findall(r'(destroyed|damaged|abandoned|captured):\s*(\d+)', m.group(3))}
            declared['total'] = int(m.group(2))
            categories[cur_cat] = {'declared': declared, 'types': OrderedDict()}
        elif name == 'details' and cur_cat:
            summary = el.find('summary')
            if not summary:
                continue
            parsed = parse_type_summary(summary.get_text(' ', strip=True))
            if not parsed:
                continue
            count, type_name = parsed
            counts = {'Destroyed': 0, 'Captured': 0, 'Damaged': 0, 'Abandoned': 0}
            for a in el.find_all('a'):
                p = parse_entry_link(a.get_text(' ', strip=True))
                if not p or p[1] is None:
                    continue
                counts[p[1].capitalize()] += p[0]

            # Ugyanaz a típusnév két kategóriában (ritka, pl. lőszerszállító) →
            # az ELSŐ előfordulásba összegzünk, mert az analysis_losses.py a
            # Type oszlopot indexként használja és nem tűr duplikátumot.
            owner = type_index.get(type_name)
            if owner is None:
                type_index[type_name] = cur_cat
                categories[cur_cat]['types'][type_name] = {**counts, 'Sum_Loss': count}
            else:
                log.warning("Duplikált típusnév: '%s' (%s és %s) — összevonva az elsőbe.",
                            type_name, owner, cur_cat)
                row = categories[owner]['types'][type_name]
                for k in counts:
                    row[k] += counts[k]
                row['Sum_Loss'] += count

    if not categories:
        log.error("Egyetlen kategória-fejléc sem illeszkedett. Változott az Oryx <h3> formátuma?")
        return None

    # Ellenőrzés az Oryx saját összesítésével szemben
    rows = []
    mismatches = 0
    for cat, info in categories.items():
        rows.append({'Type': cat, 'Destroyed': None, 'Captured': None,
                     'Damaged': None, 'Abandoned': None, 'Sum_Loss': None})
        total = 0
        for type_name, c in info['types'].items():
            rows.append({'Type': type_name, **c})
            total += c['Sum_Loss']
        declared_total = info['declared']['total']
        if total != declared_total:
            mismatches += 1
            log.warning("Kategória-összeg eltérés: %s → számolt %d, Oryx szerint %d",
                        cat, total, declared_total)
        else:
            log.info("  %-50s %5d db (OK)", cat, total)

    df = pd.DataFrame(rows, columns=COLUMNS)
    log.info("Scraping kész: %d kategória, %d sor, %d összeg-eltérés",
             len(categories), len(df), mismatches)
    return df


def scrape() -> pd.DataFrame:
    log.info(f"Scraping (RUS): {URL}")
    try:
        resp = fetch(URL)
        if resp is None:
            log.error("Nem sikerült lekérni az oldalt több kísérlet után sem.")
            return None
    except Exception as e:
        log.error(f"Hálózati hiba: {e}")
        return None
    return parse_page(resp.content)


def save_to_excel(df: pd.DataFrame, filename: str, sheet_date: str = None):
    current_date = sheet_date or datetime.now().strftime('%Y-%m-%d')
    os.makedirs(os.path.dirname(os.path.abspath(filename)), exist_ok=True)

    if os.path.exists(filename):
        wb = load_workbook(filename)
        if current_date in wb.sheetnames:
            log.info(f"Már létezik lap erre a dátumra: {current_date} — kihagyva.")
            return
    else:
        wb = Workbook()
        if 'Sheet' in wb.sheetnames:
            del wb['Sheet']

    ws = wb.create_sheet(current_date)
    for r in dataframe_to_rows(df, index=False, header=True):
        ws.append(r)
    wb.save(filename)
    log.info(f"Mentve: {current_date} → {filename}")


def run(filename: str = None, html_path: str = None, sheet_date: str = None):
    if not filename:
        filename = DEFAULT_FILE
    if html_path:
        # Offline mód: mentett HTML-ből (teszteléshez / utólagos pótláshoz)
        with open(html_path, 'r', encoding='utf-8') as f:
            df = parse_page(f.read())
    else:
        df = scrape()
    if df is not None:
        save_to_excel(df, filename, sheet_date)
    return df is not None


if __name__ == '__main__':
    args = sys.argv[1:]
    html_path = sheet_date = None
    if '--html' in args:
        i = args.index('--html'); html_path = args[i + 1]; del args[i:i + 2]
    if '--date' in args:
        i = args.index('--date'); sheet_date = args[i + 1]; del args[i:i + 2]
    ok = run(args[0] if args else None, html_path, sheet_date)
    sys.exit(0 if ok else 1)
