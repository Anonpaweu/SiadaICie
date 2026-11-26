"""
Non-GRM Scanner ("Tryb bez schematu wagonu") for PKP Intercity trains that lack graphical seatmaps.

Uses e-IC's public read-only endpoints (zero browser/WebView overhead, zero seat locking):
1. GET https://api-gateway.intercity.pl/grm/grm/available/{train_num}/{YYYYMMDD}
2. POST https://api-gateway.intercity.pl/server/public/endpoint/Pociagi (wyszukajPolaczenia)
3. POST https://api-gateway.intercity.pl/server/public/endpoint/Sprzedaz (sprawdzCenyLite)
"""
import asyncio
from datetime import date, timedelta
import logging
import re
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

import httpx

from backend.app.models.schemas import AnalyzeResponse, StepResponse, StrategyResponse
from backend.app.optimizer.solver import format_transfers_pl
from backend.app.scraper.grm_parser import build_eic_search_url
from backend.app.scraper.route_resolver import StationStop, filter_and_merge_stops
from backend.app.scraper.schedule_scraper import get_system_ssl_context, resolve_real_route

logger = logging.getLogger(__name__)


def compute_segment_date(travel_date: str, day_offset: int = 0) -> str:
    """Offsets an ISO YYYY-MM-DD date string by `day_offset` days."""
    if not day_offset:
        return travel_date
    try:
        dt = date.fromisoformat(travel_date.strip())
        return (dt + timedelta(days=int(day_offset))).isoformat()
    except ValueError:
        return travel_date

EIC_API_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Origin": "https://ebilet.intercity.pl",
    "Referer": "https://ebilet.intercity.pl/",
    "Content-Type": "application/json",
    "Accept": "application/json, text/plain, */*",
    "app-version": "1.6.20",
}

GRM_API_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Origin": "https://ebilet.intercity.pl",
    "Referer": "https://ebilet.intercity.pl/",
}

GRM_AVAILABLE_URL = "https://api-gateway.intercity.pl/grm/grm/available/{train_num}/{yyyymmdd}"
EIC_POCIAGI_URL = "https://api-gateway.intercity.pl/server/public/endpoint/Pociagi"
EIC_SPRZEDAZ_URL = "https://api-gateway.intercity.pl/server/public/endpoint/Sprzedaz"

# Module-level cache mapping 7-digit HAFAS EVA code -> e-IC internal numeric station ID (e.g. "5100069" -> 248)
_EIC_SHORT_ID_CACHE: Dict[str, int] = {}

_STANDING_PRIORITY = {"OVERBOOKED": 3, "SPECIAL_ONLY": 2, "AVAILABLE": 1}


def extract_train_digits_and_category(train_number: str) -> Tuple[str, str]:
    """Extracts (digits, category) from strings like 'IC 6138' or '6138'."""
    raw = (train_number or "").strip()
    m_cat = re.match(r"^(EIP|EIC|IC|TLK)\b", raw, re.IGNORECASE)
    cat = m_cat.group(1).upper() if m_cat else "IC"
    m_num = re.search(r"\b(\d{1,5})\b", raw)
    num = m_num.group(1) if m_num else ""
    return num, cat


def check_grm_available(train_number: str, travel_date: str) -> bool:
    """
    Checks GET /grm/grm/available/{train_num}/{YYYYMMDD}.
    Returns False ONLY when the endpoint explicitly responds 200 with {"available": false}.
    On network errors or unexpected responses, returns True so the standard GRM pipeline runs.
    """
    num, _ = extract_train_digits_and_category(train_number)
    if not num or not travel_date:
        return True
    yyyymmdd = travel_date.strip().replace("-", "")
    if len(yyyymmdd) != 8 or not yyyymmdd.isdigit():
        return True

    url = GRM_AVAILABLE_URL.format(train_num=num, yyyymmdd=yyyymmdd)
    try:
        r = httpx.get(
            url,
            headers=GRM_API_HEADERS,
            timeout=6.0,
            verify=get_system_ssl_context(),
        )
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, dict) and data.get("available") is False:
                return False
    except Exception as exc:
        logger.debug("check_grm_available failed for %s (%s): %s", train_number, travel_date, exc)
    return True


