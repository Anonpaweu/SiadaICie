"""
Seat Optimization Solver (DAG / Dynamic Programming).

Finds optimal seat strategies for a train journey, handling:
- Continuous availability blocks (intervals)
- Overlapping windows with early transfers
- Quadratic carriage distance penalties
- Standing corridor fallback
- Disconnected EZT unit penalties
- Top-K diverse strategy generation
"""
import math
from typing import List, Dict, Tuple, Optional, Set
from pydantic import BaseModel


class SeatMeta(BaseModel):
    seat_uid: str               # np. "10_12" (Wagon 10, Miejsce 12)
    carriage_no: int            # np. 10
    seat_no: str                # np. "12"
    compartment_no: Optional[int] = None  # Numer przedziału (lub rzędu w bezprzedziałowym)
    position: str = "MIDDLE"    # "WINDOW", "AISLE", "MIDDLE"


class AvailabilityBlock(BaseModel):
    block_id: str
    seat: SeatMeta
    start_seg: int              # Indeks stacji początkowej bloku (np. 0 dla stacji A)
    end_seg: int                # Indeks stacji końcowej bloku (np. 2 dla stacji C)
    duration_min: int           # Łączny czas jazdy w minutach na tym odcinku
    is_standing: bool = False   # True tylko dla sztucznego węzła ratunkowego (korytarz)


class PathStep(BaseModel):
    seat_uid: str
    carriage_no: int
    seat_no: str
    from_station_idx: int
    to_station_idx: int
    from_station_name: str
    to_station_name: str
    duration_min: int
    is_standing: bool = False
    transfer_Instruction: Optional[str] = None


class OptimizationStrategy(BaseModel):
    rank: int
    title: str
    total_score: float
    transfers_count: int
    total_standing_min: int
    carriages_used: List[int]
    steps: List[PathStep]


class OptimizerConfig(BaseModel):
    w_duration: float = 1000.0
    w_final_bonus: float = 250.0
    p_base_transfer: float = 120.0
    p_standing_per_min: float = 25.0
    prefer_window: bool = False
    w_wars_crossing_penalty: float = 80.0
    wars_carriage_no: Optional[int] = None
    # Lista par wagonów bez przejścia międzywagonowego (np. podwójny Flirt/Pendolino: [(8, 9)])
    disconnected_carriages: List[Tuple[int, int]] = []


def calculate_transfer_penalty(
    seat_a: SeatMeta,
    seat_b: SeatMeta,
    is_standing_b: bool,
    config: OptimizerConfig
) -> float:
    """
    Oblicza koszt fizycznej przesiadki z miejsca A na miejsce B.
    Silnie premiuje ten sam przedział / ten sam wagon i nakłada karę kwadratową za dalekie spacery.
    """
    if is_standing_b or seat_a.seat_uid == "STANDING" or seat_b.seat_uid == "STANDING":
        return config.p_base_transfer + 100.0

    if seat_a.seat_uid == seat_b.seat_uid:
        return 0.0

    c_a, c_b = seat_a.carriage_no, seat_b.carriage_no
    delta_c = abs(c_a - c_b)

    # Sprawdź, czy między wagonem A i B nie ma fizycznej blokady przejścia (np. 2 złączone jednostki EZT)
    for disc_1, disc_2 in config.disconnected_carriages:
        low, high = min(disc_1, disc_2), max(disc_1, disc_2)
        if min(c_a, c_b) <= low and max(c_a, c_b) >= high:
            # Wymaga wyjścia na peron na stacji pośredniej! Bardzo wysoka kara.
            return config.p_base_transfer + 600.0

    # Przypadek 1: Ten sam wagon
    if delta_c == 0:
        if (
            seat_a.compartment_no is not None
            and seat_b.compartment_no is not None
            and seat_a.compartment_no == seat_b.compartment_no
        ):
            dist_penalty = 15.0  # Ten sam przedział / rząd - wystarczy się przesiąść obok
        else:
            comp_diff = abs((seat_a.compartment_no or 1) - (seat_b.compartment_no or 5))
            dist_penalty = 35.0 + 2.0 * comp_diff
        return config.p_base_transfer + dist_penalty

    # Przypadek 2: Zmiana wagonu (nieliniowa kara kwadratowa)
    dist_penalty = 60.0 * delta_c + 25.0 * (delta_c ** 2)

    # Dodatkowa kara za przechodzenie przez wagon gastronomiczny WARS
    if config.wars_carriage_no is not None:
        if min(c_a, c_b) < config.wars_carriage_no < max(c_a, c_b):
            dist_penalty += config.w_wars_crossing_penalty

    return config.p_base_transfer + dist_penalty


