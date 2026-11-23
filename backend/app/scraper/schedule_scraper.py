"""
Schedule Scraper and Route Provider for PKP Intercity trains.

Resolves real intermediate stops, departure and arrival times,
and segment durations for train connections between Polish stations.
Implements 2-tier architecture:
1. Live extraction from official PKP timetable service (rozklad-pkp.pl / HAFAS)
2. Offline / Cached route registry in SQLite for popular corridors and train lines
"""
import concurrent.futures
import json
import logging
import re
from datetime import datetime
from html import unescape
from typing import List, Optional, Dict, Tuple
import httpx
from pydantic import BaseModel

from backend.app.db.database import get_connection, init_db
from backend.app.db.seed_stations import normalize_polish_name
from backend.app.scraper.route_resolver import (
    StationStop,
    RouteSegment,
    filter_and_merge_stops,
    build_route_segments,
    METROPOLITAN_HUBS,
)

logger = logging.getLogger(__name__)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pl-PL,pl;q=0.9,en-US;q=0.8,en;q=0.7",
}

FULL_DAY_WINDOWS = ["00:00", "04:30", "09:00", "13:30", "18:00", "21:30"]


def clean_pkp_train_name(raw: str) -> str:
    """
    Validates and cleans train name to strictly PKP Intercity brands (EIP, EIC, IC, TLK).
    Rejects regional carriers (IR, R, KM, KD, KS, ŁKA, KW, Os) and UI text (e.g. 'prognozowany...').
    """
    s = unescape(raw).strip()
    s = re.sub(r'\s+', ' ', s)
    m = re.match(r'^(EIP|EIC|IC|TLK)\s*(\d{3,5})$', s, re.IGNORECASE)
    if m:
        return f"{m.group(1).upper()} {m.group(2)}"
    return ""

# Predefined real routes for major PKP Intercity corridors (fallback when offline)
KNOWN_REAL_CORRIDORS: Dict[str, List[Dict]] = {
    "WARSZAWA_GDANSK": [
        {"name": "Warszawa Centralna", "eva_code": "5100065", "arr_min": 0, "dep_min": 0},
        {"name": "Warszawa Wschodnia", "eva_code": "5100066", "arr_min": 7, "dep_min": 9},
        {"name": "Modlin", "eva_code": "5102206", "arr_min": 35, "dep_min": 36},
        {"name": "Nasielsk", "eva_code": "5100178", "arr_min": 48, "dep_min": 49},
        {"name": "Ciechanów", "eva_code": "5100095", "arr_min": 72, "dep_min": 73},
        {"name": "Mława", "eva_code": "5100219", "arr_min": 95, "dep_min": 96},
        {"name": "Działdowo", "eva_code": "5100100", "arr_min": 110, "dep_min": 111},
        {"name": "Iława Główna", "eva_code": "5100075", "arr_min": 145, "dep_min": 147},
        {"name": "Malbork", "eva_code": "5100040", "arr_min": 180, "dep_min": 182},
        {"name": "Tczew", "eva_code": "5100061", "arr_min": 200, "dep_min": 202},
        {"name": "Gdańsk Główny", "eva_code": "5100009", "arr_min": 225, "dep_min": 225},
    ],
    "WARSZAWA_KRAKOW": [
        {"name": "Warszawa Wschodnia", "eva_code": "5100066", "arr_min": 0, "dep_min": 0},
        {"name": "Warszawa Centralna", "eva_code": "5100065", "arr_min": 6, "dep_min": 11},
        {"name": "Warszawa Zachodnia", "eva_code": "5100067", "arr_min": 15, "dep_min": 16},
        {"name": "Opoczno Południe", "eva_code": "5104744", "arr_min": 65, "dep_min": 66},
        {"name": "Włoszczowa Północ", "eva_code": "5104660", "arr_min": 95, "dep_min": 96},
        {"name": "Miechów", "eva_code": "5100218", "arr_min": 135, "dep_min": 136},
        {"name": "Kraków Główny", "eva_code": "5100028", "arr_min": 155, "dep_min": 155},
    ],
    "WARSZAWA_POZNAN": [
        {"name": "Warszawa Wschodnia", "eva_code": "5100066", "arr_min": 0, "dep_min": 0},
        {"name": "Warszawa Centralna", "eva_code": "5100065", "arr_min": 6, "dep_min": 11},
        {"name": "Warszawa Zachodnia", "eva_code": "5100067", "arr_min": 14, "dep_min": 16},
        {"name": "Kutno", "eva_code": "5100032", "arr_min": 65, "dep_min": 67},
        {"name": "Koło", "eva_code": "5100258", "arr_min": 92, "dep_min": 93},
        {"name": "Konin", "eva_code": "5100026", "arr_min": 115, "dep_min": 117},
        {"name": "Września", "eva_code": "5100157", "arr_min": 145, "dep_min": 146},
        {"name": "Poznań Główny", "eva_code": "5100081", "arr_min": 175, "dep_min": 175},
    ],
    "POZNAN_WROCLAW": [
        {"name": "Poznań Główny", "eva_code": "5100081", "arr_min": 0, "dep_min": 0},
        {"name": "Kościan", "eva_code": "5100124", "arr_min": 25, "dep_min": 26},
        {"name": "Leszno", "eva_code": "5100036", "arr_min": 45, "dep_min": 47},
        {"name": "Rawicz", "eva_code": "5100202", "arr_min": 68, "dep_min": 69},
        {"name": "Żmigród", "eva_code": "5104359", "arr_min": 85, "dep_min": 86},
        {"name": "Oborniki Śląskie", "eva_code": "5100182", "arr_min": 100, "dep_min": 101},
        {"name": "Wrocław Główny", "eva_code": "5100069", "arr_min": 120, "dep_min": 120},
    ]
}


