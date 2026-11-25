"""
Tests for the SeatOptimizer (solver.py).

5 rigorous tests verifying the algorithm's intelligence:
1. Single seat covering whole route -> 0 transfers
2. Classic B-C block -> same-carriage transfer in wagon 10
3. Overlapping windows -> early transfer at D, prefer nearby wagon 7 over distant wagon 2
4. Standing fallback -> STANDING step on fully booked segment C-D
5. Disconnected EZT units -> avoid crossing unit boundary 8->9
"""
import pytest
from backend.app.optimizer.solver import (
    solve_top_strategies,
    SeatMeta,
    OptimizerConfig,
)
from backend.app.scraper.mock_provider import (
    scenario_classic_b_c_block,
    scenario_overlapping_windows_and_proximity,
    scenario_standing_corridor_fallback,
    scenario_disconnected_units,
    STATION_NAMES_7,
)


class TestSingleSeatWholeRoute:
    """Test 1: If one seat is free for 100% of route A-G, transfers_count must be 0."""

    def test_single_seat_whole_route(self):
        station_names = STATION_NAMES_7
        segment_durations = [30, 30, 30, 30, 30, 30]

        availability_matrix = {
            "5_20": [1, 1, 1, 1, 1, 1],  # Free the whole route
        }
        seats_meta = {
            "5_20": SeatMeta(seat_uid="5_20", carriage_no=5, seat_no="20"),
        }

        strategies = solve_top_strategies(
            availability_matrix, seats_meta, station_names, segment_durations
        )

        assert len(strategies) >= 1, "Should find at least 1 strategy"
        best = strategies[0]
        assert best.transfers_count == 0, f"Expected 0 transfers, got {best.transfers_count}"
        assert best.total_standing_min == 0, "Should not need to stand"
        assert len(best.steps) == 1, f"Expected 1 step, got {len(best.steps)}"
        assert best.steps[0].seat_uid == "5_20"
        assert best.steps[0].from_station_idx == 0
        assert best.steps[0].to_station_idx == 6


class TestClassicBCBlock:
    """Test 2: Classic B-C block scenario — algorithm must transfer within carriage 10."""

    def test_classic_b_c_block_same_carriage(self):
        avail, meta, names, durations = scenario_classic_b_c_block()

        strategies = solve_top_strategies(avail, meta, names, durations)

        assert len(strategies) >= 1
        best = strategies[0]

        # The algorithm must cover the entire route A -> G
        assert best.steps[0].from_station_idx == 0
        assert best.steps[-1].to_station_idx == 6

        # All carriages used should be wagon 10 only
        assert best.carriages_used == [10], f"Expected [10], got {best.carriages_used}"

        # Must use both seats 10_12 and 10_14
        seat_uids_used = {s.seat_uid for s in best.steps}
        assert "10_12" in seat_uids_used, "Must use seat 10_12"
        assert "10_14" in seat_uids_used, "Must use seat 10_14"

        # Seat 10_12 must be used for the C-G segment (last leg to end)
        final_step = best.steps[-1]
        assert final_step.seat_uid == "10_12", f"Final step should be on 10_12, got {final_step.seat_uid}"
        assert final_step.to_station_idx == 6, "Final step must reach station G"


class TestOverlappingWindowsAndProximity:
    """Test 3: Overlapping windows — early transfer at D, prefer wagon 7 over wagon 2."""

    def test_overlapping_window_early_transfer_and_carriage_proximity(self):
        avail, meta, names, durations = scenario_overlapping_windows_and_proximity()

        strategies = solve_top_strategies(avail, meta, names, durations)

        assert len(strategies) >= 1
        best = strategies[0]

        # Route must be fully covered A -> G
        assert best.steps[0].from_station_idx == 0
        assert best.steps[-1].to_station_idx == 6

        # Step 1: Start on seat 8_13 (A to C)
        step1 = best.steps[0]
        assert step1.seat_uid == "8_13", f"First step should be 8_13, got {step1.seat_uid}"
        assert step1.from_station_idx == 0  # A
        assert step1.to_station_idx == 2    # C

        # Step 2: Transfer at C to seat 6_40
        step2 = best.steps[1]
        assert step2.seat_uid == "6_40", f"Second step should be 6_40, got {step2.seat_uid}"
        assert step2.from_station_idx == 2  # C

        # KEY: Step 2 should end at D (not F!), enabling early transfer
        assert step2.to_station_idx == 3, (
            f"Second step should end at station D (idx 3), got {step2.to_station_idx}. "
            "Algorithm should prefer early transfer to a seat free until the end."
        )

        # Step 3: Transfer at D to seat 7_24 (nearby wagon 7, NOT distant wagon 2!)
        step3 = best.steps[2]
        assert step3.seat_uid == "7_24", (
            f"Third step should be 7_24 (wagon 7, nearby), got {step3.seat_uid}. "
            "Algorithm must prefer wagon 7 over distant wagon 2!"
        )
        assert step3.from_station_idx == 3  # D
        assert step3.to_station_idx == 6    # G (end)


