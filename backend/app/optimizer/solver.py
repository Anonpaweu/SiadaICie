"""
Seat Optimization Solver (DAG / Dynamic Programming).

Finds optimal seat strategies for a train journey, handling:
- Continuous availability blocks (intervals)
- Overlapping windows with early transfers
- Quadratic carriage distance penalties with profile multipliers
- Standing corridor fallback
- Disconnected EZT unit penalties
- Equivalent-seat pruning for fast DP on large seat matrices
- Top-K diverse strategy generation (preferring distinct carriages when possible)
"""
from typing import List, Dict, Tuple, Optional, Set
from pydantic import BaseModel

from backend.app.models.domain import OptimizationProfile
from backend.app.engine.scorer import get_profile_multipliers


class SeatMeta(BaseModel):
    seat_uid: str               # np. "10_12" (Wagon 10, Miejsce 12)
    carriage_no: int            # np. 10
    seat_no: str                # np. "12"
    compartment_no: Optional[int] = None  # Numer przedziału (lub rzędu w bezprzedziałowym)


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
    transfer_instruction: Optional[str] = None
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
    profile: OptimizationProfile = OptimizationProfile.BALANCED
    w_duration: float = 1000.0
    w_final_bonus: float = 250.0
    p_base_transfer: float = 1000.0  # W_TICKET = 1000.0
    p_standing_per_min: float = 25.0
    w_wars_crossing_penalty: float = 80.0
    wars_carriage_no: Optional[int] = None
    # Lista par wagonów bez przejścia międzywagonowego (np. podwójny Flirt/Pendolino: [(8, 9)])
    disconnected_carriages: List[Tuple[int, int]] = []


def parse_profile(raw_profile: Optional[str]) -> OptimizationProfile:
    """Safely parse profile name into OptimizationProfile, defaulting to BALANCED."""
    if not raw_profile:
        return OptimizationProfile.BALANCED
    try:
        return OptimizationProfile(raw_profile.strip().upper())
    except ValueError:
        return OptimizationProfile.BALANCED


def format_transfers_pl(count: int) -> str:
    """Formats transfer count with proper Polish pluralization."""
    n = int(count)
    if n <= 0:
        return "bez przesiadek"
    if n == 1:
        return "1 przesiadka"
    if 2 <= (n % 10) <= 4 and not (12 <= (n % 100) <= 14):
        return f"{n} przesiadki"
    return f"{n} przesiadek"


def _merge_consecutive_standing_blocks(path: List[AvailabilityBlock]) -> List[AvailabilityBlock]:
    """Merges adjacent STANDING corridor blocks into a single continuous corridor leg."""
    merged: List[AvailabilityBlock] = []
    for block in path:
        if block.is_standing and merged and merged[-1].is_standing and merged[-1].end_seg == block.start_seg:
            prev = merged[-1]
            merged[-1] = AvailabilityBlock(
                block_id=f"STANDING_{prev.start_seg}_{block.end_seg}",
                seat=prev.seat,
                start_seg=prev.start_seg,
                end_seg=block.end_seg,
                duration_min=prev.duration_min + block.duration_min,
                is_standing=True,
            )
        else:
            merged.append(block)
    return merged


def calculate_transfer_penalty(
    seat_a: SeatMeta,
    seat_b: SeatMeta,
    is_standing_b: bool,
    config: OptimizerConfig,
) -> float:
    """
    Oblicza koszt fizycznej przesiadki z miejsca A na miejsce B z uwzględnieniem profilu.
    Baza: W_TICKET = 1000.0 * base_transfer_multiplier za każdą przesiadkę.
    Odległość fizyczna (mnożona przez distance_multiplier):
    - ten sam wagon i ten sam przedział: +15 pkt
    - ten sam wagon, inny przedział: +40 pkt
    - inny wagon: +100 * (delta_w ** 2)
    """
    dist_mult, transfer_mult = get_profile_multipliers(config.profile)
    base_transfer = config.p_base_transfer * transfer_mult

    if seat_a.seat_uid == "STANDING" and (is_standing_b or seat_b.seat_uid == "STANDING"):
        return 0.0

    if is_standing_b or seat_a.seat_uid == "STANDING" or seat_b.seat_uid == "STANDING":
        return base_transfer + 100.0

    if seat_a.seat_uid == seat_b.seat_uid:
        return 0.0

    c_a, c_b = seat_a.carriage_no, seat_b.carriage_no
    delta_c = abs(c_a - c_b)

    # Sprawdź, czy między wagonem A i B nie ma fizycznej blokady przejścia (np. 2 złączone jednostki EZT)
    for disc_1, disc_2 in config.disconnected_carriages:
        low, high = min(disc_1, disc_2), max(disc_1, disc_2)
        if min(c_a, c_b) <= low and max(c_a, c_b) >= high:
            return base_transfer + 600.0 * dist_mult

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
        return base_transfer + dist_penalty * dist_mult

    # Przypadek 2: Zmiana wagonu (nieliniowa kara kwadratowa 100 * delta^2)
    dist_penalty = 100.0 * (delta_c ** 2)

    # Dodatkowa kara za przechodzenie przez wagon gastronomiczny WARS
    if config.wars_carriage_no is not None:
        if min(c_a, c_b) < config.wars_carriage_no < max(c_a, c_b):
            dist_penalty += config.w_wars_crossing_penalty

    return base_transfer + dist_penalty * dist_mult