def time_to_minutes(t_str: str) -> int:
    """Converts HH:MM string to minutes of the day."""
    try:
        parts = t_str.strip().split(":")
        return int(parts[0]) * 60 + int(parts[1])
    except Exception:
        return 0


def format_minutes_to_time(m: int) -> str:
    """Converts minutes of the day to HH:MM."""
    h = (m // 60) % 24
    mins = m % 60
    return f"{h:02d}:{mins:02d}"


def get_cached_route(train_number: str, travel_date: str) -> Optional[List[StationStop]]:
    """Retrieves cached stops list from SQLite if available."""
    init_db()
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT stops_json FROM cached_routes WHERE train_number = ? AND travel_date = ?",
            (train_number, travel_date),
        )
        row = cur.fetchone()
        if row:
            stops_raw = json.loads(row["stops_json"])
            return [StationStop(**s) for s in stops_raw]
        return None
    finally:
        conn.close()


def save_cached_route(train_number: str, travel_date: str, stops: List[StationStop]) -> None:
    """Saves stops list into cached_routes table in SQLite."""
    init_db()
    conn = get_connection()
    try:
        cur = conn.cursor()
        stops_json = json.dumps([s.model_dump() for s in stops], ensure_ascii=False)
        cur.execute(
            """
            INSERT INTO cached_routes (train_number, travel_date, stops_json, created_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(train_number, travel_date) DO UPDATE SET
                stops_json = excluded.stops_json,
                created_at = CURRENT_TIMESTAMP
            """,
            (train_number, travel_date, stops_json),
        )
        conn.commit()
    finally:
        conn.close()


