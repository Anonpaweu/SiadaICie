"""Unit tests for special-seat SVG tag parsing, hierarchy, filters, and API recompute."""
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.models.domain import FullTrainAvailabilityMap, SeatFilterPrefs, SegmentAvailabilityMap
from backend.app.optimizer.solver import OptimizerConfig, solve_top_strategies
from backend.app.engine.availability_parser import availability_map_to_matrix
from backend.app.scraper.grm_parser import (
    TAG_BIKE,
    TAG_CHILD,
    TAG_ELDERLY,
    TAG_PREGNANT,
    TAG_PRM_ASSIST,
    TAG_QUIET,
    TAG_WHEELCHAIR,
    TAG_WINDOW,
    is_seat_allowed,
    parse_grm_wagon_svg_with_meta,
    seat_priority_tier,
)
from backend.app.scraper.ebilet_seat_scraper import LiveTrainScanner


client = TestClient(app)


def test_parse_grm_svg_with_meta_extracts_all_special_and_comfort_tags():
    svg = """
    <svg xmlns:eic="http://www.intercity.pl/eic" viewBox="0 0 100 100">
        <g aria-label="Miejsce 31 klasa 2, Wolne, niewybrane" data-class="second class">
            <eic:special ref="1" pl="Miejsce dla osoby z rowerem" carriagecode="24" iconcode="S1">Miejsce dla osoby z rowerem</eic:special>
            <text>31</text>
        </g>
        <g aria-label="Miejsce 32 klasa 2, okno, strefa ciszy, Wolne" data-class="second class"><text>32</text></g>
        <g aria-label="Miejsce 33 klasa 2, miejsce dla kobiety w ciąży, Wolne"><text>33</text></g>
        <g aria-label="Miejsce 34 klasa 2, miejsce dla osoby z dzieckiem, Wolne"><text>34</text></g>
        <g aria-label="Miejsce 35 klasa 2, miejsce dla seniora, Wolne"><text>35</text></g>
        <g aria-label="Miejsce 36 klasa 2, miejsce dla osoby o ograniczonej sprawności, Wolne"><text>36</text></g>
        <g aria-label="Miejsce 37 klasa 2, dla osoby na wózku inwalidzkim, Wolne"><text>37</text></g>
    </svg>
    """
    seats, tags_map = parse_grm_wagon_svg_with_meta(svg, ticket_class=2, include_wheelchair=True)
    assert seats == ["31", "32", "33", "34", "35", "36", "37"]
    assert tags_map["31"] == [TAG_BIKE]
    assert tags_map["32"] == [TAG_QUIET, TAG_WINDOW]
    assert tags_map["33"] == [TAG_PREGNANT]
    assert tags_map["34"] == [TAG_CHILD]
    assert tags_map["35"] == [TAG_ELDERLY]
    assert tags_map["36"] == [TAG_PRM_ASSIST]
    assert tags_map["37"] == [TAG_WHEELCHAIR]

    defaults = SeatFilterPrefs()
    assert defaults.allow_bike is True
    assert is_seat_allowed(tags_map["31"], defaults) is True
    assert is_seat_allowed(tags_map["32"], defaults) is True
    assert is_seat_allowed(tags_map["33"], defaults) is False
    assert is_seat_allowed(tags_map["34"], defaults) is False
    assert is_seat_allowed(tags_map["35"], defaults) is False
    assert is_seat_allowed(tags_map["36"], defaults) is False
    assert is_seat_allowed(tags_map["37"], defaults) is False

    assert seat_priority_tier(tags_map["32"]) == 0
    assert seat_priority_tier(tags_map["31"]) == 1
    assert seat_priority_tier(tags_map["33"]) == 2
    assert seat_priority_tier(tags_map["36"]) == 2
    assert seat_priority_tier(tags_map["37"]) == 3


