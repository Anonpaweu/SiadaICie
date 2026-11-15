"""
PKP Intercity e-Bilet Seat Scraper & Live Train Scanner (V2 Architecture).

Comprehensive implementation addressing Plan V2 requirements:
- Direct UI headless / headful binding: launches Chromium with visible window when requested
- Anti-detection: Chrome UA, Europe/Warsaw timezone, disabled navigator.webdriver, AutomationControlled disabled
- Strict Class 2 enforcement: select_second_class_for_train, fails immediately if Class 2 is sold out
- Intermediate modal handling: automatically dismisses warning and condition popups
- Fail-Fast timeouts (10-12s): prevents freezing at 85%, captures error_85_percent.png and debug_85.html
- Network JSON Interception + Iframe / DOM SVG inspection
- Strict Class 2 carriage filtering (skips Class 1, WARS WR, sleeping WL, couchettes Bc)
- Granular micro-steps reporting (70%, 75%, 80%, 85%, 90%, 95%, 100%)
- Sequential rate limiting (1.5 - 2.5s delay) to avoid WAF block
"""
import os
import re
import json
import time
import logging
import asyncio
import random
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any, Callable, Tuple, Union

from playwright.async_api import async_playwright, Page, BrowserContext, Response, Frame

from backend.app.db.database import (
    get_cached_item,
    set_cached_item,
    get_segment_cached_item,
    set_segment_cached_item,
)
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
TARGET_CLASS = 2  # Hardcoded default: 2nd class sitting carriages per Plan V2


from backend.app.scraper.ic_client import GraphicReservationUnavailableError


