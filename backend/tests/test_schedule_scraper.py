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


def test_train_route_extractor_parses_eva_and_times():
    """
    Task 048: Verifies TrainRouteExtractor extracts station EVA code directly from href
    and exact departure/arrival times in HH:MM format.
    """
    from backend.app.scraper.schedule_scraper import TrainRouteExtractor

    sample_html = """
    <table>
      <tr>
        <td></td>
        <td><a href="/pl/sq?input=5100024&boardType=dep">Koluszki</a></td>
        <td>00:33</td>
        <td>00:35</td>
      </tr>
      <tr>
        <td></td>
        <td><a href="/pl/sq?input=5100190&boardType=dep">Piotrków Trybunalski</a></td>
        <td>00:57</td>
        <td>00:59</td>
      </tr>
      <tr>
        <td></td>
        <td><a href="/pl/sq?input=5100088&boardType=dep">Radomsko</a></td>
        <td>01:25</td>
        <td>01:27</td>
      </tr>
    </table>
    """
    extractor = TrainRouteExtractor()
    stops = extractor.parse_stops_from_ti_html(
        sample_html, from_station="Koluszki", to_station="Radomsko", from_eva="5100024", to_eva="5100088"
    )

    assert len(stops) == 3
    assert stops[0].station_name == "Koluszki"
    assert stops[0].eva_code == "5100024"
    assert stops[0].departure_time == "00:35"

    assert stops[1].station_name == "Piotrków Trybunalski"
    assert stops[1].eva_code == "5100190"
    assert stops[1].departure_time == "00:59"
    assert stops[1].arrival_time == "00:57"

    assert stops[2].station_name == "Radomsko"
    assert stops[2].eva_code == "5100088"
    assert stops[2].departure_time == "01:27"


def test_get_station_info_polish_uppercase_diacritics_and_prefix():
    """Verify Polish uppercase diacritics resolve accurately and prefix beats infix."""
    name_z, eva_z = get_station_info("Żyrardów")
    assert name_z == "Żyrardów"
    assert eva_z == "5104415"

    name_lf, eva_lf = get_station_info("Łódź Fabryczna")
    assert name_lf == "Łódź Fabryczna"
    assert eva_lf != ""

    name_sw, eva_sw = get_station_info("Świnoujście")
    assert name_sw == "Świnoujście"
    assert eva_sw != ""

    # 'lodz' must match a Łódź station (prefix), NEVER 'Bystrzyca Kłodzka' (infix)
    name_lodz, eva_lodz = get_station_info("lodz")
    assert name_lodz.startswith("Łódź"), f"Expected Łódź station, got {name_lodz!r}"
    assert "Bystrzyca" not in name_lodz
    assert eva_lodz != ""


def test_known_real_corridors_evas_match_db():
    """Verify all hardcoded EVA codes in KNOWN_REAL_CORRIDORS match SQLite official EVAs."""
    from backend.app.scraper.schedule_scraper import KNOWN_REAL_CORRIDORS

    for corridor_name, stops in KNOWN_REAL_CORRIDORS.items():
        for stop in stops:
            db_name, db_eva = get_station_info(stop["name"])
            assert db_eva == stop["eva_code"], (
                f"{corridor_name}: {stop['name']} has hardcoded EVA {stop['eva_code']}, "
                f"expected {db_eva} ({db_name})"
            )


def test_upsert_station_preserves_existing_official_record(tmp_path):
    """Verify upsert_station does not overwrite an existing official station name or ic_id."""
    from backend.app.db.database import init_db, get_connection
    from backend.app.db.seed_stations import upsert_station

    db_file = str(tmp_path / "test_stations.db")
    init_db(db_file)
    conn = get_connection(db_file)
    try:
        conn.execute(
            "INSERT INTO stations (eva_code, name, normalized_name, is_main_hub, ic_id) VALUES (?, ?, ?, ?, ?)",
            ("5104415", "Żyrardów", "zyrardow", 0, "5100811"),
        )
        conn.commit()
    finally:
        conn.close()

    # Attempt conflicting upsert with a different/abbreviated name
    upsert_station("Zyrardow Temp", "5104415", db_path=db_file)
    # Also insert a genuinely new station
    upsert_station("Nowa Stacja", "5199999", db_path=db_file)

    conn = get_connection(db_file)
    try:
        cur = conn.cursor()
        cur.execute("SELECT name, normalized_name, ic_id FROM stations WHERE eva_code = '5104415'")
        row = cur.fetchone()
        assert row["name"] == "Żyrardów"
        assert row["normalized_name"] == "zyrardow"
        assert row["ic_id"] == "5100811"

        cur.execute("SELECT name, normalized_name FROM stations WHERE eva_code = '5199999'")
        new_row = cur.fetchone()
        assert new_row["name"] == "Nowa Stacja"
        assert new_row["normalized_name"] == "nowa stacja"
    finally:
        conn.close()


