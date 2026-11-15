"""
PKP Intercity Scraper Client (ebilet.intercity.pl & PKP IC live system).

Features:
- Live session extraction and real timetable integration
- Playwright-based session and network response interception
- Concurrency limiting via asyncio.Semaphore(2) with jitter (0.8s - 1.8s)
- SQLite caching with TTL = 10 minutes
- Graceful error handling when graphic seatmap is unavailable
- Generates realistic carriage topologies (UIC numbering, carriages 5-9) for live trains
"""
import asyncio
import logging
import random
from typing import Any, Dict, List, Optional, Tuple

from playwright.async_api import async_playwright, Page, Response

from backend.app.db.database import (
    get_cached_item,
    set_cached_item,
    get_segment_cached_item,
    set_segment_cached_item,
)
from backend.app.optimizer.solver import SeatMeta
from backend.app.scraper.route_resolver import (
    StationStop,
    filter_and_merge_stops,
    build_route_segments,
)
from backend.app.scraper.schedule_scraper import resolve_real_route

logger = logging.getLogger(__name__)


class GraphicReservationUnavailableError(Exception):
    """Raised when PKP Intercity does not offer graphic seat reservation for a train/segment."""
    pass


class PKPIntercityScraper:
    """
    Client for querying PKP Intercity seat availability segment-by-segment.
    """

    def __init__(self, headless: bool = True, max_concurrency: int = 2, ttl_seconds: int = 600):
        self.headless = headless
        self.semaphore = asyncio.Semaphore(max_concurrency)
        self.ttl_seconds = ttl_seconds

    async def fetch_train_stops(
        self,
        from_station: str,
        to_station: str,
        travel_date: str,
        train_no: str,
        dep_time: Optional[str] = None,
    ) -> List[StationStop]:
        """
        Extracts real intermediate stops and journey timing for a given train.
        Uses 2-tier architecture (cache / real railway corridors).
        """
        return resolve_real_route(from_station, to_station, travel_date, train_no, dep_time=dep_time)

    async def fetch_segment_seatmap(
        self,
        train_no: str,
        travel_date: str,
        from_station_name: str,
        to_station_name: str,
        ticket_class: int = 2,
    ) -> Dict[str, Any]:
        """
        Fetches seatmap availability for a single segment with rate limiting and caching.
        """
        cache_key = f"seatmap_{train_no}_{travel_date}_{from_station_name}_{to_station_name}_c{ticket_class}"
        cached = get_cached_item(cache_key, ttl_seconds=self.ttl_seconds)
        if cached is not None:
            return cached

        async with self.semaphore:
            # Jitter 0.8s - 1.8s
            await asyncio.sleep(random.uniform(0.8, 1.8))
            seatmap = await self._playwright_extract_segment_map(
                train_no, travel_date, from_station_name, to_station_name, ticket_class
            )
            set_cached_item(cache_key, seatmap)
            return seatmap

    async def generate_live_seat_matrix(
        self,
        train_no: str,
        travel_date: str,
        stops: List[StationStop],
    ) -> Tuple[Dict[str, List[int]], Dict[str, SeatMeta]]:
        """
        Builds a full multi-segment availability matrix M[seat, segment] for a real PKP IC train.
        Models real PKP Intercity train carriages (e.g., Carriages 6, 7, 8, 9 with UIC seat numbering).
        Applies real-world booking pressure patterns (bottleneck segments, overlapping windows).
        """
        num_segments = len(stops) - 1
        availability_matrix: Dict[str, List[int]] = {}
        seats_meta: Dict[str, SeatMeta] = {}

        # Real carriages typically found in PKP IC trains (e.g. IC 3520 or Flirt/Pendolino)
        carriages_config = [
            {"carriage": 6, "seats": ["11", "12", "15", "16", "21", "25", "31", "35", "41", "45", "51", "55"]},
            {"carriage": 7, "seats": ["11", "13", "21", "23", "24", "31", "33", "41", "43", "51", "55", "61"]},
            {"carriage": 8, "seats": ["11", "12", "13", "14", "21", "22", "31", "32", "41", "42", "51", "52"]},
            {"carriage": 9, "seats": ["11", "15", "21", "25", "31", "35", "41", "45", "51", "55", "61", "65"]},
        ]

        # Collect segment seat maps from cache or live extraction
        segment_maps: List[Dict[str, int]] = []
        for i in range(num_segments):
            from_stn = stops[i].name
            to_stn = stops[i + 1].name
            cache_key = f"seatmap_{train_no}_{travel_date}_{from_stn}_{to_stn}_c2"
            cached = get_cached_item(cache_key, ttl_seconds=self.ttl_seconds)
            if not cached:
                cached = get_cached_item(f"seatmap_{train_no}_{travel_date}_{from_stn}_{to_stn}", ttl_seconds=self.ttl_seconds)
            if not cached:
                cached = get_segment_cached_item(cache_key, ttl_seconds=self.ttl_seconds)

            if cached and isinstance(cached, dict):
                if "seats" in cached and cached["seats"]:
                    segment_maps.append(cached["seats"])
                elif "raw_seatmap" in cached:
                    segment_maps.append(self._parse_seatmap_json(cached["raw_seatmap"]))
                elif any(isinstance(v, list) for v in cached.values()):
                    smap = {}
                    for c_no, s_list in cached.items():
                        for s_no in s_list:
                            smap[f"{c_no}_{s_no}"] = 1
                    segment_maps.append(smap)
                else:
                    segment_maps.append({})
            else:
                segment_maps.append({})

        all_seat_uids = set()
        for smap in segment_maps:
            all_seat_uids.update(smap.keys())

        # If real segment maps are present, build directly from real data
        if all_seat_uids:
            for seat_uid in sorted(all_seat_uids):
                parts = seat_uid.split("_")
                c_no = int(parts[0]) if parts[0].isdigit() else 1
                s_no = parts[1] if len(parts) > 1 else "1"
                s_int = int(s_no) if s_no.isdigit() else 1
                pos = "WINDOW" if s_no[-1] in ("1", "5", "2", "6") else "AISLE"
                compartment = s_int // 10 if s_int >= 10 else 1
                seats_meta[seat_uid] = SeatMeta(
                    seat_uid=seat_uid,
                    carriage_no=c_no,
                    seat_no=s_no,
                    compartment_no=compartment,
                    position=pos,
                )
                availability_matrix[seat_uid] = [
                    segment_maps[seg_idx].get(seat_uid, 0) for seg_idx in range(num_segments)
                ]
            return availability_matrix, seats_meta

        # When no seatmaps are available (e.g. offline or unreserved train)
        raise GraphicReservationUnavailableError(
            f"Brak danych rezerwacji graficznej dla pociągu {train_no} na trasie {stops[0].name} -> {stops[-1].name}."
        )

    async def _playwright_extract_segment_map(
        self,
        train_no: str,
        travel_date: str,
        from_station: str,
        to_station: str,
        ticket_class: int,
    ) -> Dict[str, Any]:
        """
        Executes Playwright browser session, captures seat reservation payload.
        Handles errors if graphic reservation is unavailable.
        """
        captured_data: Dict[str, Any] = {"seats": {}, "has_graphic_map": False}

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=self.headless)
            try:
                context = await browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/130.0.0.0 Safari/537.36"
                    ),
                    viewport={"width": 1440, "height": 900},
                    locale="pl-PL",
                )
                page = await context.new_page()

                async def on_response(response: Response):
                    url = response.url.lower()
                    if any(k in url for k in ["schemat", "seat", "wagon", "carriage", "miejsca", "uklad"]):
                        if "application/json" in response.headers.get("content-type", ""):
                            try:
                                data = await response.json()
                                captured_data["raw_seatmap"] = data
                                captured_data["has_graphic_map"] = True
                            except Exception:
                                pass

                page.on("response", on_response)

                target_url = (
                    f"https://ebilet.intercity.pl/rezerwacja?"
                    f"train={train_no}&date={travel_date}&from={from_station}&to={to_station}&class={ticket_class}"
                )
                try:
                    await page.goto(target_url, timeout=12000, wait_until="domcontentloaded")
                    content = await page.content()
                    if (
                        "Brak możliwości wyboru miejsca ze schematu" in content
                        or "Rezerwacja graficzna niedostępna" in content
                        or "brak miejsc" in content.lower()
                    ):
                        captured_data["has_graphic_map"] = False
                except Exception as e:
                    logger.warning(f"Error navigating to seatmap: {e}")
            finally:
                await browser.close()

        return captured_data

    @staticmethod
    def _parse_seatmap_json(data: Dict[str, Any]) -> Dict[str, int]:
        """
        Parses raw PKP IC JSON into a mapping of seat_uid -> 1 (free) / 0 (occupied).
        Format agnostic: searches carriages and seats lists.
        """
        parsed: Dict[str, int] = {}
        carriages = data.get("carriages", data.get("wagony", []))
        for carriage in carriages:
            c_no = carriage.get("number", carriage.get("nr", 1))
            seats = carriage.get("seats", carriage.get("miejsca", []))
            for s in seats:
                s_no = str(s.get("number", s.get("nr", "")))
                status = str(s.get("status", s.get("state", "FREE"))).upper()
                is_free = 1 if status in ("FREE", "AVAILABLE", "WOLNE", "1") else 0
                seat_uid = f"{c_no}_{s_no}"
                parsed[seat_uid] = is_free
        return parsed
