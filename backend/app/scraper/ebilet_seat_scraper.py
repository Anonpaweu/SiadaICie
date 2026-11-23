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
from datetime import datetime, timedelta
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
from backend.app.scraper.dom_scripts import CAROUSEL_METADATA_JS, SVG_SEATS_EXTRACTOR_JS
from backend.app.scraper.grm_parser import (
    PRM_WHEELCHAIR_MARKERS,
    SOLD_OUT_MARKERS,
    TARGET_CLASS,
    build_eic_search_url,
    is_prm_wheelchair_label,
    is_sold_out_reason,
    parse_grm_wagon_svg,
    sanitize_filename,
    select_grm_target_wagons,
    shift_query_time,
)
from backend.app.scraper.schedule_scraper import resolve_real_route, get_station_info
from backend.app.scraper.route_resolver import filter_and_merge_stops
from backend.app.scraper.ic_client import GraphicReservationUnavailableError

logger = logging.getLogger(__name__)

DEBUG_SNAPSHOTS_DIR = Path("logs/debug_snapshots")
NETWORK_API_LOG = DEBUG_SNAPSHOTS_DIR / "network_api_log.jsonl"
LAST_SCAN_FILE = Path("logs/last_scan_map.txt")

BLOCKED_TRACKER_DOMAINS = (
    "google-analytics.com",
    "googletagmanager.com",
    "doubleclick.net",
    "facebook.net",
    "facebook.com/tr",
    "hotjar.com",
    "clarity.ms",
    "newrelic.com",
    "sentry.io",
)


def is_debug_scraper_enabled() -> bool:
    """Returns True only when DEBUG_SCRAPER env flag enables happy-path HTML/PNG dumps and JSONL logging."""
    return os.getenv("DEBUG_SCRAPER", "").strip().lower() in ("1", "true", "yes")


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


def force_unhide_chrome_windows() -> None:
    """
    On Windows, child processes spawned by background tasks inherit
    STARTUPINFO.wShowWindow = SW_HIDE, causing GUI processes (Chrome) to be hidden
    from the desktop. This helper enumerates top-level windows and forces Chrome
    windows to become visible and maximized (SW_MAXIMIZE = 3).
    """
    if os.name != 'nt':
        return
    try:
        import ctypes
        user32 = ctypes.windll.user32

        def enum_cb(hwnd, extra):
            try:
                class_buf = ctypes.create_unicode_buffer(256)
                user32.GetClassNameW(hwnd, class_buf, 256)
                c_name = class_buf.value

                length = user32.GetWindowTextLengthW(hwnd)
                title = ""
                if length > 0:
                    buff = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buff, length + 1)
                    title = buff.value

                if "Chrome_WidgetWin_1" in c_name or any(k in title.lower() for k in ["chrome", "intercity", "e-ic", "bilet"]):
                    user32.ShowWindow(hwnd, 3)  # SW_MAXIMIZE
                    user32.SetForegroundWindow(hwnd)
            except Exception:
                pass
            return True

        EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)
        user32.EnumWindows(EnumWindowsProc(enum_cb), 0)
    except Exception:
        pass