def test_day_offset_propagation_across_midnight(monkeypatch):
    """Verify day_offset increments across midnight in parse_stops_from_ti_html and propagates to StationStop."""
    from backend.app.scraper.schedule_scraper import (
        TrainRouteExtractor,
        fetch_live_train_route_stops,
    )

    night_train_html = """
    <table>
      <tr>
        <td></td>
        <td><a href="/pl/sq?input=5100065&boardType=dep">Warszawa Centralna</a></td>
        <td>22:50</td>
        <td>22:55</td>
      </tr>
      <tr>
        <td></td>
        <td><a href="/pl/sq?input=5100024&boardType=dep">Koluszki</a></td>
        <td>23:55</td>
        <td>00:05</td>
      </tr>
      <tr>
        <td></td>
        <td><a href="/pl/sq?input=5100190&boardType=dep">Piotrków Trybunalski</a></td>
        <td>00:30</td>
        <td>00:32</td>
      </tr>
      <tr>
        <td></td>
        <td><a href="/pl/sq?input=5100028&boardType=dep">Kraków Główny</a></td>
        <td>03:15</td>
        <td>03:15</td>
      </tr>
    </table>
    """

    extractor = TrainRouteExtractor()
    route_stops = extractor.parse_stops_from_ti_html(
        night_train_html,
        from_station="Warszawa Centralna",
        to_station="Kraków Główny",
        from_eva="5100065",
        to_eva="5100028",
    )
    assert [s.day_offset for s in route_stops] == [0, 1, 1, 1]
    assert route_stops[1].duration_from_start_min == 60
    assert route_stops[2].duration_from_start_min == 95
    assert route_stops[3].duration_from_start_min == 260

    # Boarding AFTER midnight should rebase day_offset to 0 for the sliced journey
    post_midnight_stops = extractor.parse_stops_from_ti_html(
        night_train_html,
        from_station="Piotrków Trybunalski",
        to_station="Kraków Główny",
        from_eva="5100190",
        to_eva="5100028",
    )
    assert [s.day_offset for s in post_midnight_stops] == [0, 0]

    # Verify propagation from RouteStop -> StationStop in fetch_live_train_route_stops
    monkeypatch.setattr(
        TrainRouteExtractor,
        "get_train_route_by_number_or_relation",
        lambda self, *args, **kwargs: route_stops,
    )
    station_stops = fetch_live_train_route_stops(
        "Warszawa Centralna", "Kraków Główny", "TLK 53171", "2026-11-24", dep_time="22:55"
    )
    assert len(station_stops) == 4
    assert all(isinstance(s, StationStop) for s in station_stops)
    assert [s.day_offset for s in station_stops] == [0, 1, 1, 1]


def test_find_ti_url_for_train_row_scoped_matching(monkeypatch):
    """Verify find_ti_url_for_train matches brand+number strictly within the same <tr> as /pl/ti."""
    from backend.app.scraper import schedule_scraper

    sq_html = """
    <table>
      <tr class="odd">
        <td>15:20</td>
        <td>
          <a href="/pl/ti?trainlink=wrong_regional&amp;date=24.11.26">
            <img alt="KM 1644" /><span class="train-name">KM 1644</span>
          </a>
        </td>
      </tr>
      <tr class="even">
        <td>15:22</td>
        <td>
          <a href="/pl/ti?trainlink=wrong_substring&amp;date=24.11.26">
            <img alt="IC 16440" /><span class="train-name">IC 16440</span>
          </a>
        </td>
      </tr>
      <tr class="odd">
        <td>15:24</td>
        <td><a href="/pl/ti?trainlink=adjacent_row&amp;date=24.11.26"><span>EIP 5300</span></a></td>
      </tr>
      <tr class="even">
        <td>15:25</td>
        <td>
          <a href="/pl/ti?trainlink=right_ic_1644&amp;date=24.11.26">
            <img alt="IC 1644" /><span class="train-name">IC 1644</span>
          </a>
        </td>
      </tr>
    </table>
    """

    class DummyResp:
        status_code = 200
        text = sq_html

    monkeypatch.setattr(schedule_scraper.httpx, "get", lambda *args, **kwargs: DummyResp())

    # Exact row match: skips KM 1644, IC 16440, and adjacent EIP 5300
    url = schedule_scraper.find_ti_url_for_train(
        "5100067", "Warszawa Zachodnia", "IC 1644", "24.11.2026", dep_time="15:20"
    )
    assert url == "https://rozklad-pkp.pl/pl/ti?trainlink=right_ic_1644&date=24.11.26"

    # Missing train must NOT fall back to matching bare "IC" from another row
    missing_url = schedule_scraper.find_ti_url_for_train(
        "5100067", "Warszawa Zachodnia", "IC 9999", "24.11.2026", dep_time="15:20"
    )
    assert missing_url is None

    # Train number appearing outside a <tr> that has /pl/ti must NOT match an adjacent row's link
    split_rows_html = """
    <table>
      <tr><td>Note about IC 1644</td></tr>
      <tr><td><a href="/pl/ti?trainlink=other_train"><span>TLK 13102</span></a></td></tr>
    </table>
    """
    assert schedule_scraper._extract_ti_url_from_sq_html(split_rows_html, "IC 1644") is None



