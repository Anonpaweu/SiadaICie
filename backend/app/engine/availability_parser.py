"""
Parser and Formatter for Train Seat Availability Maps ("wX mY/mZ").

Parses plain text reports of seat availability per segment into structured
domain models (SegmentAvailabilityMap, FullTrainAvailabilityMap) and converts
them into optimization matrices.
"""
import re
from typing import Dict, List, Tuple, Optional, Any
from backend.app.models.domain import (
    SegmentAvailabilityMap,
    FullTrainAvailabilityMap,
)
from backend.app.optimizer.solver import SeatMeta


def normalize_station_name(name: str) -> str:
    """
    Normalizes station names across common abbreviations and variants.
    E.g. 'Częstochowa str.' -> 'Częstochowa Stradom'
         'Częstochowa str' -> 'Częstochowa Stradom'
         'Częstochowa gł.' -> 'Częstochowa Główna'
    """
    s = name.strip()
    # Remove leading numbering like '1.', '1)'
    s = re.sub(r'^\d+[\.\)]\s*', '', s).strip()

    # Normalization of common abbreviations
    s = re.sub(r'\bstr\b\.?', 'Stradom', s, flags=re.IGNORECASE)
    s = re.sub(r'\bgł\b\.?', 'Główny', s, flags=re.IGNORECASE)
    s = re.sub(r'\bgl\b\.?', 'Główny', s, flags=re.IGNORECASE)
    s = re.sub(r'\bglowny\b', 'Główny', s, flags=re.IGNORECASE)
    s = re.sub(r'\bglowna\b', 'Główna', s, flags=re.IGNORECASE)
    s = re.sub(r'\btryb\b\.?', 'Trybunalski', s, flags=re.IGNORECASE)

    # Clean multiple spaces
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def parse_carriage_seats(line: str) -> List[Tuple[str, List[str]]]:
    """
    Parses carriage and seat definitions from a line or substring.
    Supports formats:
      - 'W3 m87/m84/m91'
      - 'w5 m82 |'
      - 'w6 M15/m53'
      - 'W4 m76/'
      - 'w5: [82]'
      - 'w6: [15, 53]'
      - 'w7: [25, 31, 33, 35]'
    Returns a list of (carriage_no, [seat_numbers]).
    """
    results: List[Tuple[str, List[str]]] = []

    # Pattern 1: Carriage with brackets, e.g. w5: [82], w6: [15, 53]
    bracket_matches = list(re.finditer(r'[wW](\d+)\s*:\s*\[(.*?)\]', line))
    if bracket_matches:
        for bm in bracket_matches:
            c_no = bm.group(1)
            raw_seats = bm.group(2)
            seat_nums = re.findall(r'\b\d+\b', raw_seats)
            if seat_nums:
                results.append((c_no, sorted(list(set(seat_nums)), key=lambda x: int(x))))
        return results

    # Pattern 2: Multi-carriage separated on one line, e.g. 'w5 m82, w6 m15/m53' or 'W3 m91'
    # Split by commas or pipes if multiple carriages are on the same line
    parts = re.split(r'[,|]\s*(?=[wW]\d+)', line)
    for part in parts:
        c_match = re.search(r'[wW](\d+)', part)
        if not c_match:
            continue
        c_no = c_match.group(1)
        sub_after_c = part[c_match.end():]

        # Extract seat numbers
        # Could be 'm87/m84/m91' or ': [84, 87]' or '87, 84'
        seat_nums = re.findall(r'(?:[mM]\s*)?(\d+)', sub_after_c)
        if seat_nums:
            clean_seats = sorted(list(set(seat_nums)), key=lambda x: int(x))
            results.append((c_no, clean_seats))

    return results


