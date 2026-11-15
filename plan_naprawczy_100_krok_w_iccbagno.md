# Analiza Problemu, Audyt Kodu i 100-Punktowy Plan Naprawczy (`IccBagno`)

## Część 1: Rozwiązanie Twojej zagadki (Koluszki $\rightarrow$ Opole)

Przeanalizujmy Twoje dane wejściowe pod kątem **minimalnej liczby biletów/przesiadek** oraz **bliskości miejsc**:

**Dostępne odcinki (6 segmentów):**

1. **Koluszki – Piotrków:** `w5: [82]`, `w6: [15, 53]`

2. **Piotrków – Radomsko (15:12):** `w3: [91]`, `w4: [76]`, `w5: [32, 33, 65, 74, 82, 88]`, `w6: [15, 66, 68, 78]`, `w7: [25, 31, 33, 35, 37, 51, 53, 55, 65, 66, 75]`, `w8: [33]`

3. **Radomsko – Częstochowa (15:40):** `w3: [84, 87, 91]`, `w4: [76]`, `w5: [32, 33, 45, 65, 74, 76, 82, 88]`, `w6: [15, 68, 78, 86]`, `w7: [25, 31, 33, 35, 37, 51, 53, 54, 55, 65, 66, 75]`, `w8: [33, 73, 84, 92, 98]`

4. **Częstochowa – Częstochowa Stradom (15:46):** `w3: [84]`, `w5: [24, 68, 74, 75, 76, 77, 81, 82, 83, 85, 88]`, `w6: [43, 64, 78]`, `w7: [26, 36, 46, 51, 52, 53, 54, 61, 63, 65, 66, 68, 71, 75]`, `w8: [36, 54, 61, 63, 98]`

5. **Częstochowa Stradom – Lubliniec (16:06):** `w3: [84]`, `w5: [81, 83, 88]`

6. **Lubliniec – Opole:** `w3: [83, 84, 106]`, `w4: [11]`, `w6: [74]`, `w7: [28, 51, 53]`, `w8: [46, 72]`

### Wynik optymalizacji (1 przesiadka / 2 bilety na całą trasę!):

Gdy prześledzimy ciągłość miejsc w poszczególnych wagonach, okazuje się, że **wystarczy tylko jedna zmiana miejsca** (i to z możliwością wyboru momentu przesiadki!):

* **Wariant A (Najdłuższy pierwszy odcinek – przesiadka dopiero na Częstochowa Stradom):**

  1. **Koluszki** $\rightarrow$ **Częstochowa Stradom (4 odcinki ciągiem):** **Wagon 5, Miejsce 82** (jest wolne na: *Koluszki-Piotrków*, *Piotrków-Radomsko*, *Radomsko-Częstochowa* oraz *Częstochowa-Częstochowa Stradom*).

  2. **Częstochowa Stradom** $\rightarrow$ **Opole (2 ostatnie odcinki ciągiem):** **Wagon 3, Miejsce 84** (wolne na: *Częstochowa Str.-Lubliniec* oraz *Lubliniec-Opole*).

* **Wariant B (Najwcześniejsze zajęcie miejsca docelowego – przesiadka już w Radomsku):**

  1. **Koluszki** $\rightarrow$ **Radomsko (2 odcinki):** **Wagon 5, Miejsce 82** (lub **Wagon 6, Miejsce 15**).

  2. **Radomsko** $\rightarrow$ **Opole (4 ostatnie odcinki ciągiem aż do końca!):** **Wagon 3, Miejsce 84** (wolne nieprzerwanie przez *Radomsko-Częstochowa*, *Częstochowa-Częstochowa Str.*, *Częstochowa Str.-Lubliniec* i *Lubliniec-Opole*).

* **Wariant C (Przesiadka w tym samym wagonie na sąsiednie miejsce – jeśli kończysz w Lublińcu):**

  * W **Wagonie 5** jedziesz na **miejscu 82** od Koluszek aż do Częstochowy Stradom, a od Częstochowy (lub Stradomia) do Lublińca przesiadasz się o jedno miejsce obok w tym samym przedziale – na **miejsce 81, 83 lub 88** w Wagonie 5! Dopiero na ostatni odcinek *Lubliniec – Opole* przechodzisz do **Wagonu 3 na miejsce 84**.

## Część 2: Co dokładnie jest zepsute w `IccBagno.zip`? (Audyt kodu)

1. **`backend/app/scraper/ic_client.py` – Kompletnie zmyślone wagony i miejsca (linie 109–148):**

   * Obecny kod pobiera tylko HTML z wyszukiwarki `rozklad-pkp.pl`, sprawdza ikonkę czy są miejsca i **w pętli `for c_idx in range(1, 4)` generuje na sztywno 3 nieistniejące wagony (1, 2, 3) po 20 miejsc (`range(1, 21)`)**!

   * Na dodatek używa pseudolosowego wzoru `is_avail = ((s_idx + seg_idx) % 2 == 0)`, żeby udawać, że część miejsc jest zajęta, a część wolna. Dlatego w aplikacji widziałeś kompletnie zmyślone numery wagonów i miejsc.

2. **`backend/app/scraper/schedule_scraper.py` – Brak użycia numeru pociągu i godzin przy skakaniu po odcinkach:**

   * Korzysta z mobilnej wersji `rozklad-pkp.pl` zamiast z oficjalnego rozkładu z dokładnymi godzinami przyjazdu i odjazdu na każdej stacji pośredniej.

   * Kiedy potem `ic_client.py` pyta o odcinek `B -> C`, wysyła zawsze tę samą godzinę początkową (`12:00`), zamiast godziny odjazdu pociągu ze stacji `B` (np. `15:12` w Piotrkowie, `15:40` w Radomsku, `15:46` w Częstochowie). Przez to na dalszych odcinkach łapie zupełnie inne pociągi!

