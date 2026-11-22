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
        assert "PKP Seat Optimizer" in response.text


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

    def test_warszawa_zachodnia_eva_code_correct(self):
        from backend.app.main import KNOWN_STATIONS

        by_name = {s["name"]: s["eva_code"] for s in KNOWN_STATIONS}
        assert by_name["Warszawa Zachodnia"] == "5100067"
        assert by_name["Wrocław Główny"] == "5100069"

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
