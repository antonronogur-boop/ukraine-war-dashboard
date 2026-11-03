"""
mod_scraper.py  –  Napi MoD veszteségadat frissítő (v3 — DOM-alapú kinyerés)

Forrás:  https://index.minfin.com.ua/en/russian-invading/casualties/
Cél:     Hozzáfűzi a legfrissebb napot a DAILY tömbhöz az index.html-ben

Futtatás:
    python scripts/mod_scraper.py               # default: index.html
    python scripts/mod_scraper.py index.html    # explicit elérési út
    python scripts/mod_scraper.py index.html --html mentett_oldal.html   # offline teszt

MI VÁLTOZOTT A v3-BAN (2026-11):
    A korábbi verzió a teljes oldal szövegén futtatott regexeket
    ("Cruise missiles\\s*—\\s*(\\d+)"). A minfin oldalon a felirat így néz ki:
        <li>Cruise <span class="idx-help" title="shot down">missiles</span>&nbsp;— 5140</li>
    A get_text('\\n') ebből "Cruise\\nmissiles\\n— 5140"-et csinál, amire a
    szóközös minta NEM illeszkedik → a mező None lett, a run() pedig az előző
    napi értéket vitte tovább. Eredmény: a cirkálórakéta-szám 2026-07-31-től
    (4950) 78 napon át nem változott, miközben a forrás 5140-ig nőtt.
    → A v3 a DOM szerkezetét olvassa: ul.see-also > li.gold (a legfrissebb nap),
      azon belül span.black = dátum, div.casualties li = egy-egy mező. A felirat
      a <li> teljes szövege (a <small>(+N)</small> napi változás nélkül), így a
      belső <span>/<abbr> elemek nem zavarnak. Tartalékként megmaradt a régi
      regex-alapú út, ha a DOM-szerkezet változna.
    Plusz: kinyerés után a mezőket összevetjük a tegnapi értékkel, és ha egy
    mező NEM változott, de a <small>(+N)</small> napi változás szerepel a
    forrásban, WARNING-ot írunk (carry-forward gyanú).

Logika:
    1. Letölti a minfin.com.ua oldalt
    2. Kinyeri a legutolsó nap adatait
    3. Beolvassa az index.html-t
    4. Ha az adat már szerepel (dátum alapján), nem változtat semmit
    5. Ha új, hozzáfűzi a DAILY tömbhöz és elmenti
"""

import re
import sys
import os
import json
import logging
import requests
from bs4 import BeautifulSoup
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)

URL = 'https://index.minfin.com.ua/en/russian-invading/casualties/'
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_HTML = os.path.normpath(os.path.join(SCRIPT_DIR, '..', 'index.html'))

# A minfin feliratai (normalizálva: kisbetű, egyszeres szóköz) → DAILY mező.
# A felirat a <li> szövege a "—" előtt, a napi változás (<small>) nélkül.
LABEL_MAP = {
    'military personnel':     'personnel',
    'tanks':                  'tanks',
    'armored fighting vehicle': 'afv',
    'artillery systems':      'artillery',
    'mlrs':                   'mlrs',
    'anti-aircraft warfare':  'airdef',
    'planes':                 'planes',
    'helicopters':            'helicopters',
    'uav':                    'uav',
    'cruise missiles':        'missiles',
    'ships (boats)':          'ships',
    'submarines':             'submarines',
    'cars and cisterns':      'vehicles',
    'special equipment':      'special',
    'ground robotic systems': 'robots',
}
FIELDS = ['personnel', 'tanks', 'afv', 'artillery', 'mlrs', 'airdef', 'planes',
          'helicopters', 'uav', 'missiles', 'ships', 'submarines', 'vehicles',
          'special', 'robots']


def _norm(s: str) -> str:
    return re.sub(r'\s+', ' ', s.replace(' ', ' ')).strip().lower()


