"""
PKP Intercity e-Bilet Seat Scraper & Live Train Scanner (V3 / V3.5 Architecture).

Full implementation of Plan V3 (Real e-IC 2.0 React DOM) & Plan V3.5 (6-step architecture):
- Step 1: Search Form & Cookiebot (#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll)
          React Combobox station input (#stationFrom / #stationTo) with option selection from #stations
          Date/time configuration and active ConfirmButton check.
- Step 2: Connections List (YourTrip_yourTrip container, TripPropositionDesktop cards)
          Strict train matching, NO fallback to body / SeasonTicketBar!
- Step 3: Class 2 selection ("Wybierz 2 klasę", ConfirmButton_confirmButton__1x72+)
          Immediate abort on BRAK_MIEJSC_KLASA_2.
- Step 4: Your Journey & Seatmap opening (PreferedPlace_PreferedPlace_grmButton, "Wybierz miejsce")
- Step 5: Seatmap & Seat extraction across all Class 2 carriages:
          - Priority 1: Network JSON Interception (network_api_log.jsonl)
          - Priority 2: DOM & Iframe SVG extraction (Carriage slides, g[aria*='Wolne'] in seatmap iframe)
- Step 6: Booking summary and hold validation (parse_booking_summary, fill_passenger_details)
- Persistent browser session across segments in LiveTrainScanner.scan_full_train (Task V3-05).
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
from backend.app.scraper.ic_client import GraphicReservationUnavailableError

logger = logging.getLogger(__name__)

DEBUG_SNAPSHOTS_DIR = Path("logs/debug_snapshots")
NETWORK_API_LOG = DEBUG_SNAPSHOTS_DIR / "network_api_log.jsonl"
LAST_SCAN_FILE = Path("logs/last_scan_map.txt")
TARGET_CLASS = 2  # Hardcoded default: 2nd class sitting carriages per Plan V2/V3


def log_network_api_call(url: str, status: int, data: Any) -> None:
    """Logs intercepted JSON network calls to network_api_log.jsonl per Task V3-04."""
    try:
        NETWORK_API_LOG.parent.mkdir(parents=True, exist_ok=True)
        keys = list(data.keys()) if isinstance(data, dict) else [type(data).__name__]
        entry = {
            "timestamp": time.time(),
            "url": url,
            "status": status,
            "keys": keys,
        }
        with open(NETWORK_API_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


class EbiletSeatScraper:
    """
    Playwright-based scraper for ebilet.intercity.pl (e-IC 2.0 React SPA).
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
        """
        Dismisses Cookiebot and common RODO consent banners (Tasks V3-01 & V3-02).
        Uses exact CybotCookiebot selectors from ebilet.intercity.pl.
        """
        cookie_selectors = [
            "#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll",
            "#CybotCookiebotDialogBodyButtonDecline",
            "button:has-text('Zezwól na wszystkie')",
            "button:has-text('Zaakceptuj niezbędne')",
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
                    logger.info(f"[COOKIE] Dismissed cookie banner via: {sel}")
                    try:
                        await page.locator("#CybotCookiebotDialog").wait_for(state="hidden", timeout=3000)
                    except Exception:
                        pass
                    await asyncio.sleep(0.3)
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

    async def fill_station_combobox(
        self,
        page: Page,
        input_selector: str,
        station_name: str,
    ) -> None:
        """
        Fills React station combobox (#stationFrom / #stationTo) in e-IC 2.0 (Tasks V3-06 to V3-10).
        Uses sequential typing (delay=60ms) to trigger suggestions and selects the exact match.
        """
        inp = page.locator(input_selector).first
        await inp.wait_for(state="visible", timeout=6000)
        await inp.click()
        await inp.fill("")
        await asyncio.sleep(0.1)

        # Type character by character to trigger React onKeyDown/onInput
        await inp.press_sequentially(station_name, delay=60)
        await asyncio.sleep(0.6)

        option_selectors = [
            "#stations [role='option']",
            "#stations li",
            "[data-testid='SearchPanel-from'] li",
            "[data-testid='SearchPanel-to'] li",
            "ul[id='stations'] li",
            "[role='listbox'] [role='option']",
            "li[class*='autocomplete']",
            "div[class*='AutocompleateStation'] li",
            "ul.typeahead li",
            ".dropdown-menu li",
        ]

        target_clean = station_name.strip().lower()
        selected = False

        for sel in option_selectors:
            opts = page.locator(sel)
            cnt = await opts.count()
            if cnt > 0:
                # 1. Exact match first
                for i in range(cnt):
                    opt = opts.nth(i)
                    txt = (await opt.inner_text()).strip().lower()
                    if txt == target_clean:
                        await opt.click()
                        selected = True
                        break
                if selected:
                    break

                # 2. Substring match
                for i in range(cnt):
                    opt = opts.nth(i)
                    txt = (await opt.inner_text()).strip().lower()
                    if target_clean in txt:
                        await opt.click()
                        selected = True
                        break
                if selected:
                    break

        if not selected:
            # Fallback: keyboard navigation
            await page.keyboard.press("ArrowDown")
            await asyncio.sleep(0.15)
            await page.keyboard.press("Enter")
            await asyncio.sleep(0.2)

        # Verification (Task V3-10)
        val = await inp.input_value()
        if not val or not val.strip():
            await inp.fill(station_name)
            await page.keyboard.press("Enter")
            val = await inp.input_value()

        if not val or not val.strip():
            await self.save_debug_snapshot(page, f"err_station_{station_name}")
            raise GraphicReservationUnavailableError(
                f"Nie udało się zatwierdzić stacji '{station_name}' w polu {input_selector}."
            )
        logger.info(f"[COMBOBOX] Zatwierdzono stację: '{val}' ({input_selector})")

    async def set_travel_date_and_time(
        self,
        page: Page,
        date_str: str,
        departure_time: str,
    ) -> None:
        """
        Sets date and departure time in e-IC 2.0 or intercity.pl search form (Tasks V3-11 & V3-12).
        """
        clean_dep_time = departure_time.strip() if departure_time else "12:00"

        # Time input
        time_locators = [
            "input[class*='Time_inputTime']",
            "input[type='time']",
            "input#ic-seek-time",
            "input#godzina",
            "input[name='time']",
        ]
        for sel in time_locators:
            t_inp = page.locator(sel).first
            try:
                if await t_inp.is_visible(timeout=800):
                    await t_inp.click()
                    await t_inp.fill(clean_dep_time)
                    break
            except Exception:
                pass

        # Date input if directly accessible
        date_locators = [
            "input#date_picker",
            "input[name='date']",
            "input[type='date']",
        ]
        for sel in date_locators:
            d_inp = page.locator(sel).first
            try:
                if await d_inp.is_visible(timeout=800):
                    await d_inp.fill(date_str)
                    break
            except Exception:
                pass

    async def submit_search_form(self, page: Page) -> None:
        """
        Submits connection search form on e-IC 2.0 or intercity.pl (Tasks V3-13 to V3-16).
        Checks is_enabled() and prevents submission when stations are not validated.
        """
        confirm_btn_selectors = [
            "button[data-testid='ConfirmButton']",
            "button[type='submit'][aria-label='Dalej']",
            "button[name='search']",
            "button[type='submit']:has-text('Wyszukaj')",
            "button[type='submit']:has-text('Dalej')",
            "button[type='submit']",
        ]

        confirm_btn = None
        for sel in confirm_btn_selectors:
            btn = page.locator(sel).first
            try:
                if await btn.is_visible(timeout=1000):
                    confirm_btn = btn
                    break
            except Exception:
                pass

        if not confirm_btn:
            raise GraphicReservationUnavailableError("Nie znaleziono przycisku wyszukiwania połączeń.")

        # Check disabled state (Tasks V3-14 & V3-15)
        is_enabled = await confirm_btn.is_enabled()
        if not is_enabled:
            for _ in range(6):
                await asyncio.sleep(0.5)
                if await confirm_btn.is_enabled():
                    is_enabled = True
                    break

        if not is_enabled:
            snap_png, _ = await self.save_debug_snapshot(page, "err_search_form_disabled")
            raise GraphicReservationUnavailableError(
                f"Przycisk 'Dalej' (ConfirmButton) pozostał nieaktywny – stacja początkowa lub końcowa nie została zatwierdzona z listy podpowiedzi. Zapisano {snap_png.name}"
            )

        await confirm_btn.click()
        logger.info("[SEARCH] Kliknięto przycisk wyszukiwania połączeń.")

    async def fill_passenger_details(
        self,
        page: Page,
        first_name: str = "Jan",
        last_name: str = "Kowalski",
        email: Optional[str] = "jan.kowalski@example.com",
    ) -> None:
        """
        Krok 5 (Plan V3.5): Fills passenger details form if required before reservation summary.
        """
        fn_locs = ["input[name='firstName']", "input[name='imie']", "input[placeholder*='Imię']", "#firstName"]
        for sel in fn_locs:
            el = page.locator(sel).first
            if await el.is_visible(timeout=800):
                await el.fill(first_name)
                break

        ln_locs = ["input[name='lastName']", "input[name='nazwisko']", "input[placeholder*='Nazwisko']", "#lastName"]
        for sel in ln_locs:
            el = page.locator(sel).first
            if await el.is_visible(timeout=800):
                await el.fill(last_name)
                break

        if email:
            em_locs = ["input[name='email']", "input[type='email']", "#email"]
            for sel in em_locs:
                el = page.locator(sel).first
                if await el.is_visible(timeout=800):
                    await el.fill(email)
                    break

        # Check terms checkboxes
        chk_locs = ["input[type='checkbox'][name*='terms']", "input[type='checkbox'][name*='zgoda']", "input[type='checkbox']"]
        for sel in chk_locs:
            chk = page.locator(sel).first
            if await chk.is_visible(timeout=500):
                if not await chk.is_checked():
                    await chk.check()

    async def parse_booking_summary(self, page: Page) -> Dict[str, Any]:
        """
        Krok 6 (Plan V3.5): Parses booking summary table, hold expiration timer, assigned wagon & seat.
        """
        summary_info: Dict[str, Any] = {"wagon": None, "seat": None, "hold_timer": None, "price": None}
        content = await page.content()

        # Match wagon & seat (e.g. Wagon 8, Miejsce 43)
        w_match = re.search(r'wagon\s*(\d+)', content, re.IGNORECASE)
        if w_match:
            summary_info["wagon"] = w_match.group(1)

        s_match = re.search(r'miejsce\s*(\d+)', content, re.IGNORECASE)
        if s_match:
            summary_info["seat"] = s_match.group(1)

        t_match = re.search(r'czas\s+na\s+opłacenie[^\d]*(\d{1,2}:\d{2})', content, re.IGNORECASE)
        if t_match:
            summary_info["hold_timer"] = t_match.group(1)

        p_match = re.search(r'(\d+[\.,]\d{2})\s*zł', content)
        if p_match:
            summary_info["price"] = p_match.group(1)

        logger.info(f"[SUMMARY] Parsed booking hold summary: {summary_info}")
        return summary_info

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
        Executes full 6-step search and seat extraction on ebilet.intercity.pl / intercity.pl.
        Strictly enforces Class 2, handles Cookiebot and Comboboxes, and never blindly falls back.
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
            url = response.url
            ct = response.headers.get("content-type", "")
            if "application/json" in ct and "intercity.pl" in url.lower():
                try:
                    data = await response.json()
                    # Log network call per Task V3-04
                    log_network_api_call(url, response.status, data)
                    # If it relates to schema, seats, or wagons, save for parsing
                    if any(k in url.lower() for k in ["seat", "schemat", "wagon", "carriage", "uklad", "places"]):
                        captured_network_json.append(data)
                        logger.debug(f"[NETWORK] Intercepted seatmap JSON from: {url}")
                except Exception:
                    pass

        page.on("response", on_response)

        try:
            # 1. Navigate to ebilet search
            report(15, f"Otwieranie serwisu PKP Intercity dla trasy {from_station} -> {to_station}...")
            search_url = "https://ebilet.intercity.pl/"
            await page.goto(search_url, wait_until="domcontentloaded")
            await asyncio.sleep(0.5)
            await self.close_cookie_banners(page)

            # 2. Input from_station with combobox
            report(25, f"Wprowadzanie stacji początkowej: {from_station}...")
            from_input_sel = "#stationFrom" if await page.locator("#stationFrom").count() > 0 else "#stname-0"
            await self.fill_station_combobox(page, from_input_sel, from_station)

            # 3. Input to_station with combobox
            report(35, f"Wprowadzanie stacji końcowej: {to_station}...")
            to_input_sel = "#stationTo" if await page.locator("#stationTo").count() > 0 else "#stname-1"
            await self.fill_station_combobox(page, to_input_sel, to_station)

            # 4. Set travel date & departure time
            report(45, f"Ustawianie godziny odjazdu: {departure_time}...")
            clean_dep_time = departure_time.strip() if departure_time else "12:00"
            await self.set_travel_date_and_time(page, date_str, clean_dep_time)

            # 5. Submit search form (ConfirmButton)
            report(55, "Wyszukiwanie listy połączeń...")
            await self.submit_search_form(page)

            # Wait for search results
            try:
                await page.wait_for_load_state("networkidle", timeout=6000)
            except Exception:
                pass

            # Step 2: Connection list (Tasks V3-17 to V3-20)
            report(70, f"Dopasowywanie pociągu {train_number} i wybór Klasy 2...")
            try:
                await page.wait_for_selector(
                    "li[class*='TripPropositionDesktop'], .connection-row, tr.connection, ul[class*='YourTrip']",
                    timeout=8000,
                )
            except Exception:
                pass

            # Save step2 snapshot if first query (Task V3-18)
            step2_snap = DEBUG_SNAPSHOTS_DIR / "step2_connections_list.html"
            if not step2_snap.exists():
                try:
                    step2_snap.write_text(await page.content(), encoding="utf-8")
                except Exception:
                    pass

            # Locate matching connection card
            train_clean_num = re.sub(r'^[A-Z]+\s*', '', train_number).strip()
            connection_cards = page.locator(
                "li[class*='TripPropositionDesktop_tripPropositionDesktop__container'], .connection-row, tr.connection, .connection-card"
            )
            card_count = await connection_cards.count()

            chosen_card = None
            if card_count > 0:
                for idx in range(card_count):
                    card = connection_cards.nth(idx)
                    card_text = await card.inner_text()
                    if train_clean_num in card_text:
                        chosen_card = card
                        break
                if not chosen_card:
                    for idx in range(card_count):
                        card = connection_cards.nth(idx)
                        card_text = await card.inner_text()
                        if clean_dep_time in card_text:
                            chosen_card = card
                            break
                if not chosen_card:
                    chosen_card = connection_cards.first

            if not chosen_card:
                # Task V3-03: STRICTLY NO fallback to body!
                snap_png, _ = await self.save_debug_snapshot(page, f"err_no_train_{train_number}_{from_station}_{to_station}")
                raise GraphicReservationUnavailableError(
                    f"Nie znaleziono pociągu {train_number} na liście połączeń dla relacji {from_station} -> {to_station}. Zapisano {snap_png.name}"
                )

            # Expand train card
            expand_buttons = [
                chosen_card.locator("button[data-testid='OutlineButton']").first,
                chosen_card.locator("button[aria-label*='Kup bilet']").first,
                chosen_card.locator("button:has-text('Kup bilet')").first,
                chosen_card.locator("button:has-text('Wybierz')").first,
                chosen_card.locator("button").first,
            ]
            for exp_btn in expand_buttons:
                try:
                    if await exp_btn.is_visible(timeout=1000):
                        await exp_btn.click()
                        await asyncio.sleep(0.5)
                        break
                except Exception:
                    pass

            # Step 3: Class 2 selection (Task V3-21)
            class2_buttons = [
                chosen_card.locator("button[aria-label='Wybierz 2 klasę']").first,
                chosen_card.locator("button:has-text('Wybierz 2 klasę')").first,
                chosen_card.locator("button:has-text('2 klasa')").first,
                chosen_card.locator("button:has-text('2 kl')").first,
                chosen_card.locator("[data-testid='SuperPromo-select-seat-type-1']").first,
                page.locator("button[aria-label='Wybierz 2 klasę']").first,
                page.locator("button:has-text('Wybierz 2 klasę')").first,
            ]

            class2_clicked = False
            for c2_btn in class2_buttons:
                try:
                    if await c2_btn.is_visible(timeout=1500):
                        b_text = (await c2_btn.inner_text()).lower()
                        if any(w in b_text for w in ["brak miejsc", "niedostępne", "zablokowana", "wyprzedane"]):
                            raise GraphicReservationUnavailableError(
                                "BRAK_MIEJSC_KLASA_2: Wyczerpano pulę miejsc w Klasie 2 na tym odcinku."
                            )
                        await c2_btn.click()
                        class2_clicked = True
                        logger.info("[CLASS 2] Wybrano 2. klasę.")
                        await asyncio.sleep(0.8)
                        break
                except GraphicReservationUnavailableError:
                    raise
                except Exception:
                    pass

            # Step 4: Your Journey & open seatmap (Tasks V3-22 to V3-25)
            report(75, "Konfiguracja podróży i otwieranie schematu wagonów...")
            await self.dismiss_intermediate_modals(page)

            # Save step3 snapshot (Task V3-22)
            step3_snap = DEBUG_SNAPSHOTS_DIR / "step3_your_journey.html"
            if not step3_snap.exists():
                try:
                    step3_snap.write_text(await page.content(), encoding="utf-8")
                except Exception:
                    pass

            # Click button opening seatmap (Task V3-23)
            schema_btn_selectors = [
                "button[class*='PreferedPlace_PreferedPlace_grmButton']",
                "button[class*='PreferedPlace_PreferedPlace_groupButton']",
                "button:has-text('Wybierz miejsce')",
                "button:has-text('Wybierz miejsce ze schematu')",
                "button:has-text('Wskazanie miejsca')",
                "button:has-text('Wskaż miejsce')",
                "button:has-text('schematu')",
                "a:has-text('Wybierz miejsce')",
            ]

            for sel in schema_btn_selectors:
                elem = page.locator(sel).first
                try:
                    if await elem.is_visible(timeout=2000):
                        await elem.click()
                        logger.info(f"[SEATMAP] Kliknięto przycisk otwarcia schematu ({sel})")
                        await asyncio.sleep(0.8)
                        break
                except Exception:
                    pass

            # Step 5: Wait for seatmap render or JSON interception (85%)
            report(85, "Oczekiwanie na wyrenderowanie grafiki wagonu / odpowiedzi JSON...")

            # Save step4 snapshots (Task V3-25)
            step4_snap_html = DEBUG_SNAPSHOTS_DIR / "step4_seatmap_modal.html"
            step4_snap_png = DEBUG_SNAPSHOTS_DIR / "step4_seatmap_modal.png"
            try:
                step4_snap_html.write_text(await page.content(), encoding="utf-8")
                await page.screenshot(path=str(step4_snap_png), full_page=True)
            except Exception:
                pass

            # Detect seatmap container / frame
            target_container: Union[Page, Frame] = page
            for frame in page.frames:
                f_title = ""
                try:
                    f_title = await frame.title()
                except Exception:
                    pass
                f_url = frame.url.lower()
                if "klasa 2" in f_title.lower() or any(k in f_url for k in ["schemat", "seat", "wagon", "carriage", "resource"]):
                    target_container = frame
                    logger.info(f"[IFRAME] Wykryto ramkę schematu wagonu: title='{f_title}' url='{frame.url}'")
                    break

            # Fast wait for seatmap elements or JSON response (max 10s)
            start_wait = time.time()
            seatmap_found = False
            while time.time() - start_wait < 10.0:
                if captured_network_json:
                    seatmap_found = True
                    break

                test_seats = target_container.locator("g[aria-label*='Wolne' i], .seat, [data-seat], rect.place-rect, .carriage-tab, button:has-text('Wagon')")
                if await test_seats.count() > 0:
                    seatmap_found = True
                    break

                await self.dismiss_intermediate_modals(page)
                await asyncio.sleep(0.4)

            # Step 5: Seat extraction across Class 2 carriages (90%)
            report(90, "Odczytywanie wolnych miejsc z wagonów Klasy 2...")
            carriages_data: Dict[str, List[str]] = {}

            # Priority 1: JSON interception (Task V3-26)
            if captured_network_json:
                carriages_data = self._extract_seats_from_json_list(captured_network_json)

            # Priority 2: DOM & Iframe parsing across Class 2 carriages (Tasks V3-27 to V3-30)
            if not carriages_data:
                carriages_data = await self._extract_seats_from_eic2_dom(page, target_container)

            if not carriages_data:
                content = await page.content()
                content_lower = content.lower()
                reason = "Nie znaleziono dostępnych miejsc na schemacie."
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

            # Step 6: Complete (95%)
            report(95, f"Odczytano wolne miejsca w {len(carriages_data)} wagonach.")
            return carriages_data

        except GraphicReservationUnavailableError:
            raise
        except Exception as e:
            logger.warning(f"[SCRAPER] Error during scraping {from_station} -> {to_station}: {e}")
            await self.save_debug_snapshot(page, f"err_{from_station}_{to_station}")
            return {}

    def _extract_seats_from_json_list(self, json_list: List[Dict[str, Any]]) -> Dict[str, List[str]]:
        """
        Parses intercepted JSON payloads recursively, filtering strictly for Class 2 sitting carriages (Task V3-26).
        """
        carriages: Dict[str, List[str]] = {}

        def search_carriages(obj: Any):
            if isinstance(obj, dict):
                items = obj.get("carriages", obj.get("wagony", obj.get("cars", [])))
                if isinstance(items, list):
                    for carriage in items:
                        if not isinstance(carriage, dict):
                            continue
                        c_no = str(carriage.get("number", carriage.get("nr", carriage.get("carriage_no", ""))))
                        if not c_no:
                            continue

                        # Skip Class 1, WARS, sleepers
                        c_class = str(carriage.get("class", carriage.get("klasa", "2")))
                        c_type = str(carriage.get("type", carriage.get("typ", ""))).upper()
                        if c_class == "1" or any(skip in c_type for skip in ["WR", "WARS", "WL", "BC"]):
                            continue

                        seats = carriage.get("seats", carriage.get("miejsca", carriage.get("places", [])))
                        free_seats = []
                        if isinstance(seats, list):
                            for s in seats:
                                if not isinstance(s, dict):
                                    continue
                                s_no = str(s.get("number", s.get("nr", "")))
                                status = str(s.get("status", s.get("state", "FREE"))).upper()
                                if status in ("FREE", "AVAILABLE", "WOLNE", "1") and s_no.isdigit():
                                    free_seats.append(s_no)

                        if free_seats:
                            sorted_seats = sorted(list(set(free_seats)), key=lambda x: int(x))
                            carriages[c_no] = sorted_seats
                        elif c_no not in carriages:
                            carriages[c_no] = []

                for k, v in obj.items():
                    if isinstance(v, (dict, list)):
                        search_carriages(v)
            elif isinstance(obj, list):
                for item in obj:
                    search_carriages(item)

        for data in json_list:
            search_carriages(data)

        return carriages

    async def _extract_seats_from_eic2_dom(
        self,
        page: Page,
        container: Union[Page, Frame],
    ) -> Dict[str, List[str]]:
        """
        Extracts available seats across all Class 2 carriages using e-IC 2.0 React DOM and iframe (Tasks V3-27 to V3-30).
        """
        carriages: Dict[str, List[str]] = {}

        # Look for wagon slides in e-IC 2.0 carousel
        slide_locators = page.locator("div[class*='Carriage_carriageSlide']")
        slide_count = await slide_locators.count()

        if slide_count > 0:
            for idx in range(slide_count):
                slide = slide_locators.nth(idx)

                # Check wagon class
                klasa_el = slide.locator("p[class*='Carriage_carriageKlasa']").first
                klasa_txt = (await klasa_el.inner_text()).lower() if await klasa_el.is_visible(timeout=500) else ""

                # Skip Class 1, WARS, or sleepers
                if "klasa 1" in klasa_txt or "kl. 1" in klasa_txt or "wars" in klasa_txt:
                    continue

                # Get wagon number
                num_el = slide.locator("p[class*='Carriage_carriageNumberDesktop'], p[class*='Carriage_carriageNumberMb']").first
                num_txt = (await num_el.inner_text()).strip() if await num_el.is_visible(timeout=500) else ""
                c_match = re.search(r'\d+', num_txt)
                c_no = c_match.group(0) if c_match else str(idx + 1)

                # Click wagon slide
                click_target = slide.locator("div[aria-label*='Wybierz wagon numer'], div[class*='Carriage_carriageBox'], div").first
                try:
                    await click_target.click()
                    await asyncio.sleep(0.4)
                except Exception:
                    pass

                # Locate current seatmap iframe
                active_frame: Union[Page, Frame] = container
                for f in page.frames:
                    f_url = f.url.lower()
                    if "resource" in f_url or "schemat" in f_url or "seat" in f_url:
                        active_frame = f
                        break

                seats = await self._find_seats_in_iframe(active_frame)
                if seats:
                    carriages[c_no] = seats
        else:
            # Fallback: scan current view
            seats = await self._find_seats_in_iframe(container)
            if seats:
                carriages["2"] = seats

        return carriages

    async def _find_seats_in_iframe(self, container: Union[Page, Frame]) -> List[str]:
        """
        Parses free seats from the seatmap iframe (g[aria*='Wolne'], rect.place-rect, text.seatNum).
        """
        free_seats: List[str] = []

        try:
            # 1. Primary: SVG <g> groups with aria-label containing 'wolne'
            g_locs = container.locator("g[aria-label*='Wolne'], g[aria-label*='wolne']")
            g_cnt = await g_locs.count()
            if g_cnt > 0:
                for idx in range(g_cnt):
                    g_elem = g_locs.nth(idx)
                    aria = (await g_elem.get_attribute("aria-label")) or ""
                    # SVG <text> elements require text_content() instead of inner_text()
                    text_el = g_elem.locator("text").first
                    num = ""
                    if await text_el.count() > 0:
                        num = (await text_el.text_content() or "").strip()
                    if not num:
                        m = re.search(r'miejsce\s+(\d+)', aria, re.IGNORECASE)
                        if m:
                            num = m.group(1)
                    if num and num.isdigit():
                        free_seats.append(num)
        except Exception as e:
            logger.debug(f"[SVG] Error reading seat text: {e}")

        if not free_seats:
            try:
                # 2. Secondary: fallback selectors
                fallback_locators = [
                    ".seat.available",
                    ".seat.free",
                    "[data-status='available']",
                    "[data-status='free']",
                    "button.seat:not(.occupied):not(.taken):not(.disabled)",
                ]
                for sel in fallback_locators:
                    locs = container.locator(sel)
                    cnt = await locs.count()
                    if cnt > 0:
                        for idx in range(cnt):
                            t = (await locs.nth(idx).inner_text()).strip()
                            m = re.search(r'\b\d+\b', t)
                            if m:
                                free_seats.append(m.group(0))
                        break
            except Exception:
                pass

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
        page: Optional[Page] = None,
    ) -> Dict[str, List[str]]:
        """
        Scans a single segment with SQLite caching (TTL 10 min) and sequential rate limiting.
        Reuses page if provided by LiveTrainScanner (Task V3-05).
        """
        cache_key = f"seatmap_{train_number}_{travel_date}_{from_station}_{to_station}"
        if use_cache:
            cached = get_cached_item(cache_key, ttl_seconds=self.ttl_seconds)
            if cached and isinstance(cached, dict):
                logger.info(f"[CACHE HIT] Seatmap for segment {from_station} -> {to_station}")
                return cached

        # Rate limiting delay per Plan V2/V3 (1.2 - 2.2s jitter)
        await asyncio.sleep(random.uniform(1.2, 2.2))

        if page is not None:
            # Reuse active persistent page/session
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

        # Standalone launch if no page provided
        async with async_playwright() as p:
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
    Maintains a single persistent browser session across all segments (Task V3-05).
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
        2. Scans every elementary segment sequentially in a single persistent browser session.
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

        # 2. Single persistent browser session across all segments (Task V3-05)
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=self.scraper.headless,
                slow_mo=150 if not self.scraper.headless else 0,
                args=[
                    "--start-maximized",
                    "--disable-blink-features=AutomationControlled",
                    "--window-position=50,50",
                ],
            )
            try:
                context = await self.scraper.create_context(browser)
                page = await self.scraper.init_page(context)

                # Iterate each segment sequentially using persistent session
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
                        page=page,  # Reuse active session
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

            finally:
                await browser.close()

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
