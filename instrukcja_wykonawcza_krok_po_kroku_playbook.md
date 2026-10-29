# Instrukcja Wykonawcza Krok po Kroku dla Agenta AI (`AGENT_EXECUTION_PLAYBOOK.md`)

> **DYREKTYWA GŁÓWNA DLA AGENTA AI (ANTIGRAVITY / CURSOR / WINDSURF):**
> Jesteś autonomicznym inżynierem oprogramowania realizującym projekt **PKP Intercity Seat Optimizer**.
> 
> **Twoje zasady pracy:**
> 1. **Kolejność dokumentów:** Zawsze korzystaj z trzech plików w repozytorium:
>    - `AGENT_EXECUTION_PLAYBOOK.md` (ten plik) – wyznacza dokładną kolejność kroków, komendy testowe oraz kryteria akceptacji.
>    - `ARCHITECTURE_AND_SCRAPER.md` – zawiera gotowy kod referencyjny (`RouteResolver`, `SeatOptimizer`, `SQLite schema`), którego masz użyć.
>    - `tasks.md` – zawiera pełny opis architektury, matematyki i przypadków brzegowych.
> 2. **Zasada "Backend & Logic First, UI Later":** Nie buduj rozbudowanego interfejsu graficznego na początku! Najpierw stwórz i przetestuj w 100% silnik matematyczny, resolver stacji oraz narzędzie CLI w terminalu. Dopiero gdy wszystkie testy przejdą pomyślnie, zbuduj prosty, funkcjonalny interfejs webowy.
> 3. **Aktualizacja postępów (`[ ]` -> `[x]`):** Po ukończeniu każdego podpunktu i **pozytywnym przejściu testu w terminalu**, zmodyfikuj ten plik (`AGENT_EXECUTION_PLAYBOOK.md`), zmieniając `[ ]` na `[x]` przy wykonanym zadaniu. **Nigdy nie oznaczaj zadania jako `[x]` bez uruchomienia testu i zobaczenia zielonego wyniku `PASSED`!**

---

## ETAP 1: Inicjalizacja Środowiska i Struktury Projektu

W tym etapie tworzysz szkielet katalogów dla backendu w Pythonie i instalujesz niezbędne zależności.

- [x] **Krok 1.1: Utworzenie drzewa katalogów**
  Utwórz następującą strukturę plików:
  ```text
  backend/
  ├── app/
  │   ├── __init__.py
  │   ├── main.py
  │   ├── cli.py
  │   ├── models/
  │   │   ├── __init__.py
  │   │   └── schemas.py
  │   ├── db/
  │   │   ├── __init__.py
  │   │   ├── schema.sql
  │   │   └── database.py
  │   ├── scraper/
  │   │   ├── __init__.py
  │   │   ├── route_resolver.py
  │   │   ├── mock_provider.py
  │   │   └── ic_client.py
  │   └── optimizer/
  │       ├── __init__.py
  │       └── solver.py
  ├── tests/
  │   ├── __init__.py
  │   ├── test_route_resolver.py
  │   ├── test_optimizer.py
  │   └── test_api.py
  └── requirements.txt
  ```

- [x] **Krok 1.2: Konfiguracja `requirements.txt` oraz wirtualnego środowiska**
  Zapisz w `backend/requirements.txt`:
  ```text
  fastapi>=0.115.0
  uvicorn[standard]>=0.30.0
  pydantic>=2.9.0
  playwright>=1.48.0
  httpx>=0.27.0
  pytest>=8.3.0
  pytest-asyncio>=0.24.0
  rich>=13.9.0
  ```
  *Weryfikacja:* Utwórz środowisko `venv`, zainstaluj pakiety (`pip install -r backend/requirements.txt`) i sprawdź `pytest --version`. Po sukcesie oznacz `[x]`.

---

## ETAP 2: Implementacja i Testy `RouteResolver` oraz `NodeMerger` (Skąd znamy trasę pociągu)

Zanim dotkniesz scrapera czy optymalizatora, musisz zaimplementować moduł rozwiązujący stacje pośrednie i łączący przystanki aglomeracyjne.

- [x] **Krok 2.1: Implementacja bazy SQLite i `route_resolver.py`**
  1. Skopiuj schemat SQL z pliku `ARCHITECTURE_AND_SCRAPER.md` (Sekcja 1.1) do `backend/app/db/schema.sql` i napisz prosty inicjalizator bazy w `backend/app/db/database.py`.
  2. Zaimplementuj `backend/app/scraper/route_resolver.py` na podstawie kodu referencyjnego z `ARCHITECTURE_AND_SCRAPER.md` (Sekcja 1.2).