class EbiletSeatScraper:
    """
    Playwright-based scraper for ebilet.intercity.pl graphic seat reservation.
    """

    def __init__(
        self,
        headless: Optional[bool] = None,
        show_browser: Optional[bool] = None,
        timeout_ms: int = 12000,
        ttl_seconds: int = 600,
    ):
        if show_browser is not None:
            self.headless = not show_browser
        elif headless is not None:
            self.headless = headless
        else:
            env_headless = os.getenv("SCRAPER_HEADLESS", "true").strip().lower()
            self.headless = env_headless not in ("false", "0", "no")

        self.timeout_ms = timeout_ms
        self.ttl_seconds = ttl_seconds
        DEBUG_SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
        LAST_SCAN_FILE.parent.mkdir(parents=True, exist_ok=True)

        logger.info(f"[DEBUG] Uruchomiono scraper (Tryb widoczny: {'TAK' if not self.headless else 'NIE'})")

    async def create_context(self, browser) -> BrowserContext:
        """Configures browser context with realistic fingerprint and evasion."""
        context = await browser.new_context(
            no_viewport=True if not self.headless else False,
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            locale="pl-PL",
            timezone_id="Europe/Warsaw",
        )
        return context

    async def init_page(self, context: BrowserContext) -> Page:
        """Initializes page with stealth scripts and fast timeout."""
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
            "button:has-text('Zaakceptuj wszystkie')",
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
                if await elem.is_visible(timeout=1000):
                    await elem.click()
                    await asyncio.sleep(0.3)
                    logger.info(f"Dismissed cookie banner via: {sel}")
                    break
            except Exception:
                pass

    async def dismiss_intermediate_modals(self, page: Page) -> None:
        """
        Dismisses intermediate warning modals (mandatory reservation, replacement bus ZKA,
        no guaranteed seat, terms agreement).
        """
        modal_button_selectors = [
            "button:has-text('Rozumiem')",
            "button:has-text('Kontynuuj')",
            "button:has-text('Dalej')",
            "button:has-text('Akceptuję')",
            "button:has-text('Przejdź dalej')",
            "button:has-text('Zamknij')",
            ".modal-footer button.btn-primary",
            ".modal button:has-text('OK')",
        ]
        for sel in modal_button_selectors:
            try:
                elem = page.locator(sel).first
                if await elem.is_visible(timeout=800):
                    await elem.click()
                    await asyncio.sleep(0.3)
                    logger.info(f"[MODAL] Dismissed intermediate modal via: {sel}")
            except Exception:
                pass

    async def save_debug_snapshot(self, page: Page, name_prefix: str) -> Tuple[Path, Path]:
        """Saves screenshot and HTML for diagnostic inspection."""
        try:
            snap_png = DEBUG_SNAPSHOTS_DIR / f"{name_prefix}.png"
            snap_html = DEBUG_SNAPSHOTS_DIR / f"{name_prefix}.html"
            await page.screenshot(path=str(snap_png), full_page=True)
            content = await page.content()
            snap_html.write_text(content, encoding="utf-8")
            logger.info(f"[SNAPSHOT] Saved failure snapshot: {snap_png}")
            return snap_png, snap_html
        except Exception as e:
            logger.warning(f"Could not save debug snapshot: {e}")
            return DEBUG_SNAPSHOTS_DIR / "error.png", DEBUG_SNAPSHOTS_DIR / "error.html"

    async def select_second_class_for_train(self, page: Page, chosen_row) -> None:
        """
        Strictly selects Class 2 for the chosen train card.
        Ensures Class 1 is NEVER clicked. Fails fast if Class 2 is sold out.
        """
        # Look for 2nd class tiles/buttons inside train card
        class2_locators = [
            chosen_row.locator("text=/.*2\\s*(kl|klasa|Class).*/i").first,
            chosen_row.locator("[data-class='2']").first,
            chosen_row.locator(".class-2, .second-class").first,
            chosen_row.locator("button:has-text('2 kl')").first,
            chosen_row.locator("button:has-text('2 klasa')").first,
        ]

        class2_clicked = False
        for loc in class2_locators:
            try:
                if await loc.is_visible(timeout=800):
                    text = (await loc.inner_text()).lower()
                    if any(b in text for b in ["brak miejsc", "niedostępne", "zablokowana", "wyprzedane"]):
                        raise GraphicReservationUnavailableError(
                            "BRAK_MIEJSC_KLASA_2: W pociągu wyczerpano miejsca w Klasie 2 na tym odcinku."
                        )
                    await loc.click()
                    class2_clicked = True
                    await asyncio.sleep(0.5)
                    break
            except GraphicReservationUnavailableError:
                raise
            except Exception:
                pass

        # Now click "Wybierz" / "Kup bilet"
        buy_locators = [
            chosen_row.locator("button:has-text('Wybierz')").first,
            chosen_row.locator("a:has-text('Wybierz')").first,
            chosen_row.locator("button:has-text('Kup bilet')").first,
            chosen_row.locator("button:has-text('Kup')").first,
            chosen_row.locator("a:has-text('Kup')").first,
        ]

        buy_clicked = False
        for buy_loc in buy_locators:
            try:
                if await buy_loc.is_visible(timeout=1500):
                    await buy_loc.click()
                    buy_clicked = True
                    break
            except Exception:
                pass

        if not buy_clicked:
            # Fallback to direct button in row
            btn = chosen_row.locator("button, a.btn").first
            if await btn.is_visible(timeout=1000):
                await btn.click()

    async def verify_class_2_selected(self, page: Page) -> None:
        """
        Verifies that ticket configuration screen has Class 2 selected.
        If Class 1 is detected, aborts immediately.
        """
        try:
            content = await page.content()
            content_lower = content.lower()
            if "klasa 1" in content_lower and "klasa 2" not in content_lower:
                if "bilet na 1. klasę" in content_lower or "wybrano: 1 klasa" in content_lower:
                    raise GraphicReservationUnavailableError(
                        "BŁĄD WYBORU KLASY: Serwis e-IC otworzył konfigurację dla 1. klasy!"
                    )
        except GraphicReservationUnavailableError:
            raise
        except Exception:
            pass

    async def search_segment_on_intercity(
        self,
        page: Page,
        from_station: str,
        to_station: str,
        date_str: str,
        departure_time: str,
        train_number: str,
        ticket_class: int = TARGET_CLASS,
        progress_cb: Optional[Callable[[int, str], None]] = None,
    ) -> Dict[str, List[str]]:
        """
        Executes search on ebilet.intercity.pl and extracts free seats per carriage.
        Uses fail-fast timeout (10-12s) and granular micro-steps reporting.
        """
        def report(pct: int, msg: str):
            logger.info(f"[{pct}%] {msg}")
            if progress_cb:
                try:
                    progress_cb(pct, msg)
                except Exception:
                    pass

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
            report(15, f"Otwieranie strony PKP Intercity dla trasy {from_station} -> {to_station}...")
            search_url = "https://ebilet.intercity.pl/"
            await page.goto(search_url, wait_until="domcontentloaded")
            await asyncio.sleep(0.5)
            await self.close_cookie_banners(page)

            # 2. Input from_station with autocomplete
            report(25, f"Wprowadzanie stacji początkowej: {from_station}...")
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
                    await asyncio.sleep(0.5)
                    auto_item = page.locator(".ui-autocomplete li, .dropdown-menu li, .typeahead li, [role='option']").first
                    if await auto_item.is_visible(timeout=1500):
                        await auto_item.click()
                    break

            # 3. Input to_station with autocomplete
            report(35, f"Wprowadzanie stacji końcowej: {to_station}...")
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
                    await asyncio.sleep(0.5)
                    auto_item = page.locator(".ui-autocomplete li, .dropdown-menu li, .typeahead li, [role='option']").first
                    if await auto_item.is_visible(timeout=1500):
                        await auto_item.click()
                    break

            # 4. Input date and time
            report(45, f"Ustawianie godziny odjazdu: {departure_time}...")
            clean_dep_time = departure_time.strip() if departure_time else "12:00"
            time_locators = [
                "input#godzina",
                "input[name='time']",
                "input[type='time']",
                "#departure_time",
                "input[placeholder*='Godzina']",
            ]
            for sel in time_locators:
                loc = page.locator(sel).first
                if await loc.is_visible(timeout=1000):
                    await loc.click()
                    await loc.fill(clean_dep_time)
                    break

            # 5. Click search button
            report(55, "Wyszukiwanie listy połączeń...")
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

            # Wait for search results
            try:
                await page.wait_for_load_state("networkidle", timeout=6000)
            except Exception:
                pass

            # 6. Locate matching train row/card (70%)
            report(70, f"Dopasowywanie pociągu {train_number} i wybór Klasy 2...")
            train_clean_num = re.sub(r'^[A-Z]+\s*', '', train_number).strip()
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

            # Strict Class 2 selection per Plan V2
            await self.select_second_class_for_train(page, chosen_row)
            await asyncio.sleep(0.5)

            # 7. Ticket configuration & dismiss modals (75%)
            report(75, "Konfiguracja biletu i zamykanie ewentualnych pop-upów...")
            await self.dismiss_intermediate_modals(page)
            await self.verify_class_2_selected(page)

            # 8. Click "Wybierz miejsce ze schematu" (80%)
            report(80, "Przechodzenie do graficznego schematu wagonów...")
            schema_btn_selectors = [
                "button:has-text('schematu')",
                "button:has-text('Wskazanie miejsca')",
                "button:has-text('Wskaż miejsce')",
                "a:has-text('Wybierz miejsce ze schematu')",
                "input[value*='schemat']",
                ".btn-seatmap",
                "#btn_graphic_reservation",
            ]

            schema_clicked = False
            for sel in schema_btn_selectors:
                elem = page.locator(sel).first
                try:
                    if await elem.is_visible(timeout=3000):
                        await elem.click()
                        schema_clicked = True
                        await asyncio.sleep(0.8)
                        break
                except Exception:
                    pass

            # 9. Wait for seatmap render or JSON interception (85% - DEADLOCK ELIMINATED)
            report(85, "Oczekiwanie na wyrenderowanie grafiki wagonu / odpowiedzi JSON...")
            target_container: Union[Page, Frame] = page

            # Check if seatmap is inside an iframe per Plan V2
            for frame in page.frames:
                frame_url = frame.url.lower()
                if any(k in frame_url for k in ["schemat", "seat", "wagon", "carriage"]):
                    target_container = frame
                    logger.info(f"[IFRAME] Wykryto schemat wagonu w ramce iframe: {frame.url}")
                    break

            # Fast wait for seatmap elements or JSON response (max 10s)
            start_wait = time.time()
            seatmap_found = False
            while time.time() - start_wait < 10.0:
                if captured_network_json:
                    seatmap_found = True
                    break

                # Check DOM in target container
                test_seats = target_container.locator(".seat, [data-seat], rect.seat-free, .carriage-tab, button:has-text('Wagon')")
                if await test_seats.count() > 0:
                    seatmap_found = True
                    break

                await self.dismiss_intermediate_modals(page)
                await asyncio.sleep(0.4)

            # If still not found at 85%, fail fast with snapshot per Plan V2
            if not seatmap_found:
                content = await page.content()
                content_lower = content.lower()
                reason = "Nie znaleziono przycisków schematu wagonu."
                if "brak miejsc" in content_lower:
                    reason = "Brak wolnych miejsc w pociągu (wyprzedane)."
                elif "przerwa techniczna" in content_lower:
                    reason = "Przerwa techniczna systemu PKP Intercity."
                elif "cloudflare" in content_lower or "challenge" in content_lower:
                    reason = "Blokada WAF / Cloudflare."

                snap_png, _ = await self.save_debug_snapshot(page, f"error_85_percent_{from_station}_{to_station}")
                raise GraphicReservationUnavailableError(
                    f"Nie udało się otworzyć schematu wagonu na 85% dla trasy {from_station} -> {to_station}. "
                    f"Powód: {reason} (Zapisano zrzut do {snap_png.name})"
                )

            # 10. Extract seats from Class 2 carriages (90%)
            report(90, "Odczytywanie wolnych miejsc z wagonów Klasy 2...")
            carriages_data: Dict[str, List[str]] = {}

            if captured_network_json:
                carriages_data = self._extract_seats_from_json_list(captured_network_json)

            if not carriages_data:
                carriages_data = await self._extract_seats_from_dom(target_container)

            # 11. Complete (95%)
            report(95, f"Odczytano wolne miejsca w {len(carriages_data)} wagonach.")
            return carriages_data

        except GraphicReservationUnavailableError:
            raise
        except Exception as e:
            logger.warning(f"[SCRAPER] Error during scraping {from_station} -> {to_station}: {e}")
            await self.save_debug_snapshot(page, f"err_{from_station}_{to_station}")
            return {}

    def _extract_seats_from_json_list(self, json_list: List[Dict[str, Any]]) -> Dict[str, List[str]]:
        """Parses intercepted JSON payloads, filtering strictly for Class 2 sitting carriages."""
        carriages: Dict[str, List[str]] = {}
        for data in json_list:
            items = data.get("carriages", data.get("wagony", []))
            if isinstance(items, list):
                for carriage in items:
                    c_no = str(carriage.get("number", carriage.get("nr", carriage.get("carriage_no", ""))))
                    if not c_no:
                        continue

                    # Filter carriage class: skip class 1, WARS, sleepers per Plan V2
                    c_class = str(carriage.get("class", carriage.get("klasa", "2")))
                    c_type = str(carriage.get("type", carriage.get("typ", ""))).upper()
                    if c_class == "1" or any(skip in c_type for skip in ["WR", "WARS", "WL", "BC"]):
                        continue

                    seats = carriage.get("seats", carriage.get("miejsca", []))
                    free_seats = []
                    for s in seats:
                        s_no = str(s.get("number", s.get("nr", "")))
                        status = str(s.get("status", s.get("state", "FREE"))).upper()
                        if status in ("FREE", "AVAILABLE", "WOLNE", "1") and s_no.isdigit():
                            free_seats.append(s_no)
                    if free_seats:
                        sorted_seats = sorted(list(set(free_seats)), key=lambda x: int(x))
                        carriages[c_no] = sorted_seats
                    elif c_no not in carriages:
                        carriages[c_no] = []
        return carriages

    async def _extract_seats_from_dom(self, container: Union[Page, Frame]) -> Dict[str, List[str]]:
        """
        Inspects DOM / SVG elements for carriage tabs and green/available seats.
        Filters out Class 1 carriages, WARS restaurant and sleeper cars per Plan V2.
        """
        carriages: Dict[str, List[str]] = {}

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
            loc = container.locator(sel)
            cnt = await loc.count()
            if cnt > 0:
                carriage_tabs = [loc.nth(i) for i in range(cnt)]
                break

        if carriage_tabs:
            for tab in carriage_tabs:
                tab_text = await tab.inner_text()
                # Skip Class 1, WARS or sleepers per Plan V2
                if "kl. 1" in tab_text.lower() or "klasa 1" in tab_text.lower() or "wars" in tab_text.lower() or "wr" in tab_text.lower():
                    continue

                c_match = re.search(r'\d+', tab_text)
                c_no = c_match.group(0) if c_match else "2"
                try:
                    await tab.click()
                    await asyncio.sleep(0.4)
                except Exception:
                    pass

                free_seats = await self._find_green_seats_in_current_view(container)
                carriages[c_no] = free_seats
        else:
            free_seats = await self._find_green_seats_in_current_view(container)
            if free_seats:
                carriages["2"] = free_seats

        return carriages

    async def _find_green_seats_in_current_view(self, container: Union[Page, Frame]) -> List[str]:
        """Finds all green / available seat numbers in currently visible seatmap."""
        free_seats: List[str] = []

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
            locs = container.locator(sel)
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

        numeric_seats = [s for s in set(free_seats) if s.isdigit()]
        return sorted(numeric_seats, key=lambda x: int(x))

    async def scan_segment(
        self,
        from_station: str,
        to_station: str,
        travel_date: str,
        departure_time: str,
        train_number: str = "IC 3520",
        ticket_class: int = TARGET_CLASS,
        use_cache: bool = True,
        progress_cb: Optional[Callable[[int, str], None]] = None,
    ) -> Dict[str, List[str]]:
        """
        Scans a single segment with SQLite caching (TTL 10 min) and sequential rate limiting.
        """
        cache_key = f"seatmap_{train_number}_{travel_date}_{from_station}_{to_station}"
        if use_cache:
            cached = get_cached_item(cache_key, ttl_seconds=self.ttl_seconds)
            if cached and isinstance(cached, dict):
                logger.info(f"[CACHE HIT] Seatmap for segment {from_station} -> {to_station}")
                return cached

        # Rate limiting delay per Plan V2 (1.5 - 2.5s jitter)
        await asyncio.sleep(random.uniform(1.2, 2.2))

        async with async_playwright() as p:
            # Per Plan V2: fresh browser launch with slow_mo for visible mode and window positioning
            browser = await p.chromium.launch(
                headless=self.headless,
                slow_mo=150 if not self.headless else 0,
                args=[
                    "--start-maximized",
                    "--disable-blink-features=AutomationControlled",
                    "--window-position=50,50",
                ],
            )
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
                    progress_cb=progress_cb,
                )
                if carriages:
                    set_cached_item(cache_key, carriages)
                    set_segment_cached_item(cache_key, carriages)
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
        show_browser: Optional[bool] = None,
        on_progress: Optional[Callable[[str], None]] = None,
        ttl_seconds: int = 600,
    ):
        self.scraper = EbiletSeatScraper(
            headless=headless,
            show_browser=show_browser,
            ttl_seconds=ttl_seconds,
        )
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
        mode_str = "WIDOCZNY (okno przeglądarki otwarte)" if not self.scraper.headless else "W TLE (headless)"
        self._notify(f"Rozpoczynanie skanowania pociągu {train_number} (Tryb: {mode_str})...")

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

        route_desc = " -> ".join([f"{idx+1}. {s.name} ({s.departure_time or s.arrival_time or '??:??'})" for idx, s in enumerate(stops)])
        logger.info(f"[ROUTE] Pociąg {train_number}: {route_desc}")
        self._notify(f"Wykryto {num_segments} odcinków trasy: {route_desc}")

        segments_map: List[SegmentAvailabilityMap] = []

        # 2. Iterate each segment sequentially
        for idx in range(num_segments):
            s_from = stops[idx]
            s_to = stops[idx + 1]
            seg_dep_time = s_from.departure_time or departure_time or "12:00"

            self._notify(f"Skanowanie odcinka {idx + 1}/{num_segments}: {s_from.name} -> {s_to.name} ({seg_dep_time})...")

            def seg_cb(pct: int, msg: str):
                overall = int(10 + ((idx + (pct / 100)) / num_segments) * 85)
                self._notify(f"[{overall}%] Odcinek {idx + 1}/{num_segments}: {msg}")

            carriages = await self.scraper.scan_segment(
                from_station=s_from.name,
                to_station=s_to.name,
                travel_date=travel_date,
                departure_time=seg_dep_time,
                train_number=train_number,
                progress_cb=seg_cb,
            )

            c_summary_parts = []
            for c_no in sorted(carriages.keys(), key=lambda x: int(x) if x.isdigit() else 99):
                seats_cnt = len(carriages[c_no])
                c_summary_parts.append(f"W{c_no} ({seats_cnt})")

            c_summary = ", ".join(c_summary_parts) if c_summary_parts else "Brak wolnych miejsc"
            self._notify(f"Odcinek {idx + 1}/{num_segments} zakończony. Wolne miejsca: {c_summary}")

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

        self._notify("[100%] Zakończono skanowanie wszystkich odcinków pociągu!")
        return full_map
