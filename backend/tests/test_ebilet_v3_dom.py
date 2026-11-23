"""
Unit and integration tests for Plan V3 / V3.5 DOM and seatmap parsing.
Uses the real HTML snapshots from 'screeny i html krok po kroku strony icc'.
"""
import bs4
import re
from pathlib import Path
import pytest

from backend.app.scraper.ebilet_seat_scraper import EbiletSeatScraper

SNAPSHOTS_DIR = Path("screeny i html krok po kroku strony icc")


class TestEbiletV3RealDOM:
    """Tests parsing on the user-provided real e-IC 2.0 HTML snapshots."""

    @pytest.fixture
    def scraper(self):
        return EbiletSeatScraper(headless=True)

    def test_step2_connections_list_parsing(self):
        """Verifies finding train cards and buy buttons on real Step 2 HTML."""
        fpath = SNAPSHOTS_DIR / "2-e-IC2 - Internetowa rezerwacja miejsc.html"
        assert fpath.exists()
        soup = bs4.BeautifulSoup(fpath.read_text(encoding="utf-8", errors="ignore"), "html.parser")
        
        cards = soup.select("li[class*='TripPropositionDesktop_tripPropositionDesktop__container']")
        assert len(cards) > 0
        
        # Train card should contain departure time 12:17 and buy button
        first_card = cards[0]
        card_text = first_card.text
        assert "12:17" in card_text
        
        buy_btn = first_card.select_one("button[data-testid='OutlineButton'], button[aria-label*='Kup bilet']")
        assert buy_btn is not None
        assert "Kup bilet" in buy_btn.get("aria-label", "")

    def test_step3_class_2_selection(self):
        """Verifies finding the Class 2 selection button on real Step 3 HTML."""
        fpath = SNAPSHOTS_DIR / "3-e-IC2 - Internetowa rezerwacja miejsc.html"
        assert fpath.exists()
        soup = bs4.BeautifulSoup(fpath.read_text(encoding="utf-8", errors="ignore"), "html.parser")
        
        c2_btns = [b for b in soup.find_all("button") if "2 klas" in b.text.lower() or "2 klas" in str(b.get("aria-label", "")).lower()]
        assert len(c2_btns) > 0
        btn = c2_btns[0]
        assert "Wybierz 2 klasę" in btn.text or "Wybierz 2 klasę" in btn.get("aria-label", "")

    def test_step4_schema_button(self):
        """Verifies finding the button opening seatmap on real Step 4 HTML."""
        fpath = SNAPSHOTS_DIR / "4-e-IC2 - Internetowa rezerwacja miejsc.html"
        assert fpath.exists()
        soup = bs4.BeautifulSoup(fpath.read_text(encoding="utf-8", errors="ignore"), "html.parser")
        
        schema_btns = [b for b in soup.find_all("button") if "miejsce" in b.text.lower() or "schemat" in b.text.lower()]
        assert len(schema_btns) > 0
        assert any("Wybierz miejsce" in b.text for b in schema_btns)

    def test_step6_wagon_slides_and_class_filtering(self):
        """Verifies identifying all 8 wagons and filtering Class 2 sitting cars."""
        fpath = SNAPSHOTS_DIR / "6-e-IC2 - Internetowa rezerwacja miejsc.html"
        assert fpath.exists()
        soup = bs4.BeautifulSoup(fpath.read_text(encoding="utf-8", errors="ignore"), "html.parser")
        
        slides = soup.select("div[class*='Carriage_carriageSlide']")
        assert len(slides) == 8

        class2_wagons = []
        for s in slides:
            k_el = s.select_one("p[class*='Carriage_carriageKlasa']")
            n_el = s.select_one("p[class*='Carriage_carriageNumberDesktop']")
            klasa = k_el.text.strip() if k_el else ""
            num = n_el.text.strip() if n_el else ""
            if "klasa 2" in klasa.lower():
                m = re.search(r'\d+', num)
                if m:
                    class2_wagons.append(m.group(0))

        # Wagons 3, 4, 5, 6, 7, 8 are Class 2 (Wagons 1 & 2 are Class 1)
        assert class2_wagons == ["3", "4", "5", "6", "7", "8"]

    def test_seatmap_iframe_free_seats_extraction(self):
        """Verifies extracting exact available seat numbers from saved seatmap iframe."""
        fpath = SNAPSHOTS_DIR / "6-e-IC2 - Internetowa rezerwacja miejsc_files" / "saved_resource(1).html"
        assert fpath.exists()
        soup = bs4.BeautifulSoup(fpath.read_text(encoding="utf-8", errors="ignore"), "html.parser")
        
        free_groups = [g for g in soup.find_all("g") if "wolne" in str(g.get("aria-label", "")).lower()]
        assert len(free_groups) == 7

        seats = []
        for g in free_groups:
            aria = g.get("aria-label", "")
            t_el = g.find("text")
            num = t_el.text.strip() if t_el else ""
            if not num:
                m = re.search(r'miejsce\s+(\d+)', aria, re.IGNORECASE)
                if m: num = m.group(1)
            seats.append(num)

        assert sorted(seats, key=lambda x: int(x)) == ["14", "17", "24", "28", "38", "43", "48"]

    def test_json_recursive_extractor(self, scraper):
        """Verifies _extract_seats_from_json_list with nested PKP IC payload."""
        payload = [{
            "data": {
                "carriages": [
                    {
                        "number": "3",
                        "class": "2",
                        "type": "B10bmnouz",
                        "seats": [
                            {"number": "14", "status": "FREE"},
                            {"number": "17", "status": "AVAILABLE"},
                            {"number": "18", "status": "OCCUPIED"},
                        ]
                    },
                    {
                        "number": "1",
                        "class": "1",
                        "seats": [{"number": "11", "status": "FREE"}]
                    },
                    {
                        "number": "5",
                        "class": "2",
                        "type": "WR",  # Restaurant car to skip
                        "seats": [{"number": "21", "status": "FREE"}]
                    }
                ]
            }
        }]

        result = scraper._extract_seats_from_json_list(payload)
        # Carriage 1 (Class 1) and 5 (WR) must be omitted
        assert "1" not in result
        assert "5" not in result
        assert "3" in result
        assert result["3"] == ["14", "17"]

    def test_wagon_aria_label_extraction_step6(self):
        """Verifies extracting wagon number from aria-label on Step 6 slides."""
        fpath = SNAPSHOTS_DIR / "6-e-IC2 - Internetowa rezerwacja miejsc.html"
        assert fpath.exists()
        soup = bs4.BeautifulSoup(fpath.read_text(encoding="utf-8", errors="ignore"), "html.parser")
        slides = soup.select("div[class*='Carriage_carriageSlide']")

        extracted_wagons = []
        for s in slides:
            box = s.select_one("div[aria-label*='wagon numer'], div[aria-label*='Wagon']")
            if box:
                aria = box.get("aria-label", "")
                m = re.search(r'wagon\s+(?:numer\s+)?(\d+)', aria, re.IGNORECASE)
                if m:
                    extracted_wagons.append(m.group(1))

        assert extracted_wagons == ["1", "2", "3", "4", "5", "6", "7", "8"]

    @pytest.mark.asyncio
    async def test_safe_click_fallback_to_js(self, scraper):
        """Verifies safe_click falls back to evaluate('el => el.click()') when click() fails."""
        from unittest.mock import AsyncMock

        mock_locator = AsyncMock()
        mock_locator.click.side_effect = Exception("Element is intercepted")
        mock_locator.evaluate.return_value = None

        result = await scraper.safe_click(mock_locator, timeout=500)
        assert result is True
        mock_locator.click.assert_called_once()
        mock_locator.evaluate.assert_called_once_with("el => el.click()")

    @pytest.mark.asyncio
    async def test_error_radar_detects_server_glitch(self, scraper):
        """Verifies error radar detects 'Ups...' glitch and triggers reload."""
        from unittest.mock import AsyncMock, MagicMock

        mock_page = MagicMock()
        mock_loc = MagicMock()
        mock_loc.is_visible = AsyncMock(return_value=True)
        mock_loc.text_content = AsyncMock(return_value="Ups! Coś poszło nie tak.")
        mock_page.locator.return_value.first = mock_loc
        mock_page.reload = AsyncMock()

        scraper.close_cookie_banners = AsyncMock()
        scraper.dismiss_intermediate_modals = AsyncMock()

        handled = await scraper.check_and_handle_error_radar(mock_page, context_label="test")
        assert handled is True
        mock_page.reload.assert_called_once()

    @pytest.mark.asyncio
    async def test_scanner_non_fatal_segment_error(self):
        """Verifies that a sold-out segment does not abort remaining segments in scan_full_train."""
        from unittest.mock import AsyncMock, patch
        from backend.app.scraper.ebilet_seat_scraper import LiveTrainScanner
        from backend.app.scraper.ic_client import GraphicReservationUnavailableError
        from backend.app.scraper.route_resolver import StationStop

        scanner = LiveTrainScanner(headless=True)
        mock_stops = [
            StationStop(seq=1, eva_code="5100028", name="Koluszki", arrival_min=0, departure_min=0, departure_time="14:40"),
            StationStop(seq=2, eva_code="5100049", name="Piotrków Trybunalski", arrival_min=20, departure_min=21, departure_time="15:01"),
            StationStop(seq=3, eva_code="5100057", name="Radomsko", arrival_min=45, departure_min=46, departure_time="15:26"),
        ]

        async def mock_scan_segment(**kwargs):
            if kwargs["from_station"] == "Koluszki":
                raise GraphicReservationUnavailableError("BRAK_MIEJSC_KLASA_2")
            return {"3": ["15", "16"]}

        scanner.scraper.scan_segment = AsyncMock(side_effect=mock_scan_segment)

        with patch("backend.app.scraper.ebilet_seat_scraper.resolve_real_route", return_value=mock_stops), \
             patch("backend.app.scraper.ebilet_seat_scraper.filter_and_merge_stops", return_value=mock_stops), \
             patch("backend.app.scraper.ebilet_seat_scraper.async_playwright") as mock_pw:

            mock_browser = AsyncMock()
            mock_ctx = AsyncMock()
            mock_page = AsyncMock()
            mock_browser.new_context.return_value = mock_ctx
            mock_ctx.new_page.return_value = mock_page

            mock_pw_inst = AsyncMock()
            mock_pw_inst.chromium.connect_over_cdp.side_effect = Exception("No CDP")
            mock_pw_inst.chromium.launch.return_value = mock_browser
            mock_pw.return_value.__aenter__.return_value = mock_pw_inst

            full_map = await scanner.scan_full_train(
                from_station="Koluszki",
                to_station="Radomsko",
                travel_date="2026-11-20",
                train_number="IC 1646",
            )

            assert len(full_map.segments) == 2
            assert full_map.segments[0].carriages == {}
            assert full_map.segments[1].carriages == {"3": ["15", "16"]}

    @pytest.mark.asyncio
    async def test_find_seats_in_iframe_evaluates_fast_js(self, scraper):
        """Verifies _find_seats_in_iframe uses in-page evaluate() and sorts seats numerically."""
        from unittest.mock import AsyncMock, MagicMock
        from backend.app.scraper.ebilet_seat_scraper import SVG_SEATS_EXTRACTOR_JS

        mock_frame = MagicMock()
        mock_frame.url = "https://ebilet.intercity.pl/schemat.html"
        mock_frame.title = AsyncMock(return_value="Schemat wagonu")
        mock_frame.evaluate = AsyncMock(return_value=["48", "14", "28", "17", "43", "24", "38"])

        mock_page = MagicMock()
        mock_page.frames = [mock_frame]
        mock_page.url = "https://ebilet.intercity.pl/schemat.html"

        seats = await scraper._find_seats_in_iframe(mock_frame)
        assert seats == ["14", "17", "24", "28", "38", "43", "48"]
        mock_frame.evaluate.assert_called_once_with(SVG_SEATS_EXTRACTOR_JS)

    def test_speedup_js_scripts_syntax(self):
        """Verifies that SVG_SEATS_EXTRACTOR_JS and CAROUSEL_METADATA_JS are valid non-empty scripts."""
        from backend.app.scraper.ebilet_seat_scraper import SVG_SEATS_EXTRACTOR_JS, CAROUSEL_METADATA_JS, sanitize_filename
        assert "wolne" in SVG_SEATS_EXTRACTOR_JS
        assert "klasa 1" in SVG_SEATS_EXTRACTOR_JS
        assert "klasa 2" in SVG_SEATS_EXTRACTOR_JS
        assert "Carriage_carriageSlide" in CAROUSEL_METADATA_JS
        assert "swiper-slide-active" in CAROUSEL_METADATA_JS
        assert sanitize_filename("Bielsko-Biała Gł. / IC 1646") == "Bielsko-Bia_a_G_._IC_1646"

    def test_json_recursive_extractor_class_1(self, scraper):
        """Verifies _extract_seats_from_json_list supports ticket_class=1."""
        payload = [{
            "carriages": [
                {"number": "1", "class": "1", "seats": [{"number": "11", "status": "FREE"}]},
                {"number": "3", "class": "2", "seats": [{"number": "22", "status": "FREE"}]},
            ]
        }]
        res_c1 = scraper._extract_seats_from_json_list(payload, ticket_class=1)
        assert "1" in res_c1 and res_c1["1"] == ["11"]
        assert "3" not in res_c1

    def test_json_recursive_extractor_no_empty_metadata_carriages(self, scraper):
        """Verifies metadata-only carriage objects without seats do not emit empty {'3': []} dicts."""
        payload = [{"wagony": [{"nr": "3", "klasa": "2"}, {"nr": "4", "klasa": "2", "seats": []}]}]
        assert scraper._extract_seats_from_json_list(payload, ticket_class=2) == {}

    def test_parse_grm_wagon_svg_real_snapshot(self):
        """Verifies parse_grm_wagon_svg on the real saved e-IC 2.0 SVG snapshot."""
        from backend.app.scraper.ebilet_seat_scraper import parse_grm_wagon_svg

        fpath = SNAPSHOTS_DIR / "6-e-IC2 - Internetowa rezerwacja miejsc_files" / "saved_resource(1).html"
        assert fpath.exists()
        raw_html = fpath.read_text(encoding="utf-8", errors="ignore")
        seats = parse_grm_wagon_svg(raw_html, ticket_class=2)
        assert seats == ["14", "17", "24", "28", "38", "43", "48"]

    def test_parse_grm_wagon_svg_class_filtering_and_prm_exclusion(self):
        """Verifies XML SVG parser filters by Class 1/2 and excludes wheelchair-only PRM seats."""
        from backend.app.scraper.ebilet_seat_scraper import parse_grm_wagon_svg

        svg_xml = """<?xml version="1.0" encoding="UTF-8"?>
        <svg xmlns="http://www.w3.org/2000/svg" schema="1322">
          <g id="s11" data-class="first class" aria-label="Miejsce 11 klasa 1, okno, Wolne, niewybrane">
            <text>11</text>
          </g>
          <g id="s12" data-class="first class" aria-label="Miejsce 12 klasa 1, Niedostepne, niewybrane">
            <text>12</text>
          </g>
          <g id="s61" data-class="second class" aria-label="Miejsce 61 klasa 2, Miejsce dla osoby na wózku, Wolne, niewybrane">
            <text>61</text>
          </g>
          <g id="s62" data-class="second class" aria-label="Miejsce 62 klasa 2, Miejsce dla opiekuna osoby na wózku, Wolne, niewybrane">
            <text>62</text>
          </g>
          <g id="s63" data-class="second class" aria-label="Miejsce 63 klasa 2, miejsce na wózku inwalidzkim, Wolne">
            <text>63</text>
          </g>
          <g id="s75" data-class="second class" aria-label="Miejsce 75 klasa 2, okno, Wolne, niewybrane">
            <text>75</text>
          </g>
          <g id="s82" data-class="second class" aria-label="Miejsce 82 klasa 2, korytarz, Wolne, niewybrane">
          </g>
          <g id="s88" data-class="second class" aria-label="Miejsce 88 klasa 2, Niedostepne, niewybrane">
            <text>88</text>
          </g>
        </svg>"""

        # Class 2: must include standard free Class 2 seats (75, 82 via aria fallback) and exclude Class 1 (11), occupied (88), and PRM wheelchair seats (61, 62, 63)
        assert parse_grm_wagon_svg(svg_xml, ticket_class=2) == ["75", "82"]
        # Class 1: must include only free Class 1 seat (11)
        assert parse_grm_wagon_svg(svg_xml, ticket_class=1) == ["11"]

    def test_select_grm_target_wagons(self, scraper):
        """Verifies _select_grm_target_wagons filters by class, skips unavailable and service cars."""
        sklad_data = {
            "klasa1": [1, 2],
            "klasa2": [2, 3, 4, 5, 6],
            "wagonyNiedostepne": [4],
            "wagonySchemat": {"1": "1321", "2": "1322", "3": "1323", "4": "1324", "5": "1325", "6": "1326"},
            "wagony": [{"nr": "5", "typ": "WR"}],
        }
        c2_targets, has_c2 = scraper._select_grm_target_wagons(sklad_data, ticket_class=2)
        assert has_c2 is True
        assert c2_targets == [("2", "1322"), ("3", "1323"), ("6", "1326")]

        c1_targets, has_c1 = scraper._select_grm_target_wagons(sklad_data, ticket_class=1)
        assert has_c1 is True
        assert c1_targets == [("1", "1321"), ("2", "1322")]

    @pytest.mark.asyncio
    async def test_extract_seats_via_grm_api_deterministic_and_sold_out(self, scraper):
        """Verifies GRM API extractor parses intercepted + fetched SVGs and detects authoritative sold-out."""
        from unittest.mock import AsyncMock, MagicMock
        from backend.app.scraper.ic_client import GraphicReservationUnavailableError

        sklad_data = {
            "klasa2": [3, 7],
            "wagonyNiedostepne": [],
            "wagonySchemat": {"3": "1323", "7": "1326"},
        }
        template = {
            "prefix": "https://api-gateway.intercity.pl/grm/wagon/svg/wbnet/IC/1644",
            "suffix": "/202611241525/202611241545/5100139/5100811",
            "headers": {},
        }
        svg_w3 = '<svg><g data-class="second class" aria-label="Miejsce 81 klasa 2, Wolne"><text>81</text></g></svg>'
        svg_w7 = '<svg><g data-class="second class" aria-label="Miejsce 12 klasa 2, Wolne"><text>12</text></g><g data-class="second class" aria-label="Miejsce 61 klasa 2, dla osoby na wózku, Wolne"><text>61</text></g></svg>'

        mock_page = MagicMock()
        scraper._fetch_grm_svg_in_session = AsyncMock(return_value=svg_w3)

        result = await scraper._extract_seats_via_grm_api(
            page=mock_page,
            sklad_data=sklad_data,
            wagon_svg_template=template,
            intercepted_svg_by_wagon={"7": svg_w7},
            ticket_class=2,
        )
        # W3 has its OWN seat 81 (NOT W7's seat 12!) and W7 excludes PRM seat 61
        assert result == {"3": ["81"], "7": ["12"]}
        scraper._fetch_grm_svg_in_session.assert_called_once_with(
            mock_page,
            "https://api-gateway.intercity.pl/grm/wagon/svg/wbnet/IC/1644/3/1323/202611241525/202611241545/5100139/5100811",
            extra_headers={},
        )

        # Authoritative sold-out when all wagons have 0 free seats
        svg_empty = '<svg><g data-class="second class" aria-label="Miejsce 12 klasa 2, Niedostepne"><text>12</text></g></svg>'
        scraper._fetch_grm_svg_in_session = AsyncMock(return_value=svg_empty)
        with pytest.raises(GraphicReservationUnavailableError, match="BRAK_MIEJSC_KLASA_2"):
            await scraper._extract_seats_via_grm_api(
                page=mock_page,
                sklad_data=sklad_data,
                wagon_svg_template=template,
                intercepted_svg_by_wagon={"7": svg_empty},
                ticket_class=2,
            )

    def test_build_direct_search_url_time_shift_and_direct_eva(self, scraper):
        """Verifies -10 min query time shift (clamped at 00:00) and direct EVA passthrough."""
        from unittest.mock import patch

        with patch("backend.app.scraper.ebilet_seat_scraper.get_station_info") as mock_lookup:
            url = scraper.build_direct_search_url(
                from_station="Warszawa Zachodnia",
                to_station="Żyrardów",
                date_str="2026-11-24",
                departure_time="15:25",
                from_eva="5100067",
                to_eva="5104415",
            )
            mock_lookup.assert_not_called()
            assert "swyj=5100067" in url
            assert "sprzy=5104415" in url
            assert "time=15:15" in url
            assert "dwyj=2026-11-24" in url

            # Midnight clamping: 00:05 - 10 min -> 00:00
            url_midnight = scraper.build_direct_search_url(
                from_station="Warszawa",
                to_station="Kraków",
                date_str="24.11.2026",
                departure_time="00:05",
                from_eva="5100065",
                to_eva="5100028",
            )
            assert "time=00:00" in url_midnight
            assert "dwyj=2026-11-24" in url_midnight

        # Fallback to get_station_info when EVA is omitted
        with patch("backend.app.scraper.ebilet_seat_scraper.get_station_info", side_effect=[("A", "5100001"), ("B", "5100002")]) as mock_lookup:
            url_fb = scraper.build_direct_search_url("A", "B", "2026-11-24", "09:08")
            assert mock_lookup.call_count == 2
            assert "swyj=5100001" in url_fb
            assert "sprzy=5100002" in url_fb
            assert "time=08:58" in url_fb

    def test_compute_segment_date_midnight_offset(self):
        """Verifies day_offset midnight date calculation for overnight trains."""
        from backend.app.scraper.ebilet_seat_scraper import LiveTrainScanner

        assert LiveTrainScanner.compute_segment_date("2026-11-24", 0) == "2026-11-24"
        assert LiveTrainScanner.compute_segment_date("2026-11-24", 1) == "2026-11-25"
        assert LiveTrainScanner.compute_segment_date("30.11.2026", 1) == "2026-12-01"
        assert LiveTrainScanner.compute_segment_date("2026-12-31", 1) == "2027-01-01"

    def test_strict_train_card_matching(self, scraper):
        """Verifies strict card id and word-boundary matching (never matches substring or wrong train)."""
        assert scraper._card_matches_train("2026-11-24 15:25:00_2026-11-24 15:45:00_9_1644_76", "", "1644") is True
        assert scraper._card_matches_train("2026-11-24 15:25:00_2026-11-24 15:45:00_9_1644_76", "", "164") is False
        assert scraper._card_matches_train("2026-11-24 15:53:00_2026-11-24 16:15:00_9_19132_76", "IC 19132 15:53", "1644") is False
        assert scraper._card_matches_train("", "IC 1644 Łużyce 15:25", "1644") is True
        assert scraper._card_matches_train("", "IC 1644 Łużyce 15:25", "164") is False

    @pytest.mark.asyncio
    async def test_find_matching_train_card_no_wrong_train_fallback(self, scraper):
        """Verifies _find_matching_train_card returns None instead of falling back to the first card."""
        from unittest.mock import AsyncMock, MagicMock

        wrong_card = MagicMock()
        wrong_card.get_attribute = AsyncMock(return_value="2026-11-24 15:53:00_2026-11-24 16:15:00_9_19132_76")
        wrong_card.inner_text = AsyncMock(return_value="IC 19132 15:53 Warszawa -> Żyrardów")

        cards_loc = MagicMock()
        cards_loc.count = AsyncMock(return_value=1)
        cards_loc.nth.return_value = wrong_card

        mock_page = MagicMock()
        mock_page.locator.return_value = cards_loc

        matched = await scraper._find_matching_train_card(mock_page, "1644")
        assert matched is None

    @pytest.mark.asyncio
    async def test_error_radar_no_reload_inside_seatmap(self, scraper):
        """Verifies check_and_handle_error_radar(..., allow_reload=False) raises instead of reloading."""
        from unittest.mock import AsyncMock, MagicMock
        from backend.app.scraper.ic_client import GraphicReservationUnavailableError

        mock_page = MagicMock()
        mock_loc = MagicMock()
        mock_loc.is_visible = AsyncMock(return_value=True)
        mock_loc.text_content = AsyncMock(return_value="Ups! Coś poszło nie tak.")
        mock_page.locator.return_value.first = mock_loc
        mock_page.reload = AsyncMock()

        with pytest.raises(GraphicReservationUnavailableError, match="SCRAPE_ERROR"):
            await scraper.check_and_handle_error_radar(mock_page, context_label="render_schematu", allow_reload=False)
        mock_page.reload.assert_not_called()

    @pytest.mark.asyncio
    async def test_scanner_honest_statuses_eva_and_midnight_date(self):
        """Verifies scan_full_train sets OK / SOLD_OUT / SCRAPE_ERROR, passes EVAs directly, and adjusts midnight dates."""
        from unittest.mock import AsyncMock, patch
        from backend.app.scraper.ebilet_seat_scraper import LiveTrainScanner
        from backend.app.scraper.ic_client import GraphicReservationUnavailableError
        from backend.app.scraper.route_resolver import StationStop

        scanner = LiveTrainScanner(headless=True)
        stop1 = StationStop(seq=1, eva_code="5100067", name="Warszawa Zachodnia", arrival_min=0, departure_min=0, departure_time="23:40")
        stop2 = StationStop(seq=2, eva_code="5104415", name="Żyrardów", arrival_min=20, departure_min=21, departure_time="00:01")
        stop3 = StationStop(seq=3, eva_code="5100294", name="Skierniewice", arrival_min=45, departure_min=46, departure_time="00:26")
        stop4 = StationStop(seq=4, eva_code="5100028", name="Koluszki", arrival_min=70, departure_min=71, departure_time="00:51")
        # Attach midnight day_offset dynamically (also supports StationStop with explicit day_offset field)
        setattr(stop1, "day_offset", 0)
        setattr(stop2, "day_offset", 1)
        setattr(stop3, "day_offset", 1)
        setattr(stop4, "day_offset", 1)
        mock_stops = [stop1, stop2, stop3, stop4]

        captured_calls = []

        async def mock_scan_segment(**kwargs):
            captured_calls.append(kwargs)
            if kwargs["from_station"] == "Warszawa Zachodnia":
                return {"3": ["14", "17"]}
            if kwargs["from_station"] == "Żyrardów":
                raise GraphicReservationUnavailableError("BRAK_MIEJSC_KLASA_2: wyprzedane")
            raise GraphicReservationUnavailableError("SCRAPE_ERROR: Nie znaleziono pociągu IC 1644")

        scanner.scraper.scan_segment = AsyncMock(side_effect=mock_scan_segment)

        with patch("backend.app.scraper.ebilet_seat_scraper.resolve_real_route", return_value=mock_stops), \
             patch("backend.app.scraper.ebilet_seat_scraper.filter_and_merge_stops", return_value=mock_stops), \
             patch("backend.app.scraper.ebilet_seat_scraper.async_playwright") as mock_pw:

            mock_browser = AsyncMock()
            mock_ctx = AsyncMock()
            mock_page = AsyncMock()
            mock_browser.new_context.return_value = mock_ctx
            mock_ctx.new_page.return_value = mock_page

            mock_pw_inst = AsyncMock()
            mock_pw_inst.chromium.connect_over_cdp.side_effect = Exception("No CDP")
            mock_pw_inst.chromium.launch.return_value = mock_browser
            mock_pw.return_value.__aenter__.return_value = mock_pw_inst

            full_map = await scanner.scan_full_train(
                from_station="Warszawa Zachodnia",
                to_station="Koluszki",
                travel_date="2026-11-24",
                train_number="IC 1644",
            )

            assert len(full_map.segments) == 3

            # Segment 0: OK
            assert full_map.segments[0].status == "OK"
            assert full_map.segments[0].error_reason is None
            assert full_map.segments[0].carriages == {"3": ["14", "17"]}

            # Segment 1: SOLD_OUT
            assert full_map.segments[1].status == "SOLD_OUT"
            assert "BRAK_MIEJSC_KLASA_2" in (full_map.segments[1].error_reason or "")
            assert full_map.segments[1].carriages == {}

            # Segment 2: SCRAPE_ERROR
            assert full_map.segments[2].status == "SCRAPE_ERROR"
            assert "SCRAPE_ERROR" in (full_map.segments[2].error_reason or "")
            assert full_map.segments[2].carriages == {}

            # Verify direct EVA passthrough and midnight day_offset date adjustment
            by_from = {c["from_station"]: c for c in captured_calls}
            assert by_from["Warszawa Zachodnia"]["from_eva"] == "5100067"
            assert by_from["Warszawa Zachodnia"]["to_eva"] == "5104415"
            assert by_from["Warszawa Zachodnia"]["travel_date"] == "2026-11-24"

            assert by_from["Żyrardów"]["from_eva"] == "5104415"
            assert by_from["Żyrardów"]["to_eva"] == "5100294"
            assert by_from["Żyrardów"]["travel_date"] == "2026-11-25"

            assert by_from["Skierniewice"]["from_eva"] == "5100294"
            assert by_from["Skierniewice"]["to_eva"] == "5100028"
            assert by_from["Skierniewice"]["travel_date"] == "2026-11-25"

    def test_modular_exports_and_grm_url_builders(self):
        """Verifies modular split exports (dom_scripts, grm_parser) and GRM URL builders."""
        from backend.app.scraper import dom_scripts, ebilet_seat_scraper, grm_parser

        assert ebilet_seat_scraper.SVG_SEATS_EXTRACTOR_JS is dom_scripts.SVG_SEATS_EXTRACTOR_JS
        assert ebilet_seat_scraper.CAROUSEL_METADATA_JS is dom_scripts.CAROUSEL_METADATA_JS
        assert ebilet_seat_scraper.parse_grm_wagon_svg is grm_parser.parse_grm_wagon_svg
        assert ebilet_seat_scraper.select_grm_target_wagons is grm_parser.select_grm_target_wagons
        assert ebilet_seat_scraper.sanitize_filename is grm_parser.sanitize_filename

        sklad_url = "https://api-gateway.intercity.pl/grm/sklad/wbnet/IC/1644/202611241923/5100136/202611241520/5100143"
        svg_url = "https://api-gateway.intercity.pl/grm/wagon/svg/wbnet/IC/1644/8/1327,WITHOUT_COMPARTMENTS/202611241520/202611241923/5100136/5100143"
        tmpl = grm_parser.parse_grm_url_template(sklad_url)
        assert tmpl == {"host": "https://api-gateway.intercity.pl", "category": "IC", "train_no": "1644"}
        assert grm_parser.parse_grm_url_template("", svg_url) == tmpl
        assert grm_parser.parse_grm_url_template("https://example.com/other") is None

        assert grm_parser.format_grm_datetime("2026-11-24", "9:05") == "202611240905"
        assert grm_parser.format_grm_datetime("24.11.2026", "15:20") == "202611241520"
        assert grm_parser.format_grm_datetime("bad-date", "15:20") is None
        assert grm_parser.format_grm_datetime("2026-11-24", "25:99") is None

        built_sklad = grm_parser.build_grm_sklad_url(
            tmpl, from_epa="5100139", to_epa="5100811", dep_dt="202611241525", arr_dt="202611241545"
        )
        assert built_sklad == (
            "https://api-gateway.intercity.pl/grm/sklad/wbnet/IC/1644/202611241545/5100139/202611241525/5100811"
        )
        built_svg = grm_parser.build_grm_svg_url(
            tmpl,
            wagon_no="3",
            schema_id="1323",
            from_epa="5100139",
            to_epa="5100811",
            dep_dt="202611241525",
            arr_dt="202611241545",
        )
        assert built_svg == (
            "https://api-gateway.intercity.pl/grm/wagon/svg/wbnet/IC/1644/3/1323/202611241525/202611241545/5100139/5100811"
        )

    def test_is_debug_scraper_enabled_flag(self, monkeypatch):
        from backend.app.scraper.ebilet_seat_scraper import is_debug_scraper_enabled

        monkeypatch.delenv("DEBUG_SCRAPER", raising=False)
        assert is_debug_scraper_enabled() is False
        monkeypatch.setenv("DEBUG_SCRAPER", "0")
        assert is_debug_scraper_enabled() is False
        monkeypatch.setenv("DEBUG_SCRAPER", "1")
        assert is_debug_scraper_enabled() is True
        monkeypatch.setenv("DEBUG_SCRAPER", "true")
        assert is_debug_scraper_enabled() is True

    @pytest.mark.asyncio
    async def test_extract_seats_via_grm_api_parallel_missing_wagons(self, scraper):
        """Verifies _extract_seats_via_grm_api concurrently fetches all missing Class 2 wagons."""
        from unittest.mock import AsyncMock, MagicMock

        sklad_data = {
            "klasa2": [3, 5, 8],
            "wagonyNiedostepne": [],
            "wagonySchemat": {"3": "1323", "5": "1325", "8": "1327"},
        }
        template = {
            "prefix": "https://api-gateway.intercity.pl/grm/wagon/svg/wbnet/IC/1644",
            "suffix": "/202611241525/202611241545/5100139/5100811",
            "headers": {},
        }

        async def mock_fetch(page, url, extra_headers=None):
            if "/3/1323/" in url:
                return '<svg><g data-class="second class" aria-label="Miejsce 31 klasa 2, Wolne"><text>31</text></g></svg>'
            if "/5/1325/" in url:
                return '<svg><g data-class="second class" aria-label="Miejsce 52 klasa 2, Wolne"><text>52</text></g></svg>'
            return None

        svg_w8 = '<svg><g data-class="second class" aria-label="Miejsce 83 klasa 2, Wolne"><text>83</text></g></svg>'
        mock_page = MagicMock()
        scraper._fetch_grm_svg_in_session = AsyncMock(side_effect=mock_fetch)

        result = await scraper._extract_seats_via_grm_api(
            page=mock_page,
            sklad_data=sklad_data,
            wagon_svg_template=template,
            intercepted_svg_by_wagon={"8": svg_w8},
            ticket_class=2,
        )
        assert result == {"3": ["31"], "5": ["52"], "8": ["83"]}
        assert scraper._fetch_grm_svg_in_session.call_count == 2

    @pytest.mark.asyncio
    async def test_scanner_fast_path_hit_and_sold_out_fallback(self):
        """Verifies Fast-Path A->B short-circuit on available seats and fallback on SOLD_OUT."""
        from unittest.mock import AsyncMock, patch
        from backend.app.scraper.ebilet_seat_scraper import LiveTrainScanner
        from backend.app.scraper.ic_client import GraphicReservationUnavailableError
        from backend.app.scraper.route_resolver import StationStop

        mock_stops = [
            StationStop(seq=0, eva_code="5100065", name="Warszawa Centralna", arrival_min=0, departure_min=0, departure_time="15:20"),
            StationStop(seq=1, eva_code="5104415", name="Żyrardów", arrival_min=25, departure_min=26, departure_time="15:46"),
            StationStop(seq=2, eva_code="5100024", name="Koluszki", arrival_min=60, departure_min=61, departure_time="16:21"),
            StationStop(seq=3, eva_code="5100069", name="Wrocław Główny", arrival_min=240, departure_min=240, arrival_time="19:20"),
        ]

        # Subcase A: Fast-Path succeeds on A->B directly
        scanner_a = LiveTrainScanner(headless=True)
        calls_a = []

        async def mock_scan_hit(**kwargs):
            calls_a.append((kwargs["from_station"], kwargs["to_station"]))
            return {"8": ["11", "12"]}

        scanner_a.scraper.scan_segment = AsyncMock(side_effect=mock_scan_hit)

        with patch("backend.app.scraper.ebilet_seat_scraper.resolve_real_route", return_value=mock_stops), \
             patch("backend.app.scraper.ebilet_seat_scraper.filter_and_merge_stops", return_value=mock_stops), \
             patch("backend.app.scraper.ebilet_seat_scraper.async_playwright") as mock_pw:
            mock_browser = AsyncMock()
            mock_ctx = AsyncMock()
            mock_page = AsyncMock()
            mock_browser.new_context.return_value = mock_ctx
            mock_ctx.new_page.return_value = mock_page
            mock_pw_inst = AsyncMock()
            mock_pw_inst.chromium.connect_over_cdp.side_effect = Exception("No CDP")
            mock_pw_inst.chromium.launch.return_value = mock_browser
            mock_pw.return_value.__aenter__.return_value = mock_pw_inst

            fast_map = await scanner_a.scan_full_train(
                from_station="Warszawa Centralna",
                to_station="Wrocław Główny",
                travel_date="2026-11-24",
                train_number="IC 1644",
                fast_path=True,
            )

        assert fast_map.fast_path_used is True
        assert fast_map.total_route_segments == 3
        assert len(fast_map.segments) == 1
        assert fast_map.segments[0].duration_min == 240
        assert fast_map.segments[0].carriages == {"8": ["11", "12"]}
        assert calls_a == [("Warszawa Centralna", "Wrocław Główny")]

        # Subcase B: Fast-Path sees SOLD_OUT -> falls back to all 3 intermediate segments
        scanner_b = LiveTrainScanner(headless=True)
        calls_b = []

        async def mock_scan_fallback(**kwargs):
            calls_b.append((kwargs["from_station"], kwargs["to_station"]))
            if kwargs["from_station"] == "Warszawa Centralna" and kwargs["to_station"] == "Wrocław Główny":
                raise GraphicReservationUnavailableError("BRAK_MIEJSC_KLASA_2: wyprzedane")
            return {"3": ["14"]}

        scanner_b.scraper.scan_segment = AsyncMock(side_effect=mock_scan_fallback)

        with patch("backend.app.scraper.ebilet_seat_scraper.resolve_real_route", return_value=mock_stops), \
             patch("backend.app.scraper.ebilet_seat_scraper.filter_and_merge_stops", return_value=mock_stops), \
             patch("backend.app.scraper.ebilet_seat_scraper.async_playwright") as mock_pw:
            mock_browser = AsyncMock()
            mock_ctx = AsyncMock()
            mock_page = AsyncMock()
            mock_browser.new_context.return_value = mock_ctx
            mock_ctx.new_page.return_value = mock_page
            mock_pw_inst = AsyncMock()
            mock_pw_inst.chromium.connect_over_cdp.side_effect = Exception("No CDP")
            mock_pw_inst.chromium.launch.return_value = mock_browser
            mock_pw.return_value.__aenter__.return_value = mock_pw_inst

            full_map = await scanner_b.scan_full_train(
                from_station="Warszawa Centralna",
                to_station="Wrocław Główny",
                travel_date="2026-11-24",
                train_number="IC 1644",
                fast_path=True,
            )

        assert full_map.fast_path_used is False
        assert full_map.total_route_segments == 3
        assert len(full_map.segments) == 3
        assert ("Warszawa Centralna", "Wrocław Główny") in calls_b
        assert ("Warszawa Centralna", "Żyrardów") in calls_b
        assert ("Koluszki", "Wrocław Główny") in calls_b

    @pytest.mark.asyncio
    async def test_scanner_respects_cancel_event(self):
        """Verifies scan_full_train aborts with CancelledError when cancel_event is set."""
        import asyncio
        from backend.app.scraper.ebilet_seat_scraper import LiveTrainScanner

        scanner = LiveTrainScanner(headless=True)
        cancel_ev = asyncio.Event()
        cancel_ev.set()

        with pytest.raises(asyncio.CancelledError):
            await scanner.scan_full_train(
                from_station="Warszawa Centralna",
                to_station="Wrocław Główny",
                travel_date="2026-11-24",
                train_number="IC 1644",
                cancel_event=cancel_ev,
            )

    def test_zero_connections_detector_ignores_after_hour_footer(self, scraper):
        """Verifies pagination footer 'Brak połączeń ... po godzinie 19:22' does not trigger false error."""
        footer_text = "tlk 53171 karpaty 19:22 > 06:04 brak połączeń w dniu 23.11.2026 po godzinie 19:22."
        assert scraper._body_indicates_zero_connections(footer_text) is False
        assert scraper._body_indicates_zero_connections("brak połączeń przed godziną 05:00.") is False
        assert scraper._body_indicates_zero_connections("brak połączeń dla wybranej relacji.") is True
        assert scraper._body_indicates_zero_connections("nie znaleziono połączeń w wybranym dniu.") is True

    @pytest.mark.asyncio
    async def test_fast_path_progress_caps_at_22_and_fallback_scans_all_segments(self):
        """
        Verifies Fast-Path progress reports stay <= 22% so fallback scanning
        across all elementary segments progresses smoothly without jumping backwards.
        """
        import re as _re
        from unittest.mock import AsyncMock, patch
        from backend.app.scraper.ebilet_seat_scraper import LiveTrainScanner
        from backend.app.scraper.ic_client import GraphicReservationUnavailableError
        from backend.app.scraper.route_resolver import StationStop

        mock_stops = [
            StationStop(seq=0, eva_code="5100065", name="Warszawa Centralna", arrival_min=0, departure_min=0, departure_time="15:20"),
            StationStop(seq=1, eva_code="5100067", name="Warszawa Zachodnia", arrival_min=5, departure_min=6, arrival_time="15:25", departure_time="15:26"),
            StationStop(seq=2, eva_code="5104415", name="Żyrardów", arrival_min=25, departure_min=26, arrival_time="15:45", departure_time="15:46"),
            StationStop(seq=3, eva_code="5100294", name="Skierniewice", arrival_min=40, departure_min=41, arrival_time="16:00", departure_time="16:01"),
            StationStop(seq=4, eva_code="5100024", name="Koluszki", arrival_min=60, departure_min=61, arrival_time="16:20", departure_time="16:21"),
        ]

        progress_msgs = []
        scanner = LiveTrainScanner(headless=True, on_progress=lambda m: progress_msgs.append(m))

        pw_calls = []

        async def mock_scan_segment(**kwargs):
            cb = kwargs.get("progress_cb")
            if cb:
                cb(90, "Odczytywanie wolnych miejsc...")
            pw_calls.append((kwargs["from_station"], kwargs["to_station"]))
            if kwargs["from_station"] == "Warszawa Centralna" and kwargs["to_station"] == "Koluszki":
                raise GraphicReservationUnavailableError("BRAK_MIEJSC_KLASA_2: wyprzedane")
            return {"3": ["14"]}

        scanner.scraper.scan_segment = AsyncMock(side_effect=mock_scan_segment)

        with patch("backend.app.scraper.ebilet_seat_scraper.resolve_real_route", return_value=mock_stops), \
             patch("backend.app.scraper.ebilet_seat_scraper.filter_and_merge_stops", return_value=mock_stops), \
             patch("backend.app.scraper.ebilet_seat_scraper.async_playwright") as mock_pw:
            mock_browser = AsyncMock()
            mock_ctx = AsyncMock()
            mock_page = AsyncMock()
            mock_browser.new_context.return_value = mock_ctx
            mock_ctx.new_page.return_value = mock_page
            mock_pw_inst = AsyncMock()
            mock_pw_inst.chromium.connect_over_cdp.side_effect = Exception("No CDP")
            mock_pw_inst.chromium.launch.return_value = mock_browser
            mock_pw.return_value.__aenter__.return_value = mock_pw_inst

            full_map = await scanner.scan_full_train(
                from_station="Warszawa Centralna",
                to_station="Koluszki",
                travel_date="2026-11-24",
                train_number="IC 1644",
                fast_path=True,
            )

        assert full_map.fast_path_used is False
        assert len(full_map.segments) == 4
        assert all(s.status == "OK" for s in full_map.segments)

        fp_pcts = [
            int(_re.search(r"\[(\d+)%\]", m).group(1))
            for m in progress_msgs
            if "Fast-Path" in m and _re.search(r"\[(\d+)%\]", m)
        ]
        assert fp_pcts and max(fp_pcts) <= 22

        assert len(pw_calls) == 5

    @pytest.mark.asyncio
    async def test_metro_hub_tail_recovery_merges_unsellable_final_hop(self):
        """
        Verifies that when a final short metropolitan hop (e.g. Warszawa Zachodnia -> Warszawa Centralna)
        is unsellable as a standalone ticket ('Nie znaleziono pociągu'), the scanner automatically merges
        it with the previous segment (Pabianice -> Warszawa Centralna) instead of leaving SCRAPE_ERROR.
        """
        from unittest.mock import AsyncMock, patch
        from backend.app.scraper.ebilet_seat_scraper import LiveTrainScanner
        from backend.app.scraper.ic_client import GraphicReservationUnavailableError
        from backend.app.scraper.route_resolver import StationStop

        mock_stops = [
            StationStop(seq=0, eva_code="5100188", name="Pabianice", arrival_min=0, departure_min=0, departure_time="20:13"),
            StationStop(seq=1, eva_code="5100067", name="Warszawa Zachodnia", arrival_min=90, departure_min=91, arrival_time="21:43", departure_time="21:44"),
            StationStop(seq=2, eva_code="5100065", name="Warszawa Centralna", arrival_min=96, departure_min=96, arrival_time="21:49"),
        ]

        scanner = LiveTrainScanner(headless=True)
        calls = []

        async def mock_scan_segment(**kwargs):
            calls.append((kwargs["from_station"], kwargs["to_station"]))
            if kwargs["from_station"] == "Pabianice" and kwargs["to_station"] == "Warszawa Zachodnia":
                return {"5": ["48"]}
            if kwargs["from_station"] == "Warszawa Zachodnia" and kwargs["to_station"] == "Warszawa Centralna":
                raise GraphicReservationUnavailableError(
                    "SCRAPE_ERROR: Nie znaleziono pociągu IC 6122 na liście połączeń dla relacji Warszawa Zachodnia -> Warszawa Centralna."
                )
            if kwargs["from_station"] == "Pabianice" and kwargs["to_station"] == "Warszawa Centralna":
                return {"5": ["48"]}
            return {}

        scanner.scraper.scan_segment = AsyncMock(side_effect=mock_scan_segment)

        with patch("backend.app.scraper.ebilet_seat_scraper.resolve_real_route", return_value=mock_stops), \
             patch("backend.app.scraper.ebilet_seat_scraper.filter_and_merge_stops", return_value=mock_stops), \
             patch("backend.app.scraper.ebilet_seat_scraper.async_playwright") as mock_pw:
            mock_browser = AsyncMock()
            mock_ctx = AsyncMock()
            mock_page = AsyncMock()
            mock_browser.new_context.return_value = mock_ctx
            mock_ctx.new_page.return_value = mock_page
            mock_pw_inst = AsyncMock()
            mock_pw_inst.chromium.connect_over_cdp.side_effect = Exception("No CDP")
            mock_pw_inst.chromium.launch.return_value = mock_browser
            mock_pw.return_value.__aenter__.return_value = mock_pw_inst

            full_map = await scanner.scan_full_train(
                from_station="Pabianice",
                to_station="Warszawa Centralna",
                travel_date="2026-11-29",
                train_number="IC 6122",
                fast_path=False,
            )

        assert len(full_map.segments) == 1
        seg = full_map.segments[0]
        assert seg.status == "OK"
        assert seg.error_reason is None
        assert seg.from_station == "Pabianice"
        assert seg.to_station == "Warszawa Centralna"
        assert seg.duration_min == 96
        assert seg.carriages == {"5": ["48"]}
        assert ("Pabianice", "Warszawa Centralna") in calls

    @pytest.mark.asyncio
    async def test_split_fast_path_recovers_blocked_overnight_route(self):
        """
        Verifies adaptive divide-and-conquer split Fast-Path:
        - When full route fails with 'Brak dostępnych wagonów Klasy 2' and split halves share a free seat,
          returns fast_path_used=True with 2 macro segments without scanning all elementary segments.
        - When one half is genuinely SOLD_OUT, falls back to elementary segments cleanly.
        """
        from unittest.mock import AsyncMock, patch
        from backend.app.scraper.ebilet_seat_scraper import LiveTrainScanner
        from backend.app.scraper.ic_client import GraphicReservationUnavailableError
        from backend.app.scraper.route_resolver import StationStop

        mock_stops = [
            StationStop(
                seq=i,
                eva_code=f"51000{i:02d}",
                name=f"Stacja_{i}",
                arrival_min=i * 20,
                departure_min=i * 20,
                departure_time=f"{8 + i // 3:02d}:{(i % 3) * 20:02d}",
                arrival_time=f"{8 + i // 3:02d}:{(i % 3) * 20:02d}",
            )
            for i in range(10)  # 9 elementary segments (>= 8 threshold)
        ]

        # Case A: Split halves share seat ("12", "11")
        scanner_a = LiveTrainScanner(headless=True)
        calls_a = []

        async def mock_split_hit(**kwargs):
            calls_a.append((kwargs["from_station"], kwargs["to_station"]))
            if kwargs["from_station"] == "Stacja_0" and kwargs["to_station"] == "Stacja_9":
                raise GraphicReservationUnavailableError(
                    "BRAK_MIEJSC_KLASA_2: Brak dostępnych wagonów Klasy 2 w składzie."
                )
            if kwargs["from_station"] == "Stacja_0" and kwargs["to_station"] == "Stacja_4":
                return {"12": ["11", "15"]}
            if kwargs["from_station"] == "Stacja_4" and kwargs["to_station"] == "Stacja_9":
                return {"12": ["11", "22"]}
            return {"12": ["11"]}

        scanner_a.scraper.scan_segment = AsyncMock(side_effect=mock_split_hit)

        with patch("backend.app.scraper.ebilet_seat_scraper.resolve_real_route", return_value=mock_stops), \
             patch("backend.app.scraper.ebilet_seat_scraper.filter_and_merge_stops", return_value=mock_stops), \
             patch("backend.app.scraper.ebilet_seat_scraper.async_playwright") as mock_pw:
            mock_browser = AsyncMock()
            mock_ctx = AsyncMock()
            mock_page = AsyncMock()
            mock_browser.new_context.return_value = mock_ctx
            mock_ctx.new_page.return_value = mock_page
            mock_pw_inst = AsyncMock()
            mock_pw_inst.chromium.connect_over_cdp.side_effect = Exception("No CDP")
            mock_pw_inst.chromium.launch.return_value = mock_browser
            mock_pw.return_value.__aenter__.return_value = mock_pw_inst

            fast_map = await scanner_a.scan_full_train(
                from_station="Stacja_0",
                to_station="Stacja_9",
                travel_date="2026-11-24",
                train_number="TLK 83170",
                fast_path=True,
            )

        assert fast_map.fast_path_used is True
        assert fast_map.total_route_segments == 9
        assert len(fast_map.segments) == 2
        assert fast_map.segments[0].from_station == "Stacja_0"
        assert fast_map.segments[0].to_station == "Stacja_4"
        assert fast_map.segments[1].from_station == "Stacja_4"
        assert fast_map.segments[1].to_station == "Stacja_9"
        assert len(calls_a) == 3

        # Case B: Second half genuinely SOLD_OUT -> falls back to 9 elementary segments
        scanner_b = LiveTrainScanner(headless=True)
        calls_b = []

        async def mock_split_miss(**kwargs):
            calls_b.append((kwargs["from_station"], kwargs["to_station"]))
            if kwargs["from_station"] == "Stacja_0" and kwargs["to_station"] == "Stacja_9":
                raise GraphicReservationUnavailableError(
                    "BRAK_MIEJSC_KLASA_2: Brak dostępnych wagonów Klasy 2 w składzie."
                )
            if kwargs["from_station"] == "Stacja_4" and kwargs["to_station"] == "Stacja_9":
                raise GraphicReservationUnavailableError(
                    "BRAK_MIEJSC_KLASA_2: Wszystkie dostępne wagony Klasy 2 mają 0 wolnych miejsc."
                )
            return {"12": ["11"]}

        scanner_b.scraper.scan_segment = AsyncMock(side_effect=mock_split_miss)

        with patch("backend.app.scraper.ebilet_seat_scraper.resolve_real_route", return_value=mock_stops), \
             patch("backend.app.scraper.ebilet_seat_scraper.filter_and_merge_stops", return_value=mock_stops), \
             patch("backend.app.scraper.ebilet_seat_scraper.async_playwright") as mock_pw:
            mock_browser = AsyncMock()
            mock_ctx = AsyncMock()
            mock_page = AsyncMock()
            mock_browser.new_context.return_value = mock_ctx
            mock_ctx.new_page.return_value = mock_page
            mock_pw_inst = AsyncMock()
            mock_pw_inst.chromium.connect_over_cdp.side_effect = Exception("No CDP")
            mock_pw_inst.chromium.launch.return_value = mock_browser
            mock_pw.return_value.__aenter__.return_value = mock_pw_inst

            fallback_map = await scanner_b.scan_full_train(
                from_station="Stacja_0",
                to_station="Stacja_9",
                travel_date="2026-11-24",
                train_number="TLK 83170",
                fast_path=True,
            )

        assert fallback_map.fast_path_used is False
        assert len(fallback_map.segments) == 9
