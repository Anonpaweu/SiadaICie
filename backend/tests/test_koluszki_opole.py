"""
Tests for parsing and solving the Koluszki -> Opole route puzzle (Tasks 017–022, 031–033).
"""
import pytest
from backend.app.engine.availability_parser import (
    parse_availability_text,
    format_availability_map,
    availability_map_to_matrix,
    normalize_station_name,
)

KOLUSZKI_OPOLE_RAW = """
1. Koluszki – Piotrków: w5: [82], w6: [15, 53]
2. Piotrków – Radomsko (15:12): w3: [91], w4: [76], w5: [32, 33, 65, 74, 82, 88], w6: [15, 66, 68, 78], w7: [25, 31, 33, 35, 37, 51, 53, 55, 65, 66, 75], w8: [33]
3. Radomsko – Częstochowa (15:40): w3: [84, 87, 91], w4: [76], w5: [32, 33, 45, 65, 74, 76, 82, 88], w6: [15, 68, 78, 86], w7: [25, 31, 33, 35, 37, 51, 53, 54, 55, 65, 66, 75], w8: [33, 73, 84, 92, 98]
4. Częstochowa – Częstochowa str. (15:46): w3: [84], w5: [24, 68, 74, 75, 76, 77, 81, 82, 83, 85, 88], w6: [43, 64, 78], w7: [26, 36, 46, 51, 52, 53, 54, 61, 63, 65, 66, 68, 71, 75], w8: [36, 54, 61, 63, 98]
5. Częstochowa str – Lubliniec (16:06): w3: [84], w5: [81, 83, 88]
6. Lubliniec – Opole: w3: [83, 84, 106], w4: [11], w6: [74], w7: [28, 51, 53], w8: [46, 72]
"""


def test_parse_koluszki_opole():
    """
    Task 018: Checks that the parser detects exactly 6 segments:
      1. Koluszki -> Piotrków
      2. Piotrków -> Radomsko (15:12)
      3. Radomsko -> Częstochowa (15:40)
      4. Częstochowa -> Częstochowa str. (15:46)
      5. Częstochowa str -> Lubliniec (16:06)
      6. Lubliniec -> Opole
    """
    avail_map = parse_availability_text(KOLUSZKI_OPOLE_RAW)
    assert len(avail_map.segments) == 6

    # Verify segment endpoints and departure times
    s0 = avail_map.segments[0]
    assert s0.from_station == "Koluszki"
    assert s0.to_station == "Piotrków"

    s1 = avail_map.segments[1]
    assert s1.from_station == "Piotrków"
    assert s1.to_station == "Radomsko"
    assert s1.departure_time == "15:12"

    s2 = avail_map.segments[2]
    assert s2.from_station == "Radomsko"
    assert s2.to_station == "Częstochowa"
    assert s2.departure_time == "15:40"

    s3 = avail_map.segments[3]
    assert s3.from_station == "Częstochowa"
    assert s3.to_station == "Częstochowa Stradom"
    assert s3.departure_time == "15:46"

    s4 = avail_map.segments[4]
    assert s4.from_station == "Częstochowa Stradom"
    assert s4.to_station == "Lubliniec"
    assert s4.departure_time == "16:06"

    s5 = avail_map.segments[5]
    assert s5.from_station == "Lubliniec"
    assert s5.to_station == "Opole"


def test_station_normalization():
    """
    Task 019: Verifies that 'Częstochowa str.' and 'Częstochowa str'
    are normalized to the exact same station name.
    """
    norm1 = normalize_station_name("Częstochowa str.")
    norm2 = normalize_station_name("Częstochowa str")
    assert norm1 == norm2
    assert norm1 == "Częstochowa Stradom"


def test_segment_seats_and_matrix_statuses():
    """
    Tasks 020, 021, 022:
      - Task 020: On Koluszki - Piotrków, only 3 seats are free: (W5, 82), (W6, 15), (W6, 53)
      - Task 021: (W5, 82) has status 1 (AVAILABLE) on segments 0, 1, 2, 3 and 0 (OCCUPIED) on 4, 5
      - Task 022: (W3, 84) has status 1 (AVAILABLE) on segments 2, 3, 4, 5 (from Radomsko to Opole)
    """
    avail_map = parse_availability_text(KOLUSZKI_OPOLE_RAW)

    # Task 020: Check segment 0 seats
    s0 = avail_map.segments[0]
    all_s0_seats = []
    for c_no, seats in s0.carriages.items():
        for s in seats:
            all_s0_seats.append((c_no, s))
    assert set(all_s0_seats) == {("5", "82"), ("6", "15"), ("6", "53")}

    # Build matrix
    matrix, meta, stations, durations = availability_map_to_matrix(avail_map)

    # Task 021: W5 m82 available on segments 0, 1, 2, 3 and occupied on 4, 5
    assert "5_82" in matrix
    assert matrix["5_82"] == [1, 1, 1, 1, 0, 0]

    # Task 022: W3 m84 available on segments 2, 3, 4, 5
    assert "3_84" in matrix
    assert matrix["3_84"] == [0, 0, 1, 1, 1, 1]


