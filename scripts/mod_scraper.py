"""
mod_scraper.py  –  Napi MoD veszteségadat frissítő

Forrás:  https://index.minfin.com.ua/en/russian-invading/casualties/
Cél:     Hozzáfűzi a legfrissebb napot a DAILY tömbhöz az index.html-ben

Futtatás:
    python scripts/mod_scraper.py               # default: index.html
    python scripts/mod_scraper.py index.html    # explicit elérési út

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


def fetch_latest() -> dict | None:
    """Lekéri a minfin.com.ua-ról a legfrissebb nap adatait."""
    log.info(f'Letöltés: {URL}')
    try:
        resp = requests.get(URL, timeout=30, headers={'User-Agent': 'Mozilla/5.0'})
        resp.raise_for_status()
    except Exception as e:
        log.error(f'Hálózati hiba: {e}')
        return None

    soup = BeautifulSoup(resp.text, 'html.parser')
    text = soup.get_text('\n')

    # Legfrissebb dátum keresése (DD.MM.YYYY formátum)
    date_match = re.search(r'(\d{2})\.(\d{2})\.(\d{4})', text)
    if not date_match:
        log.error('Nem található dátum az oldalon.')
        return None

    day, month, year = date_match.groups()
    date_str = f'{year}-{month}-{day}'
    log.info(f'Legfrissebb adat dátuma: {date_str}')

    def extract(pattern):
        m = re.search(pattern, text)
        if m:
            return int(re.sub(r'[^\d]', '', m.group(1)))
        return None

    # Értékek kinyerése
    data = {
        'date': date_str,
        'personnel': extract(r'Military personnel.*?aprx\.\s*([\d\s,]+)\s*people'),
        'tanks':       extract(r'Tanks\s*[—–-]\s*([\d,\s]+)'),
        'afv':         extract(r'Armored fighting vehicle\s*[—–-]\s*([\d,\s]+)'),
        'artillery':   extract(r'Artillery systems\s*[—–-]\s*([\d,\s]+)'),
        'mlrs':        extract(r'MLRS.*?[—–-]\s*([\d,\s]+)'),
        'airdef':      extract(r'Anti-aircraft warfare\s*[—–-]\s*([\d,\s]+)'),
        'planes':      extract(r'Planes\s*[—–-]\s*([\d,\s]+)'),
        'helicopters': extract(r'Helicopters\s*[—–-]\s*([\d,\s]+)'),
        'uav':         extract(r'UAV.*?[—–-]\s*([\d,\s]+)'),
        'missiles':    extract(r'Cruise missiles\s*[—–-]\s*([\d,\s]+)'),
        'ships':       extract(r'Ships.*?[—–-]\s*([\d,\s]+)'),
        'submarines':  extract(r'Submarines\s*[—–-]\s*([\d,\s]+)'),
        'vehicles':    extract(r'Cars and cisterns\s*[—–-]\s*([\d,\s]+)'),
        'special':     extract(r'Special equipment\s*[—–-]\s*([\d,\s]+)'),
        'robots':      extract(r'Ground robotic systems\s*[—–-]\s*([\d,\s]+)'),
    }

    # Ellenőrzés: a legfontosabb mezők megvannak-e
    if not data['personnel'] or not data['tanks']:
        log.error('Nem sikerült kinyerni az adatokat. Változott az oldal struktúrája?')
        log.debug(f'Részleges adat: {data}')
        return None

    # Hiányzó értékek 0-ra állítása
    for k, v in data.items():
        if k != 'date' and v is None:
            data[k] = 0
            log.warning(f'Hiányzó mező, 0-ra állítva: {k}')

    log.info(f"Kinyert adat: személyi={data['personnel']}, tank={data['tanks']}, uav={data['uav']}")
    return data


def update_html(html_path: str, new_data: dict) -> bool:
    """
    Beolvassa az index.html-t, ellenőrzi van-e már az adat,
    és ha nem, hozzáfűzi a DAILY tömbhöz.
    Visszatér: True ha változott, False ha nem.
    """
    log.info(f'HTML olvasása: {html_path}')
    with open(html_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Ellenőrzés: már benne van-e ez a dátum?
    if f'"date":"{new_data["date"]}"' in content:
        log.info(f'Már létezik ez a dátum: {new_data["date"]} — nincs teendő.')
        return False

    # Az utolsó sor megkeresése a DAILY tömbben
    # Pattern: az utolsó {...} a tömbben, amit }] követ
    last_entry_pattern = r'(\{date:"[^"]+",personnel:\d+[^}]+\})\s*\n\s*\];'
    m = re.search(last_entry_pattern, content)
    if not m:
        log.error('Nem található a DAILY tömb vége az index.html-ben.')
        return False

    # Új sor összeállítása
    new_line = (
        f'  {{date:"{new_data["date"]}",'
        f'personnel:{new_data["personnel"]},'
        f'tanks:{new_data["tanks"]},'
        f'afv:{new_data["afv"]},'
        f'artillery:{new_data["artillery"]},'
        f'mlrs:{new_data["mlrs"]},'
        f'airdef:{new_data["airdef"]},'
        f'planes:{new_data["planes"]},'
        f'helicopters:{new_data["helicopters"]},'
        f'uav:{new_data["uav"]},'
        f'missiles:{new_data["missiles"]},'
        f'ships:{new_data["ships"]},'
        f'submarines:{new_data["submarines"]},'
        f'vehicles:{new_data["vehicles"]},'
        f'special:{new_data["special"]},'
        f'robots:{new_data["robots"]}}}'
    )

    # Beillesztés: az utolsó } után vesszőt teszünk, majd az új sort
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
        return False

    changed = update_html(html_path, data)
    if changed:
        log.info('index.html sikeresen frissítve.')
    return changed


if __name__ == '__main__':
    html = sys.argv[1] if len(sys.argv) > 1 else None
    ok = run(html)
    # Exit 0 ha volt változás (Actions commit-ot triggerel),
    # Exit 0 is ha nem volt változás (nincs commit szükséges)
    sys.exit(0)