def parse_availability_text(raw_text: str) -> FullTrainAvailabilityMap:
    """
    Parses full availability report text into FullTrainAvailabilityMap.
    Detects segment headers (e.g. 'Koluszki - Piotrków', 'Piotrków – Radomsko 15:12')
    and carriage/seat specifications.
    """
    lines = raw_text.strip().splitlines()
    segments: List[SegmentAvailabilityMap] = []

    current_from = ""
    current_to = ""
    current_time = ""
    current_carriages: Dict[str, List[str]] = {}

    def commit_segment():
        nonlocal current_from, current_to, current_time, current_carriages
        if current_from and current_to:
            seg = SegmentAvailabilityMap(
                segment_index=len(segments),
                from_station=current_from,
                to_station=current_to,
                departure_time=current_time,
                carriages=current_carriages,
            )
            segments.append(seg)
        current_from = ""
        current_to = ""
        current_time = ""
        current_carriages = {}

    # Header pattern: matches 'StationA - StationB [time]' or '1. StationA – StationB (15:12):'
    header_pattern = re.compile(
        r'^(?:\d+[\.\)]\s*)?([A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż\.\s]+?)\s*(?:-|–|—|->|→)\s*([A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż\.\s]+?)(?:\s*(?:\(?\b(\d{1,2}:\d{2})\b\)?)|\s*:\s*|\s*$)'
    )

    for line in lines:
        line_s = line.strip()
        if not line_s:
            continue

        # Check if line contains a dash separating stations
        # But ensure it's not a seat definition line like 'w5 - m82'
        has_station_dash = any(sep in line_s for sep in [" - ", " – ", " — ", " -> ", " → "])
        is_seat_line_only = bool(re.match(r'^[wW]\d+', line_s))

        if has_station_dash and not is_seat_line_only:
            # We found a new segment header! Commit previous if active
            commit_segment()

            # Extract time if present anywhere in the line
            time_m = re.search(r'\b(\d{1,2}:\d{2})\b', line_s)
            dep_time = time_m.group(1) if time_m else ""

            # Remove time and parentheses for station name parsing
            line_clean = re.sub(r'\(?\b\d{1,2}:\d{2}\b\)?', '', line_s)
            line_clean = re.sub(r'^\d+[\.\)]\s*', '', line_clean).strip()

            # Split by separator
            sep_m = re.search(r'\s*(?:-|–|—|->|→)\s*', line_clean)
            if sep_m:
                raw_from = line_clean[:sep_m.start()].strip()
                raw_to_and_rest = line_clean[sep_m.end():].strip()
                # If there's a colon followed by carriages, e.g. "Piotrków: w5: [82]"
                if ":" in raw_to_and_rest:
                    raw_to, rest = raw_to_and_rest.split(":", 1)
                    raw_to = raw_to.strip()
                else:
                    raw_to = raw_to_and_rest
                    rest = ""

                current_from = normalize_station_name(raw_from)
                current_to = normalize_station_name(raw_to)
                current_time = dep_time

                # Check if carriage seats are on this same line after colon
                if rest:
                    c_seats = parse_carriage_seats(rest)
                    for c_no, s_list in c_seats:
                        current_carriages[c_no] = sorted(list(set(current_carriages.get(c_no, []) + s_list)), key=lambda x: int(x))
            continue

        # Otherwise, parse carriage seats on this line
        c_seats = parse_carriage_seats(line_s)
        if c_seats:
            for c_no, s_list in c_seats:
                current_carriages[c_no] = sorted(list(set(current_carriages.get(c_no, []) + s_list)), key=lambda x: int(x))

    # Commit last segment
    commit_segment()

    return FullTrainAvailabilityMap(segments=segments)