async def get_browser_instance(p, headless: bool = True) -> Tuple[Any, bool]:
    """
    Connects to an existing Chrome browser with remote debugging (CDP, e.g. port 9223 from Kocur.py)
    if available, otherwise launches Google Chrome / Chromium with stealth options.
    Returns (browser, is_cdp).
    """
    cdp_port = os.getenv("CHROME_DEBUG_PORT", "9223").strip()
    cdp_url = os.getenv("CHROME_CDP_URL", f"http://127.0.0.1:{cdp_port}").strip()

    # Try connecting to existing Chrome process via CDP first (per Kocur.py pattern)
    try:
        browser = await p.chromium.connect_over_cdp(cdp_url, timeout=2000)
        logger.info(f"[BROWSER] Pomyślnie połączono z działającym Google Chrome przez CDP ({cdp_url})")
        return browser, True
    except Exception:
        pass

    launch_args = [
        "--start-maximized",
        "--disable-blink-features=AutomationControlled",
        "--no-sandbox",
        "--disable-infobars",
    ]
    launch_kwargs = {
        "headless": headless,
        "slow_mo": 100 if not headless else 0,
        "args": launch_args,
    }
    try:
        browser = await p.chromium.launch(channel="chrome", **launch_kwargs)
        logger.info(f"[BROWSER] Uruchomiono Google Chrome (headless={headless})")
    except Exception as e:
        logger.warning(f"[BROWSER] Google Chrome niedostępny ({e}), uruchamiam domyślne Chromium")
        browser = await p.chromium.launch(**launch_kwargs)
    return browser, False


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

    async def create_context(self, browser, storage_state: Optional[Dict[str, Any]] = None) -> BrowserContext:
        """Configures browser context with realistic fingerprint, optional warmed storage_state, and evasion."""
        ctx_kwargs: Dict[str, Any] = {
            "no_viewport": True if not self.headless else False,
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            "locale": "pl-PL",
            "timezone_id": "Europe/Warsaw",
        }
        if isinstance(storage_state, dict) and storage_state:
            ctx_kwargs["storage_state"] = storage_state
        context = await browser.new_context(**ctx_kwargs)
        return context

    async def init_page(self, context: BrowserContext) -> Page:
        """Initializes page with stealth scripts, heavy-asset filtering, and fast timeout."""
        page = await context.new_page()
        page.set_default_timeout(self.timeout_ms)
        await page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        async def _route_filter(route) -> None:
            try:
                req = route.request
                url_lower = (req.url or "").lower()
                if any(dom in url_lower for dom in BLOCKED_TRACKER_DOMAINS):
                    await route.abort()
                    return
                res_type = (req.resource_type or "").lower()
                if res_type in ("media", "font"):
                    await route.abort()
                    return
                if (
                    res_type == "image"
                    and "/grm/" not in url_lower
                    and ".svg" not in url_lower
                    and "api-gateway.intercity.pl" not in url_lower
                ):
                    await route.abort()
                    return
                await route.continue_()
            except Exception:
                pass

        try:
            await page.route("**/*", _route_filter)
        except Exception as e:
            logger.debug(f"[ROUTE] Could not attach resource blocker: {e}")

        if not self.headless:
            force_unhide_chrome_windows()
        return page

    async def get_browser_instance(self, p, headless: Optional[bool] = None) -> Tuple[Any, bool]:
        """Convenience method delegating to get_browser_instance."""
        h = self.headless if headless is None else headless
        return await get_browser_instance(p, headless=h)

    async def safe_click(self, locator, timeout: int = 2500) -> bool:
        """
        Resilient click helper incorporating lessons from Kocur.py.
        1. Scrolls element into view.
        2. Tries Playwright click.
        3. If intercepted, covered by overlay, or timed out, falls back to native JS click: evaluate('el => el.click()').
        """
        try:
            await locator.scroll_into_view_if_needed(timeout=timeout)
        except Exception:
            pass

        try:
            await locator.click(timeout=timeout)
            return True
        except Exception as e:
            logger.debug(f"[SAFE_CLICK] Standard click failed ({e}), attempting JS click fallback...")
            try:
                await locator.evaluate("el => el.click()")
                return True
            except Exception as e_js:
                logger.debug(f"[SAFE_CLICK] JS click fallback also failed: {e_js}")
                return False

    async def check_and_handle_error_radar(
        self,
        page: Page,
        context_label: str = "",
        allow_reload: bool = True,
    ) -> bool:
        """
        Error radar inspired by Kocur.py.
        Detects PKP server error states ('Ups...', 'poszło nie tak', 'Wystąpił nieoczekiwany błąd').
        When allow_reload=True (Step 1/2), waits with jitter (2.5 - 4.0s), reloads the page, and dismisses banners.
        When allow_reload=False (Step 4/5 seatmap modal), raises GraphicReservationUnavailableError instead of
        destroying the open seatmap modal with a page reload.
        Returns True if an error was detected and handled via reload.
        """
        error_patterns = [
            "text=/\\bUps\\b/i",
            "text=/poszło nie tak/i",
            "text=/nieoczekiwany błąd/i",
            "text=/Wystąpił błąd/i",
            "text=/Spróbuj ponownie/i",
            "text=/Sesja wygasła/i",
            "div[class*='SeatsWbnet_seatsError']",
        ]
        for pattern in error_patterns:
            try:
                loc = page.locator(pattern).first
                if await loc.is_visible(timeout=500):
                    err_text = ""
                    try:
                        err_text = (await loc.text_content() or "").strip()[:80]
                    except Exception:
                        pass
                    if not allow_reload:
                        logger.warning(
                            f"[RADAR] Wykryto błąd serwera PKP ('{err_text or pattern}') "
                            f"w kroku '{context_label}' (allow_reload=False). Przerywam odczyt do czystej powtórki."
                        )
                        raise GraphicReservationUnavailableError(
                            f"SCRAPE_ERROR: Błąd serwera PKP ('{err_text or pattern}') w kroku '{context_label}'."
                        )
                    wait_time = random.uniform(2.5, 4.0)
                    logger.warning(
                        f"[RADAR] Wykryto błąd serwera PKP ('{err_text or pattern}') "
                        f"w kroku '{context_label}'. Czekam {wait_time:.1f}s i odświeżam stronę..."
                    )
                    await asyncio.sleep(wait_time)
                    await page.reload(wait_until="domcontentloaded", timeout=20000)
                    await asyncio.sleep(random.uniform(1.5, 2.5))
                    await self.close_cookie_banners(page)
                    await self.dismiss_intermediate_modals(page)
                    return True
            except GraphicReservationUnavailableError:
                raise
            except Exception:
                pass
        return False

    async def close_cookie_banners(self, page: Page) -> None:
        """
        Dismisses Cookiebot and common RODO consent banners (Tasks V3-01 & V3-02).
        Uses exact CybotCookiebot selectors from ebilet.intercity.pl with JS click fallback.
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
                    clicked = await self.safe_click(elem, timeout=1000)
                    if clicked:
                        logger.info(f"[COOKIE] Dismissed cookie banner via: {sel}")
                        try:
                            await page.locator("#CybotCookiebotDialog").wait_for(state="hidden", timeout=3000)
                        except Exception:
                            pass
                        await asyncio.sleep(0.08)
                        break
            except Exception:
                pass

    async def dismiss_intermediate_modals(self, page: Page) -> None:
        """
        Dismisses intermediate warning modals (mandatory reservation, replacement bus ZKA,
        no guaranteed seat, terms agreement) with JS click fallback.
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
                    if await self.safe_click(elem, timeout=800):
                        await asyncio.sleep(0.08)
                        logger.info(f"[MODAL] Dismissed intermediate modal via: {sel}")
            except Exception:
                pass

    async def save_debug_snapshot(self, page: Page, name_prefix: str) -> Tuple[Path, Path]:
        """Saves screenshot and HTML for diagnostic inspection, including error_85_percent.png."""
        try:
            safe_prefix = sanitize_filename(name_prefix)
            snap_png = DEBUG_SNAPSHOTS_DIR / f"{safe_prefix}.png"
            snap_html = DEBUG_SNAPSHOTS_DIR / f"{safe_prefix}.html"
            try:
                await page.screenshot(path=str(snap_png), full_page=True, timeout=8000)
            except Exception:
                await page.screenshot(path=str(snap_png), timeout=5000)
            content = await page.content()
            snap_html.write_text(content, encoding="utf-8")

            # Always update canonical error_85_percent.png and debug_85.html for UI diagnostics
            err_png = DEBUG_SNAPSHOTS_DIR / "error_85_percent.png"
            err_html = DEBUG_SNAPSHOTS_DIR / "debug_85.html"
            try:
                import shutil
                shutil.copyfile(snap_png, err_png)
                err_html.write_text(content, encoding="utf-8")
            except Exception:
                pass

            logger.info(f"[SNAPSHOT] Saved failure snapshot: {snap_png}")
            return snap_png, snap_html
        except Exception as e:
            logger.warning(f"Could not save debug snapshot: {e}")
            return DEBUG_SNAPSHOTS_DIR / "error.png", DEBUG_SNAPSHOTS_DIR / "error.html"

    @staticmethod
    def _shift_query_time(departure_time: str, offset_minutes: int = -10) -> str:
        """Shifts HH:MM departure time by offset_minutes (default -10 min), clamped at 00:00."""
        return shift_query_time(departure_time, offset_minutes=offset_minutes)

    @staticmethod
    def _select_grm_target_wagons(
        sklad_data: Dict[str, Any],
        ticket_class: int = TARGET_CLASS,
    ) -> Tuple[List[Tuple[str, str]], bool]:
        """
        Extracts authoritative available (wagon_no, schema_id) pairs for ticket_class from
        /grm/sklad/wbnet/ JSON, skipping wagonyNiedostepne, wrong class, and WARS/sleepers.
        """
        return select_grm_target_wagons(sklad_data, ticket_class=ticket_class)

    @staticmethod
    def parse_grm_wagon_svg(svg_xml: str, ticket_class: int = TARGET_CLASS) -> List[str]:
        """Instance/static wrapper for parse_grm_wagon_svg."""
        return parse_grm_wagon_svg(svg_xml, ticket_class=ticket_class)

    def build_direct_search_url(
        self,
        from_station: str,
        to_station: str,
        date_str: str,
        departure_time: str,
        direct_only: bool = True,
        from_eva: str = "",
        to_eva: str = "",
    ) -> str:
        """
        Buduje bezpośredni adres URL wyszukiwania połączeń w e-IC 2.0 (Krok 2)
        na podstawie identyfikatorów EVA stacji, omijając formularz wyszukiwania.
        """
        resolved_from_eva = (from_eva or "").strip()
        resolved_to_eva = (to_eva or "").strip()

        if not resolved_from_eva:
            _, resolved_from_eva = get_station_info(from_station)
        if not resolved_to_eva:
            _, resolved_to_eva = get_station_info(to_station)

        if not resolved_from_eva or not resolved_to_eva:
            logger.warning(
                f"[DIRECT URL] Brak kodu EVA dla stacji: "
                f"from='{from_station}' (eva='{resolved_from_eva}'), to='{to_station}' (eva='{resolved_to_eva}')."
            )

        return build_eic_search_url(
            from_eva=resolved_from_eva,
            to_eva=resolved_to_eva,
            date_str=date_str,
            departure_time=departure_time,
            direct_only=direct_only,
        )

    @staticmethod
    def _card_matches_train(card_id: str, card_text: str, train_clean_num: str) -> bool:
        """
        Strict train card matcher: checks outer card id (e.g. '..._1644_76') or exact
        word-boundary match on card text. Never matches partial substrings like '164' in '1644'.
        """
        if not train_clean_num:
            return False
        if card_id:
            if f"_{train_clean_num}_" in card_id or re.search(
                rf"(?:_|\|){re.escape(train_clean_num)}(?:_|\||$)", card_id
            ):
                return True
        if card_text:
            if re.search(rf"\b{re.escape(train_clean_num)}\b", card_text):
                return True
        return False

    async def _find_matching_train_card(self, page: Page, train_clean_num: str):
        """
        Finds the outer connection card strictly matching train_clean_num.
        Targets ONLY outer connection cards with [id] (or fallback connection classes),
        matching first by card id, then by strict \\b{train_clean_num}\\b regex on card text.
        Returns the matching Locator or None (NEVER falls back to first card).
        """
        if not train_clean_num:
            return None

        outer_selector = (
            "li[class*='TripPropositionDesktop_tripPropositionDesktop__container'][id], "
            ".connection-card, .connection-row, tr.connection"
        )
        connection_cards = page.locator(outer_selector)
        card_count = await connection_cards.count()
        if card_count == 0:
            return None

        # Pass 1: authoritative match on outer card `id` attribute
        for idx in range(card_count):
            card = connection_cards.nth(idx)
            try:
                card_id = (await card.get_attribute("id")) or ""
                if card_id and self._card_matches_train(card_id, "", train_clean_num):
                    logger.info(f"[TRAIN MATCH] Dopasowano pociąg po ID karty '{card_id}' (kafelek #{idx+1})")
                    return card
            except Exception:
                continue

        # Pass 2: strict word-boundary match on card inner text
        for idx in range(card_count):
            card = connection_cards.nth(idx)
            try:
                card_text = (await card.inner_text()) or ""
                if self._card_matches_train("", card_text, train_clean_num):
                    logger.info(f"[TRAIN MATCH] Dopasowano pociąg po \\b{train_clean_num}\\b w tekście kafelka #{idx+1}")
                    return card
            except Exception:
                continue

        return None

    @staticmethod
    def _body_indicates_zero_connections(body_low: str) -> bool:
        """
        Detects true 'no connections found' pages while ignoring e-IC 2.0 pagination footers
        such as 'Brak połączeń w dniu 23.11.2026 po godzinie 19:22.' shown below valid train cards.
        """
        if not body_low:
            return False
        if "nie znaleziono połączeń" in body_low:
            return True
        if "brak połączeń" in body_low:
            if "po godzinie" in body_low or "przed godziną" in body_low or "przed godzina" in body_low:
                return False
            return True
        return False

    async def _fetch_grm_svg_in_session(
        self,
        page: Page,
        url: str,
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> Optional[str]:
        """Fetches a /grm/wagon/svg/wbnet/ XML URL using the authenticated browser page session."""
        safe_headers: Dict[str, str] = {}
        if extra_headers:
            for k, v in extra_headers.items():
                kl = k.lower()
                if kl in ("authorization", "accept", "accept-language") or kl.startswith("x-"):
                    safe_headers[k] = v

        try:
            res = await asyncio.wait_for(
                page.evaluate(
                    """async ([targetUrl, hdrs]) => {
                        const ctrl = new AbortController();
                        const timer = setTimeout(() => ctrl.abort(), 6500);
                        try {
                            const r = await fetch(targetUrl, {
                                method: 'GET',
                                credentials: 'include',
                                headers: hdrs || {},
                                signal: ctrl.signal,
                            });
                            const txt = await r.text();
                            return { ok: r.ok, status: r.status, text: txt };
                        } finally {
                            clearTimeout(timer);
                        }
                    }""",
                    [url, safe_headers],
                ),
                timeout=7.5,
            )
            if isinstance(res, dict) and res.get("ok") and res.get("text"):
                return str(res["text"])
            logger.debug(f"[GRM API] in-page fetch returned status={res.get('status') if isinstance(res, dict) else res} for {url}")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug(f"[GRM API] in-page fetch failed for {url}: {e}")

        # Secondary fallback: Playwright APIRequestContext sharing browser context cookies
        try:
            async def _ctx_get() -> Optional[str]:
                resp = await page.context.request.get(url, headers=safe_headers, timeout=8000)
                if resp.ok:
                    return await resp.text()
                return None

            return await asyncio.wait_for(_ctx_get(), timeout=9.0)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug(f"[GRM API] context.request.get failed for {url}: {e}")

        return None

    async def _extract_seats_via_grm_api(
        self,
        page: Page,
        sklad_data: Dict[str, Any],
        wagon_svg_template: Dict[str, Any],
        intercepted_svg_by_wagon: Dict[str, str],
        ticket_class: int = TARGET_CLASS,
    ) -> Optional[Dict[str, List[str]]]:
        """
        Authoritative seat extraction using intercepted /grm/sklad/wbnet/ JSON and
        concurrently fetched /grm/wagon/svg/wbnet/ XML payloads for all target carriages.
        Returns:
          - Dict[wagon_no, List[seat_no]] if all target wagons were authoritatively fetched/parsed
          - None if template is unavailable or fetching failed (caller should fall back to DOM)
        Raises GraphicReservationUnavailableError with BRAK_MIEJSC_KLASA_* if authoritatively sold out.
        """
        targets, has_class_key = self._select_grm_target_wagons(sklad_data, ticket_class=ticket_class)

        if not targets:
            if has_class_key:
                raise GraphicReservationUnavailableError(
                    f"BRAK_MIEJSC_KLASA_{ticket_class}: Brak dostępnych wagonów Klasy {ticket_class} w składzie."
                )
            return None

        prefix = wagon_svg_template.get("prefix", "")
        suffix = wagon_svg_template.get("suffix", "")
        headers = wagon_svg_template.get("headers") or {}

        if not prefix and not all(w_no in intercepted_svg_by_wagon for w_no, _ in targets):
            return None

        missing = [(w_no, schema_id) for w_no, schema_id in targets if not intercepted_svg_by_wagon.get(w_no)]
        if missing and prefix:
            wagon_sem = asyncio.Semaphore(4)

            async def _fetch_one(w_no: str, schema_id: str) -> Tuple[str, Optional[str]]:
                svg_url = f"{prefix}/{w_no}/{schema_id}{suffix}"
                async with wagon_sem:
                    xml = await self._fetch_grm_svg_in_session(page, svg_url, extra_headers=headers)
                return w_no, xml

            try:
                fetched_results = await asyncio.wait_for(
                    asyncio.gather(*[_fetch_one(w_no, schema_id) for w_no, schema_id in missing]),
                    timeout=18.0,
                )
            except asyncio.TimeoutError:
                logger.warning("[GRM API] Timeout podczas równoległego pobierania SVG wagonów — fallback DOM.")
                return None

            for w_no, svg_xml in fetched_results:
                if svg_xml:
                    intercepted_svg_by_wagon[w_no] = svg_xml

        carriages: Dict[str, List[str]] = {}
        fetched_count = 0
        for w_no, schema_id in targets:
            svg_xml = intercepted_svg_by_wagon.get(w_no)
            if svg_xml and "<svg" in svg_xml.lower():
                seats = self.parse_grm_wagon_svg(svg_xml, ticket_class=ticket_class)
                carriages[w_no] = seats
                fetched_count += 1
                logger.info(f"[GRM API] Wagon {w_no} (schema={schema_id}, Klasa {ticket_class}): {len(seats)} wolnych miejsc")
            else:
                logger.warning(f"[GRM API] Nie udało się pobrać SVG dla wagonu {w_no} (schema={schema_id})")

        if fetched_count < len(targets):
            logger.warning(
                f"[GRM API] Pobrano tylko {fetched_count}/{len(targets)} wagonów przez API GRM — przełączam na fallback DOM."
            )
            return None

        if not any(len(s) > 0 for s in carriages.values()):
            raise GraphicReservationUnavailableError(
                f"BRAK_MIEJSC_KLASA_{ticket_class}: Wszystkie dostępne wagony Klasy {ticket_class} "
                f"({', '.join(carriages.keys())}) mają 0 wolnych miejsc."
            )

        return carriages

    async def _close_seatmap_modal(self, page: Page) -> None:
        """Best-effort dismissal of the open seatmap modal."""
        try:
            close_btn = page.locator(
                "button[class*='css-nk1pxf']:has-text('Zamknij'), button[aria-label='Zamknij'], button:has-text('Zamknij')"
            ).first
            if await close_btn.is_visible(timeout=400):
                await self.safe_click(close_btn, timeout=600)
            else:
                await page.keyboard.press("Escape")
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
        has_warmed_session: bool = False,
        from_eva: str = "",
        to_eva: str = "",
    ) -> Dict[str, List[str]]:
        """
        Realizuje odczyt miejsc ze schematu wagonów na trasie from_station -> to_station.
        Otwiera bezpośrednio listę połączeń (Krok 2), ściśle dopasowuje pociąg (z paginacją
        Później/Wcześniej, bez fallbacku na inny pociąg), wybiera klasę (1 lub 2), otwiera
        schemat i odczytuje wolne miejsca autorytatywnie przez GRM API (/grm/sklad/wbnet/ +
        /grm/wagon/svg/wbnet/), z poprawionym fallbackiem karuzeli DOM.
        """
        clean_dep_time = departure_time.strip() if departure_time else "12:00"

        def report(pct: int, msg: str):
            logger.info(f"[{pct}%] {msg}")
            if progress_cb:
                try:
                    progress_cb(pct, msg)
                except Exception:
                    pass

        sklad_info: Dict[str, Any] = {"data": None, "url": ""}
        wagon_svg_template: Dict[str, Any] = {"url": "", "prefix": "", "suffix": "", "headers": {}}
        intercepted_svg_by_wagon: Dict[str, str] = {}
        wagon_svg_events: Dict[str, asyncio.Event] = {}

        async def on_response(response: Response):
            url = response.url
            url_lower = url.lower()
            if "intercity.pl" not in url_lower:
                return
            try:
                if "/grm/sklad/wbnet/" in url_lower and response.status == 200:
                    data = await response.json()
                    if is_debug_scraper_enabled():
                        log_network_api_call(url, response.status, data)
                    if isinstance(data, dict) and any(
                        k in data for k in ("klasa1", "klasa2", "wagonySchemat", "wagony")
                    ):
                        sklad_info["data"] = data
                        sklad_info["url"] = url
                        logger.info(
                            f"[GRM API] Przechwycono /grm/sklad/wbnet/: "
                            f"klasa1={data.get('klasa1')}, klasa2={data.get('klasa2')}, "
                            f"niedostepne={data.get('wagonyNiedostepne')}"
                        )
                    return

                if "/grm/wagon/svg/wbnet/" in url_lower and response.status == 200:
                    m = re.match(
                        r"^(https?://[^/]+/grm/wagon/svg/wbnet/[^/]+/[^/]+)/([^/]+)/([^/]+)(/.*)$",
                        url,
                    )
                    if m:
                        wagon_svg_template["prefix"] = m.group(1)
                        wagon_svg_template["suffix"] = m.group(4)
                        wagon_svg_template["url"] = url
                        try:
                            wagon_svg_template["headers"] = dict(response.request.headers or {})
                        except Exception:
                            pass
                        w_no = m.group(2)
                        svg_text = await response.text()
                        if svg_text and "<svg" in svg_text.lower():
                            intercepted_svg_by_wagon[w_no] = svg_text
                            ev = wagon_svg_events.get(w_no)
                            if ev:
                                ev.set()
                            logger.debug(f"[GRM API] Przechwycono SVG wagonu {w_no} z {url}")
                    return

                if is_debug_scraper_enabled():
                    ct = response.headers.get("content-type", "")
                    if "application/json" in ct:
                        data = await response.json()
                        log_network_api_call(url, response.status, data)
            except Exception:
                pass

        page.on("response", on_response)

        try:
            # Krok 1 & 2: Otwarcie bezpośredniego adresu wyszukiwania połączeń
            report(20, f"Otwieranie listy połączeń dla trasy {from_station} -> {to_station}...")
            direct_url = self.build_direct_search_url(
                from_station,
                to_station,
                date_str,
                departure_time,
                from_eva=from_eva,
                to_eva=to_eva,
            )
            if "swyj=&" in direct_url or "sprzy=&" in direct_url:
                raise GraphicReservationUnavailableError(
                    f"SCRAPE_ERROR: Brak kodu EVA dla trasy {from_station} -> {to_station} (url={direct_url})."
                )

            logger.info(f"[DIRECT URL] Nawigacja bezpośrednia do listy połączeń: {direct_url}")

            # Jeśli kontekst nie był jeszcze rozgrzany (brak cookies sesyjnych), odwiedź stronę główną
            if not has_warmed_session and "ebilet.intercity.pl" not in page.url:
                try:
                    await page.goto("https://ebilet.intercity.pl/", wait_until="domcontentloaded", timeout=15000)
                    await self.close_cookie_banners(page)
                    await asyncio.sleep(0.1)
                except Exception as e:
                    logger.warning(f"[WARMUP] Ostrzeżenie podczas otwierania strony głównej: {e}")

            await page.goto(direct_url, wait_until="domcontentloaded", timeout=25000)
            await asyncio.sleep(0.1)
            await self.close_cookie_banners(page)
            await self.check_and_handle_error_radar(page, "otwarcie_wyszukiwania", allow_reload=True)

            if not self.headless:
                force_unhide_chrome_windows()

            # Oczekiwanie na kafelki pociągów na liście połączeń (Krok 2)
            # Czekamy WYŁĄCZNIE na loader listy połączeń (nie na per-card loadery cen!)
            report(35, f"Oczekiwanie na listę połączeń dla relacji {from_station} -> {to_station}...")
            start_wait = time.time()
            reloaded = False
            cards_found = False

            outer_cards_selector = (
                "li[class*='TripPropositionDesktop_tripPropositionDesktop__container'][id], "
                ".connection-card, .connection-row, tr.connection"
            )
            page_loader_selector = "[class*='trainLoaderWithText'], [class*='TrainLoaderWithText']"

            while time.time() - start_wait < 35.0:
                if await self.check_and_handle_error_radar(page, "lista_polaczen", allow_reload=True):
                    continue

                loader_count = await page.locator(page_loader_selector).count()
                cards = page.locator(outer_cards_selector)
                card_count = await cards.count()

                if card_count > 0 and loader_count == 0:
                    cards_found = True
                    break

                # Szybkie wykrycie błędu kodu stacji (kod: 303) lub braku połączeń bez czekania 35s
                if loader_count == 0 and time.time() - start_wait > 4.0 and card_count == 0:
                    try:
                        # Re-check card_count just before evaluating body text in case React rendered cards
                        if await cards.count() > 0:
                            cards_found = True
                            break
                        body_low = (await page.locator("body").inner_text()).lower()
                        if "kod stacji spoza zakresu" in body_low:
                            snap_png, _ = await self.save_debug_snapshot(page, f"err_kod303_{train_number}_{from_station}_{to_station}")
                            raise GraphicReservationUnavailableError(
                                f"SCRAPE_ERROR: Kod stacji spoza zakresu (kod 303) dla {from_station} -> {to_station}. Zapisano {snap_png.name}"
                            )
                        if self._body_indicates_zero_connections(body_low):
                            snap_png, _ = await self.save_debug_snapshot(page, f"err_no_trains_{train_number}_{from_station}_{to_station}")
                            raise GraphicReservationUnavailableError(
                                f"SCRAPE_ERROR: System PKP Intercity nie znalazł żadnych połączeń dla relacji {from_station} -> {to_station} w dniu {date_str} o godz. {clean_dep_time}."
                            )
                    except GraphicReservationUnavailableError:
                        raise
                    except Exception:
                        pass

                # Jeśli e-IC zawiesiło ładowanie na dłużej niż 16s bez kart, odśwież stronę
                if time.time() - start_wait > 16.0 and not reloaded and card_count == 0:
                    logger.warning(f"[CONNECTION LIST] Długie ładowanie ({time.time() - start_wait:.1f}s), odświeżam stronę...")
                    reloaded = True
                    try:
                        await page.reload(wait_until="domcontentloaded", timeout=15000)
                        await asyncio.sleep(1.0)
                        await self.close_cookie_banners(page)
                    except Exception:
                        pass
                    continue

                await asyncio.sleep(0.4)

            if not cards_found:
                try:
                    body_text = (await page.locator("body").inner_text()).lower()
                    if "kod stacji spoza zakresu" in body_text:
                        snap_png, _ = await self.save_debug_snapshot(page, f"err_kod303_{train_number}_{from_station}_{to_station}")
                        raise GraphicReservationUnavailableError(
                            f"SCRAPE_ERROR: Kod stacji spoza zakresu (kod 303) dla {from_station} -> {to_station}. Zapisano {snap_png.name}"
                        )
                    if self._body_indicates_zero_connections(body_text):
                        snap_png, _ = await self.save_debug_snapshot(page, f"err_no_trains_{train_number}_{from_station}_{to_station}")
                        raise GraphicReservationUnavailableError(
                            f"SCRAPE_ERROR: System PKP Intercity nie znalazł żadnych połączeń dla relacji {from_station} -> {to_station} w dniu {date_str} o godz. {clean_dep_time}."
                        )
                except GraphicReservationUnavailableError:
                    raise
                except Exception:
                    pass

                snap_png, _ = await self.save_debug_snapshot(page, f"err_no_train_{train_number}_{from_station}_{to_station}")
                raise GraphicReservationUnavailableError(
                    f"SCRAPE_ERROR: Nie znaleziono pociągu {train_number} na liście połączeń dla relacji {from_station} -> {to_station} po 35s oczekiwania. Zapisano {snap_png.name}"
                )

            # Step 2: Connection list — strict matching + pagination (NO fallback to wrong train!)
            report(70, f"Dopasowywanie pociągu {train_number} i wybór Klasy {ticket_class}...")

            if is_debug_scraper_enabled():
                step2_snap = DEBUG_SNAPSHOTS_DIR / "step2_connections_list.html"
                if not step2_snap.exists():
                    try:
                        step2_snap.write_text(await page.content(), encoding="utf-8")
                    except Exception:
                        pass

            train_clean_num = re.sub(r"^[A-Z]+\s*", "", train_number).strip()
            chosen_card = await self._find_matching_train_card(page, train_clean_num)

            # Pagination: if target train isn't on page 1, try "Później" (up to 2x) then "Wcześniej" (up to 2x)
            if not chosen_card:
                for direction_label in ("Później", "Wcześniej"):
                    for step_idx in range(2):
                        btn = page.locator(f"button:has-text('{direction_label}')").first
                        try:
                            if not await btn.is_visible(timeout=800) or not await btn.is_enabled():
                                break
                            prev_count = await page.locator(outer_cards_selector).count()
                            logger.info(
                                f"[TRAIN MATCH] Pociąg {train_number} nieobecny na bieżącej liście — klikam '{direction_label}' ({step_idx + 1}/2)..."
                            )
                            if not await self.safe_click(btn, timeout=1500):
                                break
                            # Wait briefly for pagination results to load
                            wait_pg = time.time()
                            while time.time() - wait_pg < 5.0:
                                await asyncio.sleep(0.3)
                                if await page.locator(page_loader_selector).count() == 0:
                                    curr_count = await page.locator(outer_cards_selector).count()
                                    if curr_count != prev_count or time.time() - wait_pg > 1.2:
                                        break
                            chosen_card = await self._find_matching_train_card(page, train_clean_num)
                            if chosen_card:
                                break
                        except Exception:
                            break
                    if chosen_card:
                        break

            if not chosen_card:
                # STRICT: NEVER fall back to connection_cards.first (scraping the wrong train corrupts results!)
                snap_png, _ = await self.save_debug_snapshot(page, f"err_no_train_{train_number}_{from_station}_{to_station}")
                raise GraphicReservationUnavailableError(
                    f"SCRAPE_ERROR: Nie znaleziono pociągu {train_number} na liście połączeń dla relacji {from_station} -> {to_station}. Zapisano {snap_png.name}"
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
                    if await exp_btn.is_visible(timeout=1500):
                        if await self.safe_click(exp_btn, timeout=1500):
                            await asyncio.sleep(0.15)
                            break
                except Exception:
                    pass

            # Step 3: Class 1 or 2 selection
            await self.check_and_handle_error_radar(page, f"wybor_klasy_{ticket_class}", allow_reload=True)
            if ticket_class == 1:
                class_buttons = [
                    chosen_card.locator("button[aria-label*='1 klas']").first,
                    chosen_card.locator("button:has-text('Wybierz 1 klasę')").first,
                    chosen_card.locator("button:has-text('1 klasa')").first,
                    chosen_card.locator("button:has-text('1 kl')").first,
                    chosen_card.locator("[data-testid='SuperPromo-select-seat-type-0']").first,
                    page.locator("button[aria-label*='1 klas']").first,
                    page.locator("button:has-text('Wybierz 1 klasę')").first,
                ]
            else:
                class_buttons = [
                    chosen_card.locator("button[aria-label*='2 klas']").first,
                    chosen_card.locator("button:has-text('Wybierz 2 klasę')").first,
                    chosen_card.locator("button:has-text('2 klasa')").first,
                    chosen_card.locator("button:has-text('2 kl')").first,
                    chosen_card.locator("[data-testid='SuperPromo-select-seat-type-1']").first,
                    page.locator("button[aria-label*='2 klas']").first,
                    page.locator("button:has-text('Wybierz 2 klasę')").first,
                ]

            for c_btn in class_buttons:
                try:
                    if await c_btn.is_visible(timeout=1500):
                        b_text = (await c_btn.inner_text()).lower()
                        if any(w in b_text for w in ["brak miejsc", "niedostępne", "zablokowana", "wyprzedane"]):
                            raise GraphicReservationUnavailableError(
                                f"BRAK_MIEJSC_KLASA_{ticket_class}: Wyczerpano pulę miejsc w Klasie {ticket_class} na tym odcinku."
                            )
                        if await self.safe_click(c_btn, timeout=1500):
                            logger.info(f"[CLASS {ticket_class}] Wybrano {ticket_class}. klasę.")
                            await asyncio.sleep(0.15)
                            break
                except GraphicReservationUnavailableError:
                    raise
                except Exception:
                    pass

            # Step 4: Your Journey & open seatmap (allow_reload=False from here onward!)
            report(75, "Konfiguracja podróży i otwieranie schematu wagonów...")
            await self.dismiss_intermediate_modals(page)
            await self.check_and_handle_error_radar(page, "twoja_podroz", allow_reload=False)

            if is_debug_scraper_enabled():
                step3_snap = DEBUG_SNAPSHOTS_DIR / "step3_your_journey.html"
                if not step3_snap.exists():
                    try:
                        step3_snap.write_text(await page.content(), encoding="utf-8")
                    except Exception:
                        pass

            schema_btn_selectors = [
                "button[class*='PreferedPlace_PreferedPlace_grmButton']",
                "button[class*='PreferedPlace_PreferedPlace_groupButton']",
                "button[class*='PreferedPlace']",
                "button:has-text('Wybierz miejsce')",
                "button:has-text('Wybierz miejsce ze schematu')",
                "button:has-text('Wskazanie miejsca')",
                "button:has-text('Wskaż miejsce')",
                "button:has-text('schematu')",
                "a:has-text('Wybierz miejsce')",
            ]

            schema_clicked = False
            for sel in schema_btn_selectors:
                elem = page.locator(sel).first
                try:
                    await elem.wait_for(state="visible", timeout=2500)
                    if await self.safe_click(elem, timeout=2000):
                        logger.info(f"[SEATMAP] Kliknięto przycisk otwarcia schematu ({sel})")
                        schema_clicked = True
                        await asyncio.sleep(0.2)
                        break
                except Exception:
                    continue

            if not schema_clicked:
                logger.warning("[SEATMAP] Żaden z selektorów otwarcia schematu nie stał się widoczny.")

            # Step 5: Wait for GRM API (/grm/sklad/wbnet/ + /grm/wagon/svg/wbnet/) or DOM seatmap render
            report(85, "Oczekiwanie na dane GRM / wyrenderowanie schematu wagonów...")

            if is_debug_scraper_enabled():
                step4_snap_html = DEBUG_SNAPSHOTS_DIR / "step4_seatmap_modal.html"
                step4_snap_png = DEBUG_SNAPSHOTS_DIR / "step4_seatmap_modal.png"
                try:
                    step4_snap_html.write_text(await page.content(), encoding="utf-8")
                    await page.screenshot(path=str(step4_snap_png), full_page=True)
                except Exception:
                    pass

            target_container: Union[Page, Frame] = page
            start_wait = time.time()
            seatmap_dom_seen_at: Optional[float] = None
            while time.time() - start_wait < 10.0:
                # Authoritative GRM API ready (sklad JSON + SVG URL template captured)
                if sklad_info["data"] is not None and wagon_svg_template["prefix"]:
                    break

                # If sklad JSON arrived and there are 0 available wagons for this class, stop waiting early
                if sklad_info["data"] is not None:
                    targets_chk, has_key_chk = self._select_grm_target_wagons(
                        sklad_info["data"], ticket_class=ticket_class
                    )
                    if has_key_chk and not targets_chk:
                        break

                for f in page.frames:
                    f_url = f.url.lower()
                    if f != page and not any(skip in f_url for skip in ["cookiebot", "consent", "blank", "google", "doubleclick"]):
                        if await f.locator("rect.place-rect, image.place, g[data-class], g[aria-label*='miejsce' i], button.seat-overlay-button").count() > 0:
                            target_container = f
                            if seatmap_dom_seen_at is None:
                                seatmap_dom_seen_at = time.time()
                            break

                if seatmap_dom_seen_at is None:
                    test_seats = page.locator("div[class*='Carriage_carriageSlide'], button.carriagesBtnNext")
                    if await test_seats.count() > 0:
                        seatmap_dom_seen_at = time.time()

                # Give GRM API responses up to 1.5s after DOM renders before falling back to DOM
                if seatmap_dom_seen_at is not None and (time.time() - seatmap_dom_seen_at) > 1.5:
                    break

                await self.dismiss_intermediate_modals(page)
                await self.check_and_handle_error_radar(page, "render_schematu", allow_reload=False)
                await asyncio.sleep(0.1)

            # Step 5: Seat extraction across target class carriages (90%)
            report(90, f"Odczytywanie wolnych miejsc z wagonów Klasy {ticket_class}...")
            carriages_data: Dict[str, List[str]] = {}

            # Priority 1: Authoritative GRM API (/grm/sklad/wbnet/ + /grm/wagon/svg/wbnet/ XML)
            if sklad_info["data"] is not None:
                api_carriages = await self._extract_seats_via_grm_api(
                    page=page,
                    sklad_data=sklad_info["data"],
                    wagon_svg_template=wagon_svg_template,
                    intercepted_svg_by_wagon=intercepted_svg_by_wagon,
                    ticket_class=ticket_class,
                )
                if api_carriages is not None:
                    carriages_data = api_carriages
                    await self._close_seatmap_modal(page)
                    report(95, f"[GRM API] Odczytano wolne miejsca w {len(carriages_data)} wagonach.")
                    return carriages_data

            # Priority 2: Fixed DOM & Iframe carousel fallback (only if GRM API wasn't intercepted/complete)
            if not carriages_data:
                carriages_data = await self._extract_seats_from_eic2_dom(
                    page,
                    target_container,
                    ticket_class=ticket_class,
                    intercepted_svg_by_wagon=intercepted_svg_by_wagon,
                    wagon_svg_events=wagon_svg_events,
                )

            if not carriages_data or not any(len(s) > 0 for s in carriages_data.values()):
                content = await page.content()
                content_lower = content.lower()
                if "brak miejsc" in content_lower or "wyprzedane" in content_lower:
                    reason = f"BRAK_MIEJSC_KLASA_{ticket_class}: Brak wolnych miejsc w pociągu (wyprzedane)."
                elif carriages_data and all(len(s) == 0 for s in carriages_data.values()):
                    reason = (
                        f"BRAK_MIEJSC_KLASA_{ticket_class}: Wszystkie sprawdzone wagony "
                        f"({', '.join(carriages_data.keys())}) mają 0 wolnych miejsc."
                    )
                elif "przerwa techniczna" in content_lower:
                    reason = "SCRAPE_ERROR: Przerwa techniczna systemu PKP Intercity."
                elif "cloudflare" in content_lower or "challenge" in content_lower:
                    reason = "SCRAPE_ERROR: Blokada WAF / Cloudflare."
                else:
                    reason = "SCRAPE_ERROR: Nie udało się odczytać schematu wagonu na 85%."

                snap_png, _ = await self.save_debug_snapshot(page, f"error_85_percent_{from_station}_{to_station}")
                raise GraphicReservationUnavailableError(
                    f"{reason} (trasa {from_station} -> {to_station}, zrzut {snap_png.name})"
                )

            # Step 6: Complete (95%)
            report(95, f"Odczytano wolne miejsca w {len(carriages_data)} wagonach.")
            return carriages_data

        except asyncio.CancelledError:
            raise
        except GraphicReservationUnavailableError:
            raise
        except Exception as e:
            logger.warning(f"[SCRAPER] Error during scraping {from_station} -> {to_station}: {e}")
            await self.save_debug_snapshot(page, f"err_{from_station}_{to_station}")
            raise GraphicReservationUnavailableError(
                f"SCRAPE_ERROR: Błąd podczas skanowania {from_station} -> {to_station}: {e}"
            ) from e
        finally:
            try:
                page.remove_listener("response", on_response)
            except Exception:
                pass

    def _extract_seats_from_json_list(
        self,
        json_list: List[Dict[str, Any]],
        ticket_class: int = TARGET_CLASS,
    ) -> Dict[str, List[str]]:
        """
        Parses intercepted JSON payloads recursively, filtering strictly for the requested ticket_class (1 or 2).
        """
        carriages: Dict[str, List[str]] = {}
        wrong_class_str = "2" if ticket_class == 1 else "1"

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

                        # Skip wrong class, WARS, sleepers
                        c_class = str(carriage.get("class", carriage.get("klasa", str(ticket_class))))
                        c_type = str(carriage.get("type", carriage.get("typ", ""))).upper()
                        if c_class == wrong_class_str or any(skip in c_type for skip in ["WR", "WARS", "WL", "BC"]):
                            continue

                        seats = carriage.get("seats", carriage.get("miejsca", carriage.get("places")))
                        if not isinstance(seats, list) or len(seats) == 0:
                            continue

                        free_seats = []
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

                for k, v in obj.items():
                    if isinstance(v, (dict, list)):
                        search_carriages(v)
            elif isinstance(obj, list):
                for item in obj:
                    search_carriages(item)

        for data in json_list:
            search_carriages(data)

        return carriages

    async def _get_seatmap_frame_signature(self, page: Page, fallback_container: Union[Page, Frame]) -> Tuple[Optional[Frame], str]:
        """Locates the active seatmap iframe and returns (frame, content_signature) to detect wagon switches."""
        seat_frame = None
        for f in page.frames:
            f_url = f.url.lower()
            if f != page and not any(skip in f_url for skip in ["cookiebot", "consent", "blank", "google", "doubleclick"]):
                if any(k in f_url for k in ["resource", "schemat", "seat", "wagon", "srcdoc"]):
                    seat_frame = f
                    break
        target = seat_frame or fallback_container
        sig = ""
        try:
            if hasattr(target, "evaluate"):
                sig = await target.evaluate(
                    """() => {
                        const svg = document.querySelector('svg');
                        if (!svg) return '';
                        const schema = svg.getAttribute('schema') || '';
                        const gCount = svg.querySelectorAll('g[aria-label]').length;
                        const firstG = svg.querySelector('g[id]');
                        return schema + '|' + gCount + '|' + (firstG ? firstG.id : '') + '|' + svg.innerHTML.length;
                    }"""
                )
        except Exception:
            sig = ""
        return seat_frame, str(sig or "")

    async def _scroll_slide_into_carousel_viewport(self, page: Page, slide) -> None:
        """Scrolls Swiper carousel left or right across >10 wagons until the slide is inside the viewport."""
        btn_next = page.locator("button.carriagesBtnNext, button[aria-label*='w prawo']").first
        btn_prev = page.locator("button.carriagesBtnPrev, button[aria-label*='w lewo']").first

        for _ in range(15):
            try:
                pos = await slide.evaluate(
                    """el => {
                        const rect = el.getBoundingClientRect();
                        const swiper = el.closest('.swiper, .swiper-container, [class*="Carriage_carriages"]') || el.parentElement;
                        const sRect = swiper ? swiper.getBoundingClientRect() : { left: 0, right: window.innerWidth };
                        if (rect.width === 0) return 'hidden';
                        if (rect.right > sRect.right + 4) return 'right';
                        if (rect.left < sRect.left - 4) return 'left';
                        return 'visible';
                    }"""
                )
            except Exception:
                break

            if pos == "visible":
                break
            nav_btn = btn_prev if pos == "left" else btn_next
            try:
                if await nav_btn.is_visible(timeout=200) and await nav_btn.is_enabled():
                    await self.safe_click(nav_btn, timeout=500)
                    await asyncio.sleep(0.1)
                else:
                    break
            except Exception:
                break

    async def _extract_seats_from_eic2_dom(
        self,
        page: Page,
        container: Union[Page, Frame],
        ticket_class: int = TARGET_CLASS,
        intercepted_svg_by_wagon: Optional[Dict[str, str]] = None,
        wagon_svg_events: Optional[Dict[str, asyncio.Event]] = None,
    ) -> Dict[str, List[str]]:
        """
        DOM carousel fallback (used ONLY when /grm/sklad/wbnet/ API was not intercepted).
        Never checks swiper-slide-active; inspects Carriage_carriageBox background-image for 'current-',
        scrolls right/left across long trains, and waits for the specific wagon's SVG response or
        iframe SVG schema/content change before reading seats.
        """
        carriages: Dict[str, List[str]] = {}
        is_class1 = int(ticket_class) == 1

        slide_locators = page.locator("div[class*='Carriage_carriageSlide']")
        slide_count = await slide_locators.count()

        async def _switch_and_read_slide(slide, c_no: str, idx: int, already_active: bool) -> List[str]:
            logger.info(f"[CAROUSEL] Przetwarzanie wagonu Klasy {ticket_class}: Nr {c_no} (slajd #{idx+1})...")

            if not already_active:
                _, prev_sig = await self._get_seatmap_frame_signature(page, container)
                await self._scroll_slide_into_carousel_viewport(page, slide)

                # Prepare event to await exact /grm/wagon/svg/wbnet/.../{c_no}/ response if network fires it
                ev = None
                if wagon_svg_events is not None:
                    ev = asyncio.Event()
                    wagon_svg_events[c_no] = ev
                    if intercepted_svg_by_wagon is not None:
                        intercepted_svg_by_wagon.pop(c_no, None)

                try:
                    await slide.evaluate(
                        """el => {
                            el.scrollIntoView({ inline: 'center', block: 'nearest' });
                            const btn = el.querySelector('div[aria-label*="Wybierz wagon numer"], div[class*="Carriage_carriageBox"], div[role="button"]') || el;
                            btn.click();
                        }"""
                    )
                except Exception:
                    click_target = slide.locator(
                        "div[aria-label*='Wybierz wagon numer'], div[class*='Carriage_carriageBox'], div[role='button']"
                    ).first
                    if await click_target.count() == 0:
                        click_target = slide
                    await self.safe_click(click_target, timeout=1200)

                # Wait up to 3.0s for wagon SVG response OR (current- style AND iframe SVG signature change)
                switch_start = time.time()
                while time.time() - switch_start < 3.0:
                    if ev is not None and ev.is_set():
                        await asyncio.sleep(0.08)
                        break

                    is_now_current = False
                    try:
                        is_now_current = await slide.evaluate(
                            """el => {
                                const box = el.querySelector("div[class*='Carriage_carriageBox']") || el;
                                const s = ((box.getAttribute('style') || '') + ' ' + (box.style && box.style.backgroundImage ? box.style.backgroundImage : '')).toLowerCase();
                                return s.includes('current-');
                            }"""
                        )
                    except Exception:
                        pass

                    _, curr_sig = await self._get_seatmap_frame_signature(page, container)
                    if is_now_current and curr_sig and curr_sig != prev_sig:
                        break
                    await asyncio.sleep(0.08)

            # Prefer intercepted SVG XML for this exact wagon if it arrived during click
            if intercepted_svg_by_wagon and c_no in intercepted_svg_by_wagon:
                return self.parse_grm_wagon_svg(intercepted_svg_by_wagon[c_no], ticket_class=ticket_class)

            seat_frame, _ = await self._get_seatmap_frame_signature(page, container)
            if seat_frame:
                try:
                    await seat_frame.locator("rect.place-rect, image.place, g[data-class]").first.wait_for(
                        state="attached", timeout=1200
                    )
                except Exception:
                    pass
            else:
                await asyncio.sleep(0.15)

            seats = await self._find_seats_in_iframe(seat_frame or container, ticket_class=ticket_class)
            if not seats:
                await self.check_and_handle_error_radar(page, context_label=f"wagon_{c_no}", allow_reload=False)
                seats = await self._find_seats_in_iframe(seat_frame or container, ticket_class=ticket_class)
            return seats

        if slide_count > 0:
            slides_meta = []
            try:
                if ticket_class == 2:
                    slides_meta = await page.evaluate(CAROUSEL_METADATA_JS)
                else:
                    slides_meta = await page.evaluate(CAROUSEL_METADATA_JS, ticket_class)
            except Exception as e:
                logger.debug(f"[CAROUSEL] evaluate metadata failed ({e})")

            if slides_meta and isinstance(slides_meta, list):
                for item in slides_meta:
                    if item.get("skip"):
                        continue
                    idx = item.get("index", 0)
                    c_no = str(item.get("carriageNumber", "")).strip()
                    if not c_no:
                        continue

                    slide = slide_locators.nth(idx)
                    is_active = bool(item.get("isActive", False))
                    seats = await _switch_and_read_slide(slide, c_no, idx, already_active=is_active)
                    logger.info(f"[SEATS] Wagon {c_no}: odczytano {len(seats)} wolnych miejsc: {seats[:10]}...")
                    carriages[c_no] = seats
            else:
                # Fallback: sequential DOM parsing for slides (supports both Class 1 and Class 2)
                for idx in range(slide_count):
                    slide = slide_locators.nth(idx)
                    klasa_el = slide.locator("p[class*='Carriage_carriageKlasa']").first
                    klasa_txt = ""
                    try:
                        if await klasa_el.count() > 0:
                            klasa_txt = (await klasa_el.text_content() or "").strip().lower()
                    except Exception:
                        pass
                    slide_txt = ""
                    try:
                        slide_txt = (await slide.inner_text()).lower()
                    except Exception:
                        pass

                    is_class_1_only = (
                        "klasa 1" in klasa_txt or "kl. 1" in klasa_txt or "klasa 1" in slide_txt
                    ) and ("klasa 2" not in klasa_txt and "klasa 2" not in slide_txt)
                    is_class_2_only = (
                        "klasa 2" in klasa_txt or "kl. 2" in klasa_txt or "klasa 2" in slide_txt
                    ) and ("klasa 1" not in klasa_txt and "klasa 1" not in slide_txt)
                    is_wrong_class = is_class_2_only if is_class1 else is_class_1_only
                    is_service_car = any(
                        skip in klasa_txt or skip in slide_txt for skip in ["wars", "restaurac", "sypialn", "kuszet"]
                    )
                    if is_wrong_class or is_service_car:
                        continue

                    c_no = ""
                    try:
                        box_el = slide.locator("div[aria-label*='wagon numer'], div[aria-label*='Wagon'], [aria-label*='wagon']").first
                        if await box_el.count() > 0:
                            box_aria = (await box_el.get_attribute("aria-label")) or ""
                            m = re.search(r"wagon\s+(?:numer\s+)?(\d+)", box_aria, re.IGNORECASE) or re.search(
                                r"numer\s+(\d+)", box_aria, re.IGNORECASE
                            )
                            if m:
                                c_no = m.group(1)
                    except Exception:
                        pass

                    if not c_no:
                        try:
                            num_el = slide.locator(
                                "p[class*='Carriage_carriageNumberDesktop'], p[class*='Carriage_carriageNumberMb'], p[class*='Carriage_carriageNumber']"
                            ).first
                            if await num_el.count() > 0:
                                num_txt = (await num_el.text_content() or "").strip()
                                m = re.search(r"\d+", num_txt)
                                if m:
                                    c_no = m.group(0)
                        except Exception:
                            pass

                    if not c_no and slide_txt:
                        m = re.search(r"wagon\s*(\d+)", slide_txt, re.IGNORECASE) or re.search(r"\b(\d+)\b", slide_txt)
                        if m:
                            c_no = m.group(1)

                    if not c_no:
                        continue

                    is_active = False
                    try:
                        box_style = (
                            await slide.locator("div[class*='Carriage_carriageBox']").first.get_attribute("style")
                        ) or ""
                        is_active = "current-" in box_style.lower()
                    except Exception:
                        pass

                    seats = await _switch_and_read_slide(slide, c_no, idx, already_active=is_active)
                    carriages[c_no] = seats
        else:
            seats = await self._find_seats_in_iframe(container, ticket_class=ticket_class)
            if seats:
                carriages["2"] = seats

        await self._close_seatmap_modal(page)
        return carriages

    async def _find_seats_in_iframe(
        self,
        container: Union[Page, Frame],
        ticket_class: int = TARGET_CLASS,
    ) -> List[str]:
        """
        Parses free seats from the seatmap iframe in < 5ms using in-page evaluate():
        1. Primary: SVG <g> groups with aria-label containing 'wolne'
        2. Secondary: button.seat-overlay-button with aria-label containing 'wolne' or data-place
        3. Tertiary: image.place with status="1" (free seat graphics)
        Filters out wrong-class seats and PRM wheelchair-only seats.
        Falls back to resilient Python locators if evaluate() is not available.
        """
        target_frames: List[Union[Page, Frame]] = []
        page_frames = []
        if hasattr(container, "frames"):
            page_frames = container.frames
        elif hasattr(container, "page") and container.page:
            page_frames = container.page.frames

        # Strictly exclude tracking/consent/cookiebot frames
        valid_frames = [
            f for f in page_frames
            if not any(skip in getattr(f, "url", "").lower() for skip in ["cookiebot", "consent", "blank", "google", "doubleclick"])
        ]

        for f in valid_frames:
            f_title = ""
            try:
                f_title = (await f.title()).lower() if hasattr(f, "title") else ""
            except Exception:
                f_title = ""
            f_url = getattr(f, "url", "").lower()
            if any(k in f_title for k in ["schemat", "miejsce", "wagon", "klasa"]) or any(k in f_url for k in ["resource", "schemat", "seat", "wagon", "srcdoc"]):
                target_frames.insert(0, f)
            elif f != container:
                target_frames.append(f)

        if container not in target_frames and not any(skip in getattr(container, "url", "").lower() for skip in ["cookiebot", "consent", "blank"]):
            target_frames.insert(0, container)

        # Ultra-fast in-page evaluate() execution across candidate frames (<5ms)
        for cf in target_frames:
            try:
                if hasattr(cf, "evaluate"):
                    if ticket_class == 2:
                        res = await cf.evaluate(SVG_SEATS_EXTRACTOR_JS)
                    else:
                        res = await cf.evaluate(SVG_SEATS_EXTRACTOR_JS, ticket_class)
                    if res and isinstance(res, list):
                        valid = [str(s) for s in res if str(s).isdigit()]
                        if valid:
                            return sorted(list(set(valid)), key=lambda x: int(x))
            except Exception as e:
                logger.debug(f"[EVALUATE] In-page seat evaluation error in frame: {e}")

        # Fallback: Python locator iteration if evaluate() yielded nothing (handles both Class 1 and Class 2 + PRM)
        is_class1 = int(ticket_class) == 1

        def _skip_wrong_class(aria_l: str, d_class_l: str) -> bool:
            if is_class1:
                return "second class" in d_class_l or ("klasa 2" in aria_l and "klasa 1" not in aria_l)
            return "first class" in d_class_l or ("klasa 1" in aria_l and "klasa 2" not in aria_l)

        free_seats: List[str] = []
        for cf in target_frames:
            try:
                g_locs = cf.locator("g[aria-label*='Wolne' i], g[aria-label*='wolne' i]")
                g_cnt = await g_locs.count()
                if g_cnt > 0:
                    for idx in range(g_cnt):
                        g_elem = g_locs.nth(idx)
                        aria = (await g_elem.get_attribute("aria-label")) or ""
                        d_class = (await g_elem.get_attribute("data-class")) or ""
                        if _skip_wrong_class(aria.lower(), d_class.lower()) or is_prm_wheelchair_label(aria):
                            continue
                        text_el = g_elem.locator("text").first
                        num = ""
                        if await text_el.count() > 0:
                            num = (await text_el.text_content() or "").strip()
                        if not num:
                            m = re.search(r"miejsce\s+(\d+)", aria, re.IGNORECASE)
                            if m:
                                num = m.group(1)
                        if num and num.isdigit():
                            free_seats.append(num)
                    if free_seats:
                        break
            except Exception:
                pass

            try:
                b_locs = cf.locator("button.seat-overlay-button")
                b_cnt = await b_locs.count()
                if b_cnt > 0:
                    for idx in range(b_cnt):
                        b_elem = b_locs.nth(idx)
                        aria = (await b_elem.get_attribute("aria-label")) or ""
                        aria_l = aria.lower()
                        if "wolne" not in aria_l:
                            continue
                        if _skip_wrong_class(aria_l, "") or is_prm_wheelchair_label(aria_l):
                            continue
                        place_val = (await b_elem.get_attribute("data-place")) or ""
                        if place_val and place_val.isdigit():
                            free_seats.append(place_val)
                        else:
                            m = re.search(r"miejsce\s+(\d+)", aria, re.IGNORECASE)
                            if m and m.group(1).isdigit():
                                free_seats.append(m.group(1))
                    if free_seats:
                        break
            except Exception:
                pass

        if not free_seats:
            try:
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
                            m = re.search(r"\b\d+\b", t)
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
        has_warmed_session: bool = False,
        from_eva: str = "",
        to_eva: str = "",
    ) -> Dict[str, List[str]]:
        """
        Scans a single segment with SQLite caching (TTL 10 min).
        Reuses page if provided by LiveTrainScanner.
        Raises GraphicReservationUnavailableError on both explicit SOLD_OUT and final SCRAPE_ERROR
        so callers can distinguish honest sold-out segments from scrape failures.
        """
        cache_key = f"seatmap_{train_number}_{travel_date}_{from_station}_{to_station}_c{ticket_class}"
        if use_cache:
            cached = get_cached_item(cache_key, ttl_seconds=self.ttl_seconds)
            if cached and isinstance(cached, dict) and any(len(v) > 0 for v in cached.values()):
                logger.info(f"[CACHE HIT] Seatmap for segment {from_station} -> {to_station} (Klasa {ticket_class})")
                return cached

        # Ultra-short jitter to stagger concurrent requests cleanly
        await asyncio.sleep(random.uniform(0.05, 0.15))

        if page is not None:
            last_error: Optional[Exception] = None
            for attempt in range(2):
                try:
                    carriages = await self.search_segment_on_intercity(
                        page=page,
                        from_station=from_station,
                        to_station=to_station,
                        date_str=travel_date,
                        departure_time=departure_time,
                        train_number=train_number,
                        ticket_class=ticket_class,
                        progress_cb=progress_cb,
                        has_warmed_session=has_warmed_session,
                        from_eva=from_eva,
                        to_eva=to_eva,
                    )
                    if carriages and any(len(v) > 0 for v in carriages.values()):
                        set_cached_item(cache_key, carriages)
                        return carriages
                    if attempt == 0:
                        logger.info(f"[RETRY] Brak danych dla {from_station} -> {to_station}, ponawiam próbę...")
                        await asyncio.sleep(1.5)
                    else:
                        raise GraphicReservationUnavailableError(
                            f"SCRAPE_ERROR: Brak danych o miejscach po 2 próbach dla {from_station} -> {to_station}."
                        )
                except asyncio.CancelledError:
                    raise
                except GraphicReservationUnavailableError as e:
                    last_error = e
                    logger.warning(f"[SCAN] Rezerwacja graficzna niedostępna dla {from_station} -> {to_station}: {e}")
                    if is_sold_out_reason(str(e)):
                        raise
                    if attempt == 0:
                        await asyncio.sleep(2.0)
                    else:
                        raise
                except Exception as e:
                    last_error = e
                    logger.warning(f"[SCAN] Błąd w próbie {attempt+1} dla {from_station} -> {to_station}: {e}")
                    if attempt == 0:
                        await asyncio.sleep(2.0)
                    else:
                        await self.save_debug_snapshot(page, f"err_{from_station}_{to_station}")
                        raise GraphicReservationUnavailableError(
                            f"SCRAPE_ERROR: Nieoczekiwany błąd dla {from_station} -> {to_station}: {e}"
                        ) from e
            if last_error:
                raise last_error
            raise GraphicReservationUnavailableError(
                f"SCRAPE_ERROR: Nie udało się odczytać miejsc dla {from_station} -> {to_station}."
            )

        # Standalone launch if no page provided
        async with async_playwright() as p:
            browser, is_cdp = await self.get_browser_instance(p, headless=self.headless)
            try:
                context = await self.create_context(browser)
                page = await self.init_page(context)
                if not self.headless:
                    try:
                        await page.bring_to_front()
                    except Exception:
                        pass
                carriages = await self.search_segment_on_intercity(
                    page=page,
                    from_station=from_station,
                    to_station=to_station,
                    date_str=travel_date,
                    departure_time=departure_time,
                    train_number=train_number,
                    ticket_class=ticket_class,
                    progress_cb=progress_cb,
                    from_eva=from_eva,
                    to_eva=to_eva,
                )
                if carriages and any(len(v) > 0 for v in carriages.values()):
                    set_cached_item(cache_key, carriages)
                return carriages
            finally:
                await browser.close()


