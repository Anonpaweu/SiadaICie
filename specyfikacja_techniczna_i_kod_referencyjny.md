# Specyfikacja Techniczna, Scraper i Kod Referencyjny (`ARCHITECTURE_AND_SCRAPER.md`)

> **Cel dokumentu:** Ten plik zawiera gotowe wzorce implementacyjne, schematy danych, zapytania SQL oraz referencyjny kod algorytmiczny w języku Python. Agent AI budujący aplikację powinien traktować ten dokument jako bezpośrednią bazę kodu źródłowego, eliminującą konieczność zgadywania struktur danych czy logiki biznesowej.

---

## 1. Skąd program zna trasę pociągu? (`RouteResolver` & `NodeMerger`)

Aby program był w 100% autonomiczny i nie wymagał od użytkownika ręcznego sprawdzania stacji pośrednich na `portalpasazera.pl`, stosujemy architekturę dwuwarstwową:
1. **Warstwa podstawowa (Live Session Extraction z `ebilet.intercity.pl`):** Podczas wyszukiwania pociągu na trasie $A \to G$, system rezerwacji PKP IC zwraca szczegóły wybranego połączenia wraz z listą wszystkich stacji pośrednich, czasami przyjazdu/odjazdu oraz – co najważniejsze – wewnętrznymi identyfikatorami stacji (`station_id` w standardzie HAFAS/EVA, np. `5100065` dla Warszawy Centralnej).
2. **Warstwa zapasowa / Offline (Lokalna baza SQLite z plików GTFS PKP Intercity):** Służy do błyskawicznego autouzupełniania nazw stacji, weryfikacji geometrii trasy oraz testowania offline.

### 1.1. Schemat bazy danych SQLite (`backend/app/db/schema.sql`)
Baza przechowuje słownik stacji, zgrupowane węzły aglomeracyjne oraz cache rozkładu jazdy.

```sql
CREATE TABLE IF NOT EXISTS stations (
    eva_code TEXT PRIMARY KEY,          -- np. "5100065" (kod HAFAS/EVA używany przez PKP IC)
    name TEXT NOT NULL,                 -- np. "Warszawa Centralna"
    normalized_name TEXT NOT NULL,      -- np. "warszawa centralna" (do wyszukiwania)
    metropolitan_group TEXT,            -- np. "WARSZAWA", "TROJMIASTO", "GOP", NULL dla zwykłych
    is_main_hub BOOLEAN DEFAULT 0       -- 1 dla głównej stacji w aglomeracji (np. W-wa Centralna, Gdańsk Gł.)
);

CREATE TABLE IF NOT EXISTS cached_routes (
    train_number TEXT NOT NULL,         -- np. "IC 6502"
    travel_date TEXT NOT NULL,          -- np. "2026-11-15"
    stops_json TEXT NOT NULL,           -- Zserializowana lista obiektów StationStop
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (train_number, travel_date)
);
```

### 1.2. Kompletny kod referencyjny: `NodeMerger` i `RouteResolver`
Plik: `backend/app/scraper/route_resolver.py`

Ten moduł odpowiada za rozwiązanie problemu stacji aglomeracyjnych (np. *Warszawa Zachodnia – Centralna – Wschodnia* lub *Gdańsk Główny – Wrzeszcz – Oliwa – Sopot – Gdynia Główna*). Odpytywanie systemu PKP IC o odcinek 4-minutowy (np. *Gdańsk Wrzeszcz – Gdańsk Oliwa*) kończy się często błędem blokady sprzedaży aglomeracyjnej i marnuje czas scrapera.

```python
from typing import List, Optional
from pydantic import BaseModel

class StationStop(BaseModel):
    seq: int
    eva_code: str
    name: str
    arrival_min: int        # Minuta od początku trasy użytkownika (Stacja A = 0)
    departure_min: int      # Minuta odjazdu od początku trasy użytkownika
    metropolitan_group: Optional[str] = None
    is_main_hub: bool = False

class RouteSegment(BaseModel):
    seg_idx: int
    from_station: StationStop
    to_station: StationStop
    duration_min: int

# Słownik znanych węzłów aglomeracyjnych w Polsce (zapobiega błędom blokady sprzedaży wewnątrz aglomeracji)
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

def filter_and_merge_stops(raw_stops: List[StationStop], min_segment_minutes: int = 8) -> List[StationStop]:
    """
    Redukuje przystanki wewnątrz tej samej aglomeracji, jeśli czas przejazdu między nimi
    jest bardzo krótki (< min_segment_minutes), zachowując zawsze pierwszą i ostatnią stację
    całej podróży użytkownika oraz główne węzły (is_main_hub).
    """
    if len(raw_stops) <= 2:
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
            # Jeśli bieżąca stacja jest głównym hubem (np. Warszawa Centralna po Zachodniej),
            # a poprzednia nie była stacją startową całej podróży (index 0), zamień je:
            if current.is_main_hub and len(filtered) > 1:
                filtered[-1] = current
            # W przeciwnym razie pomiń ten drobny przystanek pośredni
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
            # Usuń przedostatni przystanek podmiejski tuż przed stacją docelową
            filtered.pop()

    filtered.append(final_stop)  # Zawsze zachowaj stację końcową G

    # Przenumeruj indeksy sekwencyjne (0, 1, 2, ...)
    for idx, stop in enumerate(filtered):
        stop.seq = idx

    return filtered

def build_route_segments(stops: List[StationStop]) -> List[RouteSegment]:
    """Zamienia przefiltrowaną listę N stacji na N-1 odcinków elementarnych."""
    segments: List[RouteSegment] = []
    for i in range(len(stops) - 1):
        s_from = stops[i]
        s_to = stops[i + 1]
        duration = max(1, s_to.arrival_min - s_from.departure_min)
        segments.append(
            RouteSegment(
                seg_idx=i,
                from_station=s_from,
                to_station=s_to,
                duration_min=duration,
            )
        )
    return segments
```