def parse_sprawdz_ceny_entry(ceny_list: List[Dict[str, Any]], ticket_class: int) -> Dict[str, Any]:
    """
    Parses a single connection's `ceny` array from `sprawdzCenyLite` for `ticket_class`.
    Returns:
      - has_standard_seats: bool
      - standing_status: None | "AVAILABLE" | "SPECIAL_ONLY" | "OVERBOOKED"
      - price_pln: Optional[float]
      - message: str
    """
    candidates = [c for c in (ceny_list or []) if isinstance(c, dict) and c.get("klasa") == ticket_class]
    if not candidates:
        return {
            "has_standard_seats": False,
            "standing_status": "OVERBOOKED",
            "price_pln": None,
            "message": "Brak oferty dla wybranej klasy.",
        }

    entry = next((c for c in candidates if c.get("rodzajMiejscaKod") == 1), candidates[0])
    cena_grosze = int(entry.get("cena") or 0)
    price_pln = round(cena_grosze / 100.0, 2) if cena_grosze > 0 else None

    komunikaty = entry.get("komunikaty") or []
    komunikat_tekst = str(entry.get("komunikatTekst") or "").strip()
    codes = {k.get("komunikatKod") for k in komunikaty if isinstance(k, dict)}

    if cena_grosze > 0 and not komunikaty and not komunikat_tekst:
        return {
            "has_standard_seats": True,
            "standing_status": None,
            "price_pln": price_pln,
            "message": "",
        }

    low = komunikat_tekst.lower()
    if 48 in codes or "bez gwarancji" in low:
        status = "AVAILABLE"
    elif 47 in codes or "miejsca specjalne" in low:
        status = "SPECIAL_ONLY"
    else:
        status = "OVERBOOKED"

    return {
        "has_standard_seats": False,
        "standing_status": status,
        "price_pln": price_pln,
        "message": komunikat_tekst,
    }


async def _query_wyszukaj_polaczenia(
    client: httpx.AsyncClient,
    from_eva: str,
    to_eva: str,
    travel_date: str,
    end_date: str,
    train_num_str: str,
) -> Optional[Dict[str, Any]]:
    if not from_eva.isdigit() or not to_eva.isdigit():
        return None
    payload = {
        "metoda": "wyszukajPolaczenia",
        "wersja": "1.6.20_desktop",
        "url": "https://ebilet.intercity.pl/wyszukiwanie",
        "dataWyjazdu": f"{travel_date} 00:00:00",
        "dataPrzyjazdu": f"{end_date} 23:59:59",
        "stacjaWyjazdu": int(from_eva),
        "stacjaPrzyjazdu": int(to_eva),
        "czasNaPrzesiadkeMin": 0,
        "stacjePrzez": [],
        "polaczeniaBezposrednie": 1,
        "polaczeniaNajszybsze": 0,
        "liczbaPolaczen": 0,
        "kategoriePociagow": [],
        "kodyPrzewoznikow": [],
        "rodzajeMiejsc": [],
        "typyMiejsc": [],
        "czasNaPrzesiadkeMax": 1440,
        "braille": 0,
        "liczbaPrzesiadekMax": 0,
        "atrybutyHandlowe": [],
        "urzadzenieNr": 956,
    }
    try:
        r = await client.post(EIC_POCIAGI_URL, json=payload, headers=EIC_API_HEADERS, timeout=15.0)
        if r.status_code != 200:
            return None
        data = r.json()
        for conn in data.get("polaczenia", []):
            pociagi = conn.get("pociagi") or []
            if len(pociagi) != 1:
                continue
            tr = pociagi[0]
            if str(tr.get("nrPociagu", "")).strip() == train_num_str:
                return tr
    except Exception as exc:
        logger.debug("wyszukajPolaczenia failed for %s -> %s: %s", from_eva, to_eva, exc)
    return None


