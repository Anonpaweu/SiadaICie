# Dokumentacja Architektoniczna i Plan Wdrożenia: PKP Intercity Seat Optimizer ("Siedzisz.pl")

> **Instrukcja dla Agenta AI (Antigravity / Cursor / Windsurf):**
> Ten dokument stanowi kompletną specyfikację techniczną ("Single Source of Truth"). Twoim zadaniem jest implementacja systemu krok po kroku zgodnie z poniższymi założeniami architektonicznymi, strukturami danych, równaniami matematycznymi oraz obsługą przypadków brzegowych. Nie pomijaj żadnego z kroków i nie upraszczaj logiki algorytmu grafowego.

---

## 1. Opis Problemu i Cel Biznesowy

System sprzedaży PKP Intercity (`ebilet.intercity.pl`) alokuje miejsca w sposób zachłanny. Jeśli pasażer chce kupić bilet w relacji $S_0 \to S_K$ (np. stacje $A \to G$), a wszystkie miejsca mają chociaż jeden zajęty odcinek pośredni (np. $B \to C$), system zwraca informację: **"Brak gwarancji miejsca do siedzenia"** (lub całkowicie blokuje sprzedaż w pociągach kategorii EIP/EIC).

W rzeczywistości w pociągu niemal zawsze znajdują się wolne fotele na poszczególnych odcinkach. Celem aplikacji jest:
1. Automatyczne pobranie listy stacji pośrednich oraz dokładnych godzin przyjazdów/odjazdów dla wybranego pociągu na trasie $S_0 \to S_K$.
2. Odpytanie systemu rezerwacyjnego PKP IC o mapy zajętości wszystkich wagonów dla każdego pojedynczego segmentu elementarnego $(S_i, S_{i+1})$.
3. Zbudowanie trójwymiarowej macierzy zajętości miejsc w czasie i przestrzeni pociągu.
4. Wyznaczenie za pomocą **algorytmu najkrótszej ścieżki w skierowanym grafie acyklicznym (DAG) / Programowania Dynamicznego** optymalnych strategii podróży, które:
   - Maksymalizują nieprzerwany czas siedzenia na jednym miejscu (szczególnie w drugiej połowie lub do samego końca trasy).
   - Minimalizują liczbę przesiadek wewnątrz pociągu.
   - **Minimalizują fizyczny dystans przesiadki** (preferencja zmiany miejsca w tym samym przedziale $\to$ tym samym wagonie $\to$ sąsiednim wagonie, silna kara za spacery np. z wagonu 3 do 9 i z powrotem).
   - Wykorzystują tzw. **"Zasób Zakładki" (Overlapping Windows)** – jeśli miejsce $M_1$ jest wolne na $A \to D$, a miejsce $M_2$ jest wolne na $C \to G$, system inteligentnie ocenia, czy lepiej przesiąść się na stacji $C$ (np. mniej ludzi na korytarzu), czy na stacji $D$ (dłuższy pierwszy odcinek).
   - Obsługują sytuację, w której na pewnym krótkim odcinku $S_i \to S_{i+1}$ fizycznie nie ma żadnego wolnego miejsca w całym pociągu (kontrolowany stan `STANDING_CORRIDOR`).

---

## 2. Architektura Systemu i Stack Technologiczny

System składa się z 3 głównych modułów działających lokalnie lub w kontenerach Docker:

### 2.1. Backend (Python 3.11+ / FastAPI)
* **Framework API:** `FastAPI` + `Uvicorn` + `WebSockets` (do strumieniowania postępu scrapowania w czasie rzeczywistym na frontend).
* **Silnik Scrapujący:** `Playwright` (async, Chromium w trybie headless z wtyczką `playwright-stealth`) + `httpx` (do szybkich strzałów REST API po przechwyceniu tokenów sesyjnych i ciasteczek z przeglądarki).
* **Silnik Obliczeniowy:** `NumPy` (operacje macierzowe) oraz natywny moduł algorytmiczny oparty na programowaniu dynamicznym.
* **Cache i Kolejkowanie:** `diskcache` lub `Redis` (lokalny cache na pobrane rozkłady jazdy oraz mapy wagonów z TTL = 10 minut, aby nie powtarzać zapytań przy zmianie wag algorytmu przez użytkownika).
* **Walidacja Danych:** `Pydantic v2`.

### 2.2. Frontend (React 18+ / TypeScript / Vite)
* **UI & Styling:** `TailwindCSS` + komponenty `shadcn/ui` + ikony `Lucide React`.
* **Zarządzanie stanem i API:** `@tanstack/react-query` + natywne `WebSocket API`.
* **Wizualizacja Gantta (Macierz Miejsc):** Wirtualizowana tabela (`@tanstack/react-virtual`) renderująca setki miejsc i kilkanaście stacji bez spadku płynności, z zaznaczoną ścieżką optymalnej podróży.

---

## 3. Struktura Katalogów Projektu

Agent musi utworzyć i utrzymywać dokładnie poniższą strukturę plików:

