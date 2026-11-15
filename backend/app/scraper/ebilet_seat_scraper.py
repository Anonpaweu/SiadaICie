"""
PKP Intercity e-Bilet Seat Scraper & Live Train Scanner.

Extracts real-time seat availability segment-by-segment using Playwright.
Features:
- Anti-detection browser context (real user agent, pl-PL locale, Europe/Warsaw timezone, disabled navigator.webdriver)
- Headless / visible browser mode (SCRAPER_HEADLESS=false)
- Network JSON interception + DOM/SVG inspection for seat maps
- SQLite caching (TTL 10 min / 600s)
- Diagnostic snapshots (logs/debug_snapshots/) on error
- Full multi-segment train scanner with progress reporting
- Automatic export to logs/last_scan_map.txt
"""
import os
import re
import json
import time
import logging
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any, Callable, Tuple

from playwright.async_api import async_playwright, Page, BrowserContext, Response

from backend.app.db.database import get_cached_item, set_cached_item
from backend.app.models.domain import (
    SegmentAvailabilityMap,
    FullTrainAvailabilityMap,
)
from backend.app.engine.availability_parser import format_availability_map
from backend.app.scraper.schedule_scraper import resolve_real_route
from backend.app.scraper.route_resolver import filter_and_merge_stops

logger = logging.getLogger(__name__)

DEBUG_SNAPSHOTS_DIR = Path("logs/debug_snapshots")
LAST_SCAN_FILE = Path("logs/last_scan_map.txt")


class GraphicReservationUnavailableError(Exception):
    """Raised when graphic seat reservation is unavailable or blocked."""
    pass