def get_station_info(station_name: str) -> Tuple[str, str]:
    """
    Finds matching station in SQLite database using Polish diacritic normalization.
    Search order:
      1. Exact match: name = ? OR normalized_name = ?
      2. Prefix match: normalized_name LIKE 'norm%'
      3. Infix match: normalized_name LIKE '%norm%'
    Ordered by is_main_hub DESC, length(name) ASC.
    Returns (official_name, eva_code).
    """
    raw_query = (station_name or "").strip()
    norm_query = normalize_polish_name(raw_query)
    if not norm_query:
        return station_name, ""

    conn = get_connection()
    try:
        cur = conn.cursor()

        # 1. Exact match (handles both official Polish diacritics and ASCII-normalized names)
        cur.execute(
            """
            SELECT name, eva_code FROM stations
            WHERE name = ? OR normalized_name = ?
            ORDER BY is_main_hub DESC, length(name) ASC
            LIMIT 1
            """,
            (raw_query, norm_query),
        )
        row = cur.fetchone()
        if row:
            return row["name"], row["eva_code"]

        # 2. Prefix match (e.g. 'lodz' -> 'Łódź ...', never 'Bystrzyca Kłodzka')
        cur.execute(
            """
            SELECT name, eva_code FROM stations
            WHERE normalized_name LIKE ?
            ORDER BY is_main_hub DESC, length(name) ASC
            LIMIT 1
            """,
            (f"{norm_query}%",),
        )
        row = cur.fetchone()
        if row:
            return row["name"], row["eva_code"]

        # 3. Infix match fallback
        cur.execute(
            """
            SELECT name, eva_code FROM stations
            WHERE normalized_name LIKE ?
            ORDER BY is_main_hub DESC, length(name) ASC
            LIMIT 1
            """,
            (f"%{norm_query}%",),
        )
        row = cur.fetchone()
        if row:
            return row["name"], row["eva_code"]

        return station_name, ""
    finally:
        conn.close()


def _fetch_single_window_trains(
    from_name: str,
    from_eva: str,
    to_name: str,
    to_eva: str,
    date_formatted: str,
    time_str: str,
    direct_only: bool = True,
) -> List[Dict]:
    """Helper to query a single time window from rozklad-pkp.pl."""
    url = "https://rozklad-pkp.pl/pl/tp"
    params = {
        "REQ0JourneyStopsS0A": "1",
        "REQ0JourneyStopsS0G": from_name,
        "REQ0JourneyStopsZ0A": "1",
        "REQ0JourneyStopsZ0G": to_name,
        "date": date_formatted,
        "time": time_str,
        "start": "start",
    }
    if from_eva:
        params["REQ0JourneyStopsS0ID"] = f"A=1@L={from_eva}@"
    if to_eva:
        params["REQ0JourneyStopsZ0ID"] = f"A=1@L={to_eva}@"
    if direct_only:
        params["REQ0HafasNoOfChanges"] = "0"

    try:
        r = httpx.get(url, params=params, headers=DEFAULT_HEADERS, follow_redirects=True, timeout=10)
        if r.status_code != 200:
            return []

        trains = []
        for m in re.finditer(
            r'<tr[^>]+class="[^"]*tpOverview[^"]*"[^>]*>(.*?)(?=<tr[^>]+class="[^"]*tpOverview|$)',
            r.text,
            re.DOTALL,
        ):
            chunk = m.group(1)

            # Strict PKP Intercity category match - iterate through alts in chunk
            clean_name = ""
            for a in re.findall(r'alt="([^"]*)"', chunk):
                c = clean_pkp_train_name(a)
                if c:
                    clean_name = c
                    break
            if not clean_name:
                continue

            dep_match = re.search(r'odj.*?<span>(\d{1,2}:\d{2})</span>', chunk, re.DOTALL | re.IGNORECASE)
            arr_match = re.search(r'przyj.*?<span>(\d{1,2}:\d{2})</span>', chunk, re.DOTALL | re.IGNORECASE)
            dur_match = re.search(r'czas przejazdu:&nbsp;(\d+:\d{2})', chunk)
            changes_match = re.search(r'przesiadki:\s*(\d+)', chunk)

            dep_time = dep_match.group(1) if dep_match else ""
            arr_time = arr_match.group(1) if arr_match else ""
            duration = dur_match.group(1) if dur_match else ""
            changes = int(changes_match.group(1)) if changes_match else 0

            # Suffix with train commercial name if available (e.g. "EIC 1804 BOLESŁAW PRUS")
            name_m = re.search(r'Nazwa:&nbsp;([A-ZĄĆĘŁŃÓŚŹŻ\s\-]+)', chunk)
            train_display = clean_name
            if name_m:
                t_subname = name_m.group(1).strip()
                if len(t_subname) >= 3 and not any(k in t_subname for k in ["KIERUNEK", "ODLEG", "WAGON", "KLIMATYZACJA"]):
                    train_display = f"{clean_name} {t_subname}"

            if dep_time:
                trains.append({
                    "train_number": train_display,
                    "departure": dep_time,
                    "arrival": arr_time,
                    "duration": duration,
                    "changes": changes,
                })
        return trains
    except Exception as e:
        logger.warning(f"Live timetable query error for window {time_str}: {e}")
        return []


