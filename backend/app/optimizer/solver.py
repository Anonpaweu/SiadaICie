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
    compartment: Optional[int] = None
    alternative_seats_in_same_carriage: List[str] = []


class OptimizationStrategy(BaseModel):
    rank: int
    title: str
    total_score: float
    transfers_count: int
    total_standing_min: int
    carriages_used: List[int]
    steps: List[PathStep]
    tickets_to_buy: List[str] = []
    purchase_instructions: List[str] = []


class OptimizerConfig(BaseModel):
    w_duration: float = 1000.0
    w_final_bonus: float = 250.0
    p_base_transfer: float = 1000.0  # W_TICKET = 1000.0
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
    Baza: W_TICKET = 1000.0 za każdą przesiadkę.
    Odległość fizyczna:
    - ten sam wagon i ten sam przedział: +15 pkt
    - ten sam wagon, inny przedział: +40 pkt
    - inny wagon: +100 * (delta_w ** 2)
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
            return config.p_base_transfer + 600.0

    # Przypadek 1: Ten sam wagon
    if delta_c == 0:
        if (
            seat_a.compartment_no is not None
            and seat_b.compartment_no is not None
            and seat_a.compartment_no == seat_b.compartment_no
        ):
            dist_penalty = 15.0  # Ten sam przedział / rząd
        else:
            dist_penalty = 40.0  # Inny przedział w tym samym wagonie
        return config.p_base_transfer + dist_penalty

    # Przypadek 2: Zmiana wagonu (nieliniowa kara kwadratowa 100 * delta^2)
    dist_penalty = 100.0 * (delta_c ** 2)

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

            # Find alternative seats in same carriage on exact same segment
            alt_seats = []
            if not b.is_standing:
                for o_uid, o_vec in availability_matrix.items():
                    if o_uid != b.seat.seat_uid:
                        o_meta = seats_meta.get(o_uid)
                        if o_meta and o_meta.carriage_no == b.seat.carriage_no:
                            if all(o_vec[k] == 1 for k in range(b.start_seg, b.end_seg)):
                                alt_seats.append(o_meta.seat_no)

            comp_no = b.seat.compartment_no
            if comp_no is None and b.seat.seat_no.isdigit():
                s_int = int(b.seat.seat_no)
                comp_no = s_int // 10 if s_int >= 10 else 1

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
                    compartment=comp_no,
                    alternative_seats_in_same_carriage=sorted(alt_seats, key=lambda x: int(x) if x.isdigit() else 99),
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

        # Generate tickets_to_buy and purchase_instructions
        tickets_to_buy: List[str] = []
        purchase_instructions: List[str] = []
        ticket_nr = 1
        for idx, s in enumerate(steps):
            if not s.is_standing:
                tickets_to_buy.append(
                    f"Bilet {ticket_nr}: {s.from_station_name} -> {s.to_station_name} (Wagon {s.carriage_no}, Miejsce {s.seat_no})"
                )
                instr = f"Krok {ticket_nr}: Kup bilet na odcinek {s.from_station_name} -> {s.to_station_name}: wybierz Wagon {s.carriage_no}, Miejsce {s.seat_no}"
                if s.alternative_seats_in_same_carriage:
                    instr += f" (lub inne wolne w tym wagonie: {', '.join('m' + x for x in s.alternative_seats_in_same_carriage[:3])})"
                purchase_instructions.append(instr)
                ticket_nr += 1
            else:
                purchase_instructions.append(
                    f"Odcinek {s.from_station_name} -> {s.to_station_name}: brak miejsc siedzących – przejście na korytarz ({s.duration_min} min)"
                )

        strategies.append(
            OptimizationStrategy(
                rank=rank,
                title=title,
                total_score=round(total_score, 2),
                transfers_count=transfers_count,
                total_standing_min=standing_min,
                carriages_used=carriages_used,
                steps=steps,
                tickets_to_buy=tickets_to_buy,
                purchase_instructions=purchase_instructions,
            )
        )

    return strategies