def _prune_equivalent_seats(
    availability_matrix: Dict[str, List[int]],
    seats_meta: Dict[str, SeatMeta],
    excluded_seats: Set[str],
    excluded_carriages: Set[int],
) -> Dict[str, List[int]]:
    """
    Deduplicates seats sharing the exact same (carriage_no, compartment_no, availability_vector).
    Keeps up to 2 representatives per equivalence class (so same-compartment transfers are still possible)
    which dramatically speeds up DP on large trains with 100+ free seats without losing optimality.
    """
    grouped_counts: Dict[Tuple[int, Optional[int], Tuple[int, ...]], int] = {}
    pruned: Dict[str, List[int]] = {}

    for seat_uid, vec in availability_matrix.items():
        if seat_uid in excluded_seats:
            continue
        meta = seats_meta[seat_uid]
        if meta.carriage_no in excluded_carriages:
            continue
        key = (meta.carriage_no, meta.compartment_no, tuple(vec))
        cnt = grouped_counts.get(key, 0)
        if cnt < 2:
            grouped_counts[key] = cnt + 1
            pruned[seat_uid] = vec

    return pruned


def generate_candidate_blocks(
    availability_matrix: Dict[str, List[int]],
    seats_meta: Dict[str, SeatMeta],
    segment_durations: List[int],
) -> List[AvailabilityBlock]:
    """
    Generuje wszystkie pod-przedziały [s_start, s_end) dla każdego miejsca, które jest ciągle wolne.
    Dzięki wygenerowaniu wszystkich pod-przedziałów (a nie tylko maksymalnych),
    algorytm w naturalny sposób obsługuje przesiadki na zakładkę.
    """
    num_segments = len(segment_durations)
    blocks: List[AvailabilityBlock] = []

    for seat_uid, seg_vector in availability_matrix.items():
        meta = seats_meta[seat_uid]
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
    config: OptimizerConfig,
) -> float:
    """Oblicza punkty użyteczności za pojedynczy blok ciągłego siedzenia."""
    if block.is_standing:
        return -config.p_standing_per_min * block.duration_min

    # Nieliniowa premia za długi nieprzerwany czas siedzenia (wykładnik 1.6)
    ratio = block.duration_min / max(1.0, float(total_trip_min))
    score = config.w_duration * (ratio ** 1.6)

    # Bonus za dojechanie na tym miejscu do samego końca trasy
    if block.end_seg == num_segments:
        score += config.w_final_bonus * ratio

    return score