- [x] **Krok 2.2: Napisanie i uruchomienie testów jednostkowych w `backend/tests/test_route_resolver.py`**
  Napisz **minimum 3 testy jednostkowe**:
  1. `test_warsaw_hub_merging`: Przekaż trasę *Kraków Główny (0 min) -> Warszawa Zachodnia (140 min) -> Warszawa Centralna (145 min) -> Warszawa Wschodnia (152 min) -> Gdańsk Główny (320 min)*. Sprawdź, czy `filter_and_merge_stops()` zredukowało trzy stacje warszawskie do jednej głównej (`Warszawa Centralna`).
  2. `test_tricities_end_merging`: Przekaż trasę kończącą się w Gdyni: *... -> Gdańsk Główny (300 min) -> Gdańsk Wrzeszcz (305 min) -> Gdańsk Oliwa (309 min) -> Sopot (314 min) -> Gdynia Główna (322 min)*. Sprawdź, czy zachowane zostały główne węzły (`Gdańsk Główny` oraz stacja docelowa `Gdynia Główna`), a drobne przystanki 4-minutowe zostały pominięte.
  3. `test_build_route_segments`: Sprawdź, czy dla $N$ przefiltrowanych stacji funkcja `build_route_segments()` zwraca dokładnie $N-1$ ciągłych odcinków z poprawnie wyliczonym `duration_min`.
  *Komenda weryfikacyjna:*
  ```bash
  pytest backend/tests/test_route_resolver.py -v
  ```
  *Kryterium akceptacji:* Wszystkie 3 testy muszą zwrócić `PASSED`. Zaktualizuj checkbox na `[x]`.

---

## ETAP 3: Implementacja i Rygorystyczne Testy Silnika Matematycznego (`SeatOptimizer` DAG)

To najważniejszy etap w całym projekcie. Algorytm musi bezbłędnie wyszukiwać okna nakładające się (zakładki) oraz preferować bliskie wagony.

- [x] **Krok 3.1: Implementacja `backend/app/optimizer/solver.py`**
  Przenieś i dostosuj kod silnika optymalizacji z pliku `ARCHITECTURE_AND_SCRAPER.md` (Sekcja 2) do `backend/app/optimizer/solver.py`. Upewnij się, że wszystkie klasy (`SeatMeta`, `AvailabilityBlock`, `OptimizerConfig`, `solve_top_strategies`) są kompletnie otypowane.

- [x] **Krok 3.2: Implementacja generatora scenariuszy testowych (`backend/app/scraper/mock_provider.py`)**
  Zaimplementuj generator macierzy $M[S, K]$ dla 4 kluczowych scenariuszy opisanych w `ARCHITECTURE_AND_SCRAPER.md` (Sekcja 4) oraz `tasks.md`:
  1. `scenario_classic_b_c_block()`: Trasa A-B-C-D-E-F-G. Miejsce `10_12` wolne na A-B i C-G (zajęte tylko na B-C). Miejsce `10_14` wolne na A-C.
  2. `scenario_overlapping_windows_and_proximity()`: Trasa A-B-C-D-E-F-G (każdy odcinek po 30 min = 180 min łącznie).
     - Miejsce `8_13` (Wagon 8, m. 13): wolne A-C `[1, 1, 0, 0, 0, 0]`.
     - Miejsce `6_40` (Wagon 6, m. 40): wolne C-F `[0, 0, 1, 1, 1, 0]`.
     - Miejsce `7_24` (Wagon 7, m. 24): wolne D-G `[0, 0, 0, 1, 1, 1]`.
     - Miejsce `2_55` (Wagon 2, m. 55): wolne D-G `[0, 0, 0, 1, 1, 1]`.
  3. `scenario_standing_corridor_fallback()`: Na odcinku C-D (15 min) wszystkie miejsca w pociągu mają status `0` (zajęte).
  4. `scenario_disconnected_units()`: Podwójny skład EZT (wagony 1-8 oraz 9-16, brak przejścia między 8 a 9).

