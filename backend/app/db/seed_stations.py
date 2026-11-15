"""
Seed official PKP Intercity stations database directly from PKP Intercity source.

Source: https://www.intercity.pl/js/station.js
Populates the 'stations' table in SQLite with official names, normalized names, and EVA codes.
"""
import re
import sqlite3
import httpx
from pathlib import Path
from backend.app.db.database import get_connection, init_db
from backend.app.scraper.route_resolver import METROPOLITAN_HUBS

STATIONS_JS_URL = "https://www.intercity.pl/js/station.js"


def fetch_and_parse_pkp_stations():
    """Fetches station.js from PKP Intercity and extracts all stations."""
    print(f"Pobieranie oficjalnego słownika stacji z {STATIONS_JS_URL}...")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    }

    try:
        r = httpx.get(STATIONS_JS_URL, headers=headers, timeout=15)
        r.raise_for_status()
        content = r.content
    except Exception as e:
        print(f"Błąd pobierania stacji z sieci: {e}")
        return []

    # Cleanly decode with utf-8 first (PKP Intercity station.js is utf-8 encoded)
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

    parsed_stations = []
    seen_eva = set()

    for name, norm_name, eva_code, a_code, ic_id in matches:
        name = name.strip()
        norm_name = norm_name.strip().lower()
        eva_code = eva_code.strip() or ic_id.strip()
        ic_id = ic_id.strip()

        if not eva_code or eva_code in seen_eva or not name:
            continue

        seen_eva.add(eva_code)

        # Check metropolitan group
        metro_group = None
        is_hub = 0
        if name in METROPOLITAN_HUBS:
            metro_group, is_hub = METROPOLITAN_HUBS[name]
            is_hub = 1 if is_hub else 0

        parsed_stations.append({
            "eva_code": eva_code,
            "name": name,
            "normalized_name": norm_name,
            "metropolitan_group": metro_group,
            "is_main_hub": is_hub,
            "ic_id": ic_id,
        })

    return parsed_stations


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


def normalize_polish_name(s: str) -> str:
    """Removes Polish diacritics and converts to lowercase for search indexing."""
    trans = str.maketrans("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ", "acelnoszzACELNOSZZ")
    return s.translate(trans).strip().lower()


def upsert_station(name: str, eva_code: str, db_path: str = None):
    """
    Inserts or updates a station into SQLite stations table.
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
            ON CONFLICT(eva_code) DO UPDATE SET
                name = excluded.name,
                normalized_name = excluded.normalized_name
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