async def resolve_eic_short_codes(
    client: httpx.AsyncClient,
    stops: List[StationStop],
    train_number: str,
    travel_date: str,
) -> Tuple[Dict[str, int], str, Dict[str, str]]:
    """
    Resolves EVA codes to e-IC short IDs plus train category and exact departure timestamps.
    Uses `_EIC_SHORT_ID_CACHE` when EVAs were seen earlier.
    """
    train_num_str, default_cat = extract_train_digits_and_category(train_number)
    category = default_cat
    short_codes: Dict[str, int] = {}
    exact_deps: Dict[str, str] = {}

    for st in stops:
        if st.eva_code in _EIC_SHORT_ID_CACHE:
            short_codes[st.eva_code] = _EIC_SHORT_ID_CACHE[st.eva_code]

    end_date = compute_segment_date(travel_date, max(1, stops[-1].day_offset + 1))

    # Query full relation first to anchor endpoints and exact train category
    first_eva = stops[0].eva_code
    last_eva = stops[-1].eva_code
    root_tr = await _query_wyszukaj_polaczenia(client, first_eva, last_eva, travel_date, end_date, train_num_str)
    if root_tr:
        if root_tr.get("kategoriaPociagu"):
            category = str(root_tr["kategoriaPociagu"]).upper()
        if root_tr.get("stacjaWyjazdu"):
            short_codes[first_eva] = int(root_tr["stacjaWyjazdu"])
            _EIC_SHORT_ID_CACHE[first_eva] = short_codes[first_eva]
        if root_tr.get("stacjaPrzyjazdu"):
            short_codes[last_eva] = int(root_tr["stacjaPrzyjazdu"])
            _EIC_SHORT_ID_CACHE[last_eva] = short_codes[last_eva]
        if root_tr.get("dataWyjazdu"):
            exact_deps[first_eva] = str(root_tr["dataWyjazdu"])

    missing_mid = [st for st in stops[1:-1] if st.eva_code and st.eva_code not in short_codes]
    if missing_mid and first_eva:
        sem = asyncio.Semaphore(6)

        async def _resolve_to(st: StationStop) -> None:
            async with sem:
                tr = await _query_wyszukaj_polaczenia(
                    client, first_eva, st.eva_code, travel_date, end_date, train_num_str
                )
                if tr and tr.get("stacjaPrzyjazdu"):
                    sid = int(tr["stacjaPrzyjazdu"])
                    short_codes[st.eva_code] = sid
                    _EIC_SHORT_ID_CACHE[st.eva_code] = sid

        await asyncio.gather(*[_resolve_to(st) for st in missing_mid])

    still_missing = [st for st in stops[:-1] if st.eva_code and st.eva_code not in short_codes]
    if still_missing and last_eva:
        sem = asyncio.Semaphore(6)

        async def _resolve_from(st: StationStop) -> None:
            async with sem:
                seg_date = compute_segment_date(travel_date, st.day_offset)
                tr = await _query_wyszukaj_polaczenia(
                    client, st.eva_code, last_eva, seg_date, end_date, train_num_str
                )
                if tr and tr.get("stacjaWyjazdu"):
                    sid = int(tr["stacjaWyjazdu"])
                    short_codes[st.eva_code] = sid
                    _EIC_SHORT_ID_CACHE[st.eva_code] = sid
                    if tr.get("dataWyjazdu"):
                        exact_deps[st.eva_code] = str(tr["dataWyjazdu"])

        await asyncio.gather(*[_resolve_from(st) for st in still_missing])

    return short_codes, category, exact_deps