```text
pkp-seat-optimizer/
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py                 # Entrypoint FastAPI, konfiguracja CORS i WebSockets
│   │   ├── config.py               # Zmienne środowiskowe, czasy timeoutów, wagi domyślne
│   │   ├── models/
│   │   │   ├── domain.py           # Modele wewnętrzne (Station, Segment, Seat, Carriage, TrainTopology)
│   │   │   └── schemas.py          # Modele Pydantic (Request/Response API)
│   │   ├── scrapers/
│   │   │   ├── base.py             # Zarządzanie pulą kontekstów Playwright i sesjami
│   │   │   ├── schedule_scraper.py # Pobieranie trasy i przystanków pośrednich (Rozkład PKP / Portal Pasażera)
│   │   │   ├── intercity_api.py    # Reverse-engineering e-IC 2.0: pobieranie schematów wagonów (Frekwencja/Gwarancja)
│   │   │   └── mock_provider.py    # Deterministyczny generator danych testowych (scenariusze A-G)
│   │   ├── engine/
│   │   │   ├── matrix_builder.py   # Konwersja surowych odpowiedzi z wagonów na macierz 3D dostępności
│   │   │   ├── topology.py         # Logika fizycznej budowy pociągu (spięcia EZT, wagony restauracyjne, odległości)
│   │   │   ├── optimizer.py        # Główny algorytm DP / DAG wyznaczający Top-K tras przesiadkowych
│   │   │   └── scorer.py           # Funkcje kosztu, kary za zmianę wagonu i bonusy za ciągłość
│   │   └── services/
│   │       └── cache_service.py    # Warstwa cache'owania zapytań o segmenty
│   ├── tests/
│   │   ├── test_optimizer.py       # Testy jednostkowe algorytmu (w tym przypadek A->G z zakładką C-D)
│   │   └── test_topology.py        # Testy przechodniości między wagonami
│   ├── requirements.txt
│   └── pyproject.toml
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   │   ├── SearchForm.tsx          # Formularz: Stacja A, Stacja B, Data, Nr pociągu, Suwaki preferencji
│   │   │   ├── ScrapingProgress.tsx    # Pasek postępu na żywo z logami WebSocket (np. "Sprawdzam odcinek Kutno -> Konin [3/6]")
│   │   │   ├── StrategyCards.tsx       # Karty z wyliczonymi Top-3 strategiami (Timeline podróży + instrukcja krok po kroku)
│   │   │   ├── SeatGanttMatrix.tsx     # Interaktywny wykres Gantta wszystkich wagonów i miejsc na trasie
│   │   │   └── CarriageMiniMap.tsx     # Wizualny podgląd układu foteli w wybranym wagonie
│   │   ├── types/
│   │   │   └── api.ts
│   │   ├── App.tsx
│   │   └── main.tsx
│   ├── package.json
│   └── vite.config.ts
└── tasks.md
```

---

## 4. Modele Danych i Struktury Domenowe (`domain.py`)

### 4.1. Reprezentacja Trasy i Czasu
Niech trasa pociągu od stacji początkowej użytkownika $S_0$ do stacji docelowej $S_K$ składa się z $K+1$ stacji:
$$S = [S_0, S_1, S_2, \dots, S_K]$$

Definiujemy $K$ **segmentów elementarnych** (odcinków między sąsiednimi stacjami zatrzymania pociągu):
$$e_i = (S_i, S_{i+1}) \quad \text{dla } i \in \{0, 1, \dots, K-1\}$$

Każdy segment $e_i$ posiada:
* `index`: $i \in \{0, \dots, K-1\}$
* `from_station`: Obiekt `Station` (nazwa, ID stacji w systemie PKP / kod EVA, godzina odjazdu $t_{\text{dep}}(S_i)$)
* `to_station`: Obiekt `Station` (nazwa, ID stacji, godzina przyjazdu $t_{\text{arr}}(S_{i+1})$)
* `duration_minutes`: Czas trwania odcinka $\Delta t_i = t_{\text{arr}}(S_{i+1}) - t_{\text{dep}}(S_i)$ wyrażony w minutach (kluczowa waga dla algorytmu – lepiej siedzieć przez 90-minutowy odcinek bez przystanków niż przez 10-minutowy odcinek między dwoma przedmieściami).
* `dwell_time_minutes`: Czas postoju na stacji $S_i$ przed ruszeniem w segment $e_i$ (istotne przy ocenie komfortu przesiadki – łatwiej przejść przez 3 wagony, gdy pociąg stoi 8 minut na stacji węzłowej, niż na 1-minutowym postoju).

### 4.2. Reprezentacja Miejsca i Topologii Pociągu
Każde fizyczne miejsce do siedzenia $m \in M$ w pociągu jest unikalnie identyfikowane przez krotkę `(carriage_number, seat_number)` i posiada następujące właściwości:
* `id`: string, np. `"W10_M12"`
* `carriage_number`: int (np. `10`)
* `carriage_sequence_index`: int – **rzeczywista pozycja wagonu w składzie pociągu** $(0, 1, \dots, N-1)$. Uwaga: w PKP numeracja wagonów bywa nieciągła (np. po wagonie 7 idzie wagon 10, bo wagony 8 i 9 zostały odczepione lub jest między nimi wagon gastronomiczny WARS bez numeru).
* `unit_group_id`: int – identyfikator połączonej grupy wagonów, wewnątrz której można swobodnie przejść korytarzem w trakcie jazdy.
  * *KRYTYCZNY PRZYPADEK BRZEGOWY:* Pociągi zespolone (np. $2 \times \text{ED160 Flirt}$, $2 \times \text{ED250 Pendolino}$, $2 \times \text{ED74}$) często jadą w podwójnej trakcji (np. wagony 1–8 to pierwsza jednostka, wagony 9–16 to druga jednostka). **Pomiędzy jednostką 1 a 2 NIE MA przejścia dla pasażerów w trakcie jazdy!** Przesiadka między różnymi `unit_group_id` jest dozwolona wyłącznie na stacjach o postoju $\ge 2$ minuty (wyjście na peron i wejście do drugiego składu) i obarczona wysoką karą w funkcji kosztu.
* `seat_number`: int / string (np. `12`)
* `compartment_id`: int – numer przedziału (lub sekcji/rzędu w wagonie bezprzedziałowym, wyliczany z układu miejsc PKP, zazwyczaj $\lfloor \text{seat\_number} / 10 \rfloor$ w klasycznej numeracji UIC).
* `seat_class`: `1` lub `2`.
* `position_type`: Enum (`WINDOW`, `MIDDLE`, `AISLE`, `TABLE`).
* `special_zone`: Enum (`STANDARD`, `SILENCE_ZONE`, `BIKE`, `DISABLED`, `FAMILY`).