- [x] **Krok 3.3: Napisanie i wykonanie 5 testów jednostkowych w `backend/tests/test_optimizer.py`**
  Napisz testy weryfikujące inteligencję algorytmu:
  1. `test_single_seat_whole_route`: Jeśli jedno miejsce jest wolne przez 100% trasy A-G, `transfers_count` musi wynosić `0`.
  2. `test_classic_b_c_block_same_carriage`: W scenariuszu 1 algorytm musi wybrać przesiadkę wewnątrz wagonu 10 (`10_14` -> `10_12` lub `10_12` -> `10_14` -> `10_12`) i dojechać na `10_12` od stacji C do G.
  3. `test_overlapping_window_early_transfer_and_carriage_proximity`: W scenariuszu 2 algorytm **musi**:
     - Wystartować na miejscu `8_13` (od A do C).
     - Na stacji C przejść na miejsce `6_40` (Wagon 6).
     - **Kluczowe:** Na stacji D (mimo że `6_40` jest wolne aż do F!) algorytm musi zdecydować o wcześniejszej przesiadce na miejsce wolne do samego końca G **ORAZ** musi wybrać miejsce `7_24` (sąsiedni Wagon 7) zamiast `2_55` (odległy Wagon 2).
  4. `test_standing_fallback_when_100_percent_booked`: W scenariuszu 3 algorytm nie może zwrócić `None` ani rzucić wyjątku – musi wstawić krok `STANDING` na odcinku C-D i normalne miejsca siedzące przed C oraz po D.
  5. `test_disconnected_ezts_penalty`: W scenariuszu 4 algorytm musi unikać przesiadki z wagonu 8 do wagonu 9, jeśli istnieje alternatywa w wagonie 6 lub 7.
  *Komenda weryfikacyjna:*
  ```bash
  pytest backend/tests/test_optimizer.py -v
  ```
  *Kryterium akceptacji:* Wszystkie 5 testów musi przejść na zielono (`5 passed`). Zaktualizuj checkbox na `[x]`.

---

## ETAP 4: Narzędzie Terminalowe CLI (Weryfikacja End-to-End bez UI)

Zanim zbudujesz jakikolwiek frontend czy API, stwórz skrypt CLI używający biblioteki `rich`, który wypisuje w terminalu kolorową tabelę zajętości miejsc (macierz tekstową) oraz wyliczone rekomendacje krok po kroku.

- [x] **Krok 4.1: Implementacja `backend/app/cli.py`**
  Napisz program CLI przyjmujący flagę `--scenario` (`classic`, `overlap`, `standing`), który:
  1. Wczytuje dane z `mock_provider.py`.
  2. Rysuje w terminalu tabelę dostępności miejsc na kolejnych odcinkach (`[WOLNE]` na zielono, `[ZAJĘTE]` na czerwono, `[WYBRANE]` na niebiesko).
  3. Uruchamia `solve_top_strategies()` i wypisuje Top 3 strategie wraz z instrukcjami przesiadek po polsku.

- [x] **Krok 4.2: Test uruchomieniowy CLI w terminalu**
  Wykonaj w terminalu:
  ```bash
  python -m backend.app.cli --scenario classic
  python -m backend.app.cli --scenario overlap
  ```
  *Kryterium akceptacji:* Program wykonuje się w czasie $< 0.5\text{ s}$ i wyświetla czytelny plan podróży. Zaktualizuj checkbox na `[x]`.

---

## ETAP 5: Backend API (FastAPI + Server-Sent Events)

Teraz wystawiamy przetestowaną logikę przez REST API, z którego będzie korzystać interfejs użytkownika.

- [x] **Krok 5.1: Implementacja endpointów w `backend/app/main.py`**
  Zbuduj aplikację FastAPI z obsługą CORS i następującymi endpointami:
  1. `GET /api/health` – zwraca `{"status": "ok"}`.
  2. `GET /api/stations/search?q=...` – wyszukuje stacje z lokalnej bazy/słownika.
  3. `POST /api/analyze` – przyjmuje parametry trasy (`from_station`, `to_station`, `date`, `train_no`, `mode`: `"mock_classic"`, `"mock_overlap"`, `"live"`). Zwraca pełny obiekt JSON zawierający:
     - `stations`: przefiltrowana lista stacji pośrednich,
     - `segments`: lista odcinków z czasami przejazdu,
     - `strategies`: lista Top 3 strategii z `solve_top_strategies()`,
     - `seat_matrix`: macierz dostępności wszystkich miejsc do narysowania diagramu Gantta.
  4. `GET /api/analyze/stream` – endpoint SSE (Server-Sent Events) raportujący postęp pobierania kolejnych odcinków (`{"progress": 40, "message": "Sprawdzanie odcinka C -> D..."}`).

- [x] **Krok 5.2: Automatyczne testy API w `backend/tests/test_api.py`**
  Napisz testy przy użyciu `fastapi.testclient.TestClient`:
  1. Sprawdź status `200 OK` na `/api/health`.
  2. Wyślij zapytanie `POST /api/analyze` z trybem `"mock_overlap"` i zweryfikuj strukturę zwracanego JSON-a (czy `strategies[0].steps` ma poprawne pola).
  *Komenda weryfikacyjna:*
  ```bash
  pytest backend/tests/test_api.py -v
  ```
  *Kryterium akceptacji:* Wszystkie testy w całym projekcie (`pytest backend/tests/`) przechodzą w 100%. Zaktualizuj checkbox na `[x]`.