3. **Brak parsowania tekstowej mapy (`w3 m54/m58`) oraz brak eksportu takiego raportu:**

   * Program nie buduje pośredniej, czytelnej reprezentacji tekstowej/strukturalnej (`Segment -> Wagon -> Lista wolnych miejsc`), którą użytkownik mógłby podejrzeć, zweryfikować lub nawet wkleić ręcznie.

## Część 3: Kompleksowy, 100-punktowy plan naprawczy dla Agenta AI

### KRYTYCZNE ZASADY DLA AGENTA AI (ANTIGRAVITY / CURSOR)

1. **CAŁKOWITY ZAKAZ ZMYŚLANIA DANYCH (ZERO FAKE DATA):** W pliku `backend/app/scraper/ic_client.py` znajduje się obecnie niedopuszczalny kod generujący na sztywno wagony `range(1, 4)` i miejsca `range(1, 21)` oraz wzór `(s_idx + seg_idx) % 2 == 0`. Ten kod MUSI zostać całkowicie usunięty.

2. **ŚLEDZENIE TEGO SAMEGO POCIĄGU W CZASIE:** Podczas badania odcinków pośrednich `A->B`, `B->C`, `C->D` scraper MUSI przekazywać jako czas wyszukiwania godzinę odjazdu badanego pociągu ze stacji początkowej danego odcinka (np. Piotrków -> Radomsko o `15:12`, Radomsko -> Częstochowa o `15:40`) i filtrować wyniki po numerze pociągu (np. `IC 16104` / `TLK 35102`).

3. **PRAWDZIWE NUMERY WAGONÓW I MIEJSC:** Scraper musi odczytywać rzeczywistą listę wagonów (np. `3, 4, 5, 6, 7, 8`) oraz rzeczywiste numery wolnych (zielonych) miejsc z systemu rezerwacji PKP Intercity (przez przechwycenie JSON ze schematu wagonów lub klikanie po wagonach w Playwright).

4. **FORMAT MAPKI MIEJSC (`wX mY/mZ`):** System musi generować, wyświetlać i potrafić parsować czytelną rozpiskę wolnych miejsc na każdym odcinku w formacie:

   ```
   Radomsko - Częstochowa 15:40
   W3 m87/m84/m91
   W4 m76
   
   ```

5. **WERYFIKACJA KROK PO KROKU:** Wykonuj zadania ściśle od `Task 001` do `Task 100`. Po każdym zadaniu zamień `[ ]` na `[x]`.

### ETAP 1: Audyt i czyszczenie obecnego repozytorium (Tasks 001–010)

* \[x\] **Task 001:** Usuń z pliku `backend/app/scraper/ic_client.py` linie 109–148 zawierające sztuczną pętlę `for c_idx in range(1, 4):` oraz `for s_idx in range(1, 21):` i pseudolosowe przypisywanie statusów miejsc.

* \[x\] **Task 002:** Usuń lub przenieś do osobnego katalogu archiwalnego śmieciowe pliki HTML z katalogu `scratch/` (`connection_details.html`, `koluszki_tp.html`, `query_res.html`, `rozklad_sample.html`, `session_detail.html`, `ti_sample.html`, `trainsearch*.html`, `ts_*.html`), aby nie zaśmiecały kontekstu.

* \[x\] **Task 003:** Zweryfikuj `backend/requirements.txt` i upewnij się, że zawiera: `fastapi`, `uvicorn`, `playwright`, `httpx`, `beautifulsoup4`, `lxml`, `pydantic`, `pytest`, `pytest-asyncio`.

* \[x\] **Task 004:** Sprawdź instalację przeglądarki Chromium dla Playwright komendą `playwright install chromium`.

* \[x\] **Task 005:** W pliku `backend/app/models/domain.py` zweryfikuj obiekt `RouteSegment` – upewnij się, że przechowuje pola `departure_time: str` (format `HH:MM`, np. `"15:12"`), `arrival_time: str` (format `HH:MM`) oraz `train_number: str`.

* \[x\] **Task 006:** W pliku `backend/app/models/domain.py` dodaj strukturę `SegmentAvailabilityMap`, która przechowuje dokładną mapę wolnych miejsc na jednym odcinku: `segment_index: int`, `from_station: str`, `to_station: str`, `departure_time: str`, `carriages: Dict[str, List[str]]` (gdzie kluczem jest numer wagonu np. `"3"`, a wartością lista wolnych miejsc np. `["84", "87", "91"]`).

* \[x\] **Task 007:** W pliku `backend/app/models/domain.py` dodaj strukturę `FullTrainAvailabilityMap` przechowującą listę `List[SegmentAvailabilityMap]` dla całej trasy `A -> G`.

* \[x\] **Task 008:** W pliku `backend/app/models/schemas.py` dodaj pole `raw_availability_report: str` do `OptimizationResponse`, aby frontend oraz CLI zawsze otrzymywały pełną rozpiskę tekstową (`w3 m84/m87...`) jako dowód prawdziwości pobranych danych.

* \[x\] **Task 009:** W pliku `backend/app/models/schemas.py` dodaj nowy model żądania `ManualMapRequest(raw_text: str)`, który pozwoli użytkownikowi wkleić lub wyedytować rozpiskę tekstową i natychmiast przeliczyć optymalną trasę.

* \[x\] **Task 010:** Uruchom `pytest` na wyczyszczonych modelach i upewnij się, że istniejące testy domenowe przechodzą.

### ETAP 2: Parser i Formatter Mapki Tekstowej `wX mY/mZ` (Tasks 011–022)

Zanim podepniemy nowy scraper, budujemy moduł `backend/app/engine/availability_parser.py`, który operuje na dokładnym formacie zdefiniowanym przez użytkownika (np. trasa Koluszki – Opole).

* [x] **Task 011:** Utwórz plik `backend/app/engine/availability_parser.py`.

* [x] **Task 012:** Zaimplementuj funkcję `parse_availability_text(raw_text: str) -> FullTrainAvailabilityMap`, która dzieli tekst na bloki przystanków.

