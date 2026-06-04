"""
run_all.py  –  Teljes pipeline orchestrátor

Futtatás (repo gyökeréből):
    python scripts/run_all.py

Mit csinál sorban:
    1. scraper_RUS.py  – Oryx scraping (RUS) → data/xyz_RUS.xlsx
    2. scraper_UKR.py  – Oryx scraping (UKR) → data/xyz_UKR.xlsx
    3. analysis_losses.py  – heti delta kalkuláció mindkét fájlra
    4. collector.py        – C_SUM lap generálás mindkét fájlra
    5. oryx_export.py      – JSON export a dashboardhoz → oryx_data.json

Kimenet:
    - data/xyz_RUS.xlsx  (frissítve)
    - data/xyz_UKR.xlsx  (frissítve)
    - oryx_data.json     (újragenerálva)
"""

import os
import sys
import logging
import subprocess
from pathlib import Path

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)

# Könyvtárak
ROOT = Path(__file__).parent.parent
SCRIPTS = ROOT / 'scripts'
DATA = ROOT / 'data'


def run_step(script: str, args: list = None, description: str = ''):
    """Egy Python scriptet futtat, hibajelzéssel."""
    cmd = [sys.executable, str(SCRIPTS / script)] + (args or [])
    log.info(f"▶ {description or script}")
    result = subprocess.run(cmd, capture_output=False, text=True)
    if result.returncode != 0:
        log.error(f"✗ Hiba: {script} (visszatérési kód: {result.returncode})")
        return False
    log.info(f"✓ Kész: {script}")
    return True


def main():
    log.info("=" * 60)
    log.info("Pipeline indítása")
    log.info("=" * 60)

    # Adatkönyvtár ellenőrzése
    DATA.mkdir(exist_ok=True)

    steps = [
        ('scraper_RUS.py', [str(DATA / 'xyz_RUS.xlsx')], 'Oryx scraping — Oroszország'),
        ('scraper_UKR.py', [str(DATA / 'xyz_UKR.xlsx')], 'Oryx scraping — Ukrajna'),
        ('analysis_losses.py', [str(DATA / 'xyz_RUS.xlsx')], 'Delta kalkuláció — RUS'),
        ('analysis_losses.py', [str(DATA / 'xyz_UKR.xlsx')], 'Delta kalkuláció — UKR'),
        ('collector.py', [str(DATA / 'xyz_RUS.xlsx')], 'C_SUM generálás — RUS'),
        ('collector.py', [str(DATA / 'xyz_UKR.xlsx')], 'C_SUM generálás — UKR'),
        ('oryx_export.py', [], 'JSON export → oryx_data.json'),
    ]

    failed = []
    for script, args, desc in steps:
        if not run_step(script, args, desc):
            failed.append(script)

    log.info("=" * 60)
    if failed:
        log.warning(f"Befejezve hibákkal: {failed}")
        sys.exit(1)
    else:
        log.info("Pipeline sikeresen befejezve.")
        sys.exit(0)


if __name__ == '__main__':
    main()
