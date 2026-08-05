# -*- coding: utf-8 -*-
"""fetch_with_backoff.py — kozos, udvarias lekero az Oryx-scraperekhez.

A HIBA, AMIT JAVIT (2026-08-04, "Heti Oryx adatfrissites" #15):

    RUS scraping (Oryx)  ... 9s   OK
    UKR scraping (Oryx)  ... 1s   ERROR 429 Too Many Requests
                                  -> google.com/sorry/index?continue=...

Az Oryx Blogspoton fut, tehat Google-infrastrukturan. A ket lekeres SZUNET
NELKUL ment egymas utan ugyanarrol a GitHub Actions IP-rol, es a masodikat a
botvedelem visszautasitotta. A scraper egyetlen kerest inditott,
ujraprobalkozas nelkul, ezert a futas azonnal elszallt — es vele a TELJES
heti frissites, beleertve a mar sikeresen lehuzott orosz adatot is.

HAROM VALTOZAS:

1. UJRAPROBALKOZAS NOVEKVO VARAKOZASSAL. A 429 nem vegleges hiba, hanem
   "lassits". A vegleges hibakent kezeles pont a rossz valasz ra.

2. VALODI BONGESZO-FEJLEC. A csupasz "Mozilla/5.0" onmagaban gyanus
   mintazat; a Google botvedelme tobbek kozt ezen szur. Ez nem korlatozas
   megkerulese — nyilvanos, olvasasra szant oldalt kerunk le, ritkan.

3. KOTELEZO SZUNET A KERESEK KOZOTT. Ket lekeres ket kulonbozo oldalra
   percenkent egyszer boven eleg egy HETI futashoz. A "gyorsan egymas utan"
   nem hoz semmi hasznot, csak blokkolast.
"""
import logging
import random
import time

import requests

log = logging.getLogger(__name__)

# Valodi bongeszo-fejleckeszlet. A User-Agent onmagaban keves: az Accept es a
# Accept-Language hianya ugyanolyan arulkodo mintazat.
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0.0.0 Safari/537.36"),
    "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
               "image/avif,image/webp,*/*;q=0.8"),
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
}

# Ket lekeres kozott ennyi masodperc. Heti egyszeri futasnal ez elhanyagolhato
# koltseg, cserebe a masodik keres nem fut bele az elso limitjebe.
POLITENESS_GAP_S = 20


def fetch(url, attempts=4, base_wait=15, timeout=45):
    """Lekeri az oldalt, 429/5xx eseten novekvo varakozassal ujraprobalja.

    Visszaad: requests.Response vagy None (minden kiserlet utan).
    A hivo TOVABBRA IS kezelje a None-t — a cel nem az, hogy sose hibazzon,
    hanem hogy egy atmeneti korlatozas ne minosuljon vegleges hibanak.
    """
    last = None
    for i in range(attempts):
        try:
            r = requests.get(url, timeout=timeout, headers=HEADERS)
            if r.status_code == 200:
                return r
            last = "HTTP {}".format(r.status_code)
            # 429 = rate limit, 5xx = atmeneti szerverhiba. Mindketto varhato
            # es mulo; a tobbi statusz (404, 403 tartos) nem javul varakozastol.
            if r.status_code != 429 and r.status_code < 500:
                log.error("Nem ujraprobalhato valasz: %s — %s", last, url)
                return None
        except Exception as exc:  # noqa: BLE001
            last = "{}: {}".format(type(exc).__name__, str(exc)[:120])

        if i < attempts - 1:
            # Exponencialis + veletlen szoras: ha valaha ket scraper egyszerre
            # indulna, ne ugyanabban a masodpercben probaljanak ujra.
            wait = base_wait * (2 ** i) + random.uniform(0, 5)
            log.warning("Lekeres sikertelen (%s) — ujraprobalkozas %.0f mp "
                        "mulva [%d/%d]", last, wait, i + 1, attempts)
            time.sleep(wait)

    log.error("Minden kiserlet sikertelen (%s): %s", last, url)
    return None


def polite_gap(seconds=POLITENESS_GAP_S):
    """Szunet ket kulonbozo oldal lekerese kozott."""
    log.info("Varakozas %d mp a kovetkezo lekeres elott (rate limit)", seconds)
    time.sleep(seconds)
