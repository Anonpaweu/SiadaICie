"""
Route resolver and node merger for PKP Intercity Seat Optimizer.

Handles metropolitan station merging (e.g. Warszawa Zachodnia/Centralna/Wschodnia)
and builds route segments from filtered stop lists.
"""
from typing import List, Optional, Any
from pydantic import BaseModel


class StationStop(BaseModel):
    seq: int
    eva_code: str
    name: str
    arrival_min: int        # Minuta od początku trasy użytkownika (Stacja A = 0)
    departure_min: int      # Minuta odjazdu od początku trasy użytkownika
    arrival_time: str = ""   # e.g. "15:11"
    departure_time: str = "" # e.g. "15:12"
    metropolitan_group: Optional[str] = None
    is_main_hub: bool = False
    day_offset: int = 0


RouteStop = StationStop


class RouteSegment(BaseModel):
    seg_idx: int
    from_station: Any
    to_station: Any
    duration_min: int
    from_station_name: str = ""
    to_station_name: str = ""
    departure_time: str = ""
    arrival_time: str = ""
    duration_minutes: int = 0
    train_number: str = ""


# Słownik znanych węzłów aglomeracyjnych w Polsce
METROPOLITAN_HUBS = {
    "Warszawa Zachodnia": ("WARSZAWA", False),
    "Warszawa Centralna": ("WARSZAWA", True),
    "Warszawa Śródmieście": ("WARSZAWA", False),
    "Warszawa Wschodnia": ("WARSZAWA", False),
    "Gdańsk Główny": ("TROJMIASTO", True),
    "Gdańsk Wrzeszcz": ("TROJMIASTO", False),
    "Gdańsk Oliwa": ("TROJMIASTO", False),
    "Sopot": ("TROJMIASTO", False),
    "Gdynia Główna": ("TROJMIASTO", True),
    "Katowice": ("GOP", True),
    "Sosnowiec Główny": ("GOP", False),
    "Zabrze": ("GOP", False),
    "Gliwice": ("GOP", True),
}


def enrich_stops_with_hubs(stops: List[StationStop]) -> List[StationStop]:
    """Przypisuje flagi aglomeracyjne do stacji na trasie."""
    for stop in stops:
        if stop.name in METROPOLITAN_HUBS:
            group, is_main = METROPOLITAN_HUBS[stop.name]
            stop.metropolitan_group = group
            stop.is_main_hub = is_main
    return stops


class NodeMerger:
    """
    Manages intermediate station stop filtering and metropolitan hub collapsing.
    Default mode: merge_short_segments=False (does NOT drop stops like Częstochowa -> Częstochowa Stradom).
    """
    def __init__(self, min_segment_minutes: int = 8, merge_short_segments: bool = False):
        self.min_segment_minutes = min_segment_minutes
        self.merge_short_segments = merge_short_segments

    def merge_stops(self, raw_stops: List[StationStop]) -> List[StationStop]:
        return filter_and_merge_stops(
            raw_stops,
            min_segment_minutes=self.min_segment_minutes,
            merge_short_segments=self.merge_short_segments,
        )


def filter_and_merge_stops(
    raw_stops: List[StationStop],
    min_segment_minutes: int = 8,
    merge_short_segments: bool = False,
) -> List[StationStop]:
    """
    Redukuje przystanki wewnątrz tej samej aglomeracji tylko wtedy, gdy merge_short_segments=True.
    Domyślnie merge_short_segments=False - NIE USUWA żadnych stacji pośrednich,
    na których zatrzymuje się pociąg (np. Częstochowa -> Częstochowa Stradom 6 min).
    """
    if not merge_short_segments or len(raw_stops) <= 2:
        for idx, stop in enumerate(raw_stops):
            stop.seq = idx
        return raw_stops

    raw_stops = enrich_stops_with_hubs(raw_stops)
    filtered: List[StationStop] = [raw_stops[0]]  # Zawsze zachowaj stację początkową A

    for i in range(1, len(raw_stops) - 1):
        current = raw_stops[i]
        prev = filtered[-1]
        duration_from_prev = current.arrival_min - prev.departure_min

        # Sprawdź, czy obie stacje należą do tej samej aglomeracji
        same_metro = (
            current.metropolitan_group is not None
            and current.metropolitan_group == prev.metropolitan_group
        )

        # Jeśli to ta sama aglomeracja i czas przejazdu jest krótki:
        if same_metro and duration_from_prev < min_segment_minutes:
            if current.is_main_hub and len(filtered) > 1:
                filtered[-1] = current
            continue

        filtered.append(current)

    # Sprawdź relację ostatniego zachowanego przystanku ze stacją docelową G
    final_stop = raw_stops[-1]
    if len(filtered) > 1:
        last_added = filtered[-1]
        same_metro_end = (
            final_stop.metropolitan_group is not None
            and final_stop.metropolitan_group == last_added.metropolitan_group
        )
        if same_metro_end and (final_stop.arrival_min - last_added.departure_min) < min_segment_minutes:
            filtered.pop()

    filtered.append(final_stop)

    for idx, stop in enumerate(filtered):
        stop.seq = idx

    return filtered


def build_route_segments(stops: List[StationStop], train_number: str = "") -> List[RouteSegment]:
    """Zamienia przefiltrowaną listę N stacji na N-1 odcinków elementarnych."""
    segments: List[RouteSegment] = []
    for i in range(len(stops) - 1):
        s_from = stops[i]
        s_to = stops[i + 1]
        duration = max(1, s_to.arrival_min - s_from.departure_min)
        from_name = getattr(s_from, 'name', str(s_from))
        to_name = getattr(s_to, 'name', str(s_to))
        dep_time = getattr(s_from, 'departure_time', '')
        arr_time = getattr(s_to, 'arrival_time', '')
        segments.append(
            RouteSegment(
                seg_idx=i,
                from_station=s_from,
                to_station=s_to,
                duration_min=duration,
                from_station_name=from_name,
                to_station_name=to_name,
                departure_time=dep_time,
                arrival_time=arr_time,
                duration_minutes=duration,
                train_number=train_number,
            )
        )
    return segments


build_segments = build_route_segments
