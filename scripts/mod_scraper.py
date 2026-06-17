"""
mod_scraper.py  –  Napi MoD veszteségadat frissítő

Forrás:  https://index.minfin.com.ua/en/russian-invading/casualties/
Cél:     Hozzáfűzi a legfrissebb napot a DAILY tömbhöz az index.html-ben

Futtatás:
    python scripts/mod_scraper.py               # default: index.html
    python scripts/mod_scraper.py index.html    # explicit elérési út

Változások az előző verzióhoz képest:
    - Minden kritikus mezőre kiterjesztett sanity check (nem csak UAV)
    - Részletes parse-hiba log: melyik mező, mit talált (vagy nem)
    - Ha a scraping sikertelen, exit(1) → a workflow hibával áll le
      (korábban exit(0) volt, ami csendben sikert jelzett)
    - Timeout növelve 30→45s (minfin időnként lassú)
"""

import re
import sys
import os
import logging
import requests
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)

URL = 'https://index.minfin.com.ua/en/russian-invading/casualties/'
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_HTML = os.path.normpath(os.path.join(SCRIPT_DIR, '..', 'index.html'))

# Ha bármelyik kritikus mező 0, de a személyi adat >X, az parse-hiba
SANITY_PERSONNEL_THRESHOLD = 500_000
CRITICAL_FIELDS = {
    'tanks':      100,    # valódi tank-veszteség soha nem 0 ennyi személyi mellett
    'afv':        500,
    'artillery':  500,
    'uav':      10_000,
    'vehicles': 5_000,
}


def fetch_latest() -> dict | None:
    log.info(f'Letöltés: {URL}')
    try:
        resp = requests.get(URL, timeout=45, headers={
            'User-Agent': 'Mozilla/5.0 (compatible; war-losses-dashboard/1.0)'
        })
        resp.raise_for_status()
    except Exception as e:
        log.error(f'Hálózati hiba: {e}')
        return None

    soup = BeautifulSoup(resp.text, 'html.parser')
    text = soup.get_text('\n')

    date_match = re.search(r'(\d{2})\.(\d{2})\.(\d{4})', text)
    if not date_match:
        log.error('Nem található dátum az oldalon.')
        return None

    day, month, year = date_match.groups()
    date_str = f'{year}-{month}-{day}'
    log.info(f'Legfrissebb adat dátuma: {date_str}')

    def extract(field_name: str, pattern: str):
        """Regex kinyerés normál + DOTALL fallback-kel. Részletes logolással."""
        m = re.search(pattern, text)
        if m:
            raw = re.sub(r'[^\d]', '', m.group(1))
            val = int(raw) if raw else None
            if val is None:
                log.warning(f'{field_name}: regex talált de érték üres. Pattern: {pattern!r}')
            return val

        m = re.search(pattern, text, re.DOTALL)
        if m:
            raw = re.sub(r'[^\d]', '', m.group(1))
            val = int(raw) if raw else None
            log.info(f'{field_name}: DOTALL fallback-kel sikerült.')
            return val

        log.warning(f'{field_name}: NEM TALÁLT EGYEZÉS. Pattern: {pattern!r}')
        return None

    data = {
        'date':        date_str,
        'personnel':   extract('personnel',   r'Military personnel.*?aprx\.\s*([\d\s,]+)\s*people'),
        'tanks':       extract('tanks',       r'Tanks\s*[—–-]\s*([\d,\s]+)'),
        'afv':         extract('afv',         r'Armored fighting vehicle\s*[—–-]\s*([\d,\s]+)'),
        'artillery':   extract('artillery',   r'Artillery systems\s*[—–-]\s*([\d,\s]+)'),
        'mlrs':        extract('mlrs',        r'MLRS.*?[—–-]\s*([\d,\s]+)'),
        'airdef':      extract('airdef',      r'Anti-aircraft warfare\s*[—–-]\s*([\d,\s]+)'),
        'planes':      extract('planes',      r'Planes\s*[—–-]\s*([\d,\s]+)'),
        'helicopters': extract('helicopters', r'Helicopters\s*[—–-]\s*([\d,\s]+)'),
        'uav':         extract('uav',         r'(?:UAV|[Uu]nmanned aerial|[Dd]rone).*?[—–\-]\s*([\d,\s]+)'),
        'missiles':    extract('missiles',    r'(?:Cruise\s+)?[Mm]issiles?\s*[—–-]\s*([\d,\s]+)'),
        'ships':       extract('ships',       r'Ships.*?[—–-]\s*([\d,\s]+)'),
        'submarines':  extract('submarines',  r'Submarines\s*[—–-]\s*([\d,\s]+)'),
        'vehicles':    extract('vehicles',    r'Cars and cisterns\s*[—–-]\s*([\d,\s]+)'),
        'special':     extract('special',     r'Special equipment\s*[—–-]\s*([\d,\s]+)'),
        'robots':      extract('robots',      r'Ground robotic systems\s*[—–-]\s*([\d,\s]+)'),
    }

    # Legfontosabb mezők: ha hiányoznak, az egész rekord eldobható
    if not data['personnel'] or not data['tanks']:
        log.error(
            f'Kritikus mezők hiányoznak (personnel={data["personnel"]}, tanks={data["tanks"]}). '
            f'Változott az oldal struktúrája? Rekord NEM kerül be.'
        )
        return None

    # Kiterjesztett sanity check minden kritikus mezőre
    personnel = data['personnel'] or 0
    if personnel > SANITY_PERSONNEL_THRESHOLD:
        failed = []
        for field, threshold in CRITICAL_FIELDS.items():
            val = data.get(field) or 0
            if val == 0:
                failed.append(field)
            elif val < threshold:
                log.warning(
                    f'Gyanúsan alacsony érték: {field}={val} '
                    f'(elvárható minimum ~{threshold}, személyi={personnel:,})'
                )
        if failed:
            log.error(
                f'Parse-hiba gyanú: a következő mezők 0-ra estek '
                f'de személyi veszteség={personnel:,}: {failed}. '
                f'Rekord NEM kerül be a DAILY tömbbe.'
            )
            return None

    # Hiányzó (None) értékek 0-ra állítása — csak a nem kritikus mezőknél
    parse_warnings = []
    for k, v in data.items():
        if k != 'date' and v is None:
            data[k] = 0
            parse_warnings.append(k)

    if parse_warnings:
        log.warning(f'Hiányzó mezők, 0-ra állítva: {parse_warnings}')

    log.info(
        f"Kinyert adat: személyi={data['personnel']:,}, "
        f"tank={data['tanks']}, uav={data['uav']:,}, "
        f"tüzérség={data['artillery']}"
    )
    return data


