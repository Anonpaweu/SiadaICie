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
from backend.app.scraper.schedule_scraper import resolve_real_route, get_station_info
from backend.app.scraper.route_resolver import filter_and_merge_stops
from backend.app.scraper.ic_client import GraphicReservationUnavailableError

logger = logging.getLogger(__name__)

DEBUG_SNAPSHOTS_DIR = Path("logs/debug_snapshots")
NETWORK_API_LOG = DEBUG_SNAPSHOTS_DIR / "network_api_log.jsonl"
LAST_SCAN_FILE = Path("logs/last_scan_map.txt")
TARGET_CLASS = 2  # Hardcoded default: 2nd class sitting carriages per Plan V2/V3

SVG_SEATS_EXTRACTOR_JS = r"""
(targetClass = 2) => {
    const seats = new Set();
    const isClass1Target = Number(targetClass) === 1;

    const shouldSkipClass = (aria, dClass) => {
        if (isClass1Target) {
            return dClass.includes('second class') || (aria.includes('klasa 2') && !aria.includes('klasa 1'));
        }
        return dClass.includes('first class') || (aria.includes('klasa 1') && !aria.includes('klasa 2'));
    };

    // 1. Primary: SVG <g> groups with aria-label containing 'wolne'
    const gElements = document.querySelectorAll("g[aria-label*='wolne' i], g[aria-label*='Wolne' i]");
    for (const g of gElements) {
        const aria = (g.getAttribute('aria-label') || '').toLowerCase();
        const dClass = (g.getAttribute('data-class') || '').toLowerCase();
        if (shouldSkipClass(aria, dClass)) {
            continue;
        }

        let num = '';
        const textEl = g.querySelector('text');
        if (textEl && textEl.textContent) {
            num = textEl.textContent.trim();
        }
        if (!num || !/^\d+$/.test(num)) {
            const m = aria.match(/miejsce\s+(\d+)/i);
            if (m) {
                num = m[1];
            }
        }
        if (num && /^\d+$/.test(num)) {
            seats.add(num);
        }
    }

    // 2. Secondary: button.seat-overlay-button overlays
    if (seats.size === 0) {
        const btns = document.querySelectorAll('button.seat-overlay-button');
        for (const b of btns) {
            const aria = (b.getAttribute('aria-label') || '').toLowerCase();
            if (!aria.includes('wolne')) continue;
            if (shouldSkipClass(aria, '')) continue;

            const placeVal = b.getAttribute('data-place') || '';
            if (placeVal && /^\d+$/.test(placeVal)) {
                seats.add(placeVal);
            } else {
                const m = aria.match(/miejsce\s+(\d+)/i);
                if (m && /^\d+$/.test(m[1])) {
                    seats.add(m[1]);
                }
            }
        }
    }

    // 3. Tertiary: image.place with status='1'
    if (seats.size === 0) {
        const imgs = document.querySelectorAll("image.place[status='1']");
        for (const img of imgs) {
            let parentG = img.closest('g');
            if (!parentG && img.id) {
                try {
                    parentG = document.querySelector(`g:has(#${CSS.escape(img.id)})`);
                } catch(e) {}
            }
            if (parentG) {
                const textEl = parentG.querySelector('text');
                if (textEl && textEl.textContent) {
                    const num = textEl.textContent.trim();
                    if (num && /^\d+$/.test(num)) {
                        seats.add(num);
                    }
                }
            }
        }
    }

    // 4. Fallback selectors
    if (seats.size === 0) {
        const fallbackEls = document.querySelectorAll(
            '.seat.available, .seat.free, [data-status="available"], [data-status="free"], button.seat:not(.occupied):not(.taken):not(.disabled)'
        );
        for (const el of fallbackEls) {
            const txt = (el.innerText || el.textContent || '').trim();
            const m = txt.match(/\b\d+\b/);
            if (m) {
                seats.add(m[0]);
            }
        }
    }

    return Array.from(seats);
}
"""