def generate_candidate_blocks(
    availability_matrix: Dict[str, List[int]],
    seats_meta: Dict[str, SeatMeta],
    segment_durations: List[int]
) -> List[AvailabilityBlock]:
    """
    Generuje wszystkie pod-przedziały [s_start, s_end) dla każdego miejsca, które jest ciągłe wolne.
    Dzięki wygenerowaniu wszystkich pod-przedziałów (a nie tylko maksymalnych),
    algorytm w naturalny sposób obsługuje przesiadki na zakładkę.
    """
    num_segments = len(segment_durations)
    blocks: List[AvailabilityBlock] = []

    for seat_uid, seg_vector in availability_matrix.items():
        meta = seats_meta[seat_uid]
        # Znajdź wszystkie pary (i, j) gdzie miejsce jest wolne na każdym odcinku k in [i, j-1]
        for i in range(num_segments):
            if seg_vector[i] != 1:
                continue
            current_dur = 0
            for j in range(i + 1, num_segments + 1):
                if seg_vector[j - 1] != 1:
                    break
                current_dur += segment_durations[j - 1]
                blocks.append(
                    AvailabilityBlock(
                        block_id=f"{seat_uid}_{i}_{j}",
                        seat=meta,
                        start_seg=i,
                        end_seg=j,
                        duration_min=current_dur,
                        is_standing=False,
                    )
                )

    # Dodaj awaryjne bloki "STANDING_CORRIDOR" na pojedyncze odcinki
    standing_meta = SeatMeta(seat_uid="STANDING", carriage_no=0, seat_no="KORYTARZ")
    for i in range(num_segments):
        blocks.append(
            AvailabilityBlock(
                block_id=f"STANDING_{i}_{i+1}",
                seat=standing_meta,
                start_seg=i,
                end_seg=i + 1,
                duration_min=segment_durations[i],
                is_standing=True,
            )
        )

    return blocks


def score_single_block(
    block: AvailabilityBlock,
    total_trip_min: int,
    num_segments: int,
    config: OptimizerConfig
) -> float:
    """Oblicza punkty użyteczności za pojedynczy blok ciągłego siedzenia."""
    if block.is_standing:
        return -config.p_standing_per_min * block.duration_min

    # Nieliniowa premia za długi nieprzerwany czas siedzenia (wykładnik 1.6)
    ratio = block.duration_min / max(1.0, float(total_trip_min))
    score = config.w_duration * (ratio ** 1.6)

    # Bonus za dojechanie na tym miejscu do samego końca trasy (stacja G)
    if block.end_seg == num_segments:
        score += config.w_final_bonus * ratio

    # Bonus za preferencję okna/korytarza
    if config.prefer_window and block.seat.position == "WINDOW":
        score += 20.0 * ratio

    return score


def find_optimal_path(
    blocks: List[AvailabilityBlock],
    station_names: List[str],
    segment_durations: List[int],
    config: OptimizerConfig,
    excluded_primary_seats: Set[str] = set()
) -> Optional[Tuple[float, List[AvailabilityBlock]]]:
    """
    Rozwiązuje problem najdłuższej (najbardziej opłacalnej) ścieżki w grafie DAG od stacji 0 do N
    za pomocą programowania dynamicznego.
    """
    num_segments = len(segment_durations)
    total_trip_min = sum(segment_durations)

    # Filtruj bloki należące do wykluczonych miejsc
    active_blocks = [
        b for b in blocks
        if (b.is_standing or b.seat.seat_uid not in excluded_primary_seats)
    ]

    # Grupuj bloki według stacji początkowej start_seg
    blocks_by_start: Dict[int, List[AvailabilityBlock]] = {i: [] for i in range(num_segments)}
    for b in active_blocks:
        blocks_by_start[b.start_seg].append(b)

    # dp[block_id] = (najlepszy_wynik_od_tego_bloku_do_konca, kolejny_block_id)
    dp: Dict[str, Tuple[float, Optional[str]]] = {}
    block_map: Dict[str, AvailabilityBlock] = {b.block_id: b for b in active_blocks}

    # Przetwarzamy od końca trasy w dół do stacji 0 (Bottom-Up DP)
    for s_idx in range(num_segments - 1, -1, -1):
        for b in blocks_by_start[s_idx]:
            b_score = score_single_block(b, total_trip_min, num_segments, config)

            # Jeśli ten blok kończy się na stacji docelowej G (num_segments), to koniec ścieżki
            if b.end_seg == num_segments:
                dp[b.block_id] = (b_score, None)
                continue

            # W przeciwnym razie szukamy najlepszego kolejnego bloku zaczynającego się w b.end_seg
            best_next_val = -1e9
            best_next_id = None

            for next_b in blocks_by_start[b.end_seg]:
                # Nie łącz dwóch bloków tego samego miejsca
                if not b.is_standing and b.seat.seat_uid == next_b.seat.seat_uid:
                    continue

                if next_b.block_id not in dp:
                    continue

                penalty = calculate_transfer_penalty(b.seat, next_b.seat, next_b.is_standing, config)
                candidate_val = b_score - penalty + dp[next_b.block_id][0]

                if candidate_val > best_next_val:
                    best_next_val = candidate_val
                    best_next_id = next_b.block_id

            if best_next_id is not None:
                dp[b.block_id] = (best_next_val, best_next_id)

    # Wybierz najlepszy blok startujący na stacji początkowej A (start_seg == 0)
    best_total_score = -1e9
    best_start_id = None

    for b in blocks_by_start[0]:
        if b.block_id in dp and dp[b.block_id][0] > best_total_score:
            best_total_score = dp[b.block_id][0]
            best_start_id = b.block_id

    if best_start_id is None:
        return None

    # Rekonstrukcja ścieżki
    path: List[AvailabilityBlock] = []
    curr_id: Optional[str] = best_start_id
    while curr_id is not None:
        curr_block = block_map[curr_id]
        path.append(curr_block)
        curr_id = dp[curr_id][1]

    return best_total_score, path