def _build_entry_line(d: dict) -> str:
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
    log.info(f'HTML olvasása: {html_path}')
    with open(html_path, 'r', encoding='utf-8') as f:
        content = f.read()

    date_key = f'"date":"{new_data["date"]}"'
    date_exists = date_key in content

    if date_exists:
        existing_pattern = re.compile(
            r'\{date:"' + re.escape(new_data["date"]) + r'"[^}]+uav:(\d+)[^}]*\}'
        )
        m_existing = existing_pattern.search(content)
        stored_uav = int(m_existing.group(1)) if m_existing else -1

        if stored_uav == 0 and new_data['uav'] > 0:
            log.info(
                f'{new_data["date"]}: tárolt UAV=0 (parse-hiba), '
                f'új érték={new_data["uav"]} — self-healing felülírás.'
            )
            old_line = m_existing.group(0)
            new_line = _build_entry_line(new_data)
            new_content = content.replace(old_line, new_line, 1)
            with open(html_path, 'w', encoding='utf-8') as f:
                f.write(new_content)
            log.info(f'Felülírva: {new_data["date"]} → {html_path}')
            return True
        else:
            log.info(f'Már létezik: {new_data["date"]} (UAV={stored_uav}) — nincs teendő.')
            return False

    last_entry_pattern = r'(\{date:"[^"]+",personnel:\d+[^}]+\})\s*\n\s*\];'
    m = re.search(last_entry_pattern, content)
    if not m:
        log.error('Nem található a DAILY tömb vége az index.html-ben.')
        return False

    new_line = _build_entry_line(new_data)
    new_content = content.replace(
        m.group(0),
        m.group(1) + ',\n' + new_line + '\n];'
    )

    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(new_content)

    log.info(f'Hozzáfűzve: {new_data["date"]} → {html_path}')
    return True


def run(html_path: str = None) -> bool:
    if not html_path:
        html_path = DEFAULT_HTML

    data = fetch_latest()
    if not data:
        # Hiba esetén exit(1) → a workflow hibával áll le és látható lesz az Actions logban
        log.error('Scraping sikertelen — a workflow hibával áll le.')
        sys.exit(1)

    changed = update_html(html_path, data)
    if changed:
        log.info('index.html sikeresen frissítve.')
    return changed


if __name__ == '__main__':
    html = sys.argv[1] if len(sys.argv) > 1 else None
    run(html)
    sys.exit(0)