class LiveTrainScanner:
    """
    Orchestrates full-route scanning of trains with warmed session state
    and concurrent segment workers bounded by asyncio.Semaphore.
    """

    def __init__(
        self,
        headless: Optional[bool] = None,
        show_browser: Optional[bool] = None,
        on_progress: Optional[Callable[[str], None]] = None,
        ttl_seconds: int = 600,
        concurrency: int = 3,
    ):
        self.scraper = EbiletSeatScraper(
            headless=headless,
            show_browser=show_browser,
            ttl_seconds=ttl_seconds,
        )
        self.on_progress = on_progress
        self.concurrency = max(1, concurrency)

    @staticmethod
    def compute_segment_date(travel_date: str, day_offset: int = 0) -> str:
        """Computes YYYY-MM-DD segment date adjusted by day_offset for overnight trains crossing midnight."""
        clean_date = (travel_date or "").strip()
        if "." in clean_date:
            parts = clean_date.split(".")
            if len(parts) == 3 and len(parts[2]) == 4:
                clean_date = f"{parts[2]}-{parts[1]}-{parts[0]}"
        if not day_offset:
            return clean_date
        try:
            dt = datetime.strptime(clean_date, "%Y-%m-%d")
            return (dt + timedelta(days=int(day_offset))).strftime("%Y-%m-%d")
        except Exception:
            return clean_date

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
        ticket_class: int = TARGET_CLASS,
        fast_path: bool = False,
        cancel_event: Optional[asyncio.Event] = None,
    ) -> FullTrainAvailabilityMap:
        """
        1. Obtains all intermediate stops for the train (off the event loop).
        2. Warms up cookies/Cookiebot once and transfers storage_state to isolated contexts.
        3. Optional Fast-Path (fast_path=True): checks whole A -> B route first; if free seats exist
           for 100% of the trip, returns immediately unless user forces full segment scan.
        4. Scans elementary segments concurrently via Playwright bounded by asyncio.Semaphore.
        5. Constructs FullTrainAvailabilityMap (honest OK / SOLD_OUT / SCRAPE_ERROR statuses)
           and saves to logs/last_scan_map.txt.
        """
        def _check_cancelled() -> None:
            if cancel_event is not None and cancel_event.is_set():
                raise asyncio.CancelledError("Skanowanie anulowane przez użytkownika")

        def _compute_duration_min(s_from, s_to, fallback_min: int = 30) -> int:
            arr_m = getattr(s_to, "arrival_min", None)
            dep_m = getattr(s_from, "departure_min", None)
            if isinstance(arr_m, int) and isinstance(dep_m, int) and arr_m > dep_m:
                return max(5, arr_m - dep_m)
            t1 = (getattr(s_from, "departure_time", "") or getattr(s_from, "arrival_time", "") or "").strip()
            t2 = (getattr(s_to, "arrival_time", "") or getattr(s_to, "departure_time", "") or "").strip()
            m1 = re.match(r"^(\d{1,2}):(\d{2})$", t1)
            m2 = re.match(r"^(\d{1,2}):(\d{2})$", t2)
            if m1 and m2:
                min1 = int(m1.group(1)) * 60 + int(m1.group(2))
                min2 = int(m2.group(1)) * 60 + int(m2.group(2))
                day_diff = int(getattr(s_to, "day_offset", 0) or 0) - int(getattr(s_from, "day_offset", 0) or 0)
                dur = min2 - min1 + max(0, day_diff) * 1440
                if dur < 0:
                    dur += 1440
                return max(5, dur)
            return fallback_min

        self._notify(f"[5%] Rozpoczynanie skanowania pociągu {train_number} (Klasa {ticket_class})...")
        _check_cancelled()

        # 1. Resolve intermediate route stops off the event loop
        stops = await asyncio.to_thread(
            resolve_real_route,
            from_station=from_station,
            to_station=to_station,
            travel_date=travel_date,
            train_number=train_number,
            dep_time=departure_time,
        )
        stops = filter_and_merge_stops(stops, merge_short_segments=False)
        _check_cancelled()

        num_segments = len(stops) - 1
        if num_segments <= 0:
            raise ValueError(f"Nie znaleziono odcinków dla trasy {from_station} -> {to_station}")

        route_desc = " -> ".join([f"{idx+1}. {s.name} ({s.departure_time or s.arrival_time or '??:??'})" for idx, s in enumerate(stops)])
        logger.info(f"[ROUTE] Pociąg {train_number}: {route_desc}")
        self._notify(f"[10%] Wykryto {num_segments} odcinków trasy: {route_desc}")

        segments_map: List[SegmentAvailabilityMap] = []

        # 2. Single browser process with warmed storage_state shared across isolated contexts
        async with async_playwright() as p:
            browser, is_cdp = await self.scraper.get_browser_instance(p, headless=self.scraper.headless)

            unhide_task = None
            warmup_storage_state: Optional[Dict[str, Any]] = None
            try:
                if not self.scraper.headless:
                    force_unhide_chrome_windows()

                    async def _keep_unhidden():
                        while True:
                            try:
                                force_unhide_chrome_windows()
                            except Exception:
                                pass
                            await asyncio.sleep(1.0)

                    unhide_task = asyncio.create_task(_keep_unhidden())

                # Warmup session on origin and capture cookies/localStorage, then close immediately
                try:
                    self._notify("[12%] Inicjalizacja sesji i ciasteczek...")
                    warmup_ctx = await self.scraper.create_context(browser)
                    warmup_pg = await self.scraper.init_page(warmup_ctx)
                    try:
                        await warmup_pg.goto("https://ebilet.intercity.pl/", wait_until="domcontentloaded", timeout=15000)
                        await self.scraper.close_cookie_banners(warmup_pg)
                        await asyncio.sleep(0.1)
                        try:
                            state = await warmup_ctx.storage_state()
                            if isinstance(state, dict) and state:
                                warmup_storage_state = state
                        except Exception:
                            pass
                    finally:
                        try:
                            await warmup_pg.close()
                        except Exception:
                            pass
                        try:
                            await warmup_ctx.close()
                        except Exception:
                            pass
                except Exception as e:
                    logger.warning(f"[WARMUP] Ostrzeżenie podczas inicjalizacji sesji: {e}")

                _check_cancelled()

                # 3. Fast-Path A -> B full-route check before scanning all elementary segments
                if fast_path and num_segments > 1:
                    s_first = stops[0]
                    s_last = stops[-1]
                    full_dep_time = s_first.departure_time or s_first.arrival_time or departure_time or "12:00"
                    full_day_offset = int(getattr(s_first, "day_offset", 0) or 0)
                    full_date = self.compute_segment_date(travel_date, full_day_offset)
                    full_from_eva = (getattr(s_first, "eva_code", "") or "").strip()
                    full_to_eva = (getattr(s_last, "eva_code", "") or "").strip()

                    self._notify(
                        f"[14%] Szybkie sprawdzanie (Fast-Path): sprawdzanie wolnych miejsc na całą trasę "
                        f"{s_first.name} -> {s_last.name} ({full_date} {full_dep_time})..."
                    )

                    fp_context = await self.scraper.create_context(browser, storage_state=warmup_storage_state)
                    fp_page = await self.scraper.init_page(fp_context)
                    full_carriages: Dict[str, List[str]] = {}
                    try:
                        def fp_cb(pct: int, msg: str):
                            global_pct = min(22, 13 + int(pct * 0.09))
                            self._notify(f"[{global_pct}%] Fast-Path ({s_first.name} -> {s_last.name}): {msg}")

                        full_carriages = await asyncio.wait_for(
                            self.scraper.scan_segment(
                                from_station=s_first.name,
                                to_station=s_last.name,
                                travel_date=full_date,
                                departure_time=full_dep_time,
                                train_number=train_number,
                                ticket_class=ticket_class,
                                progress_cb=fp_cb,
                                page=fp_page,
                                has_warmed_session=bool(warmup_storage_state),
                                from_eva=full_from_eva,
                                to_eva=full_to_eva,
                            ),
                            timeout=75.0,
                        )
                    except asyncio.CancelledError:
                        raise
                    except asyncio.TimeoutError:
                        logger.warning(f"[FAST-PATH] Timeout 75s dla {s_first.name} -> {s_last.name}, fallback do pełnego skanowania")
                        self._notify(
                            f"[22%] Szybkie sprawdzanie przekroczyło limit czasu — skanuję wszystkie {num_segments} odcinków..."
                        )
                    except GraphicReservationUnavailableError as e:
                        if is_sold_out_reason(str(e)):
                            self._notify(
                                f"[22%] Brak wolnego miejsca na całą trasę ({s_first.name} -> {s_last.name}) "
                                f"— przechodzę do skanowania {num_segments} odcinków pośrednich..."
                            )
                        else:
                            logger.warning(f"[FAST-PATH] Fallback do pełnego skanowania: {e}")
                            self._notify(
                                f"[22%] Szybkie sprawdzanie niedostępne — skanuję wszystkie {num_segments} odcinków..."
                            )
                    except Exception as e:
                        logger.warning(f"[FAST-PATH] Nieoczekiwany błąd, fallback do pełnego skanowania: {e}")
                        self._notify(
                            f"[22%] Szybkie sprawdzanie niedostępne — skanuję wszystkie {num_segments} odcinków..."
                        )
                    finally:
                        try:
                            await fp_page.close()
                        except Exception:
                            pass
                        try:
                            await fp_context.close()
                        except Exception:
                            pass

                    if full_carriages and any(len(s) > 0 for s in full_carriages.values()):
                        total_dur = _compute_duration_min(s_first, s_last, fallback_min=30 * num_segments)
                        fast_seg = SegmentAvailabilityMap(
                            segment_index=0,
                            from_station=s_first.name,
                            to_station=s_last.name,
                            departure_time=full_dep_time,
                            duration_min=total_dur,
                            from_eva=full_from_eva,
                            to_eva=full_to_eva,
                            segment_date=full_date,
                            carriages=full_carriages,
                            status="OK",
                            error_reason=None,
                        )
                        fast_map = FullTrainAvailabilityMap(
                            train_number=train_number,
                            travel_date=travel_date,
                            fast_path_used=True,
                            total_route_segments=num_segments,
                            segments=[fast_seg],
                        )
                        try:
                            LAST_SCAN_FILE.write_text(format_availability_map(fast_map), encoding="utf-8")
                            logger.info(f"[SCANNER] Saved fast-path scan map to {LAST_SCAN_FILE}")
                        except Exception as e:
                            logger.warning(f"Failed to write last scan map: {e}")

                        self._notify("[100%] Szybkie sprawdzanie: znaleziono wolne miejsca na 100% trasy bez przesiadek!")
                        return fast_map

                _check_cancelled()

                sem = asyncio.Semaphore(self.concurrency)
                progress_base = 22 if (fast_path and num_segments > 1) else 15
                completed_count = 0
                segment_progress = [0] * num_segments
                completed_lock = asyncio.Lock()

                def compute_global_pct() -> int:
                    avg = sum(segment_progress) / max(1, num_segments)
                    return min(98, max(progress_base, progress_base + int(avg * ((98 - progress_base) / 100.0))))

                async def scan_single_segment(idx: int) -> SegmentAvailabilityMap:
                    nonlocal completed_count
                    _check_cancelled()

                    s_from = stops[idx]
                    s_to = stops[idx + 1]
                    seg_dep_time = s_from.departure_time or s_from.arrival_time or departure_time or "12:00"
                    seg_day_offset = int(getattr(s_from, "day_offset", 0) or 0)
                    seg_date = self.compute_segment_date(travel_date, seg_day_offset)
                    seg_from_eva = (getattr(s_from, "eva_code", "") or "").strip()
                    seg_to_eva = (getattr(s_to, "eva_code", "") or "").strip()
                    seg_dur = _compute_duration_min(s_from, s_to, fallback_min=30)

                    carriages: Dict[str, List[str]] = {}
                    seg_status = "OK"
                    seg_error_reason: Optional[str] = None

                    if idx > 0:
                        await asyncio.sleep(min(idx * 0.1, 0.4))

                    async with sem:
                        _check_cancelled()
                        self._notify(
                            f"[{compute_global_pct()}%] Skanowanie odcinka {idx + 1}/{num_segments}: "
                            f"{s_from.name} -> {s_to.name} ({seg_date} {seg_dep_time})..."
                        )

                        def seg_cb(pct: int, msg: str):
                            if pct > segment_progress[idx]:
                                segment_progress[idx] = pct
                            global_pct = compute_global_pct()
                            self._notify(
                                f"[{global_pct}%] Odcinek {idx + 1}/{num_segments} ({s_from.name} -> {s_to.name}): {msg}"
                            )

                        seg_context = await self.scraper.create_context(browser, storage_state=warmup_storage_state)
                        seg_page = await self.scraper.init_page(seg_context)
                        try:
                            carriages = await asyncio.wait_for(
                                self.scraper.scan_segment(
                                    from_station=s_from.name,
                                    to_station=s_to.name,
                                    travel_date=seg_date,
                                    departure_time=seg_dep_time,
                                    train_number=train_number,
                                    ticket_class=ticket_class,
                                    progress_cb=seg_cb,
                                    page=seg_page,
                                    has_warmed_session=bool(warmup_storage_state),
                                    from_eva=seg_from_eva,
                                    to_eva=seg_to_eva,
                                ),
                                timeout=75.0,
                            )
                            if not carriages or not any(len(s) > 0 for s in carriages.values()):
                                seg_status = "SOLD_OUT"
                                seg_error_reason = f"Brak wolnych miejsc w Klasie {ticket_class}."
                                carriages = {}
                        except asyncio.CancelledError:
                            raise
                        except asyncio.TimeoutError:
                            logger.warning(f"[SCANNER] Timeout 75s skanowania odcinka {s_from.name} -> {s_to.name}")
                            seg_status = "SCRAPE_ERROR"
                            seg_error_reason = "SCRAPE_ERROR: Timeout skanowania odcinka (75s)."
                            carriages = {}
                        except GraphicReservationUnavailableError as e:
                            err_str = str(e)
                            if is_sold_out_reason(err_str):
                                logger.info(f"[SCANNER] Wyprzedany odcinek {s_from.name} -> {s_to.name}: {err_str}")
                                seg_status = "SOLD_OUT"
                            else:
                                logger.warning(f"[SCANNER] Błąd odczytu odcinka {s_from.name} -> {s_to.name}: {err_str}")
                                seg_status = "SCRAPE_ERROR"
                            seg_error_reason = err_str
                            carriages = {}
                        except Exception as e:
                            err_str = str(e)
                            logger.error(f"[SCANNER] Nieoczekiwany błąd podczas skanowania odcinka {s_from.name} -> {s_to.name}: {err_str}")
                            seg_status = "SCRAPE_ERROR"
                            seg_error_reason = err_str
                            carriages = {}
                        finally:
                            try:
                                await seg_page.close()
                            except Exception:
                                pass
                            try:
                                await seg_context.close()
                            except Exception:
                                pass

                    async with completed_lock:
                        segment_progress[idx] = 100
                        completed_count += 1
                        curr_done = completed_count
                        global_pct = compute_global_pct()

                    c_summary_parts = []
                    for c_no in sorted(carriages.keys(), key=lambda x: int(x) if x.isdigit() else 99):
                        seats_cnt = len(carriages[c_no])
                        c_summary_parts.append(f"W{c_no} ({seats_cnt})")

                    if seg_status == "SCRAPE_ERROR":
                        c_summary = f"BŁĄD ODCZYTU ({seg_error_reason})"
                    elif c_summary_parts:
                        c_summary = ", ".join(c_summary_parts)
                    else:
                        c_summary = "Brak wolnych miejsc"

                    self._notify(
                        f"[{global_pct}%] Ukończono odcinek {idx + 1}/{num_segments} ({curr_done}/{num_segments} gotowe): "
                        f"{s_from.name} -> {s_to.name}. Wolne miejsca: {c_summary}"
                    )

                    return SegmentAvailabilityMap(
                        segment_index=idx,
                        from_station=s_from.name,
                        to_station=s_to.name,
                        departure_time=seg_dep_time,
                        duration_min=seg_dur,
                        from_eva=seg_from_eva,
                        to_eva=seg_to_eva,
                        segment_date=seg_date,
                        carriages=carriages,
                        status=seg_status,
                        error_reason=seg_error_reason,
                    )

                tasks = [scan_single_segment(i) for i in range(num_segments)]
                segments_map = list(await asyncio.gather(*tasks))

            finally:
                if unhide_task:
                    unhide_task.cancel()
                await browser.close()

        # 4. Create FullTrainAvailabilityMap
        full_map = FullTrainAvailabilityMap(
            train_number=train_number,
            travel_date=travel_date,
            fast_path_used=False,
            total_route_segments=num_segments,
            segments=segments_map,
        )

        # 5. Format and save to logs/last_scan_map.txt
        formatted_txt = format_availability_map(full_map)
        try:
            LAST_SCAN_FILE.write_text(formatted_txt, encoding="utf-8")
            logger.info(f"[SCANNER] Saved last scan map to {LAST_SCAN_FILE}")
        except Exception as e:
            logger.warning(f"Failed to write last scan map: {e}")

        self._notify("[100%] Zakończono skanowanie wszystkich odcinków pociągu!")
        return full_map
