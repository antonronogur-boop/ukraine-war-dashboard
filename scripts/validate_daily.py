"""
validate_daily.py  –  Sanity gate: DAILY tömb validáció commit előtt

Futtatás:
    python scripts/validate_daily.py index.html

Mit ellenőriz:
    - Minden kumulatív érték monoton növekvő-e (nem csökken soha)
    - Nincs-e 0-ra esett kritikus mező ahol a szomszéd magas értéket mutat
    - Az utolsó rekord dátuma nem régebbi-e 3 napnál

Ha bármelyik ellenőrzés megbukik: exit(1) → a workflow leáll commit nélkül.
Ha minden rendben: exit(0) → a workflow folytatódik és commit-ol.
"""

import re
import sys
import json
import logging
from datetime import date as Date

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)

# Ha ezek a mezők 0-ra esnek és a szomszéd>küszöb, az szinte biztosan parse-hiba
CRITICAL_ZERO_CHECK = {
    'personnel': 100_000,
    'tanks':         500,
    'uav':        10_000,
    'artillery':   5_000,
    'vehicles':    5_000,
    'missiles':      500,   # missiles is ide kerül
}

# Mezők amelyekre NEM futtatunk nagy-ugrás ellenőrzést
# (első megjelenéskor óriási ugrás lehet, pl. robots 0→1306)
SKIP_SPIKE_CHECK = {'robots'}


def extract_daily(html_path: str) -> list[dict]:
    with open(html_path, 'r', encoding='utf-8') as f:
        content = f.read()

    m = re.search(r'const DAILY\s*=\s*(\[.*?\]);', content, re.DOTALL)
    if not m:
        log.error('Nem található DAILY tömb az index.html-ben.')
        sys.exit(1)

    js = m.group(1)
    js = re.sub(r'(\w+):', r'"\1":', js)
    js = re.sub(r'"(\w+)":', r'"\1":', js)

    try:
        return json.loads(js)
    except json.JSONDecodeError as e:
        log.error(f'JSON parse hiba: {e}')
        sys.exit(1)


def validate(data: list[dict]) -> bool:
    errors = []
    warnings = []

    if not data:
        errors.append('A DAILY tömb üres.')
        return False

    # 1. Utolsó rekord dátuma nem túl régi?
    last_date = Date.fromisoformat(data[-1]['date'])
    age = (Date.today() - last_date).days
    if age > 3:
        warnings.append(f'Utolsó rekord {age} napos ({last_date}) — elmaradt scraping gyanúja.')

    # 2. Monoton növekedés + kritikus 0-ellenőrzés
    fields = [k for k in data[0] if k != 'date']

    for i in range(1, len(data)):
        cur = data[i]
        prev = data[i - 1]
        date_cur = cur['date']

        for f in fields:
            cur_val = cur.get(f, 0) or 0
            prev_val = prev.get(f, 0) or 0
            delta = cur_val - prev_val

            # Kumulatív érték csökkent — ez mindig hiba
            if delta < 0:
                errors.append(
                    f'{date_cur}: {f} CSÖKKENT: {prev_val} → {cur_val} (delta={delta})'
                )

            # Kritikus mező nullára esett ahol a korábbi érték magas volt
            if f in CRITICAL_ZERO_CHECK:
                threshold = CRITICAL_ZERO_CHECK[f]
                if cur_val == 0 and prev_val > threshold:
                    errors.append(
                        f'{date_cur}: {f} = 0, előző érték {prev_val} volt '
                        f'(küszöb: {threshold}) — valószínű parse-hiba!'
                    )

    # Összesítés
    for w in warnings:
        log.warning(f'  FIGYELMEZTETÉS: {w}')
    for e in errors:
        log.error(f'  HIBA: {e}')

    if errors:
        log.error(f'Validáció MEGBUKOTT — {len(errors)} hiba. Commit NEM történik.')
        return False

    if warnings:
        log.warning(f'Validáció OK (de {len(warnings)} figyelmeztetés van). Commit folytatódik.')
    else:
        log.info('Validáció OK — minden mező rendben.')

    return True


def main():
    html_path = sys.argv[1] if len(sys.argv) > 1 else 'index.html'
    log.info(f'Validáció: {html_path}')
    data = extract_daily(html_path)
    log.info(f'{len(data)} rekord betöltve ({data[0]["date"]} → {data[-1]["date"]})')
    ok = validate(data)
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