def find_optimal_path(
    blocks: List[AvailabilityBlock],
    station_names: List[str],
    segment_durations: List[int],
    config: OptimizerConfig,
    excluded_primary_seats: Optional[Set[str]] = None,
    excluded_carriages: Optional[Set[int]] = None,
) -> Optional[Tuple[float, List[AvailabilityBlock]]]:
    """
    Rozwiązuje problem najdłuższej (najbardziej opłacalnej) ścieżki w grafie DAG od stacji 0 do N
    za pomocą programowania dynamicznego.
    """
    excluded_seats_set = excluded_primary_seats or set()
    excluded_carriages_set = excluded_carriages or set()

    num_segments = len(segment_durations)
    total_trip_min = sum(segment_durations)

    active_blocks = [
        b for b in blocks
        if b.is_standing
        or (b.seat.seat_uid not in excluded_seats_set and b.seat.carriage_no not in excluded_carriages_set)
    ]

    blocks_by_start: Dict[int, List[AvailabilityBlock]] = {i: [] for i in range(num_segments)}
    for b in active_blocks:
        blocks_by_start[b.start_seg].append(b)

    dp: Dict[str, Tuple[float, Optional[str]]] = {}
    block_map: Dict[str, AvailabilityBlock] = {b.block_id: b for b in active_blocks}

    for s_idx in range(num_segments - 1, -1, -1):
        for b in blocks_by_start[s_idx]:
            b_score = score_single_block(b, total_trip_min, num_segments, config)

            if b.end_seg == num_segments:
                dp[b.block_id] = (b_score, None)
                continue

            best_next_val = -1e12
            best_next_id = None

            for next_b in blocks_by_start[b.end_seg]:
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

    best_total_score = -1e12
    best_start_id = None

    for b in blocks_by_start[0]:
        if b.block_id in dp and dp[b.block_id][0] > best_total_score:
            best_total_score = dp[b.block_id][0]
            best_start_id = b.block_id

    if best_start_id is None:
        return None

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
    top_k: int = 3,
) -> List[OptimizationStrategy]:
    """
    Główny punkt wejściowy silnika optymalizacji. Zwraca Top K zróżnicowanych strategii podróży.
    """
    if config is None:
        config = OptimizerConfig()

    strategies: List[OptimizationStrategy] = []
    excluded_seats: Set[str] = set()
    excluded_carriages: Set[int] = set()
    seen_signatures: Set[Tuple[Tuple[str, int, int], ...]] = set()

    for rank in range(1, top_k + 1):
        # First attempt with carriage diversity (if we already picked carriages in previous ranks)
        res = None
        if excluded_carriages:
            pruned_div = _prune_equivalent_seats(availability_matrix, seats_meta, excluded_seats, excluded_carriages)
            if pruned_div:
                blocks_div = generate_candidate_blocks(pruned_div, seats_meta, segment_durations)
                candidate = find_optimal_path(blocks_div, station_names, segment_durations, config)
                if candidate is not None:
                    # Only accept carriage-diverse candidate if it doesn't force standing when seats exist
                    has_standing = any(b.is_standing for b in candidate[1])
                    if not has_standing:
                        res = candidate

        # Fallback: exclude only previously chosen primary seats
        if res is None:
            pruned = _prune_equivalent_seats(availability_matrix, seats_meta, excluded_seats, set())
            blocks = generate_candidate_blocks(pruned, seats_meta, segment_durations)
            res = find_optimal_path(blocks, station_names, segment_durations, config)

        if res is None:
            break

        total_score, path = res
        path = _merge_consecutive_standing_blocks(path)
        sig = tuple((b.seat.seat_uid, b.start_seg, b.end_seg) for b in path)
        if sig in seen_signatures:
            break
        seen_signatures.add(sig)

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
                    transfer_instruction=instruction,
                    compartment=comp_no,
                    alternative_seats_in_same_carriage=sorted(alt_seats, key=lambda x: int(x) if x.isdigit() else 99),
                )
            )

        non_standing_blocks = [b for b in path if not b.is_standing]
        if non_standing_blocks:
            longest_block = max(non_standing_blocks, key=lambda x: x.duration_min)
            excluded_seats.add(longest_block.seat.seat_uid)
            # Encourage carriage diversity in subsequent ranks
            if len(carriages_used) == 1:
                excluded_carriages.add(carriages_used[0])
        else:
            break

        transfers_count = max(0, len(non_standing_blocks) - 1)
        if transfers_count == 0 and standing_min == 0:
            title = "100% Trasy bez przesiadek (Złoty Strzał)"
        elif len(carriages_used) == 1 and standing_min == 0:
            title = f"Przesiadka w tym samym wagonie (Wagon {carriages_used[0]}, {format_transfers_pl(transfers_count)})"
        elif standing_min > 0 and transfers_count == 0:
            title = f"1 miejsce siedzące + korytarz ({standing_min} min)"
        elif standing_min > 0:
            title = f"Optymalna kombinacja ({format_transfers_pl(transfers_count)}, korytarz {standing_min} min)"
        else:
            title = f"Optymalna kombinacja ({format_transfers_pl(transfers_count)})"

        tickets_to_buy: List[str] = []
        purchase_instructions: List[str] = []
        ticket_nr = 1
        for s in steps:
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