CAROUSEL_METADATA_JS = r"""
(targetClass = 2) => {
    const slides = document.querySelectorAll("div[class*='Carriage_carriageSlide']");
    const isClass1Target = Number(targetClass) === 1;
    const result = [];
    slides.forEach((s, idx) => {
        const klasaEl = s.querySelector("p[class*='Carriage_carriageKlasa']");
        const klasaTxt = (klasaEl ? klasaEl.textContent : '').trim().toLowerCase();
        const slideTxt = (s.innerText || '').toLowerCase();
        const isClass1Only = (klasaTxt.includes('klasa 1') || klasaTxt.includes('kl. 1') || slideTxt.includes('klasa 1')) && (!klasaTxt.includes('klasa 2') && !slideTxt.includes('klasa 2'));
        const isClass2Only = (klasaTxt.includes('klasa 2') || klasaTxt.includes('kl. 2') || slideTxt.includes('klasa 2')) && (!klasaTxt.includes('klasa 1') && !slideTxt.includes('klasa 1'));
        const isWrongClass = isClass1Target ? isClass2Only : isClass1Only;
        const isService = ['wars', 'restaurac', 'sypialn', 'kuszet'].some(k => klasaTxt.includes(k) || slideTxt.includes(k));
        
        let cNo = '';
        const boxEl = s.querySelector("div[aria-label*='wagon numer'], div[aria-label*='Wagon'], [aria-label*='wagon']");
        if (boxEl) {
            const aria = boxEl.getAttribute('aria-label') || '';
            let m = aria.match(/wagon\s+(?:numer\s+)?(\d+)/i) || aria.match(/numer\s+(\d+)/i);
            if (m) cNo = m[1];
        }
        if (!cNo) {
            const numEl = s.querySelector("p[class*='Carriage_carriageNumberDesktop'], p[class*='Carriage_carriageNumberMb'], p[class*='Carriage_carriageNumber']");
            if (numEl) {
                let m = (numEl.textContent || '').match(/\d+/);
                if (m) cNo = m[0];
            }
        }
        if (!cNo && slideTxt) {
            let m = slideTxt.match(/wagon\s*(\d+)/i) || slideTxt.match(/\b(\d+)\b/);
            if (m) cNo = m[1];
        }

        const isActive = s.classList.contains('swiper-slide-active');

        result.push({
            index: idx,
            carriageNumber: cNo,
            skip: isWrongClass || isService || !cNo,
            isActive: isActive
        });
    });
    return result;
}
"""


