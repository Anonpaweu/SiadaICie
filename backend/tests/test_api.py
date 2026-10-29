"""Tests for the FastAPI endpoints."""
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

    def test_list_trains_endpoint(self):
        response = client.post("/api/v1/trains?from_station=Warszawa&to_station=Gdańsk&date=2026-11-15")
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 1
        assert "train_number" in data[0]

    def test_websocket_analyze_stream(self):
        with client.websocket_connect("/ws/analyze") as websocket:
            websocket.send_json({"train_no": "IC 3520"})
            message = websocket.receive_json()
            assert "step" in message
            assert message["step"] == "SCHEDULE_FETCHED"


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