def test_multiline_and_slash_formatting():
    """
    Task 014 & 015: Verifies support for multiline 'W3 m87/m84/m91' and formatter round-trip.
    """
    sample_text = """
    Piotrków - Radomsko 15:12
    W3 m91
    W4 m76
    W5 m32/m33/m65/m74/m82/m88
    """
    parsed = parse_availability_text(sample_text)
    assert len(parsed.segments) == 1
    seg = parsed.segments[0]
    assert seg.from_station == "Piotrków"
    assert seg.to_station == "Radomsko"
    assert seg.departure_time == "15:12"
    assert seg.carriages["3"] == ["91"]
    assert seg.carriages["4"] == ["76"]
    assert seg.carriages["5"] == ["32", "33", "65", "74", "82", "88"]

    formatted = format_availability_map(parsed)
    assert "Piotrków - Radomsko 15:12" in formatted
    assert "W3 m91" in formatted
    assert "W5 m32/m33/m65/m74/m82/m88" in formatted


def test_solve_koluszki_opole_optimal():
    """
    Tasks 031, 032, 034:
    Verifies that the optimizer finds the optimal route with exactly 1 transfer (2 tickets):
      - Step 1: W5 m82 (Koluszki -> Częstochowa Stradom) or W6 m15
      - Step 2: W3 m84 (-> Opole)
      - tickets_to_buy contains the formatted tickets list.
    """
    from backend.app.optimizer.solver import solve_top_strategies

    avail_map = parse_availability_text(KOLUSZKI_OPOLE_RAW)
    matrix, meta, stations, durations = availability_map_to_matrix(avail_map)
    strategies = solve_top_strategies(matrix, meta, stations, durations)

    assert len(strategies) >= 1
    best = strategies[0]

    # Task 032: Must require exactly 1 transfer (2 steps/tickets)
    assert best.transfers_count == 1, f"Expected 1 transfer, got {best.transfers_count}"
    assert len(best.steps) == 2, f"Expected 2 steps, got {len(best.steps)}"
    assert best.total_standing_min == 0

    step1, step2 = best.steps[0], best.steps[1]
    # First block: W5 m82 (or W6 m15)
    assert step1.seat_uid in ("5_82", "6_15")
    # Second block: W3 m84 until Opole
    assert step2.seat_uid == "3_84"
    assert step2.to_station_name == "Opole"

    # Task 034: tickets_to_buy
    assert len(best.tickets_to_buy) == 2
    assert "Bilet 1:" in best.tickets_to_buy[0]
    assert "Bilet 2:" in best.tickets_to_buy[1]
    assert "3" in best.tickets_to_buy[1] and "84" in best.tickets_to_buy[1]


def test_solve_koluszki_lubliniec_same_carriage():
    """
    Task 033: Truncating the route to Koluszki -> Lubliniec (first 5 segments).
    The algorithm must prefer staying in the SAME Carriage 5:
    W5 m82 (Koluszki -> Częstochowa Str) followed by W5 m81 (or m83/m88) to Lubliniec!
    """
    from backend.app.optimizer.solver import solve_top_strategies

    avail_map = parse_availability_text(KOLUSZKI_OPOLE_RAW)
    # Take first 5 segments (Koluszki to Lubliniec)
    truncated_map = parse_availability_text("\n".join(KOLUSZKI_OPOLE_RAW.strip().splitlines()[:5]))
    matrix, meta, stations, durations = availability_map_to_matrix(truncated_map)
    strategies = solve_top_strategies(matrix, meta, stations, durations)

    assert len(strategies) >= 1
    best = strategies[0]

    # Must stay in carriage 5 for the whole journey with minimal transfer in same compartment!
    assert best.carriages_used == [5], f"Expected only carriage [5], got {best.carriages_used}"
    assert len(best.steps) == 2
    assert best.steps[0].seat_uid == "5_82"
    assert best.steps[1].seat_uid in ("5_81", "5_83", "5_88")
