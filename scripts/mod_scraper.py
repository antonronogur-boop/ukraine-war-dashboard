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

    # FONTOS: a mezőket KIZÁRÓLAG az aktuális nap blokkjában keressük
    # (a dátum-fejtől a következő "Dynamics of losses" szakaszig).
    # Ha a TELJES oldal szövegében keresnénk (mint korábban), a regex
    # véletlenül egy másik, nem-releváns helyen is "egyezhet" — pl. a
    # diagram-jelmagyarázat összefolyt szövegében
    # ("...UAVCruise missilesShips (boats)...", szám nélkül utána),
    # vagy egy kapcsolódó cikk-ajánlóban. Pontosan ez okozta a
    # "Cruise missiles" → 0 hibát 2026-06-21, 22 és 23-án: a label
    # kétszer szerepelt a lap szövegében, és a kinyerés rossz helyen
    # próbált illeszkedni.
    block_end = text.find('Dynamics of losses', date_match.end())
    if block_end == -1:
        block_end = len(text)
    block_text = text[date_match.start():block_end]

    def extract(pattern):
        # Először normál módban próbálkozunk; ha nem sikerül, re.DOTALL-lal
        # (a minfin oldalon egyes mezők értéke más HTML-elembe kerülhet → sortörés)
        m = re.search(pattern, block_text) or re.search(pattern, block_text, re.DOTALL)
        if m:
            raw = re.sub(r'[^\d]', '', m.group(1))
            return int(raw) if raw else None
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
        'uav':         extract(r'(?:UAV|[Uu]nmanned aerial|[Dd]rone).*?[—–\-]\s*([\d,\s]+)'),
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

    # Sanity check: ha UAV 0-t kapott de minden egyéb mező nagyon magas,
    # ez szinte biztosan parse-hiba (nem az, hogy aznap tényleg 0 UAV veszett el).
    # Inkább None-t adunk vissza, mint hogy egy hibás 0-t írjunk DAILY-be.
    if data['uav'] == 0 and data['personnel'] > 500000:
        log.warning(
            'UAV mező 0-nak adódott de a személyi veszteség >500k — valószínű parse-hiba. '
            'Ellenőrizd az oldal struktúráját. A rekord NEM kerül be a DAILY tömbbe.'
        )
        return None

    # FONTOS: a hiányzó mezőket ITT NEM állítjuk 0-ra!
    # Közvetlen nyers-HTML vizsgálattal (2026-06-23) bebizonyosodott, hogy a
    # "Cruise missiles" felirat a minfin oldal teljes szöveges tartalmában
    # (a BeautifulSoup.get_text()-tel egyenértékű kinyerésben) PONTOSAN
    # egyszer fordul elő — egy <select> legördülő opciójában —, és SOHA
    # nincs mellette szám, sem 06-21, sem 06-22, sem 06-23-án. Ez nem
    # regex-hiba: az adat egyszerűen nincs ott a requests.get()-tel látható
    # HTML-ben. Emiatt a mezőt None-on hagyjuk, és a run() függvény pótolja
    # az előző napi (utolsó mentett) értékkel — SOHA nem írunk be hibás 0-t
    # egy korábban pozitív kumulatív mezőhöz.
    missing = [k for k, v in data.items() if k != 'date' and v is None]
    for k in missing:
        log.warning(f'Hiányzó mező a mai napra (None marad, run() pótolja előző értékkel): {k}')

    log.info(f"Kinyert adat: személyi={data['personnel']}, tank={data['tanks']}, uav={data['uav']}")
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

    Felülírási logika (korrupt-adat-javítás):
      Ha a dátum már létezik de az ott tárolt UAV érték 0 (korábbi parse-hiba
      miatt bekerült hibás rekord), ÉS az új UAV érték > 0 (a mai scraping
      sikeresen kinyerte), akkor a meglévő sort FELÜLÍRJA a helyes adattal.
      Ez lehetővé teszi a self-healing viselkedést: a scraper automatikusan
      kijavítja a régebb beírt nullás rekordokat, amint az oldal ismét
      helyesen parsolható.

    Visszatér: True ha változott, False ha nem.
    """
    log.info(f'HTML olvasása: {html_path}')
    with open(html_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # FONTOS: a DAILY tömbben a kulcs idézőjel NÉLKÜL szerepel (JS object-literal
    # szintaxis: {date:"2026-06-18",...}), NEM JSON-stílusban ("date":"...").
    # A korábbi verzió a JSON-stílusú kulcsot kereste, ami SOHA nem egyezett —
    # ezért date_exists mindig False lett, és minden futás új sort fűzött hozzá,
    # még akkor is, ha az adott dátum már szerepelt (pl. ütemezett + kézi indítás
    # ugyanazon a napon). Ez okozta a duplikált bejegyzéseket (2026-06-11,
    # 2026-06-18, 2026-06-19), amik hamis 0-s napi UAV deltát eredményeztek.
    date_key = f'date:"{new_data["date"]}"'
    date_exists = date_key in content

    if date_exists:
        # Megvan a dátum — megnézzük a tárolt UAV értéket
        # Pattern: {date:"YYYY-MM-DD",...,uav:NNN,...}
        existing_pattern = re.compile(
            r'\{date:"' + re.escape(new_data["date"]) + r'"[^}]+uav:(\d+)[^}]*\}'
        )
        m_existing = existing_pattern.search(content)
        stored_uav = int(m_existing.group(1)) if m_existing else -1

        if stored_uav == 0 and new_data['uav'] > 0:
            # Korrupt rekord: felülírjuk
            log.info(
                f'{new_data["date"]}: tárolt UAV=0 (parse-hiba), '
                f'új érték={new_data["uav"]} — felülírás.'
            )
            old_line = m_existing.group(0)
            new_line = _build_entry_line(new_data)
            new_content = content.replace(old_line, new_line, 1)
            with open(html_path, 'w', encoding='utf-8') as f:
                f.write(new_content)
            log.info(f'Felülírva: {new_data["date"]} → {html_path}')
            return True
        else:
            log.info(
                f'Már létezik: {new_data["date"]} (UAV={stored_uav}) — nincs teendő.'
            )
            return False

    # Új dátum — hozzáfűzés a DAILY tömb végéhez
    # Pattern: az utolsó {...} a tömbben, amit }] követ
    last_entry_pattern = r'(\{date:"[^"]+",personnel:\d+[^}]+\})\s*\n\s*\];'
    m = re.search(last_entry_pattern, content)
    if not m:
        log.error('Nem található a DAILY tömb vége az index.html-ben.')
        return False

    new_line = _build_entry_line(new_data)

    # Beillesztés: az utolsó } után vesszőt teszünk, majd az új sort
    new_content = content.replace(
        m.group(0),
        m.group(1) + ',\n' + new_line + '\n];'
    )

    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(new_content)

    log.info(f'Hozzáfűzve: {new_data["date"]} → {html_path}')
    return True


def _get_last_entry(html_path: str) -> dict | None:
    """
    Beolvassa az index.html DAILY tömbjének UTOLSÓ (legutóbb mentett)
    bejegyzését dict-ként. A run() ezt használja arra, hogy egy mai napon
    sikertelenül kinyert mezőt (None) az előző napi értékkel pótoljon,
    helyette hogy hibás 0-t írjon be.
    """
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

    entry_str = m.group(1)
    pairs = re.findall(r'(\w+):"?([^",}]+)"?', entry_str)
    result = {}
    for k, v in pairs:
        if k == 'date':
            result[k] = v
        else:
            try:
                result[k] = int(v)
            except ValueError:
                pass
    return result


def run(html_path: str = None) -> bool:
    if not html_path:
        html_path = DEFAULT_HTML

    data = fetch_latest()
    if not data:
        return False

    # Hiányzó mezők pótlása: SOHA ne kerüljön 0 egy kumulatív mezőbe csak
    # azért, mert a mai kinyerés sikertelen volt — pontosan ez okozta a
    # "missiles 4787 → 0" hibát 3 napon át (06-21, 06-22, 06-23), amit a
    # validate_daily.py helyesen blokkolt. Egy kumulatív veszteségszám
    # sosem csökkenhet, ezért a tegnapi (utolsó mentett) érték mindig jobb
    # becslés egy hibás 0-nál — legrosszabb esetben a delta annyi napra 0
    # marad, amíg az oldal újra megadja az értéket.
    missing_fields = [k for k, v in data.items() if k != 'date' and v is None]
    if missing_fields:
        last = _get_last_entry(html_path)
        for k in missing_fields:
            if last and isinstance(last.get(k), int):
                data[k] = last[k]
                log.warning(f'{k}: mai kinyerés sikertelen — előző napi érték átvéve ({last[k]}).')
            else:
                data[k] = 0
                log.warning(f'{k}: mai kinyerés sikertelen ÉS nincs előző érték — 0-ra állítva.')

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