---

## 2. Kompletny kod referencyjny Silnika Optymalizacji (`SeatOptimizer` & DAG)

Poniższy kod to serce aplikacji (`backend/app/optimizer/solver.py`). Implementuje on dokładnie to, o co prosiłeś:
1. Znajduje **wszystkie ciągłe okna dostępności (`AvailabilityBlock`)** dla każdego miejsca w pociągu (w tym okna nakładające się na siebie, np. miejsce wolne od $A\text{-}C$, inne od $C\text{-}F$, a jeszcze inne od $D\text{-}G$).
2. Pozwala na **wcześniejszą przesiadkę** (np. na stacji $D$ zamiast czekać do $F$), jeśli dzięki temu zajmiemy miejsce wolne aż do stacji końcowej $G$.
3. Nalicza **nieliniową karę kwadratową za odległość między wagonami**, dzięki czemu faworyzuje przesiadki w tym samym wagonie lub wagonie sąsiednim zamiast spacerów z wagonu 3 do 9 i z powrotem.
4. Zwraca **Top 3 zróżnicowane strategie** (np. *Strategia 1: Najmniej przesiadek*, *Strategia 2: Przesiadki w jednym wagonie*, *Strategia 3: Alternatywne wagony*).

Plik: `backend/app/optimizer/solver.py`

```python
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
    algorytm w naturalny sposób obsługuje przesiadki na zakładkę (np. A->C, C->D, D->G,
    nawet jeśli środkowe miejsce było wolne aż do F!).
    """
    num_segments = len(segment_durations)
    blocks: List[AvailabilityBlock] = []

    for seat_uid, seg_vector in availability_matrix.items():
        meta = seats_meta[seat_uid]
        # Znajdź wszystkie pary (i, j) gdzie 0 <= i < j <= num_segments takie, że miejsce jest wolne na każdym odcinku k in [i, j-1]
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

    # Dodaj awaryjne bloki "STANDING_CORRIDOR" na pojedyncze odcinki (gdyby 100% miejsc było zajętych na 1 odcinku)
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

    # Filtruj bloki należące do wykluczonych miejsc (używane przy szukaniu alternatywnych strategii Top 2 i Top 3)
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

    # Przetwarzamy od końca trasy (stacja N-1) w dół do stacji 0 (Bottom-Up DP)
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
                # Nie łącz dwóch bloków tego samego miejsca (bo istnieje jeden większy blok obejmujący oba!)
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

        # Wyklucz najdłużej używane miejsce z tej ścieżki w kolejnej iteracji,
        # aby kolejna rekomendacja (Top 2, Top 3) prezentowała inną, alternatywną opcję!
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
```

---

## 3. Architektura Scrapera `ebilet.intercity.pl` (Playwright Network Interception)

System sprzedaży PKP Intercity (`ebilet.intercity.pl`) jest aplikacją SPA (Single Page Application), która do pobrania schematu wagonu wymaga ważnej sesji i tokenów zabezpieczających. Zamiast parsować skomplikowany kod HTML/SVG z drzewa DOM przeglądarki, zastosujemy **Playwright Network Response Interception**.

### 3.1. Jak działa przechwytywanie JSON-ów z mapą miejsc:
1. Otwieramy kontekst przeglądarki Playwright (`headless=True`).
2. Rejestrujemy nasłuchiwanie na zdarzenia sieciowe: `page.on("response", handle_response)`.
3. Gdy przeglądarka (lub skrypt wykonujący `fetch()` wewnątrz kontekstu strony `page.evaluate()`) wysyła zapytanie o układ miejsc dla wybranego odcinka $S_k \to S_{k+1}$, serwer PKP IC zwraca odpowiedź JSON zawierającą listę wagonów i status każdego miejsca.
4. Nasz funkcja `handle_response` przechwytuje ten czysty JSON w locie i przekazuje do `SeatMatrixBuilder`.