def test_solver_priority_hierarchy_and_pruning_preserves_normal_and_quiet():
    avail_map = FullTrainAvailabilityMap(
        train_number="IC 6122",
        travel_date="2026-11-29",
        fast_path_used=True,
        total_route_segments=1,
        segments=[
            SegmentAvailabilityMap(
                segment_index=0,
                from_station="Wrocław Główny",
                to_station="Warszawa Centralna",
                departure_time="17:27",
                durationMin=261,
                carriages={"7": ["31", "32", "33", "34"]},
                seat_tags={
                    "7_31": [TAG_BIKE],
                    "7_32": [TAG_WHEELCHAIR],
                    "7_33": [TAG_ELDERLY],
                    "7_34": [TAG_QUIET],
                },
            )
        ],
    )
    avail, meta, stations, durations = availability_map_to_matrix(avail_map)
    all_on = SeatFilterPrefs(
        allow_bike=True,
        allow_pregnant=True,
        allow_child=True,
        allow_elderly=True,
        allow_prm_assist=True,
        allow_wheelchair=True,
    )
    res_all = solve_top_strategies(avail, meta, stations, durations, config=OptimizerConfig(seat_filters=all_on))
    assert res_all[0].steps[0].seat_no == "34"

    # Without quiet/normal seat 34: bike (31) beats elderly (33) and wheelchair (32)
    avail_no_normal = {k: v for k, v in avail.items() if k != "7_34"}
    res_bike = solve_top_strategies(avail_no_normal, meta, stations, durations, config=OptimizerConfig(seat_filters=all_on))
    assert res_bike[0].steps[0].seat_no == "31"
    assert res_bike[0].steps[0].seat_tags == [TAG_BIKE]

    # With elderly/wheelchair only: default filters return empty; enabling elderly picks 33 before 32
    avail_restricted = {k: v for k, v in avail.items() if k in ("7_32", "7_33")}
    res_none = solve_top_strategies(avail_restricted, meta, stations, durations, config=OptimizerConfig(seat_filters=SeatFilterPrefs()))
    assert res_none == []
    res_elderly = solve_top_strategies(
        avail_restricted,
        meta,
        stations,
        durations,
        config=OptimizerConfig(seat_filters=SeatFilterPrefs(allow_elderly=True)),
    )
    assert res_elderly[0].steps[0].seat_no == "33"
    assert res_elderly[0].steps[0].seat_tags == [TAG_ELDERLY]


def test_recompute_endpoint_filters_special_and_wheelchair_without_rescrape(monkeypatch):
    async def mock_scan(self, **kwargs):
        return FullTrainAvailabilityMap(
            train_number="IC 6122",
            travel_date="2026-11-29",
            fast_path_used=True,
            total_route_segments=1,
            segments=[
                SegmentAvailabilityMap(
                    segment_index=0,
                    from_station="Wrocław Główny",
                    to_station="Warszawa Centralna",
                    departure_time="17:27",
                    duration_min=261,
                    carriages={"7": ["31", "37"]},
                    seat_tags={
                        "7_31": [TAG_BIKE],
                        "7_37": [TAG_WHEELCHAIR],
                    },
                    status="OK",
                )
            ],
        )

    monkeypatch.setattr(LiveTrainScanner, "scan_full_train", mock_scan)

    resp = client.post(
        "/api/v1/optimize",
        json={
            "from_station": "Wrocław Główny",
            "to_station": "Warszawa Centralna",
            "date": "2026-11-29",
            "time": "17:27",
            "train_number": "IC 6122",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["strategies"][0]["steps"][0]["seat_no"] == "31"
    assert data["strategies"][0]["steps"][0]["seat_tags"] == [TAG_BIKE]
    assert [s["seat_no"] for s in data["seat_matrix"]] == ["31"]

    # Turn off allow_bike and turn on allow_wheelchair via /api/v1/recompute
    re_resp = client.post(
        "/api/v1/recompute",
        json={
            "profile": "BALANCED",
            "seat_filters": {
                "allow_bike": False,
                "allow_pregnant": False,
                "allow_child": False,
                "allow_elderly": False,
                "allow_prm_assist": False,
                "allow_wheelchair": True,
            },
        },
    )
    assert re_resp.status_code == 200
    re_data = re_resp.json()
    assert re_data["strategies"][0]["steps"][0]["seat_no"] == "37"
    assert re_data["strategies"][0]["steps"][0]["seat_tags"] == [TAG_WHEELCHAIR]
    assert [s["seat_no"] for s in re_data["seat_matrix"]] == ["37"]