def fetch_live_train_connections(
    from_station: str,
    to_station: str,
    travel_date: str,
    travel_time: str = "00:00",
    direct_only: bool = False,
    full_day: bool = True,
) -> List[Dict]:
    """
    Scrapes real PKP Intercity connections from official timetable portal (rozklad-pkp.pl).
    When full_day=True, queries across multiple time windows (00:00, 04:30, 09:00, 13:30, 18:00, 21:30)
    in parallel to retrieve all departures across the entire 24 hours of that day.
    When direct_only=False, queries both direct trains and transfer connections so all
    possibilities are available.
    Filters strictly to PKP Intercity operated trains (EIP, EIC, IC, TLK).
    """
    from_name, from_eva = get_station_info(from_station)
    to_name, to_eva = get_station_info(to_station)

    # Format date: ensure DD.MM.YYYY
    date_formatted = travel_date
    if "-" in travel_date:
        parts = travel_date.split("-")
        if len(parts) == 3 and len(parts[0]) == 4:
            date_formatted = f"{parts[2]}.{parts[1]}.{parts[0]}"

    windows = FULL_DAY_WINDOWS if full_day else [travel_time]

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(windows) * 2) as executor:
        direct_futures = [
            executor.submit(_fetch_single_window_trains, from_name, from_eva, to_name, to_eva, date_formatted, w, True)
            for w in windows
        ]
        transfer_futures = []
        if not direct_only:
            transfer_futures = [
                executor.submit(_fetch_single_window_trains, from_name, from_eva, to_name, to_eva, date_formatted, w, False)
                for w in windows
            ]

        direct_results = [f.result() for f in direct_futures]
        transfer_results = [f.result() for f in transfer_futures]

    all_trains = []
    seen = set()

    # 1. Add direct trains first (priority)
    for batch in direct_results:
        for t in batch:
            t_code = re.match(r'^[A-Z]+\s*\d+', t["train_number"])
            code_str = t_code.group(0) if t_code else t["train_number"]
            key = (code_str, t["departure"])
            if key not in seen:
                seen.add(key)
                all_trains.append(t)

    # 2. Add transfer connections if direct_only is False
    for batch in transfer_results:
        for t in batch:
            t_code = re.match(r'^[A-Z]+\s*\d+', t["train_number"])
            code_str = t_code.group(0) if t_code else t["train_number"]
            key = (code_str, t["departure"])
            if key not in seen:
                seen.add(key)
                all_trains.append(t)

    all_trains.sort(key=lambda x: x["departure"])
    return all_trains


def _extract_ti_url_from_sq_html(html_text: str, train_no: str) -> Optional[str]:
    """
    Extracts the /pl/ti?... URL for `train_no` by scanning individual <tr> rows so that
    train brand+number and the /pl/ti link must appear inside the exact same table row.
    """
    num_match = re.search(r'\b(\d{3,5})\b', train_no or "")
    if not num_match:
        return None
    clean_num = num_match.group(1)

    brand_match = re.match(r'^\s*(EIP|EIC|IC|TLK)\b', train_no or "", re.IGNORECASE)
    exact_brand_re = (
        re.compile(rf'\b{re.escape(brand_match.group(1))}\s*{re.escape(clean_num)}\b', re.IGNORECASE)
        if brand_match
        else None
    )
    any_ic_re = re.compile(rf'\b(EIP|EIC|IC|TLK)\s*{re.escape(clean_num)}\b', re.IGNORECASE)

    fallback_url: Optional[str] = None
    for row in re.findall(r'<tr\b[^>]*>.*?</tr>', html_text, re.DOTALL | re.IGNORECASE):
        ti_m = re.search(r'href="(/pl/ti\?[^"]*)"', row)
        if not ti_m:
            continue
        row_unescaped = unescape(row)
        if exact_brand_re and exact_brand_re.search(row_unescaped):
            return "https://rozklad-pkp.pl" + ti_m.group(1).replace("&amp;", "&")
        if fallback_url is None and any_ic_re.search(row_unescaped):
            fallback_url = "https://rozklad-pkp.pl" + ti_m.group(1).replace("&amp;", "&")

    return fallback_url