class EbiletSeatScraper:
    """
    Playwright-based scraper for ebilet.intercity.pl graphic seat reservation.
    """

    def __init__(
        self,
        headless: Optional[bool] = None,
        timeout_ms: int = 20000,
        ttl_seconds: int = 600,
    ):
        if headless is None:
            env_headless = os.getenv("SCRAPER_HEADLESS", "true").strip().lower()
            self.headless = env_headless not in ("false", "0", "no")
        else:
            self.headless = headless

        self.timeout_ms = timeout_ms
        self.ttl_seconds = ttl_seconds
        DEBUG_SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
        LAST_SCAN_FILE.parent.mkdir(parents=True, exist_ok=True)

    async def create_context(self, browser) -> BrowserContext:
        """Configures browser context with realistic fingerprint and evasion."""
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1440, "height": 900},
            locale="pl-PL",
            timezone_id="Europe/Warsaw",
        )
        return context

    async def init_page(self, context: BrowserContext) -> Page:
        """Initializes page and disables navigator.webdriver."""
        page = await context.new_page()
        page.set_default_timeout(self.timeout_ms)
        await page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        return page

    async def close_cookie_banners(self, page: Page) -> None:
        """Dismisses common cookies / RODO consent banners if present."""
        cookie_selectors = [
            "#rodo-accept",
            "#accept-cookies",
            "button#onetrust-accept-btn-handler",
            "button:has-text('Zaakceptuj')",
            "button:has-text('Akceptuj')",
            "button:has-text('Zgadzam się')",
            "button:has-text('Przejdź do serwisu')",
            ".cookie-agree-btn",
            ".rodo-btn",
        ]
        for sel in cookie_selectors:
            try:
                elem = page.locator(sel).first
                if await elem.is_visible(timeout=1500):
                    await elem.click()
                    await asyncio.sleep(0.3)
                    logger.info(f"Dismissed cookie banner via: {sel}")
                    break
            except Exception:
                pass

    async def save_debug_snapshot(self, page: Page, name_prefix: str) -> None:
        """Saves screenshot and HTML for diagnostic inspection."""
        try:
            ts = int(time.time())
            snap_path_png = DEBUG_SNAPSHOTS_DIR / f"{name_prefix}_{ts}.png"
            snap_path_html = DEBUG_SNAPSHOTS_DIR / f"{name_prefix}_{ts}.html"
            await page.screenshot(path=str(snap_path_png), full_page=True)
            content = await page.content()
            snap_path_html.write_text(content, encoding="utf-8")
            logger.info(f"[SNAPSHOT] Saved failure snapshot: {snap_path_png}")
        except Exception as e:
            logger.warning(f"Could not save debug snapshot: {e}")

    async def search_segment_on_intercity(
        self,
        page: Page,
        from_station: str,
        to_station: str,
        date_str: str,
        departure_time: str,
        train_number: str,
        ticket_class: int = 2,
    ) -> Dict[str, List[str]]:
        """
        Executes search on ebilet.intercity.pl and extracts free seats per carriage.
        Returns dict: carriage_number -> [list of free seat strings].
        """
        captured_network_json: List[Dict[str, Any]] = []

        async def on_response(response: Response):
            url = response.url.lower()
            if any(k in url for k in ["seat", "schemat", "wagon", "carriage", "uklad", "places"]):
                ct = response.headers.get("content-type", "")
                if "application/json" in ct:
                    try:
                        data = await response.json()
                        captured_network_json.append(data)
                        logger.debug(f"[NETWORK] Intercepted seatmap JSON from: {response.url}")
                    except Exception:
                        pass

        page.on("response", on_response)

        try:
            # 1. Navigate to ebilet search
            search_url = "https://ebilet.intercity.pl/"
            logger.info(f"[SCRAPER] Navigating to {search_url} for {from_station} -> {to_station} ({departure_time})")
            await page.goto(search_url, wait_until="domcontentloaded")
            await asyncio.sleep(0.5)
            await self.close_cookie_banners(page)

            # 2. Input from_station
            from_locators = [
                "input[placeholder*='początkowa']",
                "input#stacja-od",
                "input[name='from']",
                "#from_station",
                "input.station-input-from",
            ]
            for sel in from_locators:
                loc = page.locator(sel).first
                if await loc.is_visible(timeout=1000):
                    await loc.click()
                    await loc.fill(from_station)
                    await asyncio.sleep(0.8)
                    # Pick from autocomplete list
                    auto_item = page.locator(".ui-autocomplete li, .dropdown-menu li, .typeahead li, [role='option']").first
                    if await auto_item.is_visible(timeout=2000):
                        await auto_item.click()
                    break

            # 3. Input to_station
            to_locators = [
                "input[placeholder*='końcowa']",
                "input#stacja-do",
                "input[name='to']",
                "#to_station",
                "input.station-input-to",
            ]
            for sel in to_locators:
                loc = page.locator(sel).first
                if await loc.is_visible(timeout=1000):
                    await loc.click()
                    await loc.fill(to_station)
                    await asyncio.sleep(0.8)
                    auto_item = page.locator(".ui-autocomplete li, .dropdown-menu li, .typeahead li, [role='option']").first
                    if await auto_item.is_visible(timeout=2000):
                        await auto_item.click()
                    break

            # 4. Input date and time
            # Departure time close to segment departure
            time_locators = [
                "input#godzina",
                "input[name='time']",
                "input[type='time']",
                "#departure_time",
                "input[placeholder*='Godzina']",
            ]
            clean_dep_time = departure_time.strip() if departure_time else "12:00"
            for sel in time_locators:
                loc = page.locator(sel).first
                if await loc.is_visible(timeout=1000):
                    await loc.click()
                    await loc.fill(clean_dep_time)
                    break

            # 5. Click search button
            submit_locators = [
                "button[type='submit']",
                "button:has-text('Wyszukaj')",
                "button:has-text('Szukaj')",
                "#btn-search",
            ]
            for sel in submit_locators:
                loc = page.locator(sel).first
                if await loc.is_visible(timeout=1000):
                    await loc.click()
                    break

            # Wait for search results or direct navigation
            await page.wait_for_load_state("networkidle", timeout=8000)

            # 6. Locate matching train row/card
            train_clean_num = re.sub(r'^[A-Z]+\s*', '', train_number).strip()
            # Look for row containing train number or matching time
            connection_rows = page.locator(".connection-row, .train-result-row, tr.connection, .connection-card")
            row_count = await connection_rows.count()

            chosen_row = None
            if row_count > 0:
                for idx in range(row_count):
                    r = connection_rows.nth(idx)
                    text = await r.inner_text()
                    if train_clean_num in text or (clean_dep_time in text):
                        chosen_row = r
                        break
                if not chosen_row:
                    chosen_row = connection_rows.first
            else:
                chosen_row = page.locator("body")

            # 7. Click Choose/Buy button
            buy_btn = chosen_row.locator("button:has-text('Wybierz'), a:has-text('Wybierz'), button:has-text('Kup bilet'), a:has-text('Kup')").first
            if await buy_btn.is_visible(timeout=3000):
                await buy_btn.click()
                await page.wait_for_load_state("domcontentloaded", timeout=6000)
                await asyncio.sleep(0.5)

            # 8. Look for 'Wybierz miejsce ze schematu' / 'Wskazanie miejsca na schemacie wagonu'
            schema_btn_selectors = [
                "button:has-text('schematu')",
                "button:has-text('Wskazanie miejsca')",
                "a:has-text('Wybierz miejsce ze schematu')",
                "input[value*='schemat']",
                ".btn-seatmap",
                "#btn_graphic_reservation",
            ]
            for sel in schema_btn_selectors:
                elem = page.locator(sel).first
                if await elem.is_visible(timeout=3000):
                    await elem.click()
                    await asyncio.sleep(1.0)
                    break

            # 9. Extract seats via dual mechanism: Sposób A (Network JSON) or Sposób B (DOM)
            carriages_data: Dict[str, List[str]] = {}

            # Check if network JSON was intercepted
            if captured_network_json:
                carriages_data = self._extract_seats_from_json_list(captured_network_json)

            # If network interception didn't yield seats, inspect DOM
            if not carriages_data:
                carriages_data = await self._extract_seats_from_dom(page)

            return carriages_data

        except Exception as e:
            logger.warning(f"[SCRAPER] Error during scraping {from_station} -> {to_station}: {e}")
            await self.save_debug_snapshot(page, f"err_{from_station}_{to_station}")
            return {}

    def _extract_seats_from_json_list(self, json_list: List[Dict[str, Any]]) -> Dict[str, List[str]]:
        """Parses intercepted JSON payloads looking for carriages and seat statuses."""
        carriages: Dict[str, List[str]] = {}
        for data in json_list:
            items = data.get("carriages", data.get("wagony", []))
            if isinstance(items, list):
                for carriage in items:
                    c_no = str(carriage.get("number", carriage.get("nr", carriage.get("carriage_no", ""))))
                    if not c_no:
                        continue
                    seats = carriage.get("seats", carriage.get("miejsca", []))
                    free_seats = []
                    for s in seats:
                        s_no = str(s.get("number", s.get("nr", "")))
                        status = str(s.get("status", s.get("state", "FREE"))).upper()
                        # Filter out non-seat types (e.g. bike, wheelchair unless free regular seat)
                        seat_type = str(s.get("type", "REGULAR")).upper()
                        if status in ("FREE", "AVAILABLE", "WOLNE", "1") and s_no.isdigit():
                            free_seats.append(s_no)
                    if free_seats:
                        sorted_seats = sorted(list(set(free_seats)), key=lambda x: int(x))
                        carriages[c_no] = sorted_seats
                    elif c_no not in carriages:
                        carriages[c_no] = []
        return carriages

    async def _extract_seats_from_dom(self, page: Page) -> Dict[str, List[str]]:
        """
        Inspects DOM / SVG elements for carriage tabs and green/available seats.
        Iterates over carriage buttons/tabs if present.
        """
        carriages: Dict[str, List[str]] = {}

        # 1. Look for carriage tabs/buttons
        tab_selectors = [
            ".carriage-tab",
            ".wagon-tab",
            "button[data-carriage]",
            "button[data-wagon]",
            "ul.nav-tabs li a",
            "button:has-text('Wagon')",
        ]

        carriage_tabs = []
        for sel in tab_selectors:
            loc = page.locator(sel)
            cnt = await loc.count()
            if cnt > 0:
                carriage_tabs = [loc.nth(i) for i in range(cnt)]
                break

        if carriage_tabs:
            for tab in carriage_tabs:
                tab_text = await tab.inner_text()
                c_match = re.search(r'\d+', tab_text)
                c_no = c_match.group(0) if c_match else "1"
                try:
                    await tab.click()
                    await asyncio.sleep(0.4)
                except Exception:
                    pass

                free_seats = await self._find_green_seats_in_current_view(page)
                carriages[c_no] = free_seats
        else:
            # Single wagon view or directly rendered seats
            free_seats = await self._find_green_seats_in_current_view(page)
            if free_seats:
                carriages["1"] = free_seats

        return carriages

    async def _find_green_seats_in_current_view(self, page: Page) -> List[str]:
        """Finds all green / available seat numbers in currently visible seatmap."""
        free_seats: List[str] = []

        # Look for seat elements (SVG or HTML)
        seat_locators = [
            ".seat.available",
            ".seat.free",
            ".seat.wolne",
            "[data-status='available']",
            "[data-status='free']",
            "button.seat:not(.occupied):not(.taken):not(.disabled)",
            "g.seat-free text",
            "rect.seat-free",
        ]

        for sel in seat_locators:
            locs = page.locator(sel)
            cnt = await locs.count()
            if cnt > 0:
                for idx in range(cnt):
                    elem = locs.nth(idx)
                    text = (await elem.inner_text()).strip()
                    if not text:
                        text = await elem.get_attribute("data-seat") or await elem.get_attribute("data-number") or ""
                    num_match = re.search(r'\b\d+\b', text)
                    if num_match:
                        free_seats.append(num_match.group(0))
                break

        # Filter and sort
        numeric_seats = [s for s in set(free_seats) if s.isdigit()]
        return sorted(numeric_seats, key=lambda x: int(x))

    async def scan_segment(
        self,
        from_station: str,
        to_station: str,
        travel_date: str,
        departure_time: str,
        train_number: str = "IC 3520",
        ticket_class: int = 2,
        use_cache: bool = True,
    ) -> Dict[str, List[str]]:
        """
        Scans a single segment with SQLite caching (TTL 10 min).
        """
        cache_key = f"seatmap_{train_number}_{travel_date}_{from_station}_{to_station}"
        if use_cache:
            cached = get_cached_item(cache_key, ttl_seconds=self.ttl_seconds)
            if cached and isinstance(cached, dict):
                logger.info(f"[CACHE HIT] Seatmap for segment {from_station} -> {to_station}")
                return cached

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=self.headless)
            try:
                context = await self.create_context(browser)
                page = await self.init_page(context)
                carriages = await self.search_segment_on_intercity(
                    page=page,
                    from_station=from_station,
                    to_station=to_station,
                    date_str=travel_date,
                    departure_time=departure_time,
                    train_number=train_number,
                    ticket_class=ticket_class,
                )
                if carriages:
                    set_cached_item(cache_key, carriages)
                return carriages
            finally:
                await browser.close()


