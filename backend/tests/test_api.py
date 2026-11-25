"""Tests for the FastAPI endpoints."""
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from backend.app.main import app


client = TestClient(app)


class TestHealthEndpoint:
    """Test /api/health and / return 200 OK."""

    def test_health_returns_ok(self):
        response = client.get("/api/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"

    def test_root_serves_html(self):
        response = client.get("/")
        assert response.status_code == 200
        assert "SiadaICie" in response.text
        assert "BŁĄD ODCZYTU" in response.text
        assert "Błąd odczytu odcinka" in response.text
        assert "scrapeErrorWarningBanner" in response.text
        assert "btnCancelScan" in response.text
        assert "fastPathBanner" in response.text
        assert "Otwórz odcinek w e-IC" in response.text


class TestStationsSearch:
    """Test /api/stations/search returns matching stations."""

    def test_search_warszawa(self):
        response = client.get("/api/stations/search?q=Warszawa")
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 1
        assert all("Warszawa" in s["name"] for s in data)

    def test_search_no_results(self):
        response = client.get("/api/stations/search?q=Xyznonexistent")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 0

    def test_list_trains_endpoint(self, monkeypatch):
        from backend.app import main as main_mod

        sample_trains = [
            {"train_number": "IC 1510", "departure": "10:15", "arrival": "13:30", "duration": "3:15", "changes": 0},
        ]
        monkeypatch.setattr(main_mod, "fetch_live_train_connections", lambda **kwargs: sample_trains)

        response = client.post("/api/v1/trains?from_station=Warszawa&to_station=Gdańsk&date=2026-11-25")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["train_number"] == "IC 1510"

    def test_list_trains_returns_empty_when_no_connections(self, monkeypatch):
        from backend.app import main as main_mod

        monkeypatch.setattr(main_mod, "fetch_live_train_connections", lambda **kwargs: [])
        response = client.get("/api/v1/trains?from_station=Koluszki&to_station=Nowhere&date=2026-11-25")
        assert response.status_code == 200
        assert response.json() == []

    def test_search_polish_diacritics_zyrardow_and_lodz(self):
        # Uppercase Polish diacritics must match via normalized_name (not SQLite ASCII-only lower())
        resp_zyr = client.get("/api/stations/search", params={"q": "Żyrardów"})
        assert resp_zyr.status_code == 200
        zyr_data = resp_zyr.json()
        assert len(zyr_data) >= 1
        assert zyr_data[0]["name"] == "Żyrardów"
        assert zyr_data[0]["eva_code"] == "5104415"

        resp_lodz = client.get("/api/stations/search", params={"q": "Łódź"})
        assert resp_lodz.status_code == 200
        lodz_data = resp_lodz.json()
        assert len(lodz_data) >= 1
        assert lodz_data[0]["name"].startswith("Łódź")
        lodz_by_name = {s["name"]: s["eva_code"] for s in lodz_data}
        assert lodz_by_name.get("Łódź Fabryczna") == "5100317"

        resp_swin = client.get("/api/stations/search", params={"q": "Świnoujście"})
        assert resp_swin.status_code == 200
        swin_data = resp_swin.json()
        assert len(swin_data) >= 1
        assert swin_data[0]["name"] == "Świnoujście"
        assert swin_data[0]["eva_code"] == "5100059"

    def test_warszawa_zachodnia_eva_code_correct(self):
        from backend.app.main import KNOWN_STATIONS

        by_name = {s["name"]: s["eva_code"] for s in KNOWN_STATIONS}
        assert by_name["Warszawa Zachodnia"] == "5100067"
        assert by_name["Wrocław Główny"] == "5100069"

    def test_all_known_stations_eva_codes_are_accurate(self):
        from backend.app.db.database import get_connection
        from backend.app.main import KNOWN_STATIONS

        # No duplicate EVA codes
        evas = [s["eva_code"] for s in KNOWN_STATIONS]
        assert len(evas) == len(set(evas)), "Duplicate EVA codes in KNOWN_STATIONS"

        by_name = {s["name"]: s["eva_code"] for s in KNOWN_STATIONS}
        # Explicitly verify previously swapped/wrong EVAs
        assert by_name["Gdańsk Główny"] == "5100009"
        assert by_name["Gdynia Główna"] == "5100010"
        assert by_name["Poznań Główny"] == "5100081"
        assert by_name["Łódź Fabryczna"] == "5100317"
        assert by_name["Sopot"] == "5100056"
        assert by_name["Gdańsk Wrzeszcz"] == "5101307"
        assert by_name["Kutno"] == "5100032"
        assert by_name["Bydgoszcz Główna"] == "5100005"

        # Cross-check every KNOWN_STATIONS entry against pkp_optimizer.db
        conn = get_connection()
        try:
            cur = conn.cursor()
            for station in KNOWN_STATIONS:
                cur.execute("SELECT eva_code FROM stations WHERE name = ?", (station["name"],))
                row = cur.fetchone()
                assert row is not None, f"Station {station['name']} not found in DB"
                assert row["eva_code"] == station["eva_code"], (
                    f"EVA mismatch for {station['name']}: KNOWN_STATIONS={station['eva_code']} vs DB={row['eva_code']}"
                )
        finally:
            conn.close()

    def test_scan_tracker_progress_is_monotonic(self):
        from backend.app.main import ScanStatusTracker

        tracker = ScanStatusTracker()
        tracker.reset()
        tracker.update("[75%] Odcinek 1/3")
        assert tracker.progress == 75
        tracker.update("[20%] Odcinek 2/3")
        assert tracker.progress == 75
        tracker.update("[90%] Odcinek 3/3")
        assert tracker.progress == 90


class TestAnalyzeEndpoint:
    """Test POST /api/analyze with mock_overlap mode."""

    def test_analyze_mock_overlap(self):
        response = client.post(
            "/api/analyze",
            json={
                "from_station": "A",
                "to_station": "G",
                "mode": "mock_overlap",
            },
        )
        assert response.status_code == 200
        data = response.json()

        # Verify top-level structure
        assert "stations" in data
        assert "segments" in data
        assert "strategies" in data
        assert "seat_matrix" in data

        # Verify stations
        assert len(data["stations"]) == 7  # A through G
        assert data["stations"][0] == "A"
        assert data["stations"][-1] == "G"

        # Verify strategies
        assert len(data["strategies"]) >= 1
        best = data["strategies"][0]
        assert "rank" in best
        assert "title" in best
        assert "total_score" in best
        assert "transfers_count" in best
        assert "steps" in best

        # Verify steps have required fields
        for step in best["steps"]:
            assert "seat_uid" in step
            assert "carriage_no" in step
            assert "from_station_name" in step
            assert "to_station_name" in step
            assert "duration_min" in step

        # Verify the best strategy covers the full route
        assert best["steps"][0]["from_station_idx"] == 0
        assert best["steps"][-1]["to_station_idx"] == 6

    def test_analyze_live_mode_real_route(self):
        """Test POST /api/analyze in live mode resolves real route with actual stations and timing."""
        from backend.app.db.database import set_cached_item
        from backend.app.scraper.schedule_scraper import resolve_real_route
        from backend.app.scraper.route_resolver import filter_and_merge_stops

        stops = filter_and_merge_stops(resolve_real_route("Warszawa Centralna", "Gdańsk Główny", "2026-11-15", "IC 3520"))
        seats_payload = {
            f"{c}_{s}": 1 for c in (6, 7, 8) for s in ("11", "12", "15", "16", "21")
        }
        for i in range(len(stops) - 1):
            set_cached_item(
                f"seatmap_IC 3520_2026-11-15_{stops[i].name}_{stops[i+1].name}_c2",
                {"seats": seats_payload}
            )

        response = client.post(
            "/api/analyze",
            json={
                "from_station": "Warszawa Centralna",
                "to_station": "Gdańsk Główny",
                "date": "2026-11-15",
                "train_no": "IC 3520",
                "mode": "live",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["stations"]) >= 5
        assert data["stations"][0] == "Warszawa Centralna"
        assert data["stations"][-1] == "Gdańsk Główny"
        assert len(data["strategies"]) >= 1
        assert len(data["seat_matrix"]) >= 10

    def test_analyze_seat_matrix_structure(self):
        response = client.post(
            "/api/analyze",
            json={
                "from_station": "A",
                "to_station": "G",
                "mode": "mock_overlap",
            },
        )
        data = response.json()

        # Verify seat_matrix entries
        assert len(data["seat_matrix"]) >= 1
        for seat in data["seat_matrix"]:
            assert "seat_uid" in seat
            assert "carriage_no" in seat
            assert "seat_no" in seat
            assert "availability" in seat
            assert len(seat["availability"]) == 6  # 6 segments for 7 stations


class TestOptimizeFromText:
    """Tests for POST /api/v1/optimize-from-text endpoint (Tasks 084, 085)."""

    def test_optimize_from_text_koluszki_opole(self):
        fixture_path = Path(__file__).parent / "fixtures" / "koluszki_opole.txt"
        assert fixture_path.exists(), "koluszki_opole.txt fixture must exist"
        raw_text = fixture_path.read_text(encoding="utf-8")

        response = client.post(
            "/api/v1/optimize-from-text",
            json={"raw_text": raw_text},
        )
        assert response.status_code == 200
        data = response.json()

        # Verify 6 segments and 7 stations, including departure_time field
        assert len(data["segments"]) == 6
        assert "departure_time" in data["segments"][0]
        assert data["segments"][1]["departure_time"] == "15:12"
        assert len(data["stations"]) == 7
        assert data["stations"][0] == "Koluszki"
        assert data["stations"][-1] == "Opole Główne"

        # Verify strategies & clean snake_case transfer_instruction
        assert len(data["strategies"]) >= 1
        best = data["strategies"][0]
        assert best["transfers_count"] in (0, 1)
        assert len(best["steps"]) >= 1
        assert "transfer_instruction" in best["steps"][0]

        # Check purchase instructions (Task 085)
        assert len(best["purchase_instructions"]) >= 1
        assert len(data["purchase_instructions"]) >= 1
        assert any("Wagon" in instr for instr in data["purchase_instructions"])

    def test_optimize_from_text_respects_profile(self):
        fixture_path = Path(__file__).parent / "fixtures" / "koluszki_opole.txt"
        raw_text = fixture_path.read_text(encoding="utf-8")

        for profile in ("BALANCED", "MINIMAL_WALKING", "MINIMAL_TRANSFERS"):
            response = client.post(
                "/api/v1/optimize-from-text",
                json={"raw_text": raw_text, "profile": profile},
            )
            assert response.status_code == 200
            assert len(response.json()["strategies"]) >= 1

    def test_optimize_from_text_empty_input(self):
        response = client.post(
            "/api/v1/optimize-from-text",
            json={"raw_text": ""},
        )
        assert response.status_code == 400


class TestTrainRouteEndpoint:
    """Tests for GET /api/v1/train-route endpoint (Task 079)."""

    def test_get_train_route_success(self):
        response = client.get(
            "/api/v1/train-route",
            params={
                "from_station": "Koluszki",
                "to_station": "Opole Główne",
                "date": "2026-11-15",
                "train_number": "IC 16104",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["train_number"] == "IC 16104"
        assert len(data["stops"]) >= 2
        assert "Koluszki" in data["formatted_route"]


class TestErrorHandlingScraper:
    """Tests for 502 Bad Gateway error handling when scraper is blocked (Task 086)."""

    def test_live_scraper_returns_502_when_blocked(self, monkeypatch):
        from backend.app.scraper.ebilet_seat_scraper import LiveTrainScanner, GraphicReservationUnavailableError

        async def mock_scan_failure(*args, **kwargs):
            raise GraphicReservationUnavailableError("Brak możliwości wyboru miejsca ze schematu na odcinku Koluszki -> Piotrków")

        monkeypatch.setattr(LiveTrainScanner, "scan_full_train", mock_scan_failure)

        response = client.post(
            "/api/v1/optimize",
            json={
                "from_station": "Koluszki",
                "to_station": "Opole Główne",
                "date": "2026-11-15",
                "train_number": "IC 16104",
            },
        )
        assert response.status_code == 502
        assert "Brak możliwości wyboru miejsca" in response.json()["detail"]

    def test_scan_status_endpoint(self):
        response = client.get("/api/v1/scan-status")
        assert response.status_code == 200
        data = response.json()
        assert "is_running" in data
        assert "progress" in data
        assert "logs" in data

    def test_live_scraper_error_includes_snapshot_path(self, monkeypatch):
        from backend.app.scraper.ebilet_seat_scraper import LiveTrainScanner, GraphicReservationUnavailableError

        async def mock_scan_failure(*args, **kwargs):
            raise GraphicReservationUnavailableError("BRAK_MIEJSC_KLASA_2: Wyprzedano miejsca w 2. klasie")

        monkeypatch.setattr(LiveTrainScanner, "scan_full_train", mock_scan_failure)

        response = client.post(
            "/api/v1/optimize",
            json={
                "from_station": "Koluszki",
                "to_station": "Opole Główne",
                "date": "2026-11-15",
                "train_number": "IC 16104",
                "show_browser": True,
            },
        )
        assert response.status_code == 502
        assert "error_85_percent.png" in response.json()["detail"]
        assert "BRAK_MIEJSC_KLASA_2" in response.json()["detail"]

    def test_show_browser_passed_to_scanner(self, monkeypatch):
        from backend.app.scraper.ebilet_seat_scraper import LiveTrainScanner
        from backend.app.models.domain import FullTrainAvailabilityMap

        captured_args = {}

        def mock_init(self, headless=None, show_browser=None, on_progress=None, ttl_seconds=600):
            captured_args["headless"] = headless
            captured_args["show_browser"] = show_browser

        async def mock_scan(*args, **kwargs):
            captured_args["ticket_class"] = kwargs.get("ticket_class")
            return FullTrainAvailabilityMap(train_number="IC 16104", travel_date="2026-11-15", segments=[])

        monkeypatch.setattr(LiveTrainScanner, "__init__", mock_init)
        monkeypatch.setattr(LiveTrainScanner, "scan_full_train", mock_scan)

        response = client.post(
            "/api/v1/optimize",
            json={
                "from_station": "Koluszki",
                "to_station": "Opole Główne",
                "date": "2026-11-15",
                "train_number": "IC 16104",
                "ticket_class": 1,
                "profile": "MINIMAL_WALKING",
                "show_browser": True,
            },
        )
        assert captured_args.get("show_browser") is True
        assert captured_args.get("headless") is False
        assert captured_args.get("ticket_class") == 1

    def test_optimize_propagates_scrape_error_status_on_failed_segments(self, monkeypatch):
        from types import SimpleNamespace
        from backend.app.scraper.ebilet_seat_scraper import LiveTrainScanner

        async def mock_scan_with_partial_error(self, *args, **kwargs):
            return SimpleNamespace(
                train_number="IC 1644",
                travel_date="2026-11-24",
                segments=[
                    SimpleNamespace(
                        segment_index=0,
                        from_station="Warszawa Zachodnia",
                        to_station="Żyrardów",
                        departure_time="15:25",
                        carriages={},
                        status="SCRAPE_ERROR",
                        error_reason="Kod stacji spoza zakresu (timeout 35s)",
                    ),
                    SimpleNamespace(
                        segment_index=1,
                        from_station="Żyrardów",
                        to_station="Skierniewice",
                        departure_time="15:45",
                        carriages={"5": ["81", "82"]},
                        status="OK",
                        error_reason=None,
                    ),
                ],
            )

        monkeypatch.setattr(LiveTrainScanner, "scan_full_train", mock_scan_with_partial_error)

        response = client.post(
            "/api/v1/optimize",
            json={
                "from_station": "Warszawa Zachodnia",
                "to_station": "Skierniewice",
                "date": "2026-11-24",
                "time": "15:25",
                "train_number": "IC 1644",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["segments"]) == 2
        assert data["segments"][0]["status"] == "SCRAPE_ERROR"
        assert data["segments"][0]["error_reason"] == "Kod stacji spoza zakresu (timeout 35s)"
        assert data["segments"][1]["status"] == "OK"
        assert data["segments"][1]["error_reason"] is None


class TestImprovementsApi:
    """Tests for Fast-Path A->B, direct booking_url links, and per-scan cancellation endpoints."""

    def test_optimize_live_fast_path_and_booking_url(self, monkeypatch):
        from backend.app.models.domain import FullTrainAvailabilityMap, SegmentAvailabilityMap
        from backend.app.scraper.ebilet_seat_scraper import LiveTrainScanner

        captured = {}

        async def mock_scan(self, **kwargs):
            captured["fast_path"] = kwargs.get("fast_path")
            return FullTrainAvailabilityMap(
                train_number="IC 1644",
                travel_date="2026-11-24",
                fast_path_used=bool(kwargs.get("fast_path")),
                total_route_segments=5,
                segments=[
                    SegmentAvailabilityMap(
                        segment_index=0,
                        from_station="Warszawa Centralna",
                        to_station="Wrocław Główny",
                        departure_time="15:20",
                        duration_min=243,
                        from_eva="5100065",
                        to_eva="5100069",
                        segment_date="2026-11-24",
                        carriages={"8": ["11", "12"]},
                        status="OK",
                    )
                ],
            )

        monkeypatch.setattr(LiveTrainScanner, "scan_full_train", mock_scan)

        # Default: Fast-Path enabled (force_full_scan defaults to False)
        resp = client.post(
            "/api/v1/optimize",
            json={
                "from_station": "Warszawa Centralna",
                "to_station": "Wrocław Główny",
                "date": "2026-11-24",
                "time": "15:20",
                "train_number": "IC 1644",
                "scan_id": "test-fp-1",
            },
        )
        assert resp.status_code == 200
        assert captured.get("fast_path") is True
        data = resp.json()
        assert data["fast_path_used"] is True
        assert data["total_route_segments"] == 5
        assert data["segments"][0]["duration_min"] == 243
        booking_url = data["strategies"][0]["steps"][0]["booking_url"]
        assert booking_url is not None
        assert booking_url.startswith("https://ebilet.intercity.pl/wyszukiwanie?")
        assert "swyj=5100065" in booking_url
        assert "sprzy=5100069" in booking_url
        assert "dwyj=2026-11-24" in booking_url
        assert "time=15:10" in booking_url

        # Force full scan: fast_path must be False
        resp_full = client.post(
            "/api/v1/optimize",
            json={
                "from_station": "Warszawa Centralna",
                "to_station": "Wrocław Główny",
                "date": "2026-11-24",
                "time": "15:20",
                "train_number": "IC 1644",
                "force_full_scan": True,
                "scan_id": "test-fp-2",
            },
        )
        assert resp_full.status_code == 200
        assert captured.get("fast_path") is False
        assert resp_full.json()["fast_path_used"] is False

    def test_scan_status_by_scan_id_and_cancel_endpoint(self):
        from backend.app.main import scan_manager

        tracker = scan_manager.create_job("job-cancel-123")
        tracker.update("[45%] Skanowanie odcinka 2/4")

        status_resp = client.get("/api/v1/scan-status", params={"scan_id": "job-cancel-123"})
        assert status_resp.status_code == 200
        s_data = status_resp.json()
        assert s_data["scan_id"] == "job-cancel-123"
        assert s_data["progress"] == 45
        assert s_data["is_running"] is True
        assert s_data["cancelled"] is False

        cancel_resp = client.post("/api/v1/scan-cancel", json={"scan_id": "job-cancel-123"})
        assert cancel_resp.status_code == 200
        assert cancel_resp.json()["status"] == "cancelled"
        assert cancel_resp.json()["scan_id"] == "job-cancel-123"
        assert tracker.cancel_event.is_set() is True
        assert tracker.cancelled is True

        status_after = client.get("/api/v1/scan-status", params={"scan_id": "job-cancel-123"}).json()
        assert status_after["cancelled"] is True
        assert status_after["is_running"] is False

        missing_cancel = client.post("/api/v1/scan-cancel", json={"scan_id": "does-not-exist"})
        assert missing_cancel.status_code == 200
        assert missing_cancel.json()["status"] == "not_found"

    def test_optimize_from_text_has_no_booking_urls(self):
        fixture_path = Path(__file__).parent / "fixtures" / "koluszki_opole.txt"
        raw_text = fixture_path.read_text(encoding="utf-8")

        response = client.post("/api/v1/optimize-from-text", json={"raw_text": raw_text})
        assert response.status_code == 200
        data = response.json()
        assert data["fast_path_used"] is False
        for strat in data["strategies"]:
            for step in strat["steps"]:
                assert step.get("booking_url") is None


class TestStandingTicketLinksAndStatus:
    """Test standing corridor booking URLs, status classification, and full-route alternative."""

    def test_classify_standing_status_from_card_text(self):
        from backend.app.scraper.ebilet_seat_scraper import classify_standing_status_from_card_text

        avail_card = (
            "Połączenie tam 15:27 Poznań Gł. Konin Informacja Klasa 2: Nie ma już miejsc "
            "ogólnodostępnych. Można kupić bilet bez gwarancji miejsca siedzącego. Klasa 1 43,00 zł Klasa 2 21,45 zł"
        )
        overbooked_card = (
            "Połączenie tam 15:20 Warszawa Centr. Piotrków Tryb. Błąd Klasa 2: Miejsca w wybranej klasie "
            "zostały wyprzedane. Prosimy wybrać inną klasę lub inny pociąg. Klasa 1 57,00 zł Klasa 2 Niedostępna"
        )
        special_card = (
            "Połączenie tam 17:20 Warszawa Centr. Piotrków Tryb. Informacja Klasa 2: Brak miejsc "
            "ogólnodostępnych. Dostępne miejsca specjalne Klasa 1 57,00 zł Klasa 2 27,95 zł"
        )
        normal_card = "Połączenie tam 10:00 Warszawa Centr. Kraków Gł. Klasa 1 99 zł Klasa 2 59 zł KUP BILET"

        assert classify_standing_status_from_card_text(avail_card, ticket_class=2) == "AVAILABLE"
        assert classify_standing_status_from_card_text(overbooked_card, ticket_class=2) == "OVERBOOKED"
        assert classify_standing_status_from_card_text(special_card, ticket_class=2) == "SPECIAL_ONLY"
        assert classify_standing_status_from_card_text(normal_card, ticket_class=2) is None

    def test_standing_corridor_has_merged_booking_url_and_full_route_alternative(self, monkeypatch):
        from backend.app.models.domain import FullTrainAvailabilityMap, SegmentAvailabilityMap
        from backend.app.scraper.ebilet_seat_scraper import LiveTrainScanner

        async def mock_scan(self, **kwargs):
            return FullTrainAvailabilityMap(
                train_number="IC 1646",
                travel_date="2026-11-27",
                fast_path_used=False,
                total_route_segments=3,
                full_route_standing_status="AVAILABLE",
                segments=[
                    SegmentAvailabilityMap(
                        segment_index=0,
                        from_station="Warszawa Centralna",
                        to_station="Koluszki",
                        departure_time="17:20",
                        duration_min=55,
                        from_eva="5100065",
                        to_eva="5100024",
                        segment_date="2026-11-27",
                        carriages={},
                        status="SOLD_OUT",
                        standing_status="AVAILABLE",
                    ),
                    SegmentAvailabilityMap(
                        segment_index=1,
                        from_station="Koluszki",
                        to_station="Piotrków Trybunalski",
                        departure_time="18:16",
                        duration_min=25,
                        from_eva="5100024",
                        to_eva="5100190",
                        segment_date="2026-11-27",
                        carriages={},
                        status="SOLD_OUT",
                        standing_status="SPECIAL_ONLY",
                    ),
                    SegmentAvailabilityMap(
                        segment_index=2,
                        from_station="Piotrków Trybunalski",
                        to_station="Wrocław Główny",
                        departure_time="18:42",
                        duration_min=160,
                        from_eva="5100190",
                        to_eva="5100069",
                        segment_date="2026-11-27",
                        carriages={"6": ["44"]},
                        status="OK",
                    ),
                ],
            )

        monkeypatch.setattr(LiveTrainScanner, "scan_full_train", mock_scan)

        resp = client.post(
            "/api/v1/optimize",
            json={
                "from_station": "Warszawa Centralna",
                "to_station": "Wrocław Główny",
                "date": "2026-11-27",
                "time": "17:20",
                "train_number": "IC 1646",
                "scan_id": "test-standing-links",
            },
        )
        assert resp.status_code == 200
        data = resp.json()

        # Full-route A->D booking link and status exposed at top level
        assert data["full_route_standing_status"] == "AVAILABLE"
        full_url = data["full_route_booking_url"]
        assert full_url is not None
        assert "swyj=5100065" in full_url
        assert "sprzy=5100069" in full_url

        # Consecutive sold-out segments 0..2 merged into a single standing step Warszawa Centralna -> Piotrków Tryb.
        best = data["strategies"][0]
        assert len(best["steps"]) == 2
        standing_step = best["steps"][0]
        assert standing_step["is_standing"] is True
        assert standing_step["from_station_name"] == "Warszawa Centralna"
        assert standing_step["to_station_name"] == "Piotrków Trybunalski"
        # Worst-case status across (AVAILABLE, SPECIAL_ONLY) -> SPECIAL_ONLY
        assert standing_step["standing_status"] == "SPECIAL_ONLY"
        assert standing_step["booking_url"] is not None
        assert "swyj=5100065" in standing_step["booking_url"]
        assert "sprzy=5100190" in standing_step["booking_url"]

        # Recompute preserves standing_status & full_route_booking_url
        recomp = client.post("/api/v1/recompute", json={"profile": "MINIMAL_TRANSFERS"})
        assert recomp.status_code == 200
        rdata = recomp.json()
        assert rdata["full_route_booking_url"] == full_url
        assert rdata["full_route_standing_status"] == "AVAILABLE"
        assert rdata["strategies"][0]["steps"][0]["standing_status"] == "SPECIAL_ONLY"