---

## ETAP 6: Integracja Prawdziwego Scrapera (`ebilet.intercity.pl`)

Dopiero gdy cały rdzeń aplikacji i API działa i jest pokryty testami, zaimplementuj pobieranie żywych danych z PKP Intercity.

- [x] **Krok 6.1: Instalacja przeglądarek Playwright**
  Wykonaj w terminalu:
  ```bash
  playwright install chromium
  ```

- [x] **Krok 6.2: Implementacja `backend/app/scraper/ic_client.py` oraz parsera JSON/SVG**
  1. Zaimplementuj wyszukiwanie połączenia na `ebilet.intercity.pl` dla zadanej daty i stacji $A \to G$, aby wyciągnąć listę stacji pośrednich wybranego pociągu.
  2. Zaimplementuj pętlę odpytującą odcinki elementarne $S_k \to S_{k+1}$ z ograniczeniem współbieżności (`Semaphore(2)`) oraz losowym opóźnieniem (`0.8s - 1.8s`).
  3. Zaimplementuj obsługę błędów: jeśli dla wybranego pociągu system PKP IC informuje o całkowitym braku rezerwacji graficznej (np. wagon bez podziału na miejsca lub awaria), zwróć czytelny komunikat błędu do API zamiast zawieszać serwer.
  4. Dodaj caching w SQLite / pamięci z czasem życia `TTL = 10 minut`, aby ponowne odpytanie tej samej trasy nie generowało ruchu sieciowego do PKP IC.

---

## ETAP 7: Prosty, Funkcjonalny Interfejs Webowy (MVP UI)

Zgodnie z wytycznymi projektowymi, na tym etapie UI ma być przede wszystkim **surowe, szybkie, czytelne i w 100% funkcjonalne** (stylistyka techniczna / dashboardowa).

- [x] **Krok 7.1: Zbudowanie pojedynczego widoku webowego (React/Vite lub lekki HTML + Tailwind + Vanilla JS serwowany z FastAPI)**
  Interfejs musi zawierać 4 elementy na jednym ekranie:
  1. **Panel sterowania u góry:**
     - Przełącznik trybu: `Tryb Symulacji (Testy Algorytmu)` vs `Tryb Live (PKP Intercity)`.
     - Wybór predefiniowanego scenariusza testowego (np. *Scenariusz 1: Blok B-C w Wagonie 10*, *Scenariusz 2: Zakładka Wagonów 8 -> 6 -> 7*) lub pola tekstowe na własną trasę (`Stacja A`, `Stacja G`, `Data`, `Nr pociągu`).
     - Checkboxy preferencji: `[x] Unikaj dalekich spacerów między wagonami`, `[ ] Preferuj miejsce przy oknie`.
     - Przycisk `Analizuj miejsca w pociągu`.
  2. **Pasek postępu na żywo:** Pokazuje, który odcinek trasy jest aktualnie sprawdzany.
  3. **Sekcja Rekomendowanych Strategii (Karty Top 3):**
     - Wyświetla wyliczone strategie z wyraźną osią czasu (np. `A -> C: Wagon 8, Miejsce 13` $\to$ `C -> D: Wagon 6, Miejsce 40` $\to$ `D -> G: Wagon 7, Miejsce 24`).
     - Pokazuje łączny czas siedzenia, liczbę przesiadek oraz dokładne instrukcje tekstowe na których stacjach zmienić miejsce.
  4. **Interaktywna Tablica Miejsc (Gantt Chart / Heatmapa):**
     - Tabela, w której kolumny to kolejne odcinki między stacjami ($A\text{-}B, B\text{-}C, C\text{-}D, \dots$), a wiersze to miejsca w wagonach.
     - Komórki zielone = miejsce wolne na tym odcinku; komórki czerwone = miejsce zajęte; komórki ze złotą/niebieską ramką = ścieżka wybrana przez algorytm!
     - Możliwość filtrowania tabeli po numerze wagonu lub pokazania tylko miejsc uczestniczących w rekomendacji.

- [x] **Krok 7.2: Test końcowy całego systemu (End-to-End)**
  1. Uruchom serwer: `uvicorn backend.app.main:app --reload --port 8000`.
  2. Otwórz przeglądarkę pod adresem `http://localhost:8000`.
  3. Kliknij `Scenariusz 1 (Wagon 10, Miejsce 12)` oraz `Scenariusz 2 (Wagony 8 -> 6 -> 7)` i upewnij się, że tabela Gantta oraz karty rekomendacji renderują się poprawnie bez żadnych błędów w konsoli (`F12`).
  4. Oznacz ostatni checkbox jako `[x]`. Projekt osiągnął status **MVP Complete**.