* [x] **Task 013:** W `parse_availability_text` obsłuż nagłówki odcinków z godziną lub bez godziny, np.:

  * `"Koluszki - Piotrków"` -> `from_station="Koluszki"`, `to_station="Piotrków"`, `departure_time=""`

  * `"Piotrków - Radomsko 15:12"` -> `from_station="Piotrków"`, `to_station="Radomsko"`, `departure_time="15:12"`

  * `"Częstochowa - Częstochowa str. 15:46"` -> poprawna obsługa kropek i spacji w nazwach stacji.

* [x] **Task 014:** W `parse_availability_text` obsłuż linie wagonów niezależnie od wielkości liter i znaków specjalnych (`|`, ukośniki na końcu linii):

  * `"w5 m82 |"` -> Wagon `"5"`, miejsca `["82"]`

  * `"W3 m87/m84/m91"` -> Wagon `"3"`, miejsca `["87", "84", "91"]`

  * `"w6 M15/m53"` -> Wagon `"6"`, miejsca `["15", "53"]`

  * `"W4 m76/"` -> Wagon `"4"`, miejsca `["76"]` (ignorowanie pustego elementu po końcowym `/`).

* [x] **Task 015:** Zaimplementuj funkcję odwrotną `format_availability_map(avail_map: FullTrainAvailabilityMap) -> str`, która z obiektu `FullTrainAvailabilityMap` generuje czysty tekst w formacie:

  ```
  Piotrków - Radomsko 15:12
  W3 m91
  W4 m76
  W5 m32/m33/m65/m74/m82/m88
  
  ```

* [x] **Task 016:** Zaimplementuj konwerter `availability_map_to_matrix(avail_map: FullTrainAvailabilityMap) -> AvailabilityMatrix`, który:

  1. Zbiera unikalny zbiór wszystkich par `(wagon, miejsce)` występujących na którymkolwiek odcinku całej trasy.

  2. Dla każdego odcinka `seg_idx` oznacza miejsce jako `AVAILABLE`, jeśli znajduje się na liście wolnych miejsc danego wagonu w tym odcinku, lub `OCCUPIED`, jeśli go tam nie ma.

* [x] **Task 017:** Utwórz plik testowy `backend/tests/test_koluszki_opole.py` i wklej do niego jako stałą `KOLUSZKI_OPOLE_RAW` dokładny tekst z przykładu użytkownika (6 odcinków od Koluszek do Opola).

* [x] **Task 018:** Napisz test `test_parse_koluszki_opole()`, który sprawdza, czy parser wykrywa dokładnie 6 odcinków:

  1. `Koluszki` -> `Piotrków`

  2. `Piotrków` -> `Radomsko` (`15:12`)

  3. `Radomsko` -> `Częstochowa` (`15:40`)

  4. `Częstochowa` -> `Częstochowa str.` (`15:46`)

  5. `Częstochowa str` -> `Lubliniec` (`16:06`)

  6. `Lubliniec` -> `Opole`

* [x] **Task 019:** W `test_parse_koluszki_opole()` zweryfikuj normalizację nazw stacji (np. aby `"Częstochowa str."` jako stacja końcowa odcinka 4 i `"Częstochowa str"` jako stacja początkowa odcinka 5 były traktowane jako ta sama stacja).

* [x] **Task 020:** W `test_parse_koluszki_opole()` sprawdź, czy na odcinku `Koluszki - Piotrków` wolne są wyłącznie 3 miejsca: `(W5, 82)`, `(W6, 15)`, `(W6, 53)`.

* [x] **Task 021:** W `test_parse_koluszki_opole()` sprawdź, czy miejsce `(W5, 82)` ma status `AVAILABLE` na odcinkach `0, 1, 2, 3` (od Koluszek do Częstochowy Stradom) i `OCCUPIED` na odcinkach `4, 5`.

* [x] **Task 022:** W `test_parse_koluszki_opole()` sprawdź, czy miejsce `(W3, 84)` ma status `AVAILABLE` na odcinkach `2, 3, 4, 5` (od Radomska aż do Opola).

### ETAP 3: Naprawa Algorytmu Optymalizacji pod kątem Minimalnej Liczby Biletów i Bliskości Miejsc (Tasks 023–035)

Użytkownik jasno określił cel: **jak najmniejsza liczba biletów (przesiadek) dla jednej osoby** oraz **preferowanie bliskich miejsc/wagonów**, wraz z inteligentną obsługą zakładek (np. `W5 m82` od Koluszek do Częstochowy Stradom lub Radomska, a potem `W3 m84` do Opola).

* [x] **Task 023:** Przeanalizuj `backend/app/optimizer/solver.py` oraz `backend/app/engine/scorer.py`. Obecnie w `solver.py` funkcja `generate_maximal_blocks` generuje tylko maksymalne bloki ciągłe `(s_start, s_end)`, ale w `find_optimal_path` przejścia w DAG następują wyłącznie z `b1` kończącego się w `s` do `b2` zaczynającego się w `s`.

* [x] **Task 024:** **KRYTYCZNY BŁĄD W `solver.py` (linie 100–137):** W obecnym `solver.py` blok dla `(W5, 82)` ma zakres `[0, 4)` (Koluszki -> Częstochowa Stradom), a blok dla `(W3, 84)` ma zakres `[2, 6)` (Radomsko -> Opole). Ponieważ `generate_maximal_blocks` tworzy TYLKO maksymalne przedziały, w `blocks_by_start[4]` NIE MA bloku `(W3, 84)` (bo ten zaczął się już na indeksie `2`!), a blok `(W5, 82)` nie kończy się na indeksie `2` (bo kończy się dopiero na `4`). Przez to obecny `solver.py` **NIE POTRAFI** połączyć `W5 m82` z `W3 m84`!

* [x] **Task 025:** Napraw ten błąd w `backend/app/optimizer/solver.py`:

  * Kiedy dwa maksymalne bloki `B1 = [start1, end1)` oraz `B2 = [start2, end2)` nakładają się na siebie (czyli `start1 < start2 <= end1 < end2`), pasażer może przesiąść się na dowolnej stacji pośredniej `s_transfer` należącej do przedziału `[start2, end1]`.

  * Wygeneruj w DAG krawędzie przejścia dla każdego takiego punktu `s_transfer`, przycinając efektywny czas pierwszego bloku do `[start1, s_transfer)` i drugiego bloku do `[s_transfer, end2)`.