### 3.2. Wzorzec implementacyjny scrapera (`backend/app/scraper/ic_client.py`)

```python
import asyncio
import random
from typing import Dict, Any, List, Callable, Optional
from playwright.async_api import async_playwright, Page, Response

class PKPIntercityScraper:
    """
    Klient scrapujący dostępność miejsc odcinek po odcinku przy użyciu Playwright.
    Obsługuje również tryb MOCK (offline) do szybkiego testowania algorytmu.
    """
    def __init__(self, headless: bool = True, max_concurrency: int = 2):
        self.headless = headless
        self.semaphore = asyncio.Semaphore(max_concurrency)

    async def fetch_segment_seatmap(
        self,
        train_no: str,
        travel_date: str,
        from_eva: str,
        to_eva: str,
        ticket_class: int = 2
    ) -> Dict[str, Any]:
        """
        Pobiera mapę zajętości miejsc dla pojedynczego odcinka (from_eva -> to_eva).
        Używa semafora i losowego opóźnienia (jitter), aby uniknąć blokady WAF/Rate-Limit.
        """
        async with self.semaphore:
            # Losowy jitter 0.8s - 1.8s
            await asyncio.sleep(random.uniform(0.8, 1.8))
            # Właściwa logika Playwright (szczegóły w Taskach Fazy 4)
            return await self._playwright_extract_carriage_map(
                train_no, travel_date, from_eva, to_eva, ticket_class
            )

    async def _playwright_extract_carriage_map(
        self,
        train_no: str,
        travel_date: str,
        from_eva: str,
        to_eva: str,
        ticket_class: int
    ) -> Dict[str, Any]:
        """
        Uruchamia lub re-używa sesji Playwright, przechwytuje odpowiedź JSON ze schematem
        wagonów i zwraca znormalizowany słownik miejsc.
        """
        captured_seatmap: Dict[str, Any] = {}

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=self.headless)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
                viewport={"width": 1440, "height": 900},
                locale="pl-PL"
            )
            page = await context.new_page()

            async def on_response(response: Response):
                # Przechwytuj odpowiedzi API zawierające układ wagonów / schemat miejsc
                url = response.url.lower()
                if any(k in url for k in ["schemat", "seat", "wagon", "carriage", "miejsca", "uklad"]):
                    if "application/json" in response.headers.get("content-type", ""):
                        try:
                            data = await response.json()
                            captured_seatmap["raw_data"] = data
                        except Exception:
                            pass

            page.on("response", on_response)
            # Tutaj wykonywana jest nawigacja / wywołanie fetch wewnątrz sesji ebilet.intercity.pl
            # ...
            await browser.close()

        return captured_seatmap
```

---

## 4. Generator Scenariuszy Testowych (`MockDataGenerator`)

Zanim agent podłączy prawdziwy scraper do strony PKP IC, **musi** przetestować cały system na deterministycznych danych symulacyjnych (Mockach), które dokładnie odwzorowują Twoje przypadki użycia:
1. **Scenariusz 1 ("Klasyczny Blok B-C"):** Trasa $A \to G$ (6 odcinków). Miejsce 12 w wagonie 10 jest wolne na $A\text{-}B$, zajęte na $B\text{-}C$ i wolne na $C\text{-}G$. Miejsce 14 w tym samym wagonie 10 jest wolne na $A\text{-}C$.
2. **Scenariusz 2 ("Zakładka z dalekimi i bliskimi wagonami"):** Trasa $A \to G$ (6 odcinków: $A\text{-}B, B\text{-}C, C\text{-}D, D\text{-}E, E\text{-}F, F\text{-}G$).
   * Wagon 8, Miejsce 13: wolne od $A$ do $C$ (odcinki 0, 1).
   * Wagon 6, Miejsce 40: wolne od $C$ do $F$ (odcinki 2, 3, 4).
   * Wagon 7, Miejsce 24: wolne od $D$ do $G$ (odcinki 3, 4, 5).
   * Wagon 2, Miejsce 55 (pułapka dalekiego wagonu): wolne od $D$ do $G$ (odcinki 3, 4, 5).
   * **Oczekiwany wynik algorytmu:** Start w Wagonie 8 (m. 13) na $A\text{-}C$ $\to$ Przesiadka na stacji $C$ do Wagonu 6 (m. 40) tylko na odcinek $C\text{-}D$ $\to$ Wcześniejsza przesiadka na stacji $D$ do sąsiedniego Wagonu 7 (m. 24) aż do końca trasy $G$ (zamiast biec do dalekiego Wagonu 2!).