def solve_top_strategies(
    availability_matrix: Dict[str, List[int]],
    seats_meta: Dict[str, SeatMeta],
    station_names: List[str],
    segment_durations: List[int],
    config: Optional[OptimizerConfig] = None,
    top_k: int = 3
) -> List[OptimizationStrategy]:
    """
    Główny punkt wejściowy silnika optymalizacji. Zwraca Top K różnych strategii podróży.
    """
    if config is None:
        config = OptimizerConfig()

    blocks = generate_candidate_blocks(availability_matrix, seats_meta, segment_durations)
    strategies: List[OptimizationStrategy] = []
    excluded_seats: Set[str] = set()

    for rank in range(1, top_k + 1):
        res = find_optimal_path(blocks, station_names, segment_durations, config, excluded_seats)
        if res is None:
            break

        total_score, path = res
        steps: List[PathStep] = []
        carriages_used: List[int] = []
        standing_min = 0

        for idx, b in enumerate(path):
            if b.is_standing:
                standing_min += b.duration_min
            else:
                if b.seat.carriage_no not in carriages_used:
                    carriages_used.append(b.seat.carriage_no)

            instruction = None
            if idx > 0:
                prev_b = path[idx - 1]
                st_name = station_names[b.start_seg]
                if b.is_standing:
                    instruction = f"Na stacji {st_name} zwolnij miejsce {prev_b.seat.seat_no} (przejdź na korytarz na {b.duration_min} min)."
                elif prev_b.is_standing:
                    instruction = f"Na stacji {st_name} zajmij miejsce {b.seat.seat_no} w wagonie {b.seat.carriage_no}."
                elif prev_b.seat.carriage_no == b.seat.carriage_no:
                    instruction = f"Na stacji {st_name} przesiądź się w tym samym wagonie ({b.seat.carriage_no}) z miejsca {prev_b.seat.seat_no} na miejsce {b.seat.seat_no}."
                else:
                    instruction = f"Na stacji {st_name} przejdź z wagonu {prev_b.seat.carriage_no} (m. {prev_b.seat.seat_no}) do wagonu {b.seat.carriage_no} na miejsce {b.seat.seat_no}."

            steps.append(
                PathStep(
                    seat_uid=b.seat.seat_uid,
                    carriage_no=b.seat.carriage_no,
                    seat_no=b.seat.seat_no,
                    from_station_idx=b.start_seg,
                    to_station_idx=b.end_seg,
                    from_station_name=station_names[b.start_seg],
                    to_station_name=station_names[b.end_seg],
                    duration_min=b.duration_min,
                    is_standing=b.is_standing,
                    transfer_Instruction=instruction,
                )
            )

        # Wyklucz najdłużej używane miejsce z tej ścieżki w kolejnej iteracji
        non_standing_blocks = [b for b in path if not b.is_standing]
        if non_standing_blocks:
            longest_block = max(non_standing_blocks, key=lambda x: x.duration_min)
            excluded_seats.add(longest_block.seat.seat_uid)
        else:
            break

        transfers_count = max(0, len(path) - 1)
        if transfers_count == 0 and standing_min == 0:
            title = "100% Trasy bez przesiadek (Złoty Strzał)"
        elif len(carriages_used) == 1 and standing_min == 0:
            title = f"Przesiadka w tym samym wagonie (Wagon {carriages_used[0]})"
        else:
            title = f"Optymalna kombinacja ({transfers_count} przesiadka/i)"

        strategies.append(
            OptimizationStrategy(
                rank=rank,
                title=title,
                total_score=round(total_score, 2),
                transfers_count=transfers_count,
                total_standing_min=standing_min,
                carriages_used=carriages_used,
                steps=steps,
            )
        )

    return strategies