* [x] **Task 026:** Dodaj w `solver.py` wybór najlepszego punktu przesiadki wewnątrz zakładki (`[start2, end1]`):

  * Udostępnij oba warianty (lub zwróć informację o oknie przesiadki `transfer_window: "Radomsko – Częstochowa Stradom"`), aby użytkownik wiedział, że może kupić bilet nr 1 np. do Częstochowy Stradom (lub Radomska) i w dowolnym momencie między Radomskiem a Częstochową Stradom przejść na miejsce docelowe `W3 m84`.

* [x] **Task 027:** Skalibruj wagi w `backend/app/engine/scorer.py` tak, aby priorytetem nr 1 była **minimalizacja liczby biletów (przesiadek)**:

  * Każda przesiadka (dodatkowy bilet) musi mieć stały koszt bazowy `W_TICKET = 1000.0`, tak aby trasa z 1 przesiadką (2 bilety) ZAWSZE wygrywała z trasą z 2 przesiadkami (3 bilety), o ile obie pokrywają 100% trasy na siedząco.

* [x] **Task 028:** Skalibruj wagi odległości fizycznej (`calculate_distance_penalty` w `scorer.py`) jako kryterium drugorzędne (rozstrzygające między trasami o tej samej liczbie biletów):

  * Zmiana miejsca w tym samym wagonie i tym samym przedziale (`abs(s1 - s2) <= 8` lub ten sam numer przedziału PKP): kara `+15 pkt`.

  * Zmiana miejsca w tym samym wagonie (inny przedział): kara `+40 pkt`.

  * Zmiana wagonu o $\Delta w = |w_1 - w_2|$: kara $+100 \cdot (\Delta w)^2\text{ pkt}$.

* [x] **Task 029:** W `backend/app/engine/topology.py` popraw funkcję numeracji przedziałów PKP (`get_compartment`):

  * W wagonach PKP IC dwucyfrowe numery miejsc (np. `15`, `53`, `82`, `84`) mają prostą zasadę: **pierwsza cyfra (dziesiątki) to numer przedziału**, a druga cyfra to pozycja miejsca! (Np. `m81, m82, m83, m84, m85, m86, m87, m88` to ten sam 8. przedział; dla miejsc `>= 100`, np. `m106`, dwie pierwsze cyfry `10` to numer przedziału).

  * Zastąp obecne dzielenie `(n - 1) // 6` dokładną regułą kolejową PKP: `compartment = int(seat_num) // 10`.

* [x] **Task 030:** Napisz test jednostkowy w `backend/tests/test_topology.py` sprawdzający, że miejsca `81, 82, 83, 88` w wagonie 5 należą do przedziału `8`, a miejsce `106` w wagonie 3 należy do przedziału `10`.

* [x] **Task 031:** W `backend/tests/test_koluszki_opole.py` napisz test `test_solve_koluszki_opole_optimal()`, który uruchamia `SeatOptimizer` na sparsowanych danych z `KOLUSZKI_OPOLE_RAW`.

* [x] **Task 032:** Sprawdź w teście `test_solve_koluszki_opole_optimal()`, że najlepsza zwrócona trasa (`routes[0]`) wymaga **dokładnie 1 przesiadki (2 bloków)**:

  * Pierwszy blok: Wagon `"5"`, Miejsce `"82"` (lub Wagon `"6"`, Miejsce `"15"` przy przesiadce w Radomsku/Częstochowie).

  * Drugi blok: Wagon `"3"`, Miejsce `"84"` aż do stacji `Opole`.

* [x] **Task 033:** Sprawdź w teście, że jeśli uciąć trasę do odcinka `Koluszki -> Lubliniec`, optymalizator jako najlepszą opcję (najbliższe miejsca) wskaże jazdę w **tym samym Wagonie 5**: najpierw `W5 m82` (Koluszki -> Częstochowa Str.), a potem przesiadkę w tym samym przedziale na `W5 m81` (lub `m83` / `m88`) do Lublińca!

* [x] **Task 034:** Dodaj do obiektu rekomendacji czytelne podsumowanie biletów do zakupu (`tickets_to_buy`), np.:

  * `Bilet 1: Koluszki -> Częstochowa Stradom (Wagon 5, Miejsce 82)`

  * `Bilet 2: Częstochowa Stradom -> Opole (Wagon 3, Miejsce 84)`

* [x] **Task 035:** Uruchom `pytest backend/tests/test_koluszki_opole.py` – wszystkie testy muszą przejść na zielono.

### ETAP 4: Prawdziwy Scraper Trasy i Godzin Pociągu – Portal Pasażera / Rozkład (Tasks 036–050)

Program musi poznać dokładną trasę pociągu `A -> B -> C -> D -> E -> F` wraz z **godzinami odjazdu z każdej stacji**, aby później wiedzieć, o której godzinie szukać tego pociągu na kolejnych odcinkach.

* [x] **Task 036:** Przebuduj `backend/app/scraper/schedule_scraper.py`. Zamiast gubić godziny na mobilnym widoku, stwórz klasę `TrainRouteExtractor`.

* [x] **Task 037:** Dodaj w `TrainRouteExtractor` metodę `get_train_route_by_number_or_relation(from_station: str, to_station: str, date_str: str, time_str: str, train_number: Optional[str] = None) -> List[RouteStop]`.

* [x] **Task 038:** Zdefiniuj strukturę `RouteStop`:

  * `station_name: str` (np. `"Piotrków Trybunalski"`)

  * `eva_code: str` (kod HAFAS/EVA stacji, np. `"5100047"`)

  * `arrival_time: Optional[str]` (np. `"15:11"`)

  * `departure_time: Optional[str]` (np. `"15:12"`)

  * `duration_from_start_min: int`

