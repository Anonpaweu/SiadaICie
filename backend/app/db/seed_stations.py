"""
Seed official PKP Intercity stations database directly from PKP Intercity sources.

Sources:
1. Live e-IC 2.0 dictionary: POST https://api-gateway.intercity.pl/server/public/endpoint/Aktualizacja
   ({"metoda": "pobierzStacje", "urzadzenieNr": 956})
2. Fallback/supplement: https://www.intercity.pl/js/station.js

Populates the 'stations' table in SQLite with official names, normalized names, EVA codes, and EPA (ic_id) codes.
"""
import functools
import re
import sqlite3
import ssl
import sys
import httpx
from pathlib import Path
from typing import Dict, List
from backend.app.db.database import get_connection, init_db
from backend.app.scraper.route_resolver import METROPOLITAN_HUBS

STATIONS_JS_URL = "https://www.intercity.pl/js/station.js"
EIC2_STATIONS_API_URL = "https://api-gateway.intercity.pl/server/public/endpoint/Aktualizacja"
EIC2_DEVICE_NR = 956


@functools.lru_cache(maxsize=1)
def _get_system_ssl_context() -> ssl.SSLContext:
    """
    Returns an SSLContext that trusts the OS system certificate store
    (including Windows ROOT/CA stores) while keeping full TLS verification enabled.
    """
    try:
        import truststore  # type: ignore[import-not-found]
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except ImportError:
        pass

    ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
    if sys.platform == "win32" and hasattr(ssl, "enum_certificates"):
        purpose_oid = ssl.Purpose.SERVER_AUTH.oid
        der_certs = bytearray()
        for store_name in ("ROOT", "CA"):
            try:
                for cert, encoding, trust in ssl.enum_certificates(store_name):
                    if encoding == "x509_asn" and (
                        trust is True or (isinstance(trust, set) and purpose_oid in trust)
                    ):
                        der_certs.extend(cert)
            except Exception:
                pass
        if der_certs:
            try:
                ctx.load_verify_locations(cadata=bytes(der_certs))
            except Exception:
                pass
    return ctx


def normalize_polish_name(s: str) -> str:
    """Removes Polish diacritics and converts to lowercase for search indexing."""
    trans = str.maketrans("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ", "acelnoszzACELNOSZZ")
    return s.translate(trans).strip().lower()


def _is_abbreviated_variant(short_name: str, full_name: str) -> bool:
    """
    Returns True if short_name (e.g. 'Warszawa Centr.', 'Gdańsk Gł.') is an abbreviated
    form of full_name (e.g. 'Warszawa Centralna', 'Gdańsk Główny').
    Used when merging live e-IC 2.0 with station.js so full Polish names are preserved
    while real EVA conflicts (e.g. 5100093 Chabówka Stadion vs stale Chorzew Siemkowice)
    always prefer live e-IC 2.0.
    """
    if "." not in short_name or len(short_name) >= len(full_name):
        return False
    short_tokens = normalize_polish_name(short_name).replace("-", " ").split()
    full_tokens = normalize_polish_name(full_name).replace("-", " ").split()
    if not short_tokens or not full_tokens:
        return False
    return short_tokens[0].rstrip(".") == full_tokens[0] or full_tokens[0].startswith(short_tokens[0].rstrip("."))


def _build_station_record(eva_code: str, name: str, ic_id: str) -> Dict:
    """Builds a normalized station dictionary entry ready for SQLite insertion."""
    metro_group = None
    is_hub = 0
    if name in METROPOLITAN_HUBS:
        metro_group, is_hub = METROPOLITAN_HUBS[name]
        is_hub = 1 if is_hub else 0

    return {
        "eva_code": eva_code,
        "name": name,
        "normalized_name": normalize_polish_name(name),
        "metropolitan_group": metro_group,
        "is_main_hub": is_hub,
        "ic_id": ic_id,
    }


def _fetch_station_js_entries(headers: Dict[str, str]) -> Dict[str, Dict]:
    """Fetches legacy station.js dictionary keyed by eva_code."""
    print(f"Pobieranie słownika stacji z {STATIONS_JS_URL}...")
    try:
        r = httpx.get(STATIONS_JS_URL, headers=headers, timeout=15, verify=_get_system_ssl_context())
        r.raise_for_status()
        content = r.content
    except Exception as e:
        print(f"Błąd pobierania station.js z sieci: {e}")
        return {}

    text = ""
    for enc in ["utf-8", "windows-1250", "iso-8859-2"]:
        try:
            decoded = content.decode(enc)
            if "Kraków" in decoded or "Poznań" in decoded or "Gdańsk" in decoded:
                text = decoded
                break
        except Exception:
            pass

    if not text:
        text = content.decode("utf-8", errors="replace")

    pattern = re.compile(
        r"'n':\s*'([^']*)',\s*'p':\s*'([^']*)',\s*'h':\s*'([^']*)',\s*'a':\s*'([^']*)',\s*'i':\s*'([^']*)'"
    )
    matches = pattern.findall(text)

    stations_by_eva: Dict[str, Dict] = {}
    for name, _norm_name, eva_code, _a_code, ic_id in matches:
        name = name.strip()
        eva_code = eva_code.strip() or ic_id.strip()
        ic_id = ic_id.strip()

        if not eva_code or eva_code in stations_by_eva or not name:
            continue

        stations_by_eva[eva_code] = _build_station_record(eva_code, name, ic_id)

    return stations_by_eva