def find_ti_url_for_train(
    from_eva: str,
    from_name: str,
    train_no: str,
    date_formatted: str,
    dep_time: Optional[str] = None,
) -> Optional[str]:
    """
    Finds the /pl/ti train route details URL for a train departing from a station.
    Queries /pl/sq departure board at dep_time or across all 6 day windows in parallel.
    """
    def query_sq(time_str: str) -> Optional[str]:
        url = "https://rozklad-pkp.pl/pl/sq"
        params = {
            "input": from_eva or from_name,
            "date": date_formatted,
            "time": time_str,
            "boardType": "dep",
            "start": "start",
        }
        try:
            r = httpx.get(url, params=params, headers=DEFAULT_HEADERS, follow_redirects=True, timeout=10)
            if r.status_code != 200:
                return None
            return _extract_ti_url_from_sq_html(r.text, train_no)
        except Exception:
            pass
        return None

    # First attempt: if dep_time is provided, query that window
    if dep_time:
        res = query_sq(dep_time)
        if res:
            return res

    # Multiwindow parallel fallback: scan the whole day
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(FULL_DAY_WINDOWS)) as ex:
        futures = [ex.submit(query_sq, w) for w in FULL_DAY_WINDOWS]
        for f in concurrent.futures.as_completed(futures):
            res = f.result()
            if res:
                return res

    return None


class RouteStop(BaseModel):
    station_name: str
    eva_code: str
    arrival_time: Optional[str] = None
    departure_time: Optional[str] = None
    duration_from_start_min: int = 0
    seq: int = 0
    day_offset: int = 0