* [x] **Task 039:** Zaimplementuj w `TrainRouteExtractor` pobieranie szczegółów trasy z oficjalnego widoku szczegółów kursu (`traininfo.exe` / Portal Pasażera / API e-IC):

  * W obecnym `schedule_scraper.py` (linie 161–244) parser wyciąga tylko nazwę stacji i czas w minutach, ale **odrzuca dokładny string godziny odjazdu (`dep_m`) i przyjazdu (`arr_m`)**!

  * Zapisuj dokładne stringi `arrival_time` (`HH:MM`) oraz `departure_time` (`HH:MM`) dla każdej stacji na trasie.

* [x] **Task 040:** Dodaj obsługę wyszukiwania po samym numerze pociągu (np. użytkownik wpisuje nr pociągu z `portalpasazera.pl` lub `intercity.pl`, np. `"IC 16104"`, stację początkową `"Koluszki"` i końcową `"Opole Główne"`).

* [x] **Task 041:** Jeśli podano `train_number`, filtruj listę połączeń na stronie wyników tak, aby wybrać dokładnie ten pociąg, którego numer zgadza się z `train_number` (ignorując spacje i wielkość liter, np. `IC16104` == `IC 16104`).

* [x] **Task 042:** Wyciągnij ze szczegółów wybranego pociągu również kody EVA poszczególnych stacji pośrednich z linków `stboard.exe/pn?input=5100XXX` w tabeli przystanków (dzięki temu nie musimy zgadywać kodów EVA dla mniejszych stacji takich jak `Częstochowa Stradom` czy `Radomsko`!).

* [x] **Task 043:** Zaktualizuj `backend/app/db/seed_stations.py` oraz `RouteResolver` tak, aby każde nowo napotkane na trasie pociągu `(station_name, eva_code)` było automatycznie dopisywane (`INSERT OR REPLACE`) do lokalnej bazy SQLite `stations`.

* [x] **Task 044:** Popraw metodę `build_segments(stops: List[RouteStop]) -> List[RouteSegment]` w `route_resolver.py`:

  * Każdy segment `i` od `stops[i]` do `stops[i+1]` MUSI mieć przypisane:

    * `from_station_name = stops[i].station_name`

    * `to_station_name = stops[i+1].station_name`

    * `departure_time = stops[i].departure_time` (np. `"15:12"` dla Piotrkowa)

    * `arrival_time = stops[i+1].arrival_time` (np. `"15:39"` dla Radomska)

    * `duration_minutes` wyliczone z różnicy czasu przyjazdu/odjazdu (uwzględniając przejście przez północ `23:50 -> 00:15`).

* [x] **Task 045:** **KRYTYCZNA ZMIANA W `NodeMerger` (`route_resolver.py`):**

  * Obecnie `NodeMerger` ma ustawione `min_segment_minutes: int = 8`, co powoduje, że na trasie Koluszki – Opole **skleja i usuwa** przystanek `Częstochowa -> Częstochowa Stradom` (bo jazda między nimi trwa tylko 6 minut: `15:40 -> 15:46`)!

  * Tymczasem jak pokazał przykład użytkownika, na odcinku `Częstochowa - Częstochowa Str.` zwalniają się zupełnie inne miejsca niż wcześniej!

  * Zmień domyślny tryb `NodeMerger` tak, aby **NIE USUWAŁ żadnych stacji pośrednich**, na których zatrzymuje się pociąg (ustaw `merge_short_segments: bool = False` jako domyślne, pozostawiając łączenie tylko jako opcjonalny przełącznik dla użytkownika).

* [x] **Task 046:** Dodaj obsługę portalu `portalpasazera.pl` (lub bezpośredniego endpointu rozkładu PLK/HAFAS) jako weryfikatora listy stacji i godzin odjazdów dla zadanego numeru pociągu.

* [x] **Task 047:** Dodaj logowanie pełnej wykrytej trasy do konsoli przed rozpoczęciem scrapowania miejsc, np.:
  `[ROUTE] Pociąg IC XXXX: 1. Koluszki (14:45) -> 2. Piotrków Tryb. (15:12) -> 3. Radomsko (15:40) -> ...`

* [x] **Task 048:** Napisz test jednostkowy w `backend/tests/test_schedule_scraper.py` sprawdzający, że parser szczegółów pociągu wyciąga dla każdej stacji jej `eva_code` bezpośrednio z atrybutu `href` (`input=51...`) oraz dokładną godzinę `departure_time` (`HH:MM`).

* [x] **Task 049:** Napisz test w `backend/tests/test_route_resolver.py` sprawdzający, że odcinek 6-minutowy (`Częstochowa 15:40 -> Częstochowa Stradom 15:46`) NIE JEST wycinany z listy segmentów.

* [x] **Task 050:** Uruchom `pytest backend/tests/test_schedule_scraper.py backend/tests/test_route_resolver.py` i potwierdź 100% sukcesu.

### ETAP 5: Prawdziwy Scraper Wagonów i Zielonych Miejsc z PKP Intercity (Tasks 051–075)

To najważniejszy etap techniczny. Scraper musi zachowywać się dokładnie tak, jak opisał użytkownik: skakać po odcinkach `A->B`, `B->C`, `C->D` trzymając się tego samego pociągu (według godziny odjazdu ze stacji początkowej odcinka i numeru pociągu), wchodzić w wybór miejsca ze schematu, sprawdzać po kolei każdy wagon i wyciągać wszystkie wolne (zielone) miejsca.

* [x] **Task 051:** Utwórz nowy moduł `backend/app/scraper/ebilet_seat_scraper.py` oparty na `playwright.async_api`.

* [x] **Task 052:** Skonfiguruj kontekst przeglądarki Playwright (`BrowserContext`) tak, aby nie był blokowany przez system e-IC:

  * Ustaw realistyczny `user_agent` (najnowszy Chrome na Windows 10/11), `viewport={"width": 1440, "height": 900}`, `locale="pl-PL"`, `timezone_id="Europe/Warsaw"`.

  * Wyłącz flagę `navigator.webdriver` za pomocą `page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")`.