class TestStandingFallback:
    """Test 4: When 100% seats are booked on C-D, algorithm must insert STANDING step."""

    def test_standing_fallback_when_100_percent_booked(self):
        avail, meta, names, durations = scenario_standing_corridor_fallback()

        strategies = solve_top_strategies(avail, meta, names, durations)

        assert len(strategies) >= 1, "Algorithm must NOT return None or crash"
        best = strategies[0]

        # Route must be fully covered A -> G
        assert best.steps[0].from_station_idx == 0
        assert best.steps[-1].to_station_idx == 6

        # There must be a STANDING step covering segment C-D (index 2)
        standing_steps = [s for s in best.steps if s.is_standing]
        assert len(standing_steps) >= 1, "Must have at least one STANDING step"

        # The standing step must cover segment index 2 (C-D)
        standing_segments = set()
        for ss in standing_steps:
            for seg_idx in range(ss.from_station_idx, ss.to_station_idx):
                standing_segments.add(seg_idx)
        assert 2 in standing_segments, "STANDING must cover segment 2 (C-D)"

        # Standing minutes should be > 0
        assert best.total_standing_min > 0

        # There should also be normal seated steps before C and after D
        seated_steps = [s for s in best.steps if not s.is_standing]
        assert len(seated_steps) >= 2, "Should have seated steps before and after standing"


class TestDisconnectedEZTsPenalty:
    """Test 5: Disconnected EZT units — must avoid crossing wagon 8 -> 9 boundary."""

    def test_disconnected_ezts_penalty(self):
        avail, meta, names, durations, config = scenario_disconnected_units()

        strategies = solve_top_strategies(
            avail, meta, names, durations, config=config
        )

        assert len(strategies) >= 1
        best = strategies[0]

        # Route must be fully covered A -> D
        assert best.steps[0].from_station_idx == 0
        assert best.steps[-1].to_station_idx == 3

        # First step should start on wagon 8 (seat 8_10)
        assert best.steps[0].seat_uid == "8_10"

        # Second step must NOT be seat 9_20 (crosses EZT boundary)
        # It should be 7_15 or 6_30 (same unit as wagon 8)
        second_step = best.steps[1]
        assert second_step.seat_uid != "9_20", (
            f"Algorithm chose seat {second_step.seat_uid} crossing the EZT boundary! "
            "Must prefer 7_15 or 6_30 in the same unit."
        )
        assert second_step.seat_uid in ("7_15", "6_30"), (
            f"Expected 7_15 or 6_30, got {second_step.seat_uid}"
        )


class TestProfilesAndCarriageDiversity:
    """Test 6 & 7: Optimization profiles affect penalties and Top-3 prefers distinct carriages."""

    def test_top_k_prefers_distinct_carriages_for_full_route_seats(self):
        station_names = ["A", "B", "C", "D"]
        segment_durations = [30, 30, 30]
        avail = {
            "3_11": [1, 1, 1],
            "3_12": [1, 1, 1],
            "5_21": [1, 1, 1],
            "7_31": [1, 1, 1],
        }
        meta = {
            uid: SeatMeta(seat_uid=uid, carriage_no=int(uid.split("_")[0]), seat_no=uid.split("_")[1], compartment_no=1)
            for uid in avail
        }
        strategies = solve_top_strategies(avail, meta, station_names, segment_durations, top_k=3)
        assert len(strategies) == 3
        chosen_carriages = [s.carriages_used[0] for s in strategies]
        assert len(set(chosen_carriages)) == 3, f"Expected 3 distinct carriages in Top-3, got {chosen_carriages}"

    def test_minimal_walking_profile_penalizes_far_carriage_heavily(self):
        from backend.app.models.domain import OptimizationProfile
        from backend.app.optimizer.solver import calculate_transfer_penalty

        s_a = SeatMeta(seat_uid="3_11", carriage_no=3, seat_no="11", compartment_no=1)
        s_b = SeatMeta(seat_uid="8_55", carriage_no=8, seat_no="55", compartment_no=5)

        p_bal = calculate_transfer_penalty(s_a, s_b, False, OptimizerConfig(profile=OptimizationProfile.BALANCED))
        p_walk = calculate_transfer_penalty(s_a, s_b, False, OptimizerConfig(profile=OptimizationProfile.MINIMAL_WALKING))
        p_trans = calculate_transfer_penalty(s_a, s_b, False, OptimizerConfig(profile=OptimizationProfile.MINIMAL_TRANSFERS))

        assert p_walk > p_bal * 2.5
        assert p_trans != p_bal