class TrainRouteExtractor:
    """
    Extracts complete train route, intermediate stations, EVA codes, and exact arrival/departure times
    from official timetable portals (rozklad-pkp.pl / Portal Pasażera / HAFAS).
    """

    def __init__(self, timeout: int = 12):
        self.timeout = timeout

    @staticmethod
    def parse_stops_from_ti_html(
        html_text: str,
        from_station: str,
        to_station: str,
        from_eva: str = "",
        to_eva: str = "",
    ) -> List[RouteStop]:
        """
        Parses HTML from /pl/ti (or traininfo.exe), extracts station names, EVA codes from href (input=51...),
        arrival and departure times, slices between from_station and to_station,
        and computes cumulative duration in minutes.
        """
        from backend.app.db.seed_stations import upsert_station
        rows = re.findall(r'<tr[^>]*>.*?</tr>', html_text, re.DOTALL)
        raw_stops = []

        for r_row in rows:
            tds = re.findall(r'<td[^>]*>(.*?)</td>', r_row, re.DOTALL)
            if len(tds) >= 4:
                stn_m = re.findall(r'<a[^>]+>([^<]+)</a>', tds[1])
                stn_name = unescape(stn_m[0].strip()) if stn_m else unescape(re.sub(r'<[^>]+>', '', tds[1]).strip())

                arr_m = re.search(r'\b(\d{1,2}:\d{2})\b', re.sub(r'<[^>]+>', '', tds[2]))
                dep_m = re.search(r'\b(\d{1,2}:\d{2})\b', re.sub(r'<[^>]+>', '', tds[3]))

                # Task 042 & 048: Extract EVA from href or attribute
                eva_m = re.findall(r'(?:input|station_evaId)=(\d{7})', tds[1])
                eva = eva_m[0] if eva_m else ""

                arr_str = arr_m.group(1) if arr_m else None
                dep_str = dep_m.group(1) if dep_m else None

                if stn_name and (arr_str or dep_str):
                    if not dep_str:
                        dep_str = arr_str
                    if not arr_str:
                        arr_str = dep_str

                    # Task 043: Auto-upsert into SQLite stations table
                    if eva:
                        upsert_station(stn_name, eva)

                    raw_stops.append({
                        "name": stn_name,
                        "eva": eva,
                        "arr_str": arr_str,
                        "dep_str": dep_str,
                        "m_arr": time_to_minutes(arr_str),
                        "m_dep": time_to_minutes(dep_str),
                    })

        if not raw_stops:
            return []

        # Monotonic time across midnight (tracks integer day_offset for midnight-crossing trains)
        cum_stops = []
        day_offset_days = 0
        prev_time = None
        for s in raw_stops:
            cur_arr = s["m_arr"]
            cur_dep = s["m_dep"]
            if prev_time is not None and cur_arr < prev_time - 300:
                day_offset_days += 1
            arr_day = day_offset_days
            arr_min = cur_arr + arr_day * 1440
            prev_time = cur_arr

            if cur_dep < prev_time - 300:
                day_offset_days += 1
            dep_day = day_offset_days
            dep_min = cur_dep + dep_day * 1440
            prev_time = cur_dep

            cum_stops.append({
                "name": s["name"],
                "eva_code": s["eva"],
                "arrival_time": s["arr_str"],
                "departure_time": s["dep_str"],
                "arr_min": arr_min,
                "dep_min": dep_min,
                "dep_day": dep_day,
            })

        # Slice from_station to to_station
        from_idx = None
        to_idx = None
        from_norm = normalize_polish_name(from_station)
        to_norm = normalize_polish_name(to_station)

        for i, s in enumerate(cum_stops):
            s_name = s["name"].lower()
            s_norm = normalize_polish_name(s["name"])
            s_eva = s["eva_code"]
            if from_idx is None:
                if (
                    (from_eva and from_eva == s_eva)
                    or (from_station.lower() in s_name)
                    or (from_norm and from_norm in s_norm)
                ):
                    from_idx = i
                    continue
            if from_idx is not None and to_idx is None:
                if (
                    (to_eva and to_eva == s_eva)
                    or (to_station.lower() in s_name)
                    or (to_norm and to_norm in s_norm)
                ):
                    to_idx = i
                    break

        if from_idx is not None and to_idx is not None and to_idx > from_idx:
            sliced = cum_stops[from_idx : to_idx + 1]
        else:
            sliced = cum_stops

        start_min = sliced[0]["dep_min"]
        base_day = sliced[0]["dep_day"]
        route_stops: List[RouteStop] = []
        for seq, s in enumerate(sliced):
            dur = max(0, s["arr_min"] - start_min)
            route_stops.append(
                RouteStop(
                    seq=seq,
                    station_name=s["name"],
                    eva_code=s["eva_code"] or f"5100{seq:03d}",
                    arrival_time=s["arrival_time"],
                    departure_time=s["departure_time"],
                    duration_from_start_min=dur,
                    day_offset=max(0, s["dep_day"] - base_day),
                )
            )

        return route_stops

    def get_train_route_by_number_or_relation(
        self,
        from_station: str,
        to_station: str,
        date_str: str,
        time_str: str = "00:00",
        train_number: Optional[str] = None,
    ) -> List[RouteStop]:
        from_name, from_eva = get_station_info(from_station)
        to_name, to_eva = get_station_info(to_station)

        date_formatted = date_str
        if "-" in date_str:
            parts = date_str.split("-")
            if len(parts) == 3 and len(parts[0]) == 4:
                date_formatted = f"{parts[2]}.{parts[1]}.{parts[0]}"

        t_no = train_number or ""
        ti_url = find_ti_url_for_train(from_eva, from_name, t_no, date_formatted, dep_time=time_str)
        if not ti_url:
            return []

        try:
            r = httpx.get(ti_url, headers=DEFAULT_HEADERS, timeout=self.timeout, follow_redirects=True)
            if r.status_code != 200:
                return []
            stops = self.parse_stops_from_ti_html(r.text, from_station, to_station, from_eva, to_eva)

            # Task 047: Log route
            if stops:
                route_str = " -> ".join(f"{s.seq + 1}. {s.station_name} ({s.departure_time or s.arrival_time})" for s in stops)
                logger.info(f"[ROUTE] Pociąg {train_number or ''}: {route_str}")

            return stops
        except Exception as e:
            logger.warning(f"Error fetching train route details: {e}")
            return []


