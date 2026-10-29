"""
Mock data provider for deterministic testing of the SeatOptimizer.

Provides 4 key scenarios as described in the architecture documentation:
1. Classic B-C block in carriage 10
2. Overlapping windows with proximity preference
3. Standing corridor fallback (100% booked on one segment)
4. Disconnected EZT units
"""
from typing import Dict, List, Tuple
from backend.app.optimizer.solver import SeatMeta, OptimizerConfig


# Standard 7-station route: A-B-C-D-E-F-G (6 segments)
STATION_NAMES_7 = ["A", "B", "C", "D", "E", "F", "G"]


def scenario_classic_b_c_block() -> Tuple[
    Dict[str, List[int]],  # availability_matrix
    Dict[str, SeatMeta],   # seats_meta
    List[str],             # station_names
    List[int],             # segment_durations
]:
    """
    Scenariusz 1: Klasyczny blok B-C w Wagonie 10.

    Trasa A-B-C-D-E-F-G (6 odcinków, każdy ~30 min).
    - Miejsce 10_12 (Wagon 10, m. 12): wolne A-B, ZAJĘTE B-C, wolne C-G
      availability: [1, 0, 1, 1, 1, 1]
    - Miejsce 10_14 (Wagon 10, m. 14): wolne A-C, zajęte C-G
      availability: [1, 1, 0, 0, 0, 0]

    Oczekiwany wynik: przesiadka wewnątrz wagonu 10 (10_14 -> 10_12 lub 10_12 -> 10_14 -> 10_12).
    """
    station_names = STATION_NAMES_7
    segment_durations = [30, 30, 30, 30, 30, 30]  # 180 min total

    availability_matrix = {
        "10_12": [1, 0, 1, 1, 1, 1],  # wolne A-B, zajęte B-C, wolne C-G
        "10_14": [1, 1, 0, 0, 0, 0],  # wolne A-C (only A-B and B-C)
    }

    seats_meta = {
        "10_12": SeatMeta(seat_uid="10_12", carriage_no=10, seat_no="12", compartment_no=1),
        "10_14": SeatMeta(seat_uid="10_14", carriage_no=10, seat_no="14", compartment_no=1),
    }

    return availability_matrix, seats_meta, station_names, segment_durations


def scenario_overlapping_windows_and_proximity() -> Tuple[
    Dict[str, List[int]],
    Dict[str, SeatMeta],
    List[str],
    List[int],
]:
    """
    Scenariusz 2: Zakładka z wagonami 8, 6, 7 i pułapka dalekiego wagonu 2.

    Trasa A-B-C-D-E-F-G (6 odcinków, każdy po 30 min = 180 min łącznie).
    - Miejsce 8_13 (Wagon 8, m. 13): wolne A-C  [1, 1, 0, 0, 0, 0]
    - Miejsce 6_40 (Wagon 6, m. 40): wolne C-F  [0, 0, 1, 1, 1, 0]
    - Miejsce 7_24 (Wagon 7, m. 24): wolne D-G  [0, 0, 0, 1, 1, 1]
    - Miejsce 2_55 (Wagon 2, m. 55): wolne D-G  [0, 0, 0, 1, 1, 1]

    Oczekiwany wynik algorytmu:
    - Start na 8_13 (A-C)
    - Na stacji C -> przesiadka na 6_40 (C-D) — nie do końca F!
    - Na stacji D -> wcześniejsza przesiadka na 7_24 (D-G) — blisko! Nie do dalekiego 2_55!
    """
    station_names = STATION_NAMES_7
    segment_durations = [30, 30, 30, 30, 30, 30]

    availability_matrix = {
        "8_13": [1, 1, 0, 0, 0, 0],
        "6_40": [0, 0, 1, 1, 1, 0],
        "7_24": [0, 0, 0, 1, 1, 1],
        "2_55": [0, 0, 0, 1, 1, 1],
    }

    seats_meta = {
        "8_13": SeatMeta(seat_uid="8_13", carriage_no=8, seat_no="13"),
        "6_40": SeatMeta(seat_uid="6_40", carriage_no=6, seat_no="40"),
        "7_24": SeatMeta(seat_uid="7_24", carriage_no=7, seat_no="24"),
        "2_55": SeatMeta(seat_uid="2_55", carriage_no=2, seat_no="55"),
    }

    return availability_matrix, seats_meta, station_names, segment_durations


def scenario_standing_corridor_fallback() -> Tuple[
    Dict[str, List[int]],
    Dict[str, SeatMeta],
    List[str],
    List[int],
]:
    """
    Scenariusz 3: Na odcinku C-D (15 min) 100% miejsc jest zajętych.

    Trasa A-B-C-D-E-F-G.
    - Miejsce 5_10: wolne A-C [1, 1, 0, 0, 0, 0], wolne po D-G
    - Miejsce 5_11: wolne D-G [0, 0, 0, 1, 1, 1]
    Na odcinku C-D (segment 2) ŻADNE miejsce nie jest wolne.

    Oczekiwany wynik: algorytm wstawia STANDING na C-D i normalne miejsca przed i po.
    """
    station_names = STATION_NAMES_7
    segment_durations = [30, 30, 15, 30, 30, 30]  # C-D is shorter (15 min)

    availability_matrix = {
        "5_10": [1, 1, 0, 0, 0, 0],  # wolne A-C
        "5_11": [0, 0, 0, 1, 1, 1],  # wolne D-G
    }

    seats_meta = {
        "5_10": SeatMeta(seat_uid="5_10", carriage_no=5, seat_no="10"),
        "5_11": SeatMeta(seat_uid="5_11", carriage_no=5, seat_no="11"),
    }

    return availability_matrix, seats_meta, station_names, segment_durations


def scenario_disconnected_units() -> Tuple[
    Dict[str, List[int]],
    Dict[str, SeatMeta],
    List[str],
    List[int],
    OptimizerConfig,
]:
    """
    Scenariusz 4: Podwójny skład EZT (wagony 1-8 oraz 9-16, brak przejścia między 8 a 9).

    Trasa A-B-C-D (3 odcinki).
    - Miejsce 8_10 (Wagon 8): wolne A-B [1, 0, 0]
    - Miejsce 9_20 (Wagon 9): wolne B-D [0, 1, 1]  <- za granicą EZT!
    - Miejsce 7_15 (Wagon 7): wolne B-D [0, 1, 1]  <- alternatywa w tej samej jednostce
    - Miejsce 6_30 (Wagon 6): wolne B-D [0, 1, 1]  <- też w jednostce 1

    Oczekiwany wynik: algorytm unika 8->9 (przekroczenie granicy EZT) i wybiera 7_15 lub 6_30.
    """
    station_names = ["A", "B", "C", "D"]
    segment_durations = [40, 40, 40]

    availability_matrix = {
        "8_10": [1, 0, 0],
        "9_20": [0, 1, 1],
        "7_15": [0, 1, 1],
        "6_30": [0, 1, 1],
    }

    seats_meta = {
        "8_10": SeatMeta(seat_uid="8_10", carriage_no=8, seat_no="10"),
        "9_20": SeatMeta(seat_uid="9_20", carriage_no=9, seat_no="20"),
        "7_15": SeatMeta(seat_uid="7_15", carriage_no=7, seat_no="15"),
        "6_30": SeatMeta(seat_uid="6_30", carriage_no=6, seat_no="30"),
    }

    config = OptimizerConfig(
        disconnected_carriages=[(8, 9)],  # Brak przejścia między wagonami 8 i 9
    )

    return availability_matrix, seats_meta, station_names, segment_durations, config