def parse_latest_dom(html: str):
    """DOM-alapú kinyerés. Visszaad: (data, daily_change) vagy (None, None)."""
    soup = BeautifulSoup(html, 'html.parser')
    block = soup.select_one('ul.see-also > li.gold')
    if not block:
        return None, None
    date_el = block.select_one('span.black')
    m = re.match(r'(\d{2})\.(\d{2})\.(\d{4})', date_el.get_text(strip=True) if date_el else '')
    if not m:
        return None, None
    day, month, year = m.groups()
    data = {'date': f'{year}-{month}-{day}'}
    change = {}
    # FONTOS: a minfin kiszolgált HTML-jében a <li class="gold"> napi blokkok
    # NINCSENEK lezárva, ezért a html.parser az összes későbbi napot az ELSŐ
    # napba ágyazza. Ha itt egyszerűen minden leszármazott <li>-t olvasnánk,
    # a mezőket a lista LEGUTOLSÓ (legrégebbi) napja írná felül — pontosan ez
    # történt 2026-11-03-án (a 10-01-i értékek kerültek a mai dátum alá, a
    # validate_daily.py pedig helyesen blokkolta). Ezért csak az ELSŐ
    # div.casualties ELSŐ <ul>-jének KÖZVETLEN <li> gyermekeit olvassuk, és
    # minden mezőből eltávolítjuk az esetleg beágyazott további napokat.
    cas = block.find('div', class_='casualties')
    ul = cas.find('ul') if cas else None
    if not ul:
        return None, None
    for li in ul.find_all('li', recursive=False):
        for nested in li.find_all(['ul', 'div', 'li']):
            nested.decompose()
        small = li.find('small')
        delta_txt = small.get_text(strip=True) if small else ''
        if small:
            small.extract()
        txt = li.get_text(' ', strip=True).replace(' ', ' ')
        if '—' not in txt and '–' not in txt and '-' not in txt:
            continue
        parts = re.split(r'\s*[—–]\s*', txt, maxsplit=1)
        if len(parts) != 2:
            continue
        label, value = _norm(parts[0]), parts[1]
        field = LABEL_MAP.get(label)
        if not field:
            continue
        num = re.sub(r'[^\d]', '', re.sub(r'aprx\.', '', value).split('people')[0])
        if not num:
            continue
        data[field] = int(num)
        dm = re.search(r'\+?\s*(\d+)', delta_txt)
        change[field] = int(dm.group(1)) if dm else 0
    return data, change


def parse_latest_regex(html: str):
    """Tartalék: a régi, szöveges kinyerés (ha a DOM-szerkezet megváltozna)."""
    soup = BeautifulSoup(html, 'html.parser')
    text = soup.get_text('\n')
    date_match = re.search(r'(\d{2})\.(\d{2})\.(\d{4})', text)
    if not date_match:
        return None
    day, month, year = date_match.groups()
    block_end = text.find('Dynamics of losses', date_match.end())
    block_text = text[date_match.start():(block_end if block_end != -1 else len(text))]

    def extract(pattern):
        m = re.search(pattern, block_text, re.DOTALL)
        if m:
            raw = re.sub(r'[^\d]', '', m.group(1))
            return int(raw) if raw else None
        return None

    return {
        'date': f'{year}-{month}-{day}',
        'personnel':   extract(r'Military\s+personnel.*?aprx\.\s*([\d\s,]+)\s*people'),
        'tanks':       extract(r'Tanks\s*[—–-]\s*([\d,\s]+)'),
        'afv':         extract(r'Armored\s+fighting\s+vehicle\s*[—–-]\s*([\d,\s]+)'),
        'artillery':   extract(r'Artillery\s+systems\s*[—–-]\s*([\d,\s]+)'),
        'mlrs':        extract(r'MLRS.*?[—–-]\s*([\d,\s]+)'),
        'airdef':      extract(r'Anti-aircraft\s+warfare\s*[—–-]\s*([\d,\s]+)'),
        'planes':      extract(r'Planes\s*[—–-]\s*([\d,\s]+)'),
        'helicopters': extract(r'Helicopters\s*[—–-]\s*([\d,\s]+)'),
        'uav':         extract(r'(?:UAV|[Uu]nmanned aerial|[Dd]rone).*?[—–\-]\s*([\d,\s]+)'),
        'missiles':    extract(r'Cruise\s+missiles\s*[—–-]\s*([\d,\s]+)'),
        'ships':       extract(r'Ships.*?[—–-]\s*([\d,\s]+)'),
        'submarines':  extract(r'Submarines\s*[—–-]\s*([\d,\s]+)'),
        'vehicles':    extract(r'Cars\s+and\s+cisterns\s*[—–-]\s*([\d,\s]+)'),
        'special':     extract(r'Special\s+equipment\s*[—–-]\s*([\d,\s]+)'),
        'robots':      extract(r'Ground\s+robotic\s+systems\s*[—–-]\s*([\d,\s]+)'),
    }