class TestStandingMergingAndPluralization:
    """Tests for merging adjacent standing blocks, accurate transfer counting, and Polish pluralization."""

    def test_consecutive_standing_segments_merged_and_transfers_count_accurate(self):
        from backend.app.optimizer.solver import calculate_transfer_penalty

        # 5 segments across 6 stations: segments 1, 2, 3 (Wrzesnia->Slupca->Konin->Kolo) completely booked
        station_names = [
            "Poznań Główny",
            "Września",
            "Słupca",
            "Konin",
            "Koło",
            "Warszawa Centralna",
        ]
        segment_durations = [44, 13, 15, 14, 95]  # 13+15+14 = 42m continuous corridor
        avail = {
            "11_14": [1, 0, 0, 0, 0],
            "12_36": [0, 0, 0, 0, 1],
        }
        meta = {
            "11_14": SeatMeta(seat_uid="11_14", carriage_no=11, seat_no="14", compartment_no=1),
            "12_36": SeatMeta(seat_uid="12_36", carriage_no=12, seat_no="36", compartment_no=3),
        }

        strategies = solve_top_strategies(avail, meta, station_names, segment_durations)
        assert len(strategies) >= 1
        best = strategies[0]

        # Must merge segments 1, 2, 3 into ONE standing step Wrzesnia -> Kolo (42 min)
        assert len(best.steps) == 3
        assert best.steps[0].seat_uid == "11_14"
        assert best.steps[1].is_standing is True
        assert best.steps[1].from_station_name == "Września"
        assert best.steps[1].to_station_name == "Koło"
        assert best.steps[1].duration_min == 42
        assert best.steps[2].seat_uid == "12_36"

        # 2 seated steps -> 1 transfer (not inflated by standing intermediate hops)
        assert best.transfers_count == 1
        assert best.total_standing_min == 42
        assert "1 przesiadka" in best.title
        assert "42 min" in best.title
        assert len(best.purchase_instructions) == 3
        assert len(best.tickets_to_buy) == 2

        # Standing -> Standing transition carries zero penalty
        st_meta = SeatMeta(seat_uid="STANDING", carriage_no=0, seat_no="KORYTARZ")
        assert calculate_transfer_penalty(st_meta, st_meta, True, OptimizerConfig()) == 0.0

    def test_format_transfers_pl_all_cases(self):
        from backend.app.optimizer.solver import format_transfers_pl

        assert format_transfers_pl(0) == "bez przesiadek"
        assert format_transfers_pl(1) == "1 przesiadka"
        assert format_transfers_pl(2) == "2 przesiadki"
        assert format_transfers_pl(3) == "3 przesiadki"
        assert format_transfers_pl(4) == "4 przesiadki"
        assert format_transfers_pl(5) == "5 przesiadek"
        assert format_transfers_pl(11) == "11 przesiadek"
        assert format_transfers_pl(12) == "12 przesiadek"
        assert format_transfers_pl(14) == "14 przesiadek"
        assert format_transfers_pl(21) == "21 przesiadek"
        assert format_transfers_pl(22) == "22 przesiadki"
        assert format_transfers_pl(25) == "25 przesiadek"

    def test_short_route_partial_availability_never_returns_empty_strategies(self):
        # Reproduces IC 7200 Poznań Główny -> Konin (56 min total, seg 3 Słupca->Konin sold out)
        station_names = ["Poznań Główny", "Swarzędz", "Września", "Słupca", "Konin"]
        segment_durations = [9, 21, 12, 14]
        avail = {
            "12_13": [1, 0, 0, 0],
            "14_32": [0, 1, 1, 0],
        }
        meta = {
            "12_13": SeatMeta(seat_uid="12_13", carriage_no=12, seat_no="13", compartment_no=1),
            "14_32": SeatMeta(seat_uid="14_32", carriage_no=14, seat_no="32", compartment_no=3),
        }

        strategies = solve_top_strategies(avail, meta, station_names, segment_durations)
        assert len(strategies) >= 1
        assert all(any(not step.is_standing for step in st.steps) for st in strategies)
        # Rank 1 or 2 offers the longest seated block (14_32 on Swarzędz -> Słupca)
        all_step_uids = {step.seat_uid for st in strategies for step in st.steps}
        assert "14_32" in all_step_uids