* [x] **Task 053:** Dodaj możliwość uruchomienia przeglądarki w trybie widocznym (`headless=False` sterowane zmienną środowiskową `SCRAPER_HEADLESS=false`), aby użytkownik mógł na własne oczy widzieć, jak bot przeklikuje stacje i wagony na stronie PKP Intercity!

* [x] **Task 054:** Zaimplementuj metodę `search_segment_on_intercity(page: Page, from_station: str, to_station: str, date_str: str, departure_time: str, train_number: str)`:

  * Wejdź na stronę wyszukiwania `https://ebilet.intercity.pl/` (lub bezpośrednio przekaż parametry wyszukiwania odcinka).

  * Jeśli wyświetli się baner cookies (np. przycisk akceptacji zgód), automatycznie go zamknij.

* [x] **Task 055:** Wprowadź stację początkową `from_station` w pole wyszukiwarki („Stacja początkowa”), poczekaj na listę podpowiedzi autocomplete i wybierz właściwą stację.

* [x] **Task 056:** Wprowadź stację końcową `to_station` w pole „Stacja końcowa”, poczekaj na listę podpowiedzi i wybierz właściwą stację.

* [x] **Task 057:** Ustaw datę wyjazdu `date_str` oraz **godzinę wyjazdu równą `departure_time` danego odcinka** (np. jeśli pociąg odjeżdża z Piotrkowa o `15:12`, ustaw czas wyszukiwania na `15:00` lub `15:10`, aby ten pociąg był na samej górze listy wyników).

* [x] **Task 058:** Kliknij przycisk „Szukaj” i poczekaj na załadowanie listy połączeń dla odcinka `from_station -> to_station`.

* [x] **Task 059:** Na liście wyników wyszukaj kafelek/wiersz odpowiadający naszemu pociągowi:

  * Dopasuj po numerze pociągu `train_number` (np. `IC 16104` / `TLK ...`) ORAZ godzinie odjazdu zbliżonej do `departure_time` ($\pm 5$ minut).

  * Dzięki temu masz 100% pewności, że na odcinku `B -> C` sprawdzasz dokładnie ten sam pociąg, którym przyjechałeś z `A -> B`.

* [x] **Task 060:** Kliknij „Wybierz” / „Kup bilet” przy znalezionym pociągu (dla klasy 2 lub wybranej przez użytkownika klasy wagonu), aby przejść do ekranu konfiguracji biletu (1 pasażer, bilet normalny).

* [x] **Task 061:** Na ekranie wyboru miejsca kliknij przycisk **„Wybierz miejsce ze schematu”** (lub „Wskazanie miejsca na schemacie wagonu”), który otwiera graficzny podgląd wagonów.

* [x] **Task 062:** **Podwójny mechanizm ekstrakcji miejsc (Network JSON + DOM SVG/HTML):**

  * **Sposób A (Przechwytywanie odpowiedzi JSON z API e-IC):** Podłącz listener `page.on("response", ...)` przechwytujący odpowiedzi z endpointów zawierających układ wagonów i statusy miejsc (np. odpowiedzi JSON zawierające listę wagonów, numery miejsc i flagi dostępności `available` / `free` / `status`).

  * **Sposób B (Bezpośrednia inspekcja DOM / Przeklikiwanie wagonów):** Odczytaj listę zakładek/przycisków wszystkich dostępnych wagonów (np. wagony `3, 4, 5, 6, 7, 8`).

* [x] **Task 063:** Zaimplementuj pętlę przeklikującą po kolei każdy wagon na schemacie (jeśli dane dla wszystkich wagonów nie przyszły od razu w jednym JSON-ie):

  * Dla każdego przycisku wagonu (np. `Wagon 3`, `Wagon 4`...):

    1. Kliknij zakładkę wagonu.

    2. Poczekaj na wyrenderowanie schematu miejsc dla tego wagonu.

    3. Wyciągnij numer wagonu (np. `"3"`).

* [x] **Task 064:** Zaimplementuj selektor wolnych („zielonych”) miejsc na schemacie wagonu:

  * Na graficznym schemacie wagonu w `ebilet.intercity.pl` każde miejsce to element DOM/SVG posiadający numer miejsca oraz klasę/atrybut stanu (wolne miejsca są klikalne / zielone, a zajęte są wyszarzone / przekreślone / posiadają klasę `occupied`, `disabled`, `taken` lub brak klasy `available`/`free`).

  * Wyciągnij wszystkie numery miejsc, które są oznaczone jako **wolne do rezerwacji**.

* [x] **Task 065:** Odfiltruj elementy niebędące zwykłymi miejscami siedzącymi (lub oznacz je osobno), jeśli użytkownik szuka standardowego miejsca (np. miejsca dla wózków inwalidzkich czy stojaki rowerowe, chyba że użytkownik je dopuścił).

* [x] **Task 066:** Posortuj znalezione numery wolnych miejsc w każdym wagonie rosnąco numerycznie (np. `["15", "53"]`, `["83", "84", "106"]`) i zapisz je w słowniku `carriages[carriage_no] = free_seats`.

* [x] **Task 067:** Po zebraniu wszystkich wagonów dla odcinka `A -> B`, zamknij modal schematu wagonu i przejdź do kolejnego odcinka `B -> C` (używając godziny odjazdu ze stacji `B`!).

* [x] **Task 068:** Dodaj obsługę sytuacji, w której na danym krótkim odcinku (np. `Częstochowa Str. - Lubliniec`) w wybranym wagonie (np. `W6`) nie ma ani jednego wolnego miejsca – wówczas wagon ten nie ma wolnych miejsc na tym odcinku, co naturalnie oznacza, że wszystkie jego miejsca są tam `OCCUPIED`.