def format_availability_map(avail_map: FullTrainAvailabilityMap) -> str:
    """
    Formats FullTrainAvailabilityMap into clean human-readable text format:
    Piotrków - Radomsko 15:12
    W3 m91
    W4 m76
    W5 m32/m33/m65/m74/m82/m88
    """
    out_lines: List[str] = []

    for seg in avail_map.segments:
        time_suffix = f" {seg.departure_time}" if seg.departure_time else ""
        out_lines.append(f"{seg.from_station} - {seg.to_station}{time_suffix}")

        for c_no in sorted(seg.carriages.keys(), key=lambda x: int(x) if x.isdigit() else 999):
            seats = seg.carriages[c_no]
            if seats:
                formatted_seats = "/".join(f"m{s}" for s in sorted(seats, key=lambda x: int(x)))
                out_lines.append(f"W{c_no} {formatted_seats}")
        out_lines.append("")

    return "\n".join(out_lines).strip()


def availability_map_to_matrix(
    avail_map: FullTrainAvailabilityMap,
) -> Tuple[Dict[str, List[int]], Dict[str, SeatMeta], List[str], List[int]]:
    """
    Converts FullTrainAvailabilityMap into optimization matrix M[seat_uid, segment_idx]
    and associated metadata.

    Returns:
      (availability_matrix, seats_meta, station_names, segment_durations)
    """
    num_segments = len(avail_map.segments)
    if num_segments == 0:
        return {}, {}, [], []

    # Build stations sequence
    station_names: List[str] = [avail_map.segments[0].from_station]
    for seg in avail_map.segments:
        station_names.append(seg.to_station)

    # Approximate segment durations (preferring explicit segment duration, then time delta, then 30 min)
    segment_durations: List[int] = []
    for i, seg in enumerate(avail_map.segments):
        explicit_dur = getattr(seg, "duration_min", None)
        if isinstance(explicit_dur, int) and explicit_dur > 0:
            segment_durations.append(max(5, explicit_dur))
            continue

        # Calculate duration if times are available
        if seg.departure_time and i + 1 < len(avail_map.segments) and avail_map.segments[i + 1].departure_time:
            try:
                t1 = [int(x) for x in seg.departure_time.split(":")]
                t2 = [int(x) for x in avail_map.segments[i + 1].departure_time.split(":")]
                m1 = t1[0] * 60 + t1[1]
                m2 = t2[0] * 60 + t2[1]
                dur = m2 - m1
                if dur < 0:
                    dur += 1440
                segment_durations.append(max(5, dur))
            except Exception:
                segment_durations.append(30)
        else:
            segment_durations.append(30)

    # 1. Collect all unique (carriage, seat) across all segments
    all_seats: set[Tuple[str, str]] = set()
    for seg in avail_map.segments:
        for c_no, seats in seg.carriages.items():
            for s_no in seats:
                all_seats.add((str(c_no), str(s_no)))

    availability_matrix: Dict[str, List[int]] = {}
    seats_meta: Dict[str, SeatMeta] = {}

    sorted_seats = sorted(list(all_seats), key=lambda x: (int(x[0]) if x[0].isdigit() else 99, int(x[1]) if x[1].isdigit() else 999))

    for c_no, s_no in sorted_seats:
        seat_uid = f"{c_no}_{s_no}"
        s_int = int(s_no) if s_no.isdigit() else 1
        c_int = int(c_no) if c_no.isdigit() else 1

        # PKP UIC seat numbering: compartment is first digit(s)
        compartment = s_int // 10 if s_int >= 10 else 1

        merged_tags: List[str] = []
        vector: List[int] = []
        for seg in avail_map.segments:
            free_in_c = seg.carriages.get(c_no, [])
            if s_no in free_in_c:
                vector.append(1)
                seg_tags = getattr(seg, "seat_tags", None) or {}
                for t in seg_tags.get(seat_uid, []):
                    if t not in merged_tags:
                        merged_tags.append(t)
            else:
                vector.append(0)

        seats_meta[seat_uid] = SeatMeta(
            seat_uid=seat_uid,
            carriage_no=c_int,
            seat_no=s_no,
            compartment_no=compartment,
            seat_tags=merged_tags,
        )
        availability_matrix[seat_uid] = vector

    return availability_matrix, seats_meta, station_names, segment_durations
