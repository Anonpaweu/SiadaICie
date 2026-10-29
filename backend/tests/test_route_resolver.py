"""Tests for route_resolver: station merging and segment building."""
import pytest
from backend.app.scraper.route_resolver import (
    StationStop,
    filter_and_merge_stops,
    build_route_segments,
)


def _make_stop(seq: int, name: str, arrival_min: int, departure_min: int | None = None, eva_code: str = "") -> StationStop:
    """Helper to create a StationStop with sensible defaults."""
    if departure_min is None:
        departure_min = arrival_min
    if not eva_code:
        eva_code = str(seq)
    return StationStop(
        seq=seq,
        eva_code=eva_code,
        name=name,
        arrival_min=arrival_min,
        departure_min=departure_min,
    )


class TestWarsawHubMerging:
    """Test 1: Warsaw hub stations should be merged into Warszawa Centralna."""

    def test_warsaw_hub_merging(self):
        raw_stops = [
            _make_stop(0, "Kraków Główny", 0, 0),
            _make_stop(1, "Warszawa Zachodnia", 140, 142),
            _make_stop(2, "Warszawa Centralna", 145, 147),
            _make_stop(3, "Warszawa Wschodnia", 152, 154),
            _make_stop(4, "Gdańsk Główny", 320, 320),
        ]

        filtered = filter_and_merge_stops(raw_stops)
        names = [s.name for s in filtered]

        # Kraków and Gdańsk must always be present (start/end)
        assert names[0] == "Kraków Główny"
        assert names[-1] == "Gdańsk Główny"

        # Three Warsaw stations should be merged into one — Warszawa Centralna (main hub)
        warsaw_stops = [s for s in filtered if "Warszawa" in s.name]
        assert len(warsaw_stops) == 1, f"Expected 1 Warsaw stop, got {len(warsaw_stops)}: {[s.name for s in warsaw_stops]}"
        assert warsaw_stops[0].name == "Warszawa Centralna"


class TestTriCitiesEndMerging:
    """Test 2: Tricity minor stops should be dropped, keeping major hubs + destination."""

    def test_tricities_end_merging(self):
        raw_stops = [
            _make_stop(0, "Warszawa Centralna", 0, 0),
            _make_stop(1, "Kutno", 80, 82),
            _make_stop(2, "Gdańsk Główny", 300, 302),
            _make_stop(3, "Gdańsk Wrzeszcz", 305, 306),
            _make_stop(4, "Gdańsk Oliwa", 309, 310),
            _make_stop(5, "Sopot", 314, 315),
            _make_stop(6, "Gdynia Główna", 322, 322),
        ]

        filtered = filter_and_merge_stops(raw_stops)
        names = [s.name for s in filtered]

        # Start must be preserved
        assert names[0] == "Warszawa Centralna"
        # Destination must be preserved
        assert names[-1] == "Gdynia Główna"

        # Gdańsk Główny is a main hub and should be kept
        assert "Gdańsk Główny" in names

        # Minor Tricity stops (Wrzeszcz, Oliwa, Sopot) should be dropped
        for minor in ["Gdańsk Wrzeszcz", "Gdańsk Oliwa", "Sopot"]:
            assert minor not in names, f"{minor} should have been merged out"


class TestBuildRouteSegments:
    """Test 3: build_route_segments produces N-1 segments for N stops with correct durations."""

    def test_build_route_segments(self):
        stops = [
            _make_stop(0, "A", 0, 0),
            _make_stop(1, "B", 30, 32),
            _make_stop(2, "C", 60, 62),
            _make_stop(3, "D", 90, 92),
            _make_stop(4, "E", 120, 120),
        ]

        segments = build_route_segments(stops)

        # N stops -> N-1 segments
        assert len(segments) == len(stops) - 1, f"Expected {len(stops)-1} segments, got {len(segments)}"

        # Verify segment indices are sequential
        for i, seg in enumerate(segments):
            assert seg.seg_idx == i

        # Verify durations are correct (arrival of next - departure of current)
        expected_durations = [
            30 - 0,   # A(dep=0)  -> B(arr=30)
            60 - 32,  # B(dep=32) -> C(arr=60)
            90 - 62,  # C(dep=62) -> D(arr=90)
            120 - 92, # D(dep=92) -> E(arr=120)
        ]
        for seg, expected_dur in zip(segments, expected_durations):
            assert seg.duration_min == expected_dur, (
                f"Segment {seg.seg_idx}: expected duration {expected_dur}, got {seg.duration_min}"
            )

        # Verify continuity: each segment's to_station == next segment's from_station
        for i in range(len(segments) - 1):
            assert segments[i].to_station.name == segments[i + 1].from_station.name