### 4.3. Macierz Dostępności $A$
Macierz $A$ o wymiarach $|M| \times K$:
$$A[m, i] \in \{0, 1\}$$
gdzie:
* $A[m, i] = 1$, jeśli miejsce $m$ jest **WOLNE** na całym segmencie elementarnym $e_i = (S_i, S_{i+1})$.
* $A[m, i] = 0$, jeśli miejsce $m$ jest **ZAJĘTE** (lub zablokowane technicznie) na segmencie $e_i$.

Dodatkowo wprowadzamy wirtualny stan $m_{\text{stand}}$ (**Stanie na korytarzu**), dla którego:
$$\forall i \in \{0, \dots, K-1\}, \quad A[m_{\text{stand}}, i] = 1$$
Stan ten pozwala algorytmowi nigdy się nie wywalić, nawet jeśli na jednym krótkim odcinku (np. $B \to C$) 100% miejsc w całym pociągu jest wykupionych.

---

## 5. Specyfikacja Algorytmu Optymalizacji (`optimizer.py` & `scorer.py`)

### 5.1. Sformułowanie Problemu (Ciągłe Bloki vs. Pojedyncze Segmenty)
Użytkownik nie chce przesiadać się co stację, jeśli nie musi. Co więcej, **wartość (użyteczność) siedzenia rośnie nieliniowo wraz z długością nieprzerwanego bloku**, a siedzenie w spokoju do samej stacji końcowej $S_K$ ma dodatkowy priorytet psychologiczny.

Zamiast klasycznego grafu przejść co pojedynczy segment (bez pamięci), stosujemy **Graf Bloków Ciągłych (Interval DAG)** lub **Programowanie Dynamiczne po przedziałach $(i, j)$**.

Definiujemy **Blok Podróży** (Leg) jako trójkę:
$$L = (m, i, j) \quad \text{gdzie } 0 \le i < j \le K$$
oznaczającą, że pasażer zajmuje miejsce $m \in M \cup \{m_{\text{stand}}\}$ nieprzerwanie od stacji $S_i$ do stacji $S_j$ (czyli przez segmenty $e_i, e_{i+1}, \dots, e_{j-1}$).

Blok $L = (m, i, j)$ jest **dopuszczalny (valid)** wtedy i tylko wtedy, gdy miejsce $m$ jest wolne na każdym segmencie wewnątrz tego przedziału:
$$\text{Valid}(m, i, j) \iff \prod_{k=i}^{j-1} A[m, k] = 1$$

Pełna **Strategia Podróży** $\mathcal{P}$ to ciąg sąsiadujących bloków pokrywających całą trasę od $0$ do $K$:
$$\mathcal{P} = \left[ (m_1, i_0, i_1), (m_2, i_1, i_2), \dots, (m_r, i_{r-1}, i_r) \right]$$
gdzie $i_0 = 0$, $i_r = K$, oraz $m_q \neq m_{q+1}$.

Dzięki takiej definicji **automatycznie rozwiązujemy problem nakładających się okien (Overlapping Windows)** z Twojego przykładu:
* Miejsce $M_A$ (Wagon 8, m. 13) jest wolne na $A \to C$ (indeksy $0 \to 2$).
* Miejsce $M_B$ (Wagon 6, m. 40) jest wolne na $C \to F$ (indeksy $2 \to 5$).
* Miejsce $M_C$ (Wagon 7, m. 24) jest wolne na $D \to G$ (indeksy $3 \to 6$).

Ponieważ $M_B$ jest wolne na $C \to F$ $(2 \to 5)$, to w naszym modelu generuje ono poprawne pod-bloki:
* $(M_B, C \to D)$, $(M_B, C \to E)$, $(M_B, C \to F)$.
Podobnie $M_C$ wolne na $D \to G$ $(3 \to 6)$ generuje pod-bloki:
* $(M_C, D \to G)$, $(M_C, E \to G)$, $(M_C, F \to G)$.

Algorytm DP automatycznie porówna ze sobą wszystkie punkty przecięcia:
1. **Wariant 1 (Przesiadka na stacji D):** $A \to C$ na $M_A$, $C \to D$ na $M_B$, oraz długi finał $D \to G$ na $M_C$.
2. **Wariant 2 (Przesiadka na stacji E):** $A \to C$ na $M_A$, $C \to E$ na $M_B$, oraz $E \to G$ na $M_C$.
3. **Wariant 3 (Przesiadka na stacji F):** $A \to C$ na $M_A$, długi środek $C \to F$ na $M_B$, oraz krótka końcówka $F \to G$ na $M_C$.

### 5.2. Funkcja Kosztu (Penalty Score) – Matematyka Inteligencji Systemu
Szukamy strategii $\mathcal{P}$, która **minimalizuje całkowity koszt niezadowolenia (Total Penalty Score)**:
$$\text{TotalScore}(\mathcal{P}) = \sum_{q=1}^{r} \text{BlockCost}(m_q, i_{q-1}, i_q) + \sum_{q=1}^{r-1} \text{TransferPenalty}(m_q, m_{q+1}, S_{i_q})$$

#### A. Koszt pojedynczego bloku $\text{BlockCost}(m, i, j)$
Niech $T(i, j) = \sum_{k=i}^{j-1} \Delta t_k$ oznacza czas trwania bloku w minutach, a $T_{\text{total}} = T(0, K)$ całkowity czas podróży.

1. **Jeśli $m = m_{\text{stand}}$ (Stanie na korytarzu):**
   $$\text{BlockCost}(m_{\text{stand}}, i, j) = W_{\text{stand}} \cdot T(i, j) \cdot \left(1 + \frac{T(i, j)}{30}\right)$$
   *(Domyślnie $W_{\text{stand}} = 50.0$ pkt za każdą minutę stania, z kwadratowym narzutem za długie stanie).*

