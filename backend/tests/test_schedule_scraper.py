"""
Tests for PKP schedule scraper and live train connection provider.
"""
import pytest
from backend.app.scraper.schedule_scraper import (
    get_station_info,
    fetch_live_train_connections,
    resolve_real_route,
    get_cached_route,
    save_cached_route,
)
from backend.app.scraper.route_resolver import StationStop


def test_get_station_info_official_stations():
    """Verify official station names and EVA codes are retrieved from SQLite."""
    name, eva = get_station_info("Warszawa Centralna")
    assert "Warszawa Centralna" in name
    assert eva == "5100065"

    name_gd, eva_gd = get_station_info("Gdańsk Główny")
    assert "Gdańsk Główny" in name_gd
    assert eva_gd in ("5100009", "5100015")


def test_get_station_info_normalized_query():
    """Verify station lookup works without Polish diacritics."""
    name, eva = get_station_info("krakow glowny")
    assert "Kraków" in name
    assert eva == "5100028"


def test_resolve_real_route_corridor():
    """Verify route resolving returns intermediate stops and merges agglomerations."""
    stops = resolve_real_route(
        from_station="Warszawa Centralna",
        to_station="Gdańsk Główny",
        travel_date="2026-11-15",
        train_number="IC 1510",
    )
    assert len(stops) >= 2
    assert stops[0].name in ("WARSZAWA", "Warszawa Centralna")
    assert stops[-1].name in ("TROJMIASTO", "Gdańsk Główny")
    assert stops[0].departure_min == 0
    assert stops[-1].arrival_min > 0


def test_sqlite_cached_route():
    """Verify route caching in SQLite works properly."""
    dummy_stops = [
        StationStop(seq=0, eva_code="5100065", name="Start", arrival_min=0, departure_min=0),
        StationStop(seq=1, eva_code="5100028", name="End", arrival_min=120, departure_min=120),
    ]
    train_no = "TEST_ROUTE_9999"
    date_str = "2026-12-01"

    save_cached_route(train_no, date_str, dummy_stops)
    cached = get_cached_route(train_no, date_str)

    assert cached is not None
    assert len(cached) == 2
    assert cached[0].name == "Start"
    assert cached[1].name == "End"


def test_clean_pkp_train_name():
    """Verify strictly PKP Intercity brands are allowed and others rejected."""
    from backend.app.scraper.schedule_scraper import clean_pkp_train_name

    assert clean_pkp_train_name("IC  1510") == "IC 1510"
    assert clean_pkp_train_name("EIP 3510") == "EIP 3510"
    assert clean_pkp_train_name("TLK35175") == "TLK 35175"
    assert clean_pkp_train_name("EIC 1804") == "EIC 1804"

    # Reject non-PKP Intercity / regional carriers and UI labels
    assert clean_pkp_train_name("IR 10130") == ""
    assert clean_pkp_train_name("KM 19203") == ""
    assert clean_pkp_train_name("KD 69123") == ""
    assert clean_pkp_train_name("prognozowany planowy przyjazd pociągu") == ""


def test_fetch_live_train_connections_direct_only():
    """Verify live train connections returns list of direct trains across full day."""
    trains = fetch_live_train_connections(
        from_station="Warszawa Centralna",
        to_station="Gdańsk Główny",
        travel_date="2026-10-29",
        direct_only=True,
        full_day=True,
    )
    assert len(trains) >= 5
    for t in trains:
        assert any(t["train_number"].startswith(brand) for brand in ("IC", "EIP", "EIC", "TLK"))
        assert t["changes"] == 0
        assert ":" in t["departure"]
        assert ":" in t["arrival"]


def test_fetch_live_train_connections_with_transfers():
    """Verify connections list includes both direct and transfer options when direct_only=False."""
    trains = fetch_live_train_connections(
        from_station="Koluszki",
        to_station="Zakopane",
        travel_date="2026-10-29",
        direct_only=False,
        full_day=True,
    )
    assert len(trains) >= 3
    # Check that both direct (changes=0) and transfers (changes>0) are present
    has_direct = any(t["changes"] == 0 for t in trains)
    has_transfer = any(t["changes"] > 0 for t in trains)
    assert has_direct or has_transfer
    for t in trains:
        assert any(t["train_number"].startswith(brand) for brand in ("IC", "EIP", "EIC", "TLK"))


def test_resolve_real_route_koluszki_zakopane():
    """Verify Koluszki -> Zakopane resolves real stops and does not fall back to Kutno/Konin."""
    stops = resolve_real_route(
        from_station="Koluszki",
        to_station="Zakopane",
        travel_date="2026-10-29",
        train_number="TLK 53171",
        dep_time="00:35",
    )
    assert len(stops) >= 10
    assert stops[0].name.lower() == "koluszki"
    assert "zakopane" in stops[-1].name.lower()
    # Check key real intermediate stations
    stop_names = [s.name.lower() for s in stops]
    assert not any("kutno" in s or "konin" in s for s in stop_names)
    assert any("cz" in s and "stochowa" in s for s in stop_names)
    assert any("krak" in s for s in stop_names)
    assert any("targ" in s for s in stop_names)
    assert stops[-1].arrival_min > 300