def _format_dep_datetime(stop: StationStop, travel_date: str, exact_deps: Dict[str, str]) -> str:
    if stop.eva_code in exact_deps and exact_deps[stop.eva_code]:
        return exact_deps[stop.eva_code]
    seg_date = compute_segment_date(travel_date, stop.day_offset)
    hhmm = (stop.departure_time or stop.arrival_time or "12:00").strip()
    if len(hhmm) == 5:
        return f"{seg_date} {hhmm}:00"
    return f"{seg_date} 12:00:00"


async def batch_check_sprawdz_ceny_lite(
    client: httpx.AsyncClient,
    pairs: List[Tuple[int, str, int, int]],
    train_num_int: int,
    category: str,
    ticket_class: int,
) -> Dict[int, Dict[str, Any]]:
    """
    Batches multiple (pair_id, dep_datetime, from_short_id, to_short_id) queries into a single
    `sprawdzCenyLite` POST request without reserving or locking any seat.
    """
    if not pairs:
        return {}

    results: Dict[int, Dict[str, Any]] = {}
    chunk_size = 12
    for offset in range(0, len(pairs), chunk_size):
        chunk = pairs[offset:offset + chunk_size]
        polaczenia = [
            {
                "idPolaczenia": pid,
                "odcinki": [
                    {
                        "wyjazdData": dep_dt,
                        "stacjaOdKod": from_sid,
                        "stacjaDoKod": to_sid,
                        "pociagNr": train_num_int,
                        "kategoriaPociagu": category,
                    }
                ],
            }
            for (pid, dep_dt, from_sid, to_sid) in chunk
        ]
        payload = {
            "metoda": "sprawdzCenyLite",
            "wersja": "1.6.20_desktop",
            "url": "https://ebilet.intercity.pl/wyszukiwanie",
            "jezyk": "PL",
            "biletTyp": 1,
            "ofertyZaznaczone": [
                {"ofertaKod": 4, "uwzglednic": 0},
                {"ofertaKod": 14, "uwzglednic": 0},
                {"ofertaKod": 15, "uwzglednic": 0},
            ],
            "polaczenia": polaczenia,
            "podrozni": [{"kodZakupowyZnizki": 1010}],
            "urzadzenieNr": 956,
        }
        r = None
        for attempt in range(2):
            try:
                r = await client.post(EIC_SPRZEDAZ_URL, json=payload, headers=EIC_API_HEADERS, timeout=20.0)
                r.raise_for_status()
                break
            except httpx.HTTPError as exc:
                if attempt == 1:
                    logger.warning("sprawdzCenyLite failed after retry (%d pairs): %s", len(chunk), exc)
                    raise
                await asyncio.sleep(0.4)
        data = r.json() if r is not None else {}
        for item in data.get("cenyPolaczen", []):
            pid = int(item.get("idPolaczenia", -1))
            results[pid] = parse_sprawdz_ceny_entry(item.get("ceny", []), ticket_class)
    return results