class LiveTrainScanner:
    """
    Orchestrates full-route scanning of real PKP Intercity trains.
    Combines route discovery, segment-by-segment seat extraction, progress updates,
    and output formatting.
    """

    def __init__(
        self,
        headless: Optional[bool] = None,
        on_progress: Optional[Callable[[str], None]] = None,
        ttl_seconds: int = 600,
    ):
        self.scraper = EbiletSeatScraper(headless=headless, ttl_seconds=ttl_seconds)
        self.on_progress = on_progress

    def _notify(self, msg: str) -> None:
        logger.info(f"[SCANNER] {msg}")
        if self.on_progress:
            try:
                self.on_progress(msg)
            except Exception:
                pass

    async def scan_full_train(
        self,
        from_station: str,
        to_station: str,
        travel_date: str,
        train_number: str = "IC 3520",
        departure_time: Optional[str] = None,
    ) -> FullTrainAvailabilityMap:
        """
        1. Obtains all intermediate stops for the train.
        2. Scans every elementary segment.
        3. Constructs FullTrainAvailabilityMap.
        4. Saves output map to logs/last_scan_map.txt.
        """
        self._notify(f"Rozpoczynanie skanowania pociągu {train_number} na trasie {from_station} -> {to_station} ({travel_date})...")

        # 1. Resolve intermediate route stops
        stops = resolve_real_route(
            from_station=from_station,
            to_station=to_station,
            travel_date=travel_date,
            train_number=train_number,
            dep_time=departure_time,
        )
        stops = filter_and_merge_stops(stops, merge_short_segments=False)

        num_segments = len(stops) - 1
        if num_segments <= 0:
            raise ValueError(f"Nie znaleziono odcinków dla trasy {from_station} -> {to_station}")

        # Log complete route
        route_desc = " -> ".join([f"{idx+1}. {s.name} ({s.departure_time or s.arrival_time or '??:??'})" for idx, s in enumerate(stops)])
        logger.info(f"[ROUTE] Pociąg {train_number}: {route_desc}")
        self._notify(f"Wykryto {num_segments} odcinków: {route_desc}")

        segments_map: List[SegmentAvailabilityMap] = []

        # 2. Iterate each segment
        for idx in range(num_segments):
            s_from = stops[idx]
            s_to = stops[idx + 1]
            seg_dep_time = s_from.departure_time or departure_time or "12:00"

            progress_msg = f"Skanowanie odcinka {idx + 1}/{num_segments}: {s_from.name} -> {s_to.name} ({seg_dep_time})..."
            self._notify(progress_msg)

            # Scan segment
            carriages = await self.scraper.scan_segment(
                from_station=s_from.name,
                to_station=s_to.name,
                travel_date=travel_date,
                departure_time=seg_dep_time,
                train_number=train_number,
            )

            # Build summary of free seats in carriages
            c_summary_parts = []
            for c_no in sorted(carriages.keys(), key=lambda x: int(x) if x.isdigit() else 99):
                seats_cnt = len(carriages[c_no])
                c_summary_parts.append(f"W{c_no} ({seats_cnt})")

            c_summary = ", ".join(c_summary_parts) if c_summary_parts else "Brak wolnych miejsc"
            self._notify(f"Odcinek {idx + 1}/{num_segments}: Znaleziono wolne miejsca w: {c_summary}")

            seg_map = SegmentAvailabilityMap(
                segment_index=idx,
                from_station=s_from.name,
                to_station=s_to.name,
                departure_time=seg_dep_time,
                carriages=carriages,
            )
            segments_map.append(seg_map)

        # 3. Create FullTrainAvailabilityMap
        full_map = FullTrainAvailabilityMap(
            train_number=train_number,
            travel_date=travel_date,
            segments=segments_map,
        )

        # 4. Format and save to logs/last_scan_map.txt
        formatted_txt = format_availability_map(full_map)
        try:
            LAST_SCAN_FILE.write_text(formatted_txt, encoding="utf-8")
            logger.info(f"[SCANNER] Saved last scan map to {LAST_SCAN_FILE}")
        except Exception as e:
            logger.warning(f"Failed to write last scan map: {e}")

        self._notify("Zakończono skanowanie wszystkich odcinków pociągu!")
        return full_map