def _fetch_eic2_stations(headers: Dict[str, str]) -> List[Dict]:
    """Fetches live e-IC 2.0 station list via Aktualizacja/pobierzStacje."""
    print(f"Pobieranie aktualnego słownika e-IC 2.0 z {EIC2_STATIONS_API_URL}...")
    api_headers = {
        **headers,
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Origin": "https://ebilet.intercity.pl",
        "Referer": "https://ebilet.intercity.pl/",
    }
    try:
        r = httpx.post(
            EIC2_STATIONS_API_URL,
            json={"metoda": "pobierzStacje", "urzadzenieNr": EIC2_DEVICE_NR},
            headers=api_headers,
            timeout=20,
            verify=_get_system_ssl_context(),
        )
        r.raise_for_status()
        payload = r.json()
        stacje = payload.get("stacje")
        if isinstance(stacje, list):
            return stacje
        print(f"Ostrzeżenie: e-IC 2.0 nie zwrócił listy 'stacje' (klucze: {list(payload.keys())})")
        return []
    except Exception as e:
        print(f"Błąd pobierania słownika e-IC 2.0 z sieci: {e}")
        return []


def fetch_and_parse_pkp_stations():
    """
    Fetches official PKP Intercity stations by merging live e-IC 2.0 `pobierzStacje`
    with `station.js` fallback. Live e-IC 2.0 takes precedence on EVA/EPA conflicts
    (e.g. 5100093 Chabówka Stadion) and supplies all newly active Polish e-IC 2.0 stations,
    while full unabbreviated Polish hub names from station.js are preserved.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    }

    stations_by_eva = _fetch_station_js_entries(headers)
    eic2_raw = _fetch_eic2_stations(headers)

    for item in eic2_raw:
        raw_eva = item.get("kodEVA")
        eva_code = str(raw_eva).strip() if raw_eva not in (None, 0, "0", "") else ""
        raw_epa = item.get("kodEPA")
        ic_id = str(raw_epa).strip() if raw_epa not in (None, 0, "0", "") else ""
        eic_name = str(item.get("nazwa") or "").strip()

        if not eva_code or not eic_name:
            continue

        existing = stations_by_eva.get(eva_code)
        if existing is None:
            stations_by_eva[eva_code] = _build_station_record(eva_code, eic_name, ic_id)
        else:
            # Prefer live e-IC 2.0 on EVA conflicts and EPA updates, but keep fuller unabbreviated
            # Polish names from station.js when e-IC 2.0 only shortens them (e.g. 'Warszawa Centr.').
            chosen_name = existing["name"] if _is_abbreviated_variant(eic_name, existing["name"]) else eic_name
            chosen_ic_id = ic_id or existing["ic_id"]
            stations_by_eva[eva_code] = _build_station_record(eva_code, chosen_name, chosen_ic_id)

    return list(stations_by_eva.values())


def seed_stations_table(db_path: str = None):
    """Initializes and seeds the database with official PKP IC stations."""
    init_db(db_path)
    stations = fetch_and_parse_pkp_stations()

    if not stations:
        print("Nie udało się pobrać stacji z sieci. Używam wbudowanej bazy bazowej.")
        return 0

    conn = get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.executemany(
            """
            INSERT INTO stations (eva_code, name, normalized_name, metropolitan_group, is_main_hub, ic_id)
            VALUES (:eva_code, :name, :normalized_name, :metropolitan_group, :is_main_hub, :ic_id)
            ON CONFLICT(eva_code) DO UPDATE SET
                name = excluded.name,
                normalized_name = excluded.normalized_name,
                metropolitan_group = excluded.metropolitan_group,
                is_main_hub = excluded.is_main_hub,
                ic_id = excluded.ic_id
            """,
            stations,
        )
        conn.commit()
        print(f"Pomyślnie załadowano {len(stations)} oficjalnych stacji PKP Intercity do bazy!")
        return len(stations)
    finally:
        conn.close()


def upsert_station(name: str, eva_code: str, db_path: str = None):
    """
    Inserts a newly discovered station into SQLite stations table without overwriting
    existing official names or ic_id values on eva_code conflict.
    Task 043: Enables auto-discovery of newly encountered stations during scraping.
    """
    if not name or not eva_code:
        return
    norm = normalize_polish_name(name)
    conn = get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO stations (eva_code, name, normalized_name, is_main_hub)
            VALUES (?, ?, ?, 0)
            ON CONFLICT(eva_code) DO NOTHING
            """,
            (eva_code, name, norm),
        )
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


if __name__ == "__main__":
    count = seed_stations_table()
    print(f"Zakończono seedowanie bazy stacji ({count} rekordów).")