2. **Jeśli $m \in M$ (Normalne miejsce siedzące):**
   Podstawowy koszt siedzenia jest bliski zeru, ale wprowadzamy **Bonus za Ciągłość (Continuity Reward)** oraz **Bonus Finałowy (End-of-Journey Peace Bonus)**:
   $$\text{BlockCost}(m, i, j) = \text{PrefPenalty}(m) \cdot T(i, j) - W_{\text{cont}} \cdot T_{\text{total}} \cdot \left(\frac{T(i, j)}{T_{\text{total}}}\right)^{\gamma} - \mathbb{I}(j == K) \cdot W_{\text{final}} \cdot T(i, j)$$
   * **Dlaczego potęga $\gamma = 1.6$?** Ponieważ dzięki funkcji wypukłej $\left(\frac{T(i,j)}{T_{\text{total}}}\right)^{1.6}$ jeden ciągły odcinek trwający 120 minut daje znacznie większy ujemny koszt (bonus) niż dwa odcinki po 60 minut ($0.5^{1.6} + 0.5^{1.6} = 0.66 < 1.0^{1.6} = 1.0$). To naturalnie wymusza wybór najdłuższych możliwych odcinków bez przesiadek.
   * **Dlaczego $\mathbb{I}(j == K) \cdot W_{\text{final}} \cdot T(i, j)$?** Jest to bezpośrednia realizacja Twojego wymagania: *"posiedzieć w spokoju do końca"*. Jeśli dany blok kończy się na stacji docelowej $S_K$ ($j == K$), każda minuta tego ostatniego bloku otrzymuje dodatkowy bonus $W_{\text{final}}$ (np. $1.5$ pkt/min). Dzięki temu w przykładzie z miejscem 40 ($C \to F$) i miejscem 24 ($D \to G$), algorytm będzie wolał przesiąść się już na stacji $D$ na miejsce 24, aby od $D$ aż do samego końca $G$ mieć już spokój i nie musieć wstawać pod koniec trasy na stacji $F$!
   * $\text{PrefPenalty}(m)$: Drobna korekta za typ miejsca (np. $0.0$ dla okna/korytarza, $+0.2$ pkt/min dla środkowego fotela w przedziale 6-osobowym).

#### B. Kara za przesiadkę i spacer $\text{TransferPenalty}(m_1, m_2, S_{\text{transfer}})$
To tutaj realizujemy inteligentną bliskość miejsc, aby pasażer nie biegał między wagonem 3 a 9.

Niech $\Delta C = |\text{carriage\_seq}(m_1) - \text{carriage\_seq}(m_2)|$ oznacza liczbę wagonów, które trzeba przejść.

$$\text{TransferPenalty}(m_1, m_2, S) = P_{\text{base}} + P_{\text{distance}}(m_1, m_2) + P_{\text{unit}}(m_1, m_2, S)$$

Gdzie:
1. **$P_{\text{base}} = 120.0$ pkt** – stała kara za sam fakt konieczności wstania z fotela, zabrania plecaka/walizki i zmiany miejsca (odpowiada ok. 2-3 minutom stania lub utracie ciągłości).
2. **$P_{\text{distance}}(m_1, m_2)$ – kara za dystans fizyczny (nieliniowa!):**
   * **Ten sam wagon ($\Delta C = 0$) i ten sam przedział ($\text{comp}(m_1) == \text{comp}(m_2)$):**
     $$P_{\text{distance}} = 15.0 \text{ pkt}$$ *(Przesiadka na fotel obok lub naprzeciwko – niemal bezbolesna).*
   * **Ten sam wagon ($\Delta C = 0$), inny przedział/rząd:**
     $$P_{\text{distance}} = 40.0 + 3.0 \cdot |\text{comp}(m_1) - \text{comp}(m_2)| \text{ pkt}$$ *(Krótki spacer w obrębie tego samego wagonu).*
   * **Inny wagon ($\Delta C \ge 1$):**
     $$P_{\text{distance}} = 85.0 \cdot \Delta C + 25.0 \cdot (\Delta C)^2 + 50.0 \cdot N_{\text{special\_crossings}}(m_1, m_2)$$
     *Zauważ człon kwadratowy $25.0 \cdot (\Delta C)^2$:*
     * Przejście do sąsiedniego wagonu ($\Delta C = 1$): $85 + 25 = 110$ pkt (bardzo akceptowalne, np. z wagonu 6 do 7).
     * Przejście o 2 wagony ($\Delta C = 2$): $170 + 100 = 270$ pkt.
     * Przejście o 6 wagonów (np. z wagonu 3 do 9, $\Delta C = 6$): $510 + 900 = 1410$ pkt! Tak wysoka kara sprawi, że algorytm wybierze daleki wagon tylko wtedy, gdy w pobliskich wagonach nie ma absolutnie żadnych miejsc przez bardzo długi czas.
     * $N_{\text{special\_crossings}}$: Liczba wagonów restauracyjnych (WARS) lub wagonów 1. klasy z wąskim korytarzem, przez które trzeba się przecisnąć z bagażem po drodze z $m_1$ do $m_2$.
3. **$P_{\text{unit}}(m_1, m_2, S)$ – kara za brak przejścia między jednostkami EZT:**
   * Jeśli $\text{unit\_group}(m_1) \neq \text{unit\_group}(m_2)$:
     * Jeśli postój na stacji $S$ wynosi $< 2$ minuty: $P_{\text{unit}} = +\infty$ (Przejście fizycznie niemożliwe – drzwi mogą się zamknąć przed nosem na peronie).
     * Jeśli postój na stacji $S \ge 2$ minuty: $P_{\text{unit}} = 600.0$ pkt (konieczność wyjścia na peron i przebiegnięcia do drugiej jednostki).

### 5.3. Algorytm Wyznaczania Top-K Strategii (Zmodyfikowany Algorytm Dijkstry / Yen's K-Shortest Paths w DAG)
Ponieważ użytkownik może chcieć zobaczyć kilka alternatyw, system generuje **3 różne profile strategii** (poprzez delikatną modyfikację wektora wag $\vec{W}$ lub zwrócenie Top-3 zróżnicowanych ścieżek):