def _merge_standing_legs(legs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    merged: List[Dict[str, Any]] = []
    for leg in legs:
        if leg["is_standing"] and merged and merged[-1]["is_standing"] and merged[-1]["end_idx"] == leg["start_idx"]:
            prev = merged[-1]
            prev_st = prev.get("standing_status") or "AVAILABLE"
            cur_st = leg.get("standing_status") or "AVAILABLE"
            chosen_st = prev_st if _STANDING_PRIORITY.get(prev_st, 0) >= _STANDING_PRIORITY.get(cur_st, 0) else cur_st
            merged[-1] = {
                **prev,
                "end_idx": leg["end_idx"],
                "duration_min": prev["duration_min"] + leg["duration_min"],
                "standing_status": chosen_st,
            }
        else:
            merged.append(leg)
    return merged


async def _check_single_pair(
    check_batch_fn: Callable[[List[Tuple[int, str, int, int]]], Awaitable[Dict[int, Dict[str, Any]]]],
    pair_id: int,
    dep_dt: str,
    from_sid: int,
    to_sid: int,
) -> Optional[Dict[str, Any]]:
    try:
        res = await check_batch_fn([(pair_id, dep_dt, from_sid, to_sid)])
        return res.get(pair_id)
    except Exception as exc:
        logger.debug("Single pair check failed (%s -> %s): %s", from_sid, to_sid, exc)
        return None


async def plan_non_grm_legs(
    stops: List[StationStop],
    short_codes: Dict[str, int],
    exact_deps: Dict[str, str],
    train_number: str,
    category: str,
    travel_date: str,
    ticket_class: int,
    check_batch_fn: Callable[[List[Tuple[int, str, int, int]]], Awaitable[Dict[int, Dict[str, Any]]]],
    on_progress: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    """
    Plans longest guaranteed-seat legs across `stops` using single-pair binary search
    over `sprawdzCenyLite` (fast, zero chunk-poisoning, zero seat locking).
    """
    n = len(stops) - 1
    first_sid = short_codes.get(stops[0].eva_code)
    last_sid = short_codes.get(stops[-1].eva_code)

    full_route_status: Optional[str] = None
    full_route_price: Optional[float] = None
    if first_sid and last_sid:
        full_dep = _format_dep_datetime(stops[0], travel_date, exact_deps)
        entry0 = await _check_single_pair(check_batch_fn, 0, full_dep, first_sid, last_sid)
        if entry0:
            full_route_status = entry0["standing_status"]
            full_route_price = entry0["price_pln"]
            if entry0["has_standard_seats"]:
                dur = max(1, stops[-1].arrival_min - stops[0].departure_min)
                return {
                    "legs": [
                        {
                            "start_idx": 0,
                            "end_idx": n,
                            "is_standing": False,
                            "duration_min": dur,
                            "price_pln": entry0["price_pln"],
                            "standing_status": None,
                        }
                    ],
                    "fast_path_used": True,
                    "full_route_standing_status": None,
                    "full_route_price_pln": full_route_price,
                }

    raw_legs: List[Dict[str, Any]] = []
    curr = 0
    while curr < n:
        from_stop = stops[curr]
        from_sid = short_codes.get(from_stop.eva_code)
        if not from_sid:
            dur = max(1, stops[curr + 1].arrival_min - from_stop.departure_min)
            raw_legs.append(
                {
                    "start_idx": curr,
                    "end_idx": curr + 1,
                    "is_standing": True,
                    "duration_min": dur,
                    "price_pln": None,
                    "standing_status": full_route_status or "OVERBOOKED",
                }
            )
            curr += 1
            continue

        if on_progress:
            pct = min(92, 35 + int((curr / max(1, n)) * 55))
            on_progress(f"[{pct}%] Tryb bez schematu: sprawdzanie od stacji {from_stop.name}...")

        dep_dt = _format_dep_datetime(from_stop, travel_date, exact_deps)
        valid_js = [j for j in range(curr + 1, n + 1) if short_codes.get(stops[j].eva_code)]
        if not valid_js:
            dur = max(1, stops[curr + 1].arrival_min - from_stop.departure_min)
            raw_legs.append(
                {
                    "start_idx": curr,
                    "end_idx": curr + 1,
                    "is_standing": True,
                    "duration_min": dur,
                    "price_pln": None,
                    "standing_status": full_route_status or "OVERBOOKED",
                }
            )
            curr += 1
            continue

        cache_j: Dict[int, Optional[Dict[str, Any]]] = {}

        async def _probe(j_idx: int) -> Optional[Dict[str, Any]]:
            if j_idx not in cache_j:
                cache_j[j_idx] = await _check_single_pair(
                    check_batch_fn, j_idx, dep_dt, from_sid, short_codes[stops[j_idx].eva_code]
                )
            return cache_j[j_idx]

        # Probe immediate next hop first: if it has no standard seats (with real price/status),
        # skip binary search cleanly instead of probing unreachable destinations.
        first_hop = await _probe(valid_js[0])
        best_j: Optional[int] = None
        if first_hop and first_hop["has_standard_seats"]:
            best_j = valid_js[0]
            # Check farthest stop (valid_js[-1]) if curr > 0 (curr == 0 already probed full route)
            upper_pos = len(valid_js) - 1
            if curr > 0 and upper_pos > 0:
                far_info = await _probe(valid_js[upper_pos])
                if far_info and far_info["has_standard_seats"]:
                    best_j = valid_js[upper_pos]
            if best_j != valid_js[-1]:
                lo = 1
                hi = len(valid_js) - (2 if curr == 0 and valid_js[-1] == n and full_route_status is not None else 1)
                while lo <= hi:
                    mid = (lo + hi) // 2
                    cand_j = valid_js[mid]
                    mid_info = await _probe(cand_j)
                    if mid_info and mid_info["has_standard_seats"]:
                        best_j = cand_j
                        lo = mid + 1
                    else:
                        hi = mid - 1
        elif len(valid_js) > 1 and (first_hop is None or first_hop.get("price_pln") is None):
            # Fallback if immediate 1-hop is an unsellable metro boundary (e.g. Częstochowa Stradom)
            hop2 = await _probe(valid_js[1])
            if hop2 and hop2["has_standard_seats"]:
                best_j = valid_js[1]
                lo = 2
                hi = len(valid_js) - 1
                while lo <= hi:
                    mid = (lo + hi) // 2
                    cand_j = valid_js[mid]
                    mid_info = await _probe(cand_j)
                    if mid_info and mid_info["has_standard_seats"]:
                        best_j = cand_j
                        lo = mid + 1
                    else:
                        hi = mid - 1

        if best_j is not None:
            info = cache_j.get(best_j)
            dur = max(1, stops[best_j].arrival_min - from_stop.departure_min)
            raw_legs.append(
                {
                    "start_idx": curr,
                    "end_idx": best_j,
                    "is_standing": False,
                    "duration_min": dur,
                    "price_pln": info["price_pln"] if info else None,
                    "standing_status": None,
                }
            )
            curr = best_j
        else:
            gap_status = (
                first_hop["standing_status"]
                if first_hop and first_hop.get("standing_status")
                else (full_route_status or "AVAILABLE")
            )
            dur = max(1, stops[curr + 1].arrival_min - from_stop.departure_min)
            raw_legs.append(
                {
                    "start_idx": curr,
                    "end_idx": curr + 1,
                    "is_standing": True,
                    "duration_min": dur,
                    "price_pln": None,
                    "standing_status": gap_status,
                }
            )
            curr += 1

    return {
        "legs": _merge_standing_legs(raw_legs),
        "fast_path_used": False,
        "full_route_standing_status": full_route_status,
        "full_route_price_pln": full_route_price,
    }


async def scan_non_grm_train(
    from_station: str,
    to_station: str,
    travel_date: str,
    train_number: str,
    dep_time: Optional[str],
    ticket_class: int,
    on_progress: Optional[Callable[[str], None]] = None,
) -> AnalyzeResponse:
    """
    Runs end-to-end Non-GRM availability scan using e-IC REST endpoints and returns AnalyzeResponse.
    """
    if on_progress:
        on_progress("[15%] Pociąg bez schematu miejsc — pobieranie trasy...")

    raw_stops = await asyncio.to_thread(
        resolve_real_route,
        from_station=from_station,
        to_station=to_station,
        travel_date=travel_date,
        train_number=train_number,
        dep_time=dep_time,
    )
    stops = filter_and_merge_stops(raw_stops, merge_short_segments=False)
    if len(stops) < 2:
        raise ValueError(f"Nie udało się ustalić listy przystanków dla {train_number}.")

    train_num_str, _ = extract_train_digits_and_category(train_number)
    train_num_int = int(train_num_str) if train_num_str.isdigit() else 0

    if on_progress:
        on_progress("[28%] Pobieranie identyfikatorów stacji e-IC (bez blokowania miejsc)...")

    async with httpx.AsyncClient(verify=get_system_ssl_context()) as client:
        short_codes, category, exact_deps = await resolve_eic_short_codes(
            client, stops, train_number, travel_date
        )

        async def _check(pairs: List[Tuple[int, str, int, int]]) -> Dict[int, Dict[str, Any]]:
            return await batch_check_sprawdz_ceny_lite(
                client, pairs, train_num_int, category, ticket_class
            )

        plan = await plan_non_grm_legs(
            stops=stops,
            short_codes=short_codes,
            exact_deps=exact_deps,
            train_number=train_number,
            category=category,
            travel_date=travel_date,
            ticket_class=ticket_class,
            check_batch_fn=_check,
            on_progress=on_progress,
        )

    if on_progress:
        on_progress("[100%] Zakończono analizę połączenia bez schematu miejsc!")

    legs: List[Dict[str, Any]] = plan["legs"]
    station_names = [s.name for s in stops]
    first_dep = stops[0].departure_time or stops[0].arrival_time or dep_time or "08:00"
    full_route_url = (
        build_eic_search_url(
            stops[0].eva_code,
            stops[-1].eva_code,
            travel_date,
            first_dep,
            direct_only=True,
        )
        if stops[0].eva_code and stops[-1].eva_code
        else None
    )

    steps: List[StepResponse] = []
    purchase_instructions: List[str] = []
    tickets_to_buy: List[str] = []
    standing_min = 0
    seated_count = 0
    ticket_nr = 1

    # Map each elementary segment [i, i+1] to status OK vs SOLD_OUT for diagnostics/report
    seg_is_seated = [False] * (len(stops) - 1)
    seg_standing: List[Optional[str]] = [None] * (len(stops) - 1)

    for leg_idx, leg in enumerate(legs):
        s_i = leg["start_idx"]
        e_i = leg["end_idx"]
        s_from = stops[s_i]
        s_to = stops[e_i]
        leg_dep = s_from.departure_time or s_from.arrival_time or first_dep
        leg_date = compute_segment_date(travel_date, s_from.day_offset)
        url = (
            build_eic_search_url(
                s_from.eva_code,
                s_to.eva_code,
                leg_date,
                leg_dep,
                direct_only=True,
            )
            if s_from.eva_code and s_to.eva_code
            else None
        )

        if leg["is_standing"]:
            standing_min += leg["duration_min"]
            st_status = leg.get("standing_status") or "AVAILABLE"
            for k in range(s_i, e_i):
                seg_standing[k] = st_status
            instr_note = (
                "brak miejsc ogólnodostępnych — przesiadka / miejsca specjalne lub korytarz"
                if st_status == "SPECIAL_ONLY"
                else (
                    "pociąg przepełniony w tej klasie — brak biletów stojących"
                    if st_status == "OVERBOOKED"
                    else f"brak miejsc siedzących — korytarz ({leg['duration_min']} min)"
                )
            )
            steps.append(
                StepResponse(
                    seat_uid="STANDING",
                    carriage_no=0,
                    seat_no="KORYTARZ",
                    from_station_idx=s_i,
                    to_station_idx=e_i,
                    from_station_name=s_from.name,
                    to_station_name=s_to.name,
                    duration_min=leg["duration_min"],
                    is_standing=True,
                    booking_url=url,
                    standing_status=st_status,
                )
            )
            purchase_instructions.append(f"Odcinek {s_from.name} -> {s_to.name}: {instr_note}")
        else:
            seated_count += 1
            for k in range(s_i, e_i):
                seg_is_seated[k] = True
            price_part = f" ({leg['price_pln']:.2f} zł)" if leg.get("price_pln") else ""
            transfer_msg = None
            if leg_idx > 0:
                prev_leg = legs[leg_idx - 1]
                if prev_leg["is_standing"]:
                    transfer_msg = f"Od stacji {s_from.name} masz gwarantowane miejsce siedzące (przydział automatyczny e-IC)."
                else:
                    transfer_msg = f"Na stacji {s_from.name} przesiadka na nowy bilet (kolejne miejsce przydzieli system e-IC)."

            steps.append(
                StepResponse(
                    seat_uid=f"AUTO_{s_i}_{e_i}",
                    carriage_no=0,
                    seat_no="AUTO",
                    from_station_idx=s_i,
                    to_station_idx=e_i,
                    from_station_name=s_from.name,
                    to_station_name=s_to.name,
                    duration_min=leg["duration_min"],
                    is_standing=False,
                    transfer_instruction=transfer_msg,
                    booking_url=url,
                )
            )
            tickets_to_buy.append(
                f"Bilet {ticket_nr}: {s_from.name} -> {s_to.name} (miejsce siedzące, przydział e-IC{price_part})"
            )
            purchase_instructions.append(
                f"Krok {ticket_nr}: Kup bilet na odcinek {s_from.name} -> {s_to.name} — system e-IC przydzieli miejsce siedzące automatycznie{price_part}"
            )
            ticket_nr += 1

    segments_out: List[Dict[str, Any]] = []
    report_lines: List[str] = [f"[Tryb bez schematu miejsc] {train_number} ({travel_date}, Klasa {ticket_class})"]
    for i in range(len(stops) - 1):
        sf = stops[i]
        st = stops[i + 1]
        dep = sf.departure_time or sf.arrival_time or first_dep
        dur = max(1, st.arrival_min - sf.departure_min)
        is_ok = seg_is_seated[i]
        segments_out.append(
            {
                "segment_index": i,
                "from_station": sf.name,
                "to_station": st.name,
                "departure_time": dep,
                "duration_min": dur,
                "from_eva": sf.eva_code,
                "to_eva": st.eva_code,
                "segment_date": compute_segment_date(travel_date, sf.day_offset),
                "carriages": {"AUTO": ["e-IC"]} if is_ok else {},
                "seat_tags": {},
                "status": "OK" if is_ok else "SOLD_OUT",
                "error_reason": None if is_ok else f"Brak miejsc ogólnodostępnych w Klasie {ticket_class}.",
                "standing_status": None if is_ok else seg_standing[i],
            }
        )
        status_label = "DOSTĘPNE (przydział automatyczny e-IC)" if is_ok else f"BRAK OGÓLNODOSTĘPNYCH ({seg_standing[i] or 'SOLD_OUT'})"
        report_lines.append(f"{sf.name} - {st.name} {dep}: {status_label}")

    strategies_out: List[StrategyResponse] = []
    if seated_count > 0:
        transfers_count = max(0, seated_count - 1)
        if transfers_count == 0 and standing_min == 0:
            title = "100% Trasy z miejscem siedzącym (przydział e-IC)"
        elif standing_min > 0 and transfers_count == 0:
            title = f"1 odcinek siedzący + bez gwarancji ({standing_min} min)"
        elif standing_min > 0:
            title = f"Odcinki siedzące ({format_transfers_pl(transfers_count)}, korytarz {standing_min} min)"
        else:
            title = f"Podział biletów na siedząco ({format_transfers_pl(transfers_count)})"

        strategies_out.append(
            StrategyResponse(
                rank=1,
                title=title,
                total_score=1000.0 - standing_min * 25.0 - transfers_count * 100.0,
                transfers_count=transfers_count,
                total_standing_min=standing_min,
                carriages_used=[],
                steps=steps,
                purchase_instructions=purchase_instructions,
                tickets_to_buy=tickets_to_buy,
            )
        )

    return AnalyzeResponse(
        stations=station_names,
        segments=segments_out,
        strategies=strategies_out,
        seat_matrix=[],
        raw_availability_report="\n".join(report_lines),
        purchase_instructions=purchase_instructions if strategies_out else [],
        fast_path_used=bool(plan["fast_path_used"]),
        total_route_segments=len(stops) - 1,
        full_route_booking_url=full_route_url,
        full_route_standing_status=plan["full_route_standing_status"],
        no_seatmap_mode=True,
    )