def fetch_latest(html: str = None) -> dict | None:
    """Lekéri (vagy a megadott HTML-ből kinyeri) a legfrissebb nap adatait."""
    if html is None:
        log.info(f'Letöltés: {URL}')
        try:
            resp = requests.get(URL, timeout=30, headers={'User-Agent': 'Mozilla/5.0'})
            resp.raise_for_status()
            html = resp.text
        except Exception as e:
            log.error(f'Hálózati hiba: {e}')
            return None

    data, change = parse_latest_dom(html)
    if data and data.get('personnel') and data.get('tanks'):
        log.info(f"DOM-kinyerés OK: {data['date']}")
    else:
        log.warning('DOM-alapú kinyerés sikertelen — tartalék (regex) mód.')
        data, change = parse_latest_regex(html), {}
        if not data:
            log.error('Nem található dátum az oldalon.')
            return None

    for f in FIELDS:
        data.setdefault(f, None)

    if not data['personnel'] or not data['tanks']:
        log.error('Nem sikerült kinyerni az adatokat. Változott az oldal struktúrája?')
        log.debug(f'Részleges adat: {data}')
        return None

    if data['uav'] == 0 and data['personnel'] > 500000:
        log.warning('UAV mező 0 de a személyi veszteség >500k — valószínű parse-hiba. '
                    'A rekord NEM kerül be a DAILY tömbbe.')
        return None

    missing = [k for k in FIELDS if data[k] is None]
    for k in missing:
        log.warning(f'Hiányzó mező a mai napra (None marad, run() pótolja előző értékkel): {k}')

    data['_change'] = change   # forrás szerinti napi változás (csak ellenőrzéshez)
    log.info(f"Kinyert adat: dátum={data['date']}, személyi={data['personnel']}, "
             f"tank={data['tanks']}, uav={data['uav']}, rakéta={data['missiles']}")
    return data


def _build_entry_line(d: dict) -> str:
    """Összerakja egy DAILY bejegyzés JS-sorát."""
    return (
        f'  {{date:"{d["date"]}",'
        f'personnel:{d["personnel"]},'
        f'tanks:{d["tanks"]},'
        f'afv:{d["afv"]},'
        f'artillery:{d["artillery"]},'
        f'mlrs:{d["mlrs"]},'
        f'airdef:{d["airdef"]},'
        f'planes:{d["planes"]},'
        f'helicopters:{d["helicopters"]},'
        f'uav:{d["uav"]},'
        f'missiles:{d["missiles"]},'
        f'ships:{d["ships"]},'
        f'submarines:{d["submarines"]},'
        f'vehicles:{d["vehicles"]},'
        f'special:{d["special"]},'
        f'robots:{d["robots"]}}}'
    )