* [x] **Task 069:** Dodaj mechanizm zapisu zrzutu diagnostycznego (screenshot + zrzut JSON/HTML schematu do folderu `logs/debug_snapshots/`) w przypadku, gdyby na którymś odcinku selektor nie znalazł schematu wagonów, aby łatwo było podejrzeć przyczynę.

* [x] **Task 070:** Dodaj w `ic_client.py` buforowanie (cache w SQLite `segment_cache`) pobranej mapy miejsc dla danego `(train_number, date, from_station, to_station)` z czasem życia 10 minut, aby przy odświeżeniu strony nie trzeba było przeklikiwać od nowa tych samych odcinków.

* [x] **Task 071:** Połącz `TrainRouteExtractor` i `EbiletSeatScraper` w głównym serwisie `LiveTrainScanner`:

  1. Pobiera listę stacji i godzin dla pociągu.

  2. Dla każdego odcinka `i` wywołuje `EbiletSeatScraper`.

  3. Zwraca kompletny obiekt `FullTrainAvailabilityMap`.

* [x] **Task 072:** Po zakończeniu skanowania wszystkich odcinków automatycznie wywołaj `format_availability_map(avail_map)` i zapisz wygenerowaną mapkę (`w3 m84...`) do pliku tekstowego `logs/last_scan_map.txt` oraz zwróć ją w odpowiedzi API.

* [x] **Task 073:** Dodaj strumieniowanie postępu (Server-Sent Events lub endpoint statusu), aby podczas skanowania (które dla 6 odcinków potrwa ok. 20–40 sekund) użytkownik widział na żywo:
  `Skanowanie odcinka 3/6: Radomsko -> Częstochowa (15:40)... Znaleziono wolne miejsca w wagonach: W3 (3), W4 (1), W5 (8), W6 (4), W7 (12), W8 (5)`.

* [x] **Task 074:** Napisz skrypt testowy CLI `backend/app/cli_live_test.py`, który przyjmuje stację A, stację B, datę i opcjonalnie numer pociągu, odpala prawdziwe skanowanie w Playwright i wypisuje w terminalu dokładną mapkę `wX mY/mZ` oraz wyliczoną trasę przesiadek.

* [x] **Task 075:** Przetestuj działanie `cli_live_test.py` i zweryfikuj, że w kodzie nie ma już ani jednej linijki zmyślającej wagony lub miejsca.

### ETAP 6: Rozbudowa API Backendowego (FastAPI) i CLI (Tasks 076–088)

Użytkownik powinien móc korzystać z aplikacji na dwa sposoby:

1. **Automatyczny (Live Scraper):** Wpisuje stację początkową, końcową, datę i nr pociągu -> bot sam zbiera mapkę z PKP IC i liczy optymalne miejsca.

2. **Półautomatyczny / Tekstowy (Wklejenie rozpiski `w3 m54/m56`):** Wkleja rozpiskę (lub edytuje tę pobraną przez bota) i od razu dostaje optymalny plan zakupu biletów.

* [x] **Task 076:** W pliku `backend/app/main.py` usuń stare referencje do fałszywego generatora z `ic_client.py`.

* [x] **Task 077:** Dodaj w `backend/app/main.py` endpoint `POST /api/v1/optimize-from-text`:

  * Przyjmuje JSON: `{"raw_text": "Koluszki - Piotrków\nw5 m82\n..."}`.

  * Parsuje tekst przez `parse_availability_text`.

  * Buduje `AvailabilityMatrix`.

  * Uruchamia `SeatOptimizer` i zwraca pełny `OptimizationResponse` (wraz z listą biletów do kupienia, punktami przesiadek i macierzą wizualną).

* [x] **Task 078:** Zaktualizuj w `backend/app/main.py` endpoint `POST /api/v1/optimize` (tryb Live):

  * Przyjmuje `from_station`, `to_station`, `date`, `time`, `train_number` (opcjonalnie) oraz flagę `headless: bool = True`.

  * Uruchamia `LiveTrainScanner`.

  * Zwraca zarówno wygenerowaną tekstową rozpiskę `raw_availability_report` (w formacie `w3 m54/m58`), jak i wyliczone rekomendacje przesiadek.

* [x] **Task 079:** Dodaj w `backend/app/main.py` endpoint `GET /api/v1/train-route`:

  * Pozwala najpierw pobrać samą trasę pociągu stacja po stacji wraz z godzinami odjazdów (np. `Koluszki 14:45 -> Piotrków 15:12 -> Radomsko 15:40...`), aby użytkownik przed odpaleniem pełnego skanowania wagonów widział, jakie przystanki będą badane.

* [x] **Task 080:** Rozbuduj `backend/app/cli.py` o flagę `--from-file <sciezka.txt>`, która wczytuje plik tekstowy z rozpiską miejsc (np. `koluszki_opole.txt`), wypisuje tabelę dostępności i podaje dokładną instrukcję, jakie bilety kupić.

* [x] **Task 081:** Rozbuduj `backend/app/cli.py` o flagę `--show-browser`, która podczas skanowania na żywo otwiera okno Chromium (`headless=False`), pokazując użytkownikowi przeklikiwanie kolejnych odcinków i wagonów na stronie Intercity.

* [x] **Task 082:** W odpowiedzi API (`SeatBlockSchema` w `schemas.py`) dodaj pola:

  * `compartment: int` (numer przedziału wyliczony zgodnie z regułą PKP)

  * `alternative_seats_in_same_carriage: List[str]` (inne miejsca w tym samym wagonie wolne na dokładnie tym samym odcinku, np. na odcinku `Częstochowa Str - Lubliniec` w `W5` oprócz `m81` wolne są też `m83` i `m88`).

* [x] **Task 083:** W odpowiedzi API (`RouteRecommendationSchema`) dodaj sekcję `purchase_instructions: List[str]`, która generuje gotową „ściągawkę” do zakupu biletów, np.:

  * `"Krok 1: Kup bilet na odcinek Koluszki -> Częstochowa Stradom: wybierz Wagon 5, Miejsce 82"`

  * `"Krok 2: Kup bilet na odcinek Częstochowa Stradom -> Opole: wybierz Wagon 3, Miejsce 84 (Uwaga: to miejsce jest wolne już od stacji Radomsko 15:40, więc możesz się na nie przesiąść wcześniej!)"`