def sanitize_filename(name: str) -> str:
    """Sanitize station/train labels into safe cross-platform filenames."""
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "_", name.strip())
    return cleaned.strip("_")[:120] or "snapshot"


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
        """Initializes page with stealth scripts and fast timeout."""
        page = await context.new_page()
        page.set_default_timeout(self.timeout_ms)
        await page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
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

    async def check_and_handle_error_radar(self, page: Page, context_label: str = "") -> bool:
        """
        Error radar inspired by Kocur.py.
        Detects PKP server error states ('Ups...', 'poszło nie tak', 'Wystąpił nieoczekiwany błąd').
        If detected, waits with jitter (2.5 - 4.0s), reloads the page, and dismisses banners.
        Returns True if an error was detected and handled.
        """
        error_patterns = [
            "text=/Ups/i",
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

    def build_direct_search_url(
        self,
        from_station: str,
        to_station: str,
        date_str: str,
        departure_time: str,
        direct_only: bool = True,
    ) -> str:
        """
        Buduje bezpośredni adres URL wyszukiwania połączeń w e-IC 2.0 (Krok 2)
        na podstawie identyfikatorów EVA stacji, omijając formularz wyszukiwania.
        """
        _, from_eva = get_station_info(from_station)
        _, to_eva = get_station_info(to_station)

        if not from_eva or not to_eva:
            logger.warning(
                f"[DIRECT URL] Brak kodu EVA w bazie SQLite dla stacji: "
                f"from='{from_station}' (eva='{from_eva}'), to='{to_station}' (eva='{to_eva}')."
            )

        clean_date = date_str.strip()
        if "." in clean_date:
            parts = clean_date.split(".")
            if len(parts) == 3 and len(parts[2]) == 4:
                clean_date = f"{parts[2]}-{parts[1]}-{parts[0]}"

        clean_time = departure_time.strip() if departure_time else "12:00"
        polbez = "1" if direct_only else "0"

        return (
            f"https://ebilet.intercity.pl/wyszukiwanie?"
            f"dwyj={clean_date}&swyj={from_eva}&sprzy={to_eva}&"
            f"time={clean_time}&przy=0&sprzez=&ticket100=1010&ticket50=&polbez={polbez}"
        )

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
    ) -> Dict[str, List[str]]:
        """
        Realizuje odczyt miejsc ze schematu wagonów na trasie from_station -> to_station.
        Otwiera bezpośrednio listę połączeń (Krok 2), rozwija pociąg ('Kup bilet'),
        wybiera wskazaną klasę (1 lub 2), klika 'Wybierz miejsce', odczytuje wolne miejsca
        ze wszystkich wagonów wybranej klasy i zamyka modal ('Zamknij').
        """
        clean_dep_time = departure_time.strip() if departure_time else "12:00"

        def report(pct: int, msg: str):
            logger.info(f"[{pct}%] {msg}")
            if progress_cb:
                try:
                    progress_cb(pct, msg)
                except Exception:
                    pass

        captured_network_json: List[Dict[str, Any]] = []
        intercepted_carriages: Dict[str, List[str]] = {}

        async def on_response(response: Response):
            url = response.url
            ct = response.headers.get("content-type", "")
            if "application/json" in ct and "intercity.pl" in url.lower():
                try:
                    data = await response.json()
                    log_network_api_call(url, response.status, data)
                    extracted = self._extract_seats_from_json_list([data], ticket_class=ticket_class)
                    if extracted:
                        intercepted_carriages.update(extracted)
                        logger.info(f"[NETWORK] Intercepted seatmap JSON from {url} with {len(extracted)} carriages: {list(extracted.keys())}")
                    if any(k in url.lower() for k in ["seat", "schemat", "wagon", "carriage", "uklad", "places"]):
                        captured_network_json.append(data)
                        logger.debug(f"[NETWORK] Intercepted seatmap JSON from: {url}")
                except Exception:
                    pass

        page.on("response", on_response)

        try:
            # Krok 1 & 2: Otwarcie bezpośredniego adresu wyszukiwania połączeń
            report(20, f"Otwieranie listy połączeń dla trasy {from_station} -> {to_station}...")
            direct_url = self.build_direct_search_url(from_station, to_station, date_str, departure_time)
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
            await self.check_and_handle_error_radar(page, "otwarcie_wyszukiwania")

            if not self.headless:
                force_unhide_chrome_windows()

            # Oczekiwanie na kafelki pociągów na liście połączeń (Krok 2)
            report(35, f"Oczekiwanie na listę połączeń dla relacji {from_station} -> {to_station}...")
            start_wait = time.time()
            reloaded = False
            cards_found = False

            while time.time() - start_wait < 35.0:
                if await self.check_and_handle_error_radar(page, "lista_polaczen"):
                    continue

                loader_count = await page.locator(
                    "[class*='trainLoaderWithText'], [class*='TrainLoaderWithText'], .Loader_linear__OpQ5L, [class*='Loader']"
                ).count()
                cards = page.locator(
                    "li[class*='TripPropositionDesktop_tripPropositionDesktop__container'], li[class*='TripProposition'], .connection-card, .connection-row, tr.connection"
                )
                card_count = await cards.count()

                if card_count > 0 and loader_count == 0:
                    cards_found = True
                    break

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

                await asyncio.sleep(0.5)

            if not cards_found:
                try:
                    body_text = (await page.locator("body").inner_text()).lower()
                    if "brak połączeń" in body_text or "nie znaleziono połączeń" in body_text:
                        snap_png, _ = await self.save_debug_snapshot(page, f"err_no_trains_{train_number}_{from_station}_{to_station}")
                        raise GraphicReservationUnavailableError(
                            f"System PKP Intercity nie znalazł żadnych połączeń dla relacji {from_station} -> {to_station} w dniu {date_str} o godz. {clean_dep_time}."
                        )
                except GraphicReservationUnavailableError:
                    raise
                except Exception:
                    pass

                snap_png, _ = await self.save_debug_snapshot(page, f"err_no_train_{train_number}_{from_station}_{to_station}")
                raise GraphicReservationUnavailableError(
                    f"Nie znaleziono pociągu {train_number} na liście połączeń dla relacji {from_station} -> {to_station} po 35s oczekiwania. Zapisano {snap_png.name}"
                )

            # Step 2: Connection list (Tasks V3-17 to V3-20)
            report(70, f"Dopasowywanie pociągu {train_number} i wybór Klasy 2...")

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
                "li[class*='TripPropositionDesktop_tripPropositionDesktop__container'], li[class*='TripProposition'], .connection-row, tr.connection, .connection-card"
            )
            card_count = await connection_cards.count()

            chosen_card = None
            if card_count > 0:
                for idx in range(card_count):
                    card = connection_cards.nth(idx)
                    card_text = await card.inner_text()
                    if train_clean_num in card_text:
                        chosen_card = card
                        logger.info(f"[TRAIN MATCH] Dopasowano pociąg po numerze {train_clean_num} w kafelku #{idx+1}")
                        break
                if not chosen_card:
                    for idx in range(card_count):
                        card = connection_cards.nth(idx)
                        card_text = await card.inner_text()
                        if clean_dep_time in card_text:
                            chosen_card = card
                            logger.info(f"[TRAIN MATCH] Dopasowano pociąg po godzinie {clean_dep_time} w kafelku #{idx+1}")
                            break
                if not chosen_card:
                    chosen_card = connection_cards.first
                    logger.info("[TRAIN MATCH] Używam pierwszego kafelka z listy połączeń")

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
                    if await exp_btn.is_visible(timeout=1500):
                        if await self.safe_click(exp_btn, timeout=1500):
                            await asyncio.sleep(0.15)
                            break
                except Exception:
                    pass

            # Step 3: Class 1 or 2 selection
            await self.check_and_handle_error_radar(page, f"wybor_klasy_{ticket_class}")
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

            # Early exit check if network JSON already returned seatmap
            if intercepted_carriages or (captured_network_json and self._extract_seats_from_json_list(captured_network_json, ticket_class=ticket_class)):
                carriages_data = intercepted_carriages or self._extract_seats_from_json_list(captured_network_json, ticket_class=ticket_class)
                logger.info(f"[NETWORK EARLY EXIT] Przechwycono dane z API JSON dla {len(carriages_data)} wagonów przed schematem! Pomijam karuzelę DOM.")
                report(95, f"[JSON] Odczytano wolne miejsca w {len(carriages_data)} wagonach.")
                return carriages_data

            # Step 4: Your Journey & open seatmap (Tasks V3-22 to V3-25)
            report(75, "Konfiguracja podróży i otwieranie schematu wagonów...")
            await self.dismiss_intermediate_modals(page)
            await self.check_and_handle_error_radar(page, "twoja_podroz")

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
                "button[class*='PreferedPlace']",
                "button:has-text('Wybierz miejsce')",
                "button:has-text('Wybierz miejsce ze schematu')",
                "button:has-text('Wskazanie miejsca')",
                "button:has-text('Wskaż miejsce')",
                "button:has-text('schematu')",
                "a:has-text('Wybierz miejsce')",
            ]

            schema_clicked = False
            # Wait for Step 4 "Twoja podróż" to render and button to become visible
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

            # Early exit check right after clicking seatmap button
            if intercepted_carriages or (captured_network_json and self._extract_seats_from_json_list(captured_network_json)):
                carriages_data = intercepted_carriages or self._extract_seats_from_json_list(captured_network_json)
                logger.info(f"[NETWORK EARLY EXIT] Przechwycono dane z API JSON dla {len(carriages_data)} wagonów po kliknięciu schematu! Pomijam karuzelę DOM.")
                report(95, f"[JSON] Odczytano wolne miejsca w {len(carriages_data)} wagonach.")
                return carriages_data

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

            # Detect seatmap container / frame (strictly exclude cookiebot/consent/blank frames)
            target_container: Union[Page, Frame] = page
            for frame in page.frames:
                f_url = frame.url.lower()
                if frame != page and not any(skip in f_url for skip in ["cookiebot", "consent", "blank", "google", "doubleclick"]):
                    f_title = ""
                    try:
                        f_title = (await frame.title()).lower()
                    except Exception:
                        pass
                    if any(k in f_title for k in ["schemat", "miejsce", "wagon", "klasa"]) or any(k in f_url for k in ["schemat", "seat", "wagon", "carriage", "resource", "srcdoc"]):
                        target_container = frame
                        logger.info(f"[IFRAME] Wykryto ramkę schematu wagonu: title='{f_title}' url='{frame.url}'")
                        break

            # Fast wait for seatmap elements or JSON response (max 8s)
            start_wait = time.time()
            seatmap_found = False
            while time.time() - start_wait < 8.0:
                if intercepted_carriages or (captured_network_json and self._extract_seats_from_json_list(captured_network_json)):
                    carriages_data = intercepted_carriages or self._extract_seats_from_json_list(captured_network_json)
                    logger.info(f"[NETWORK EARLY EXIT] Odebrano JSON miejsc podczas oczekiwania ({len(carriages_data)} wagonów). Natychmiastowe zakończenie!")
                    report(95, f"[JSON] Odczytano wolne miejsca w {len(carriages_data)} wagonach.")
                    return carriages_data

                for f in page.frames:
                    f_url = f.url.lower()
                    if f != page and not any(skip in f_url for skip in ["cookiebot", "consent", "blank", "google", "doubleclick"]):
                        if await f.locator("rect.place-rect, image.place, g[data-class], g[aria-label*='miejsce' i], button.seat-overlay-button").count() > 0:
                            target_container = f
                            seatmap_found = True
                            break
                if seatmap_found:
                    break

                test_seats = page.locator("div[class*='Carriage_carriageSlide'], button.carriagesBtnNext")
                if await test_seats.count() > 0:
                    seatmap_found = True
                    break

                await self.dismiss_intermediate_modals(page)
                if await self.check_and_handle_error_radar(page, "render_schematu"):
                    continue
                await asyncio.sleep(0.1)

            # Step 5: Seat extraction across target class carriages (90%)
            report(90, f"Odczytywanie wolnych miejsc z wagonów Klasy {ticket_class}...")
            carriages_data: Dict[str, List[str]] = {}

            # Priority 1: JSON interception (Task V3-26 & Speedup #4)
            if intercepted_carriages:
                carriages_data = intercepted_carriages
            elif captured_network_json:
                carriages_data = self._extract_seats_from_json_list(captured_network_json, ticket_class=ticket_class)

            if carriages_data:
                logger.info(f"[NETWORK EARLY EXIT] Znaleziono {len(carriages_data)} wagonów w API JSON. Pomijam karuzelę DOM!")
                report(95, f"[JSON] Odczytano wolne miejsca w {len(carriages_data)} wagonach.")
                return carriages_data

            # Priority 2: DOM & Iframe parsing across target class carriages
            if not carriages_data:
                carriages_data = await self._extract_seats_from_eic2_dom(page, target_container, ticket_class=ticket_class)

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
        ticket_class: int = TARGET_CLASS,
    ) -> Dict[str, List[str]]:
        """
        Extracts available seats across all carriages matching ticket_class using snappy carousel switching
        and <5ms in-page SVG evaluation.
        """
        carriages: Dict[str, List[str]] = {}

        # Look for wagon slides in e-IC 2.0 carousel
        slide_locators = page.locator("div[class*='Carriage_carriageSlide']")
        slide_count = await slide_locators.count()

        if slide_count > 0:
            slides_meta = []
            try:
                if ticket_class == 2:
                    slides_meta = await page.evaluate(CAROUSEL_METADATA_JS)
                else:
                    slides_meta = await page.evaluate(CAROUSEL_METADATA_JS, ticket_class)
            except Exception as e:
                logger.debug(f"[CAROUSEL] evaluate metadata failed ({e})")

            # Fast path: in-page extracted metadata for all slides
            if slides_meta and isinstance(slides_meta, list):
                for item in slides_meta:
                    if item.get("skip"):
                        continue
                    idx = item.get("index", 0)
                    c_no = str(item.get("carriageNumber", "")).strip()
                    if not c_no:
                        continue

                    logger.info(f"[CAROUSEL] Przetwarzanie wagonu Klasy 2: Nr {c_no} (slajd #{idx+1})...")
                    slide = slide_locators.nth(idx)

                    # Snappy slide navigation: scroll & click in single JS call if not already active
                    is_active = item.get("isActive", False)
                    if not is_active:
                        if not await slide.is_visible():
                            carousel_next = page.locator("button.carriagesBtnNext, button[aria-label*='w prawo']").first
                            for _ in range(3):
                                try:
                                    if await carousel_next.is_visible(timeout=200) and await carousel_next.is_enabled():
                                        await self.safe_click(carousel_next, timeout=500)
                                        await asyncio.sleep(0.08)
                                        if await slide.is_visible():
                                            break
                                except Exception:
                                    pass

                        try:
                            await slide.evaluate("el => { el.scrollIntoView({ inline: 'center', block: 'nearest' }); const btn = el.querySelector('div[aria-label*=\"Wybierz wagon numer\"], div[class*=\"Carriage_carriageBox\"], div[role=\"button\"]') || el; btn.click(); }")
                        except Exception:
                            await self.safe_click(slide, timeout=1200)

                        # Quick wait for active class (up to 500ms, polling every 50ms)
                        activated = False
                        for _ in range(10):
                            cls = (await slide.get_attribute("class")) or ""
                            if "swiper-slide-active" in cls:
                                activated = True
                                break
                            await asyncio.sleep(0.05)

                        if not activated:
                            click_target = slide.locator(
                                "div[aria-label*='Wybierz wagon numer'], div[class*='Carriage_carriageBox'], div[role='button']"
                            ).first
                            if await click_target.count() == 0:
                                click_target = slide
                            await self.safe_click(click_target, timeout=1000)

                        await asyncio.sleep(0.15)

                    # Locate current seatmap iframe (strictly exclude Cookiebot, Consent, and Blank frames)
                    seat_frame = None
                    for f in page.frames:
                        f_url = f.url.lower()
                        if f != page and not any(skip in f_url for skip in ["cookiebot", "consent", "blank", "google", "doubleclick"]):
                            if any(k in f_url for k in ["resource", "schemat", "seat", "wagon", "srcdoc"]):
                                seat_frame = f
                                break

                    if seat_frame:
                        try:
                            await seat_frame.locator("rect.place-rect, image.place, g[data-class]").first.wait_for(state="attached", timeout=1200)
                        except Exception:
                            pass
                    else:
                        await asyncio.sleep(0.15)

                    # Extract seats in < 5ms via in-page evaluate()
                    seats = await self._find_seats_in_iframe(seat_frame or container, ticket_class=ticket_class)
                    if not seats:
                        await self.check_and_handle_error_radar(page, context_label=f"wagon_{c_no}")
                        seats = await self._find_seats_in_iframe(seat_frame or container, ticket_class=ticket_class)

                    logger.info(f"[SEATS] Wagon {c_no}: odczytano {len(seats)} wolnych miejsc: {seats[:10]}...")
                    carriages[c_no] = seats
            else:
                # Fallback: sequential DOM parsing for slides
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

                    is_class_1 = ("klasa 1" in klasa_txt or "kl. 1" in klasa_txt or "klasa 1" in slide_txt) and ("klasa 2" not in klasa_txt and "klasa 2" not in slide_txt)
                    is_service_car = any(skip in klasa_txt or skip in slide_txt for skip in ["wars", "restaurac", "sypialn", "kuszet"])
                    if is_class_1 or is_service_car:
                        continue

                    c_no = ""
                    try:
                        box_el = slide.locator("div[aria-label*='wagon numer'], div[aria-label*='Wagon'], [aria-label*='wagon']").first
                        if await box_el.count() > 0:
                            box_aria = (await box_el.get_attribute("aria-label")) or ""
                            m = re.search(r'wagon\s+(?:numer\s+)?(\d+)', box_aria, re.IGNORECASE) or re.search(r'numer\s+(\d+)', box_aria, re.IGNORECASE)
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
                                m = re.search(r'\d+', num_txt)
                                if m:
                                    c_no = m.group(0)
                        except Exception:
                            pass

                    if not c_no and slide_txt:
                        m = re.search(r'wagon\s*(\d+)', slide_txt, re.IGNORECASE) or re.search(r'\b(\d+)\b', slide_txt)
                        if m:
                            c_no = m.group(1)

                    if not c_no:
                        continue

                    logger.info(f"[CAROUSEL] Przetwarzanie wagonu Klasy 2: Nr {c_no} (slajd #{idx+1})...")
                    try:
                        await slide.evaluate("el => el.scrollIntoView({ inline: 'center', block: 'nearest' })")
                    except Exception:
                        pass

                    click_target = slide.locator(
                        "div[aria-label*='Wybierz wagon numer'], div[class*='Carriage_carriageBox'], div[role='button']"
                    ).first
                    if await click_target.count() == 0:
                        click_target = slide
                    await self.safe_click(click_target, timeout=1200)

                    seat_frame = None
                    for f in page.frames:
                        f_url = f.url.lower()
                        if f != page and not any(skip in f_url for skip in ["cookiebot", "consent", "blank", "google", "doubleclick"]):
                            if any(k in f_url for k in ["resource", "schemat", "seat", "wagon", "srcdoc"]):
                                seat_frame = f
                                break

                    seats = await self._find_seats_in_iframe(seat_frame or container)
                    carriages[c_no] = seats
        else:
            seats = await self._find_seats_in_iframe(container)
            if seats:
                carriages["2"] = seats

        # Dismiss modal
        try:
            close_btn = page.locator("button[class*='css-nk1pxf']:has-text('Zamknij'), button[aria-label='Zamknij'], button:has-text('Zamknij')").first
            if await close_btn.is_visible(timeout=400):
                await self.safe_click(close_btn, timeout=600)
            else:
                await page.keyboard.press("Escape")
        except Exception:
            pass

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
        Filters out wrong-class seats if wagon is a mixed-class carriage.
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

        # Fallback: Python locator iteration if evaluate() yielded nothing
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
                        if "first class" in d_class.lower() or ("klasa 1" in aria.lower() and "klasa 2" not in aria.lower()):
                            continue
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
                        if "wolne" not in aria.lower():
                            continue
                        if "klasa 1" in aria.lower() and "klasa 2" not in aria.lower():
                            continue
                        place_val = (await b_elem.get_attribute("data-place")) or ""
                        if place_val and place_val.isdigit():
                            free_seats.append(place_val)
                        else:
                            m = re.search(r'miejsce\s+(\d+)', aria, re.IGNORECASE)
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
        has_warmed_session: bool = False,
    ) -> Dict[str, List[str]]:
        """
        Scans a single segment with SQLite caching (TTL 10 min).
        Reuses page if provided by LiveTrainScanner.
        """
        cache_key = f"seatmap_{train_number}_{travel_date}_{from_station}_{to_station}_c{ticket_class}"
        if use_cache:
            cached = get_cached_item(cache_key, ttl_seconds=self.ttl_seconds)
            if cached and isinstance(cached, dict):
                logger.info(f"[CACHE HIT] Seatmap for segment {from_station} -> {to_station} (Klasa {ticket_class})")
                return cached

        # Ultra-short jitter to stagger concurrent requests cleanly
        await asyncio.sleep(random.uniform(0.05, 0.15))

        if page is not None:
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
                    )
                    if carriages:
                        set_cached_item(cache_key, carriages)
                        return carriages
                    if attempt == 0:
                        logger.info(f"[RETRY] Brak danych dla {from_station} -> {to_station}, ponawiam próbę...")
                        await asyncio.sleep(1.5)
                except GraphicReservationUnavailableError as e:
                    logger.warning(f"[SCAN] Rezerwacja graficzna niedostępna dla {from_station} -> {to_station}: {e}")
                    if "brak_miejsc" in str(e).lower() or "wyprzedan" in str(e).lower():
                        return {}
                    if attempt == 0:
                        await asyncio.sleep(2.0)
                    else:
                        return {}
                except Exception as e:
                    logger.warning(f"[SCAN] Błąd w próbie {attempt+1} dla {from_station} -> {to_station}: {e}")
                    if attempt == 0:
                        await asyncio.sleep(2.0)
                    else:
                        await self.save_debug_snapshot(page, f"err_{from_station}_{to_station}")
                        return {}
            return {}

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
                )
                if carriages:
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
    ) -> FullTrainAvailabilityMap:
        """
        1. Obtains all intermediate stops for the train (in worker thread so event loop stays responsive).
        2. Warms up cookies/Cookiebot once and transfers storage_state to isolated per-segment contexts.
        3. Scans elementary segments concurrently with monotonic global progress reporting.
        4. Constructs FullTrainAvailabilityMap and saves to logs/last_scan_map.txt.
        """
        self._notify(f"[5%] Rozpoczynanie skanowania pociągu {train_number} (Klasa {ticket_class})...")

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
                context = await self.scraper.create_context(browser)

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

                # Warmup session on origin and capture cookies/localStorage for all segment contexts
                try:
                    self._notify("[12%] Inicjalizacja sesji i ciasteczek...")
                    warmup_page = await self.scraper.init_page(context)
                    await warmup_page.goto("https://ebilet.intercity.pl/", wait_until="domcontentloaded", timeout=15000)
                    await self.scraper.close_cookie_banners(warmup_page)
                    await asyncio.sleep(0.1)
                    try:
                        state = await context.storage_state()
                        if isinstance(state, dict) and state:
                            warmup_storage_state = state
                    except Exception:
                        pass
                    await warmup_page.close()
                    await context.close()
                except Exception as e:
                    logger.warning(f"[WARMUP] Ostrzeżenie podczas inicjalizacji sesji: {e}")

                sem = asyncio.Semaphore(self.concurrency)
                completed_count = 0
                segment_progress = [0] * num_segments
                completed_lock = asyncio.Lock()

                def compute_global_pct() -> int:
                    avg = sum(segment_progress) / max(1, num_segments)
                    return min(98, max(15, 15 + int(avg * 0.83)))

                async def scan_single_segment(idx: int) -> SegmentAvailabilityMap:
                    nonlocal completed_count
                    s_from = stops[idx]
                    s_to = stops[idx + 1]
                    seg_dep_time = s_from.departure_time or s_from.arrival_time or departure_time or "12:00"

                    if idx > 0:
                        await asyncio.sleep(min(idx * 0.1, 0.4))

                    async with sem:
                        self._notify(
                            f"[{compute_global_pct()}%] Skanowanie odcinka {idx + 1}/{num_segments}: "
                            f"{s_from.name} -> {s_to.name} ({seg_dep_time})..."
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
                        carriages = {}
                        try:
                            carriages = await self.scraper.scan_segment(
                                from_station=s_from.name,
                                to_station=s_to.name,
                                travel_date=travel_date,
                                departure_time=seg_dep_time,
                                train_number=train_number,
                                ticket_class=ticket_class,
                                progress_cb=seg_cb,
                                page=seg_page,
                                has_warmed_session=bool(warmup_storage_state),
                            )
                        except GraphicReservationUnavailableError as e:
                            logger.warning(f"[SCANNER] Brak wolnych miejsc lub schematu dla odcinka {s_from.name} -> {s_to.name}: {e}")
                            carriages = {}
                        except Exception as e:
                            logger.error(f"[SCANNER] Nieoczekiwany błąd podczas skanowania odcinka {s_from.name} -> {s_to.name}: {e}")
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

                        c_summary = ", ".join(c_summary_parts) if c_summary_parts else "Brak wolnych miejsc"
                        self._notify(
                            f"[{global_pct}%] Ukończono odcinek {idx + 1}/{num_segments} ({curr_done}/{num_segments} gotowe): "
                            f"{s_from.name} -> {s_to.name}. Wolne miejsca: {c_summary}"
                        )

                        return SegmentAvailabilityMap(
                            segment_index=idx,
                            from_station=s_from.name,
                            to_station=s_to.name,
                            departure_time=seg_dep_time,
                            carriages=carriages,
                        )

                tasks = [scan_single_segment(i) for i in range(num_segments)]
                segments_map = list(await asyncio.gather(*tasks))

            finally:
                if unhide_task:
                    unhide_task.cancel()
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