def update_html(html_path: str, new_data: dict) -> bool:
    """
    Beolvassa az index.html-t, ellenőrzi van-e már az adat,
    és ha nem, hozzáfűzi a DAILY tömbhöz.

    Ha a dátum már létezik de az ott tárolt UAV érték 0 (korábbi parse-hiba),
    ÉS az új UAV érték > 0, akkor a meglévő sort felülírja (self-healing).

    Visszatér: True ha változott, False ha nem.
    """
    log.info(f'HTML olvasása: {html_path}')
    with open(html_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # A DAILY tömbben a kulcs idézőjel NÉLKÜL szerepel: {date:"2026-06-18",...}
    date_key = f'date:"{new_data["date"]}"'
    date_exists = date_key in content

    if date_exists:
        existing_pattern = re.compile(
            r'\{date:"' + re.escape(new_data["date"]) + r'"[^}]+uav:(\d+)[^}]*\}'
        )
        m_existing = existing_pattern.search(content)
        stored_uav = int(m_existing.group(1)) if m_existing else -1

        if stored_uav == 0 and new_data['uav'] > 0:
            log.info(f'{new_data["date"]}: tárolt UAV=0 (parse-hiba), új érték={new_data["uav"]} — felülírás.')
            new_content = content.replace(m_existing.group(0), _build_entry_line(new_data), 1)
            with open(html_path, 'w', encoding='utf-8') as f:
                f.write(new_content)
            log.info(f'Felülírva: {new_data["date"]} → {html_path}')
            return True
        log.info(f'Már létezik: {new_data["date"]} (UAV={stored_uav}) — nincs teendő.')
        return False

    last_entry_pattern = r'(\{date:"[^"]+",personnel:\d+[^}]+\})\s*\n\s*\];'
    m = re.search(last_entry_pattern, content)
    if not m:
        log.error('Nem található a DAILY tömb vége az index.html-ben.')
        return False

    new_content = content.replace(m.group(0), m.group(1) + ',\n' + _build_entry_line(new_data) + '\n];')
    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(new_content)
    log.info(f'Hozzáfűzve: {new_data["date"]} → {html_path}')
    return True


def _get_last_entry(html_path: str) -> dict | None:
    """Az index.html DAILY tömbjének utolsó bejegyzése dict-ként."""
    try:
        with open(html_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except OSError as e:
        log.warning(f'Előző bejegyzés beolvasása sikertelen ({html_path}): {e}')
        return None
    m = re.search(r'(\{date:"[^"]+",personnel:\d+[^}]+\})\s*\n\s*\];', content)
    if not m:
        log.warning('Nem található korábbi bejegyzés a DAILY tömbben — pótlás nem lehetséges.')
        return None
    result = {}
    for k, v in re.findall(r'(\w+):"?([^",}]+)"?', m.group(1)):
        if k == 'date':
            result[k] = v
        else:
            try:
                result[k] = int(v)
            except ValueError:
                pass
    return result


def run(html_path: str = None, source_html_path: str = None) -> bool:
    if not html_path:
        html_path = DEFAULT_HTML

    page_html = None
    if source_html_path:
        with open(source_html_path, 'r', encoding='utf-8') as f:
            page_html = f.read()
    data = fetch_latest(page_html)
    if not data:
        return False
    change = data.pop('_change', {})

    last = _get_last_entry(html_path)

    # Hiányzó mezők pótlása az előző napi értékkel (kumulatív mező sosem csökken,
    # ezért a tegnapi érték jobb becslés egy hibás 0-nál).
    missing_fields = [k for k in FIELDS if data.get(k) is None]
    for k in missing_fields:
        if last and isinstance(last.get(k), int):
            data[k] = last[k]
            log.warning(f'{k}: mai kinyerés sikertelen — előző napi érték átvéve ({last[k]}).')
        else:
            data[k] = 0
            log.warning(f'{k}: mai kinyerés sikertelen ÉS nincs előző érték — 0-ra állítva.')

    # Carry-forward gyanú: a forrás napi változást jelez, de a mi értékünk nem mozdult
    if last:
        for k in FIELDS:
            if change.get(k, 0) > 0 and last.get(k) == data[k]:
                log.warning(f'{k}: a forrás +{change[k]} napi változást jelez, de az érték '
                            f'megegyezik a tegnapival ({data[k]}) — ellenőrizd a kinyerést!')

    changed = update_html(html_path, data)
    if changed:
        log.info('index.html sikeresen frissítve.')
    return changed


if __name__ == '__main__':
    args = sys.argv[1:]
    src = None
    if '--html' in args:
        i = args.index('--html'); src = args[i + 1]; del args[i:i + 2]
    ok = run(args[0] if args else None, src)
    # Exit 0 akkor is, ha nem volt változás (nincs commit szükséges)
    sys.exit(0)