def fetch_live_train_route_stops(
    from_station: str,
    to_station: str,
    train_no: str,
    travel_date: str,
    dep_time: Optional[str] = None,
) -> List[StationStop]:
    """
    Scrapes the exact sequence of intermediate stops for a train from /pl/sq and /pl/ti.
    Slices the stops between from_station and to_station, correctly accumulating minutes
    across midnight crossings.
    """
    extractor = TrainRouteExtractor()
    route_stops = extractor.get_train_route_by_number_or_relation(
        from_station, to_station, travel_date, time_str=dep_time or "00:00", train_number=train_no
    )
    if not route_stops:
        return []

    station_stops = []
    for s in route_stops:
        station_stops.append(
            StationStop(
                seq=s.seq,
                eva_code=s.eva_code,
                name=s.station_name,
                arrival_min=s.duration_from_start_min,
                departure_min=s.duration_from_start_min,
                arrival_time=s.arrival_time or "",
                departure_time=s.departure_time or "",
                day_offset=s.day_offset,
            )
        )
    return station_stops


def resolve_real_route(
    from_station: str,
    to_station: str,
    travel_date: str,
    train_number: str = "IC 3520",
    dep_time: Optional[str] = None,
) -> List[StationStop]:
    """
    Resolves the sequence of intermediate station stops for a real train journey.
    1. Checks SQLite cache.
    2. Queries live official PKP timetable service (rozklad-pkp.pl).
    3. Falls back to known railway corridors (LK9, CMK, E20, E59).
    4. Applies metropolitan hub agglomeration collapsing (filter_and_merge_stops).
    """
    # 1. Check SQLite cache (ignore if fallback Kutno/Konin dummy stops)
    cached = get_cached_route(train_number, travel_date)
    if cached and len(cached) >= 2:
        if cached[0].name.lower() == from_station.lower() and cached[-1].name.lower() == to_station.lower():
            is_dummy = (
                len(cached) == 4
                and cached[1].name == "Kutno"
                and cached[2].name == "Konin"
                and "kutno" not in from_station.lower()
                and "kutno" not in to_station.lower()
            )
            if not is_dummy:
                return cached

    # 2. Try live scraping from PKP
    try:
        live_stops = fetch_live_train_route_stops(
            from_station, to_station, train_number, travel_date, dep_time=dep_time
        )
        if live_stops and len(live_stops) >= 2:
            merged = filter_and_merge_stops(live_stops)
            save_cached_route(train_number, travel_date, merged)
            return merged
    except Exception as e:
        logger.warning(f"Live scraper failed, using offline fallback: {e}")

    # 3. Match known real corridors
    from_lower = from_station.lower()
    to_lower = to_station.lower()

    corridor_key = None
    if "warszaw" in from_lower and ("gda" in to_lower or "gdyn" in to_lower or "sopot" in to_lower):
        corridor_key = "WARSZAWA_GDANSK"
    elif "warszaw" in from_lower and "krak" in to_lower:
        corridor_key = "WARSZAWA_KRAKOW"
    elif "warszaw" in from_lower and "pozna" in to_lower:
        corridor_key = "WARSZAWA_POZNAN"
    elif "pozna" in from_lower and "wroc" in to_lower:
        corridor_key = "POZNAN_WROCLAW"

    if corridor_key and corridor_key in KNOWN_REAL_CORRIDORS:
        raw_stops_data = KNOWN_REAL_CORRIDORS[corridor_key]
        raw_stops = [
            StationStop(
                seq=i,
                eva_code=s["eva_code"],
                name=s["name"],
                arrival_min=s["arr_min"],
                departure_min=s["dep_min"],
            )
            for i, s in enumerate(raw_stops_data)
        ]
        raw_stops[0].name = from_station
        raw_stops[-1].name = to_station
        merged = filter_and_merge_stops(raw_stops)
        save_cached_route(train_number, travel_date, merged)
        return merged

    # 4. Fallback: Direct segment between origin and destination
    from_name, from_eva = get_station_info(from_station)
    to_name, to_eva = get_station_info(to_station)

    raw_stops = [
        StationStop(
            seq=0,
            eva_code=from_eva or "",
            name=from_name,
            arrival_min=0,
            departure_min=0,
            departure_time=dep_time or "12:00",
        ),
        StationStop(
            seq=1,
            eva_code=to_eva or "",
            name=to_name,
            arrival_min=60,
            departure_min=60,
        ),
    ]
    return raw_stops