1. **Strategia #1: "Złoty Środek (Rekomendowana)"** – Zbalansowane wagi domyślne: maksymalny spokój do końca trasy i minimalne przesiadki w sąsiednich wagonach.
2. **Strategia #2: "Leniwy Pasażer (Minimum Chodzenia)"** – Waga $P_{\text{distance}}$ pomnożona $\times 4$. Algorytm za wszelką cenę trzyma się jednego lub dwóch sąsiednich wagonów (nawet kosztem jednej dodatkowej przesiadki w tym samym przedziale).
3. **Strategia #3: "Najmniej Przesiadek (Maksymalne Odcinki)"** – Waga $P_{\text{base}}$ pomnożona $\times 3$, a $P_{\text{distance}}$ zmniejszona o połowę. Algorytm pozwala na przejście 4 wagonów dalej, jeśli dzięki temu zredukuje liczbę przesiadek na całej trasie np. z 3 do 1.

#### Złożoność obliczeniowa i optymalizacja DP:
Liczba stacji $K \le 25$, liczba miejsc $|M| \le 600$.
Liczba stanów $(m, i)$ wynosi maksymalnie $600 \times 25 = 15\,000$.
W klasycznym DP:
$$\text{DP}[i][m] = \min_{j > i \text{ s.t. } \text{Valid}(m, i, j)} \left( \text{BlockCost}(m, i, j) + \min_{m' \in M} \left( \text{TransferPenalty}(m, m', S_j) + \text{DP}[j][m'] \right) \right)$$
Ponieważ `TransferPenalty` zależy głównie od numeru wagonu i przedziału, a nie od indywidualnych cech każdego z 600 miejsc, dla ustalonej stacji przesiadki $S_j$ grupujemy miejsca docelowe $m'$ po `(carriage_number, compartment_id)` i bierzemy tylko najlepszego reprezentanta z każdego przedziału!
Redukuje to liczbę sprawdzanych przejść z $600 \times 600$ do $600 \times 60$, dzięki czemu całe wyliczenie dla pełnego pociągu trwa **poniżej 15 milisekund w czystym Pythonie**!

---

## 6. Specyfikacja Scrapera i Reverse-Engineering PKP (`scrapers/`)

To najbardziej wrażliwa część systemu. Poniżej znajduje się dokładna instrukcja techniczna, jak agent ma zaimplementować pobieranie danych bez odbijania się od zabezpieczeń.

### 6.1. Krok 1: Pobieranie listy stacji pośrednich (`schedule_scraper.py`)
Użytkownik podaje:
* Stację początkową $S_0$ (np. `"Warszawa Centralna"`),
* Stację końcową $S_K$ (np. `"Gdańsk Główny"`),
* Datę wyjazdu (np. `"2026-11-05"`),
* Opcjonalnie godzinę lub numer pociągu (np. `"EIC 1502"` lub `"IC 3520"`).

**Jak pobrać dokładną trasę i stacje pośrednie $S_0, S_1, \dots, S_K$?**
Zamiast trudnego w scrapowaniu `portalpasazera.pl` (który ma silny Cloudflare i dynamiczne tokeny ASP.NET), używamy dwustopniowej strategii:
1. **Źródło Podstawowe (API e-IC 2.0 / wyszukiwarka połączeń `ebilet.intercity.pl`):**
   * Podczas wyszukiwania połączenia na `ebilet.intercity.pl` w przeglądarce Playwright przechwytujemy ruch sieciowy (`page.on("response", ...)`).
   * Wyszukiwarka zwraca listę pociągów wraz z przyciskiem "Szczegóły połączenia / Trasa", który pobiera JSON z pełną listą stacji zatrzymania, godzinami przyjazdu/odjazdu oraz wewnętrznymi kodami stacji (`station_id` / kod HAFAS).
2. **Źródło Zapasowe (`rozklad-pkp.pl` / API HAFAS lub Koleo API):**
   * Jeśli potrzebujemy samej listy stacji i czasów postoju, zapytanie GET/POST do `rozklad-pkp.pl` lub publicznych endpointów informacyjnych zwraca kompletną tabelę przystanków pośrednich w ułamku sekundy.

### 6.2. Krok 2: Pobieranie map zajętości wagonów z `ebilet.intercity.pl` (`intercity_api.py`)
System sprzedaży PKP Intercity (e-IC 2.0) udostępnia graficzny wybór miejsca ze schematu wagonu.

**Mechanizm działania w Playwright:**
1. Tworzymy pojedynczą instancję przeglądarki Chromium (`headless=True`) z losowym `User-Agent` i viewportem `1920x1080`.
2. Dla każdego segmentu elementarnego $e_i = (S_i, S_{i+1})$ dla $i \in \{0, \dots, K-1\}$:
   * Sprawdzamy w `CacheService`, czy klucz `f"{train_number}_{date}_{S_i.id}_{S_{i+1}.id}"` istnieje. Jeśli tak $\to$ zwracamy z cache.
   * Jeśli nie $\to$ wykonujemy zapytanie o bilet dla 1 osoby dorosłej (klasa 2 lub 1 zgodnie z wyborem użytkownika) na odcinku $S_i \to S_{i+1}$ dla konkretnego pociągu.
   * Wywołujemy akcję pobrania schematu miejsc ("Wybierz miejsce ze schematu").
   * Przechwytujemy odpowiedź JSON/SVG z endpointu zwracającego układ wagonów i statusy foteli (w API e-IC 2.0 każde miejsce ma obiekt ze statusem: `FREE` / `AVAILABLE`, `OCCUPIED` / `RESERVED`, oraz metadanymi: numer wagonu, numer miejsca, właściwości typu okno/stolik).
3. **Optymalizacja równoległości i Rate Limiting:**
   * Trasa z 7 stacjami ($A \to G$) to tylko **6 segmentów elementarnych** ($A\text{-}B, B\text{-}C, C\text{-}D, D\text{-}E, E\text{-}F, F\text{-}G$).
   * Uruchamiamy pulę maksymalnie **2–3 równoległych zakładek (BrowserContexts)** z losowym opóźnieniem `jitter` (0.8s – 1.8s) między zapytaniami, aby nie wywołać blokady IP (HTTP 429).
   * Czas pobrania wszystkich 6 segmentów wyniesie około **6–10 sekund**, a postęp każdego segmentu jest natychmiast wysyłany przez WebSocket do paska postępu w UI.

### 6.3. Przypadki Brzegowe (Edge Cases) w Scraperze i Danych PKP
Agent **MUSI** zaimplementować obsługę poniższych anomalii:
1. **Brak możliwości zakupu na jednym z segmentów (100% obłożenia):**
   * Jeśli na odcinku $B \to C$ system PKP w ogóle nie pozwala otworzyć mapy wagonów (bo wszystkie miejsca w pociągu są zajęte), scraper nie może rzucić wyjątku `TimeoutError`. Musi rozpoznać komunikat "Brak wolnych miejsc siedzących" i wypełnić kolumnę $i$ w macierzy $A[\cdot, i] = 0$ dla wszystkich miejsc fizycznych (wtedy algorytm automatycznie użyje stanu `STANDING_CORRIDOR` na tym jednym odcinku lub wskaże wagon, w którym zwolni się miejsce zaraz na stacji $C$).
2. **Wagony wyłączone na części trasy (Grupy wagonów bezpośrednich):**
   * Zdarza się, że pociąg jedzie z Krakowa do Kołobrzegu, ale wagony 14 i 15 są odczepiane w Poznaniu.
   * Jeśli wagon $W$ pojawia się w odpowiedziach dla segmentów $0 \dots 3$, ale nie istnieje w odpowiedzi dla segmentu $4$, `matrix_builder.py` musi ustawić $A[m, k] = 0$ dla wszystkich miejsc z wagonu $W$ na segmentach $k \ge 4$.
3. **Blokada sprzedaży na bardzo krótkich odcinkach (np. Warszawa Zachodnia $\to$ Warszawa Centralna $\to$ Warszawa Wschodnia):**
   * W obrębie jednego węzła aglomeracyjnego (szczególnie w Warszawie lub Trójmieście) PKP IC czasami blokuje rezerwację miejsc lub traktuje przystanki jako jedną strefę.
   * **Rozwiązanie (Smart Station Collapsing):** Przed rozpoczęciem scrapowania filtrujemy listę stacji pośrednich. Jeśli dwie kolejne stacje należą do tej samej aglomeracji, a czas przejazdu między nimi wynosi $\le 7$ minut (np. *Warszawa Zachodnia – Centralna – Wschodnia* lub *Gdańsk Wrzeszcz – Oliwa – Sopot*), łączymy je lub pomijamy wewnętrzne pod-stacje aglomeracyjne (chyba że są to stacje końcowe użytkownika). Zmniejsza to liczbę zapytań do API i eliminuje błędy blokad aglomeracyjnych!

---

## 7. Specyfikacja API i Kontrakty JSON (`schemas.py`)

### 7.1. Endpoint: `POST /api/v1/analyze` (lub komunikacja przez `WS /ws/analyze`)
#### Request Payload:
```json
{
  "from_station": "Warszawa Centralna",
  "to_station": "Gdańsk Główny",
  "date": "2026-11-05",
  "train_number": "IC 3520",
  "ticket_class": 2,
  "use_mock_data": false,
  "preferences": {
    "walking_penalty_multiplier": 1.0,
    "transfer_penalty_multiplier": 1.0,
    "prefer_window": true,
    "avoid_silence_zone": false
  }
}
```

#### Response Payload:
```json
{
  "train_info": {
    "train_number": "IC 3520 KARŁOWICZ",
    "date": "2026-11-05",
    "total_duration_minutes": 195,
    "stations": [
      {"index": 0, "name": "Stacja A", "dep_time": "10:00", "arr_time": null, "dwell_minutes": 0},
      {"index": 1, "name": "Stacja B", "dep_time": "10:32", "arr_time": "10:30", "dwell_minutes": 2},
      {"index": 2, "name": "Stacja C", "dep_time": "11:05", "arr_time": "11:00", "dwell_minutes": 5},
      {"index": 3, "name": "Stacja D", "dep_time": "11:42", "arr_time": "11:40", "dwell_minutes": 2},
      {"index": 4, "name": "Stacja E", "dep_time": "12:15", "arr_time": "12:14", "dwell_minutes": 1},
      {"index": 5, "name": "Stacja F", "dep_time": "12:45", "arr_time": "12:44", "dwell_minutes": 1},
      {"index": 6, "name": "Stacja G", "dep_time": null, "arr_time": "13:15", "dwell_minutes": 0}
    ]
  },
  "strategies": [
    {
      "rank": 1,
      "strategy_name": "Maksymalny spokój do końca + bliskie wagony",
      "total_score": -245.5,
      "transfers_count": 2,
      "seated_percentage": 100.0,
      "total_walking_carriages": 3,
      "summary_badge": "Od stacji D (11:42) jedno miejsce aż do samego końca!",
      "legs": [
        {
          "leg_index": 1,
          "type": "SEATED",
          "carriage_number": 8,
          "seat_number": "13",
          "seat_properties": ["WINDOW", "COMPARTMENT"],
          "from_station": "Stacja A",
          "to_station": "Stacja C",
          "dep_time": "10:00",
          "arr_time": "11:00",
          "duration_minutes": 60,
          "segment_indices": [0, 1],
          "transfer_instruction_after": "Na stacji C (postój 5 min) przejdź 2 wagony do przodu -> Wagon 6, miejsce 40."
        },
        {
          "leg_index": 2,
          "type": "SEATED",
          "carriage_number": 6,
          "seat_number": "40",
          "seat_properties": ["AISLE", "OPEN_SALOON"],
          "from_station": "Stacja C",
          "to_station": "Stacja D",
          "dep_time": "11:05",
          "arr_time": "11:40",
          "duration_minutes": 35,
          "segment_indices": [2],
          "note": "To miejsce jest wolne aż do stacji F, ale opłaca się przesiąść już na stacji D, aby zająć miejsce 24 w wagonie 7 wolne do samego końca trasy!",
          "transfer_instruction_after": "Na stacji D przejdź 1 wagon do tyłu -> Wagon 7, miejsce 24."
        },
        {
          "leg_index": 3,
          "type": "SEATED",
          "carriage_number": 7,
          "seat_number": "24",
          "seat_properties": ["WINDOW", "OPEN_SALOON"],
          "from_station": "Stacja D",
          "to_station": "Stacja G",
          "dep_time": "11:42",
          "arr_time": "13:15",
          "duration_minutes": 93,
          "segment_indices": [3, 4, 5],
          "transfer_instruction_after": null
        }
      ]
    }
  ],
  "matrix_preview": {
    "carriages": [6, 7, 8, 9, 10],
    "seats": [
      {
        "id": "W8_M13",
        "carriage": 8,
        "seat": "13",
        "availability": [1, 1, 0, 0, 0, 0]
      },
      {
        "id": "W6_M40",
        "carriage": 6,
        "seat": "40",
        "availability": [0, 0, 1, 1, 1, 0]
      },
      {
        "id": "W7_M24",
        "carriage": 7,
        "seat": "24",
        "availability": [0, 0, 0, 1, 1, 1]
      },
      {
        "id": "W10_M12",
        "carriage": 10,
        "seat": "12",
        "availability": [1, 0, 1, 1, 1, 1]
      }
    ]
  }
}
```

---

## 8. Plan Zadań Krok po Kroku dla Agenta AI (`Task Checklist`)

 Wykonuj poniższe fazy sekwencyjnie. Po zakończeniu każdej fazy uruchom testy weryfikacyjne przed przejściem do kolejnej.

### FAZA 1: Inicjalizacja Projektu i Modele Domenowe
- [x] **Task 1.1:** Utwórz strukturę katalogów `backend/` oraz `frontend/` zgodnie z Sekcją 3.
- [x] **Task 1.2:** Skonfiguruj środowisko Python (`requirements.txt`: `fastapi`, `uvicorn[standard]`, `pydantic`, `numpy`, `playwright`, `playwright-stealth`, `httpx`, `diskcache`, `pytest`, `pytest-asyncio`).
- [x] **Task 1.3:** Zaimplementuj w `backend/app/models/domain.py` klasy danych: `Station`, `Segment`, `Seat`, `Carriage`, `TrainTopology`, `TravelLeg`, `OptimizationStrategy`.
- [x] **Task 1.4:** Zaimplementuj w `backend/app/models/schemas.py` pełne kontrakty Pydantic v2 dla zapytań REST, komunikatów WebSocket oraz odpowiedzi zawierającej Top-3 strategie i macierz dostępności.

### FAZA 2: Silnik Topologii i Algorytm Optymalizacji (Serce Systemu)
- [x] **Task 2.1 (`topology.py`):** Zaimplementuj kalkulator odległości fizycznej między miejscami `calculate_physical_distance(seat_a, seat_b, topology)`. Uwzględnij:
  - ten sam przedział,
  - ten sam wagon (odległość rzędów/przedziałów),
  - różnicę indeksów kolejności wagonów w składzie (`carriage_sequence_index`),
  - karę za przejście przez wagon restauracyjny WARS,
  - blokadę przejścia w trakcie jazdy między niepołączonymi jednostkami EZT (`unit_group_id`).
- [x] **Task 2.2 (`scorer.py`):** Zaimplementuj funkcję kosztu `BlockCost(m, i, j)` oraz `TransferPenalty(m1, m2, station)`. Zaimplementuj wykładniczy bonus za długość nieprzerwanego bloku ($\gamma = 1.6$) oraz bonus finałowy `W_final` dla bloków kończących się na stacji docelowej $S_K$.
- [x] **Task 2.3 (`optimizer.py`):** Zaimplementuj algorytm programowania dynamicznego po przedziałach (Interval DAG DP):
  - Wygeneruj wszystkie maksymalne i pośrednie bloki ciągłe $\text{Valid}(m, i, j)$ z macierzy $A$.
  - Dodaj wirtualne miejsce `STANDING_CORRIDOR` dostępne na każdym segmencie z wysoką karą minutową.
  - Wyznacz optymalną ścieżkę dla 3 profili wagowych: *Zbalansowany (Domyślny)*, *Minimalne Chodzenie (Bliskie Wagony)*, *Minimalna Liczba Przesiadek*.
  - Dodaj generowanie czytelnych instrukcji w języku polskim dla każdego odcinka (w tym wykrywanie nakładających się okien, tzw. *Overlap Explanation*, np. *"Miejsce 40 w wagonie 6 jest wolne do stacji F, ale przesiądź się już na stacji D do wagonu 7 na miejsce 24, aby jechać bez przesiadek do końca"*).

### FAZA 3: Deterministyczny Mock Provider i Testy Jednostkowe Algorytmu
- [x] **Task 3.1 (`mock_provider.py`):** Zbuduj generator danych testowych posiadający predefiniowane scenariusze:
  - **Scenariusz 1 ("Klasyczna dziura B-C"):** Trasa $A \to G$, Wagon 10 miejsce 12 jest wolne na $A \to B$, zajęte na $B \to C$, wolne na $C \to G$. W tym samym wagonie 10 (lub sąsiednim 9) jest miejsce wolne na $B \to C$.
  - **Scenariusz 2 ("Zakładka wielowagonowa A-G"):** Dokładne odwzorowanie przypadku: Wagon 8 m. 13 wolne $A \to C$, Wagon 6 m. 40 wolne $C \to F$, Wagon 7 m. 24 wolne $D \to G$, a dla zmyłki w odległym Wagonie 2 m. 55 wolne $C \to G$ (sprawdzenie, czy algorytm wybierze bliskie wagony 8 $\to$ 6 $\to$ 7 zamiast biegać przez cały pociąg do wagonu 2, oraz czy zmieni fotel na stacji D zamiast F).
  - **Scenariusz 3 ("Hardcore Peak Hour"):** Na odcinku $C \to D$ 100% miejsc w pociągu jest zajętych – test poprawnego wygenerowania odcinka `STANDING_CORRIDOR` we właściwym wagonie docelowym.
- [x] **Task 3.2 (`test_optimizer.py`):** Napisz testy `pytest` weryfikujące wszystkie 3 powyższe scenariusze. Upewnij się, że czas wykonania algorytmu dla pociągu z 10 wagonami (600 miejsc) i 15 stacjami wynosi $< 50\text{ ms}$.

### FAZA 4: Scraper Rozkładu i Map Wagonów PKP Intercity
- [x] **Task 4.1 (`cache_service.py`):** Zaimplementuj lokalny cache (`diskcache`) zapisujący pobrane rozkłady i mapy segmentów z czasem życia (TTL) 10 minut.
- [x] **Task 4.2 (`schedule_scraper.py`):** Zaimplementuj pobieranie listy stacji pośrednich i czasów postoju dla zadanego pociągu i relacji $S_0 \to S_K$. Dodaj funkcję `collapse_agglomeration_stations(stations)` łączącą przystanki wewnątrz tego samego miasta oddalone o $\le 7$ minut jazdy (np. Warszawa Zachodnia/Centralna/Wschodnia).
- [x] **Task 4.3 (`intercity_api.py`):** Zaimplementuj silnik Playwright pobierający dostępność miejsc z `ebilet.intercity.pl`:
  - Obsługa inicjalizacji sesji i automatycznego zamykania banerów Cookies (OneTrust).
  - Przechwytywanie odpowiedzi JSON/XHR z układem wagonów i statusem miejsc dla każdego segmentu $(S_i, S_{i+1})$.
  - Parser przekształcający surowe dane z wagonów na ujednoliconą strukturę `TrainTopology` oraz macierz dostępności $A[m, i]$.
  - Mechanizm `Fallback`: jeśli scraper napotka CAPTCHA lub błąd sieciowy, umożliwia płynne przełączenie lub wyświetlenie dokładnej diagnostyki w UI.

### FAZA 5: Backend API i Strumieniowanie WebSocket
- [x] **Task 5.1 (`main.py`):** Zbuduj endpointy REST:
  - `GET /api/v1/stations?q=...` – autouzupełnianie nazw stacji kolejowych.
  - `POST /api/v1/trains` – lista pociągów bezpośrednich między $S_0$ a $S_K$ w danym dniu.
  - `POST /api/v1/analyze` – synchroniczne uruchomienie analizy (dla trybu Mock i zcache'owanych tras).
- [x] **Task 5.2 (`main.py`):** Zbuduj endpoint WebSocket `WS /ws/analyze`:
  - Wysyła zdarzenia postępu: `{"step": "SCHEDULE_FETCHED", "stations": [...]}`, `{"step": "SEGMENT_SCRAPED", "current": 3, "total": 6, "segment": "C -> D"}`, `{"step": "OPTIMIZATION_COMPLETE", "result": {...}}`.

### FAZA 6: Frontend (React + TailwindCSS) – Interfejs Pasażera
- [x] **Task 6.1:** Zainicjuj projekt Vite + React + TypeScript + TailwindCSS w katalogu `frontend/`.
- [x] **Task 6.2 (`SearchForm.tsx`):** Stwórz nowoczesny panel wyszukiwania:
  - Wybór stacji początkowej i końcowej, daty, numeru pociągu.
  - Przełącznik **"Tryb Symulacji / Demo (Scenariusze A-G)"** vs **"Tryb Live (Scraper PKP IC)"** – aby można było natychmiast testować algorytm i interfejs bez czekania na scraper.
  - Rozwijany panel **"Zaawansowane preferencje algorytmu"** z suwakami:
    - *Jak bardzo nie lubisz chodzić po pociągu?* (Niska / Średnia / Wysoka kara za zmianę wagonu),
    - *Priorytet spokoju do końca trasy* (Standardowy / Maksymalny).
- [x] **Task 6.3 (`StrategyCards.tsx`):** Zaprojektuj czytelne karty prezentujące wyliczone Top-3 strategie:
  - Wizualna oś czasu (Timeline) od stacji $A$ do $G$ z kolorowymi blokami reprezentującymi poszczególne wagony i miejsca.
  - Wyraźne oznaczenia przesiadek: ile wagonów trzeba przejść i w którą stronę, ile minut stoi pociąg na stacji przesiadkowej.
  - Inteligentne dymki wyjaśniające (np. dlaczego algorytm sugeruje przesiadkę na stacji $D$ mimo że obecne miejsce jest wolne do $F$).
- [x] **Task 6.4 (`SeatGanttMatrix.tsx`):** Zbuduj interaktywną macierz Gantta (Widok Ekspercki):
  - Kolumny: kolejne segmenty trasy $(A\text{-}B, B\text{-}C, \dots, F\text{-}G)$ z godzinami.
  - Wiersze: pogrupowane po wagonach miejsca w pociągu (z filtrowaniem: *"Pokaż tylko miejsca z rekomendowanych strategii"* lub *"Pokaż wszystkie miejsca wolne przez min. 50% trasy"*).
  - Komórki: Zielone (Wolne), Czerwone/Szare (Zajęte), **Niebieskie ze złotą obwódką (Wybrana ścieżka użytkownika w aktualnej strategii)**.
  - Możliwość ręcznego klikania w komórki na wykresie Gantta, aby zbudować lub zmodyfikować własną ścieżkę i na żywo zobaczyć przeliczony czas siedzenia oraz dystans spaceru między wagonami!