* [x] **Task 084:** Napisz test API w `backend/tests/test_api.py` dla endpointu `POST /api/v1/optimize-from-text` przesyłający pełny tekst trasy `Koluszki - Opole`.

* [x] **Task 085:** Zweryfikuj w teście `test_api.py`, że status odpowiedzi to `200 OK`, liczba segmentów wynosi `6`, a rekomendacja nr 1 zawiera instrukcję zakupu 2 biletów (`W5 m82` oraz `W3 m84`).

* [x] **Task 086:** Dodaj obsługę błędów w API: jeśli scraper napotka blokadę lub brak połączenia, API NIE MOŻE po cichu podstawić zmyślonych danych – musi zwrócić czytelny komunikat błędu HTTP `502 Bad Gateway` z opisem, na którym odcinku wystąpił problem.

* [x] **Task 087:** Zapisz przykładowy plik `backend/tests/fixtures/koluszki_opole.txt` z danymi użytkownika, aby zawsze służył jako wzorcowy test regresyjny.

* [x] **Task 088:** Uruchom `pytest backend/tests/test_api.py` i upewnij się, że wszystkie testy API przechodzą.

### ETAP 7: Aktualizacja Interfejsu Użytkownika (`index.html`) i Testy Końcowe (Tasks 089–100)

Na koniec dostosowujemy prosty interfejs w `backend/static/index.html`, aby wyświetlał prawdziwe dane, rozpiskę `wX mY/mZ` oraz pozwalał na oba tryby pracy.

* [x] **Task 089:** W `backend/static/index.html` dodaj przełącznik zakładek:

  * **Tryb 1: Skanuj pociąg automatycznie (Live PKP IC)**

  * **Tryb 2: Wklej / Edytuj rozpiskę miejsc (`w3 m54/m56`)**

* [x] **Task 090:** W Trybie 1 dodaj pole wyboru `Numer pociągu (opcjonalnie, np. IC 16104)` oraz checkbox `Pokaż okno przeglądarki podczas skanowania (podgląd na żywo)`.

* [x] **Task 091:** W Trybie 1 dodaj dwuetapowy przycisk:

  1. `1. Pobierz trasę i przystanki pociągu` (wyświetla listę stacji A -> B -> C -> D z godzinami odjazdów, np. `Piotrków 15:12`, `Radomsko 15:40`).

  2. `2. Skanuj wolne miejsca we wszystkich wagonach i optymalizuj`.

* [x] **Task 092:** W Trybie 2 dodaj duże pole `<textarea>` z przyciskiem `Załaduj przykład: Koluszki -> Opole`, które wypełnia pole dokładną rozpiską użytkownika i po kliknięciu `Oblicz najlepszą trasę` natychmiast pokazuje wynik.

* [x] **Task 093:** Po zakończeniu skanowania w Trybie 1 automatycznie wypełnij pole `<textarea>` wygenerowanym raportem `raw_availability_report` (w formacie `w5 m82...`), aby użytkownik widział czarno na białym wszystkie zielone miejsca znalezione w każdym wagonie na każdym odcinku.

* [x] **Task 094:** W sekcji wyników na górze wyświetl wyraźną ramkę **„Plan Zakupu Biletów i Przesiadek”** (`purchase_instructions`), pokazującą:

  * Od jakiej stacji do jakiej stacji siedzieć w którym wagonie i na którym miejscu.

  * Informację o zakładkach (od której stacji kolejne miejsce jest już wolne).

  * Alternatywne wolne miejsca w tym samym wagonie/przedziale.

* [x] **Task 095:** Popraw tabelę wizualną (Macierz Gantta) w `index.html`:

  * W nagłówkach kolumn wyświetlaj nie tylko nazwy stacji `A -> B`, ale też **godzinę odjazdu** (np. `Piotrków -> Radomsko (15:12)`).

  * Grupuj wiersze według prawdziwych numerów wagonów (np. `Wagon 3`, `Wagon 4`, `Wagon 5`...) i wyświetlaj numer przedziału obok numeru miejsca (np. `W5 / Miejsce 82 (Przedział 8)`).

* [x] **Task 096:** Dodaj w tabeli Gantta filtrowanie: przycisk `Pokaż tylko miejsca wchodzące w skład rekomendacji` vs `Pokaż wszystkie wolne miejsca w pociągu`.

* [x] **Task 097:** Uruchom pełny zestaw testów jednostkowych i integracyjnych: `pytest -v`. Wszystkie testy muszą przechodzić w 100%.

* [x] **Task 098:** Przetestuj w terminalu komendę CLI na pliku `koluszki_opole.txt`:
  `python -m app.cli --from-file tests/fixtures/koluszki_opole.txt` i zweryfikuj poprawność wypisanego planu.

* [x] **Task 099:** Uruchom serwer `uvicorn app.main:app --reload`, otwórz `http://localhost:8000` i przetestuj zarówno przeliczenie trasy `Koluszki -> Opole`, jak i skanowanie połączenia.

* [x] **Task 100:** Zaktualizuj wszystkie odhaczone zadania `[x]` w tym pliku i wykonaj commit zmian w repozytorium git.

## Część 4: Gotowy prompt do wklejenia dla Agenta

> Przeczytaj plik `FIX_PLAYBOOK_100_STEPS.md`. W obecnym kodzie `ic_client.py` zamockowałeś wagony i miejsca, a w `solver.py` pominąłeś przecinające się bloki miejsc. Wykonaj teraz rygorystycznie plan naprawczy z `FIX_PLAYBOOK_100_STEPS.md` punkt po punkcie od Task 001 do Task 100, zaczynając od usunięcia sztucznych danych, dodania parsera mapki `wX mY/mZ` i przejścia testów na danych z trasy Koluszki – Opole, a następnie wdrożenia prawdziwego scrapera Playwright.