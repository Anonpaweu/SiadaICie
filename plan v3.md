# PLAN_NAPRAWCZY_V3_REAL_EBILET_DOM.md — Specyfikacja Prawdziwego DOM e-IC 2.0 (React SPA) i Eliminacja Ślepego Klikania

## 1. Audyt Krytyczny: Dlaczego Scraper V2 nie wpisywał stacji i wchodził w „Bilety okresowe”?

Analiza plików `logs/debug_snapshots/err_Koluszki_Piotrków Trybunalski_1794771514.html` oraz `error_85_percent_Lubliniec_Opole Główne.html` wykazała 5 fundamentalnych błędów w `backend/app/scraper/ebilet_seat_scraper.py`:

1. **Niezamknięty Cookiebot (`CybotCookiebot`):**
   - Scraper szukał `button:has-text('Zaakceptuj wszystkie')`, podczas gdy na `ebilet.intercity.pl` przycisk nazywa się **`Zezwól na wszystkie`** i ma ID: `#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll` (lub `#CybotCookiebotDialogBodyButtonDecline` dla „Zaakceptuj niezbędne”).
2. **Fałszywe selektory pól stacji (`from_station` / `to_station`):**
   - Scraper szukał nieistniejących selektorów `input[placeholder*='początkowa']` i `input#stacja-od`.
   - W rzeczywistym DOM aplikacji Reactowej `e-IC 2.0` (`ebilet.intercity.pl`) pola te mają dokładne identyfikatory:
     - Stacja początkowa: **`input#stationFrom`** wewnątrz `div[data-testid="SearchPanel-from"]` (posiada atrybuty `role="combobox"` oraz `aria-controls="stations"`).
     - Stacja końcowa: **`input#stationTo`** wewnątrz `div[data-testid="SearchPanel-to"]` (posiada atrybuty `role="combobox"` oraz `aria-controls="stations"`).
3. **Brak wyboru elementu z listy podpowiedzi Reacta (`#stations`):**
   - W aplikacji `e-IC 2.0` samo wpisanie tekstu przez `.fill()` nie zatwierdza obiektu stacji w stanie Reacta. Dopóki stacja nie zostanie kliknięta z rozwijanej listy podpowiedzi (`#stations`, elementy `[role="option"]` / `li`), przycisk wyszukiwania pozostaje zablokowany (`disabled=""`, `aria-disabled="true"`).
4. **Fałszywy selektor przycisku wyszukiwania:**
   - Scraper szukał przycisku „Szukaj” / „Wyszukaj”.
   - W `e-IC 2.0` główny przycisk zatwierdzający formularz wyszukiwania to:
     ```html
     <button data-testid="ConfirmButton" type="submit" aria-label="Dalej"><span>Dalej</span></button>
     ```
5. **Karygodny fallback `chosen_row = page.locator("body")`, który klikał baner „Bilety okresowe”:**
   - Ponieważ stacje nie zostały wpisane, a przycisk „Dalej” był `disabled`, strona nigdy nie opuściła ekranu głównego.
   - W kroku 6 scraper nie znalazł wierszy `.connection-row` i wykonał:
     ```python
     chosen_row = page.locator("body")
     ```
   - Następnie wywołał `chosen_row.locator("button:has-text('Kup bilet')").first.click()`, co kliknęło baner reklamowy na dole strony głównej:
     `<button class="SeasonTicketBar_seasonTicketBar__button__x5mFk" aria-label="Przejdź do Bilety okresowe Kup bilet">`!
   - Przez to bot wszedł w formularz zakupu biletów miesięcznych/okresowych i tam na 85% czekał na schemat wagonu!

---

## 2. Lista Zadań Naprawczych Krok po Kroku (Tasks V3-01 do V3-35)

Wykonaj poniższe zadania w pliku `backend/app/scraper/ebilet_seat_scraper.py` (oraz powiązanych modułach). Po wykonaniu każdego punktu oznacz go `[x]`.

### ETAP 1: Prawdziwa obsługa Cookiebota i zakaz ślepego fallbacku na `body` (Tasks V3-01 – V3-05)

- [x] **Task V3-01:** W metodzie `close_cookie_banners(self, page: Page)` dodaj na sam początek listy selektorów prawdziwe ID Cookiebota z `ebilet.intercity.pl`:
  ```python
  cookie_selectors = [
      "#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll",
      "#CybotCookiebotDialogBodyButtonDecline",
      "button:has-text('Zezwól na wszystkie')",
      "button:has-text('Zaakceptuj niezbędne')",
  ]
  ```
- [x] **Task V3-02:** Po kliknięciu przycisku Cookiebota dodaj `await page.locator("#CybotCookiebotDialog").wait_for(state="hidden", timeout=3000)` (w bloku try/except), aby mieć 100% pewności, że nakładka zniknęła przed klikaniem w pola stacji.
- [x] **Task V3-03:** CAŁKOWICIE USUŃ z `search_segment_on_intercity` linijkę `else: chosen_row = page.locator("body")`! Jeśli lista połączeń się nie załadowała lub nie znaleziono pociągu, scraper NIE MOŻE klikać losowych przycisków „Kup” na stronie głównej.
- [x] **Task V3-04:** Dodaj w `search_segment_on_intercity` przechwyt wszystkich odpowiedzi JSON z sieci (`on_response`) – nie tylko zawierających słowa `seat` czy `wagon`, ale również odpowiedzi z wyszukiwarki połączeń i struktury pociągu (wszelkie wywołania XHR/fetch zwracające `application/json` z domeny `ebilet.intercity.pl`), logując ich adresy URL i strukturę kluczy do pliku `logs/debug_snapshots/network_api_log.jsonl`.
- [x] **Task V3-05:** Upewnij się, że w `LiveTrainScanner.scan_full_train` przeglądarka Playwright może utrzymywać jedną sesję (lub otwierać widoczne okno na pierwszym ekranie) bez zamykania i otwierania procesu Chromium od zera przy każdym mikro-kroku, jeśli użytkownik wybrał `show_browser=True` (dzięki czemu po pierwszym odcinku nie trzeba nawet ponownie akceptować Cookiebota!).

### ETAP 2: Prawdziwe Wprowadzanie Stacji, Daty i Godziny w e-IC 2.0 (Tasks V3-06 – V3-16)

- [x] **Task V3-06:** Napisz dedykowaną metodę `async def fill_station_combobox(self, page: Page, input_selector: str, station_name: str) -> None` obsługującą Reactowy komponent AutocompleateStation (`#stationFrom` oraz `#stationTo`).
- [x] **Task V3-07:** W `fill_station_combobox` zaimplementuj dokładną sekwencję wymaganą przez Reactowy combobox w e-IC 2.0:
  - Zlokalizuj pole `inp = page.locator(input_selector).first` (gdzie input_selector to `#stationFrom` lub `#stationTo`).
  - Poczekaj aż będzie widoczne (`await inp.wait_for(state="visible", timeout=5000)`).
  - Kliknij w pole (`await inp.click()`).
  - Wyczyść pole (`await inp.fill("")`).
  - Wpisz nazwę stacji znak po znaku za pomocą `await inp.press_sequentially(station_name, delay=60)` (w e-IC 2.0 zdarzenia onKeyDown/onInput wyzwalają pobranie listy podpowiedzi).
- [x] **Task V3-08:** Po wpisaniu tekstu w `fill_station_combobox` poczekaj na pojawienie się listy podpowiedzi (`aria-controls="stations"`):
  - Sprawdź selektory opcji:
    ```python
    option_selectors = [
        "#stations [role='option']",
        "#stations li",
        "[data-testid='SearchPanel-from'] li",
        "[data-testid='SearchPanel-to'] li",
        "ul[id='stations'] li",
        "[role='listbox'] [role='option']",
        "li[class*='autocomplete']",
        "div[class*='AutocompleateStation'] li",
    ]
    ```
  - Poczekaj maks. 3 sekundy aż pojawi się lista podpowiedzi.
- [x] **Task V3-09:** Wybierz właściwą stację z listy podpowiedzi:
  - Przeiteruj po widocznych opcjach. Jeśli tekst opcji jest dokładnie równy `station_name` (ignorując wielkość liter, np. "Koluszki" lub "Częstochowa" - aby dla "Częstochowa" nie kliknąć "Częstochowa Stradom", chyba że szukamy "Częstochowa Stradom"!), kliknij tę opcję!
  - Jeśli żadna opcja nie pasuje idealnie 1:1, kliknij pierwszą opcję zawierającą `station_name`, a w ostateczności naciśnij `await page.keyboard.press("ArrowDown")` oraz `await page.keyboard.press("Enter")`.
- [x] **Task V3-10:** Zweryfikuj po wybraniu z listy, że atrybut value pola `#stationFrom` / `#stationTo` nie jest pusty (`val = await inp.input_value()`). Jeśli jest pusty, rzuć czytelny wyjątek diagnostyczny i zapisz zrzut ekranu!
- [x] **Task V3-11:** Zaimplementuj poprawne ustawianie daty wyjazdu w komponencie `[data-testid="SearchPanel-calendar"]`:
  - Sprawdź aktualny tekst wewnątrz `[data-testid="SearchPanel-calendar"]`.
  - Jeśli data podróży (`date_str`, np. 2026-11-15) różni się od domyślnej daty dzisiejszej wyświetlanej w kalendarzu, kliknij `[data-testid="SearchPanel-calendar"]` i wybierz odpowiedni dzień miesiąca (lub wpisz datę, jeśli pojawi się input), a następnie zamknij pop-up kalendarza.
- [x] **Task V3-12:** Zaimplementuj poprawne ustawianie godziny odjazdu (`departure_time`) w polu:
  ```python
  time_input = page.locator("input[type='time'], input[class*='Time_inputTime']").first
  ```
  Kliknij w `time_input`, użyj `await time_input.fill(clean_dep_time)` (format HH:MM, np. "15:12"), a jeśli React nadpisuje wartość, użyj `await time_input.press_sequentially(clean_dep_time.replace(":", ""))` i wywołaj blur.
- [x] **Task V3-13:** Zaimplementuj kliknięcie prawdziwego przycisku wyszukiwania w e-IC 2.0:
  ```python
  confirm_btn = page.locator("button[data-testid='ConfirmButton'], button[type='submit'][aria-label='Dalej']").first
  ```
- [x] **Task V3-14:** Przed kliknięciem `confirm_btn` sprawdź, czy nie ma on już atrybutu disabled (`await expect(confirm_btn).to_be_enabled(timeout=4000)` lub pętla sprawdzająca `await confirm_btn.is_enabled()`).
- [x] **Task V3-15:** Jeśli `confirm_btn` nadal jest disabled, zapisz zrzut ekranu `err_search_form_disabled.png` wraz z wartościami stationFrom i stationTo i przerwij z dokładnym komunikatem: "Przycisk 'Dalej' (ConfirmButton) pozostał nieaktywny – stacja początkowa lub końcowa nie została zatwierdzona z listy podpowiedzi."
- [x] **Task V3-16:** Kliknij `confirm_btn` i poczekaj na przejście do ekranu „Lista połączeń” (w e-IC 2.0 przycisk na górnym pasku postępu `button:has-text('Lista połączeń')` staje się aktywny lub pojawiają się karty połączeń).

### ETAP 3: Obsługa Ekranu „Lista połączeń” i Wybór Klasy 2 w e-IC 2.0 (Tasks V3-17 – V3-25)

W nowym systemie e-IC 2.0 wszystkie klasy CSS są generowane przez CSS Modules (np. `NazwaKomponentu_klasa__hash`). Dlatego NIE WOLNO polegać wyłącznie na sztywnych nazwach klas typu `.connection-row` ze starego systemu!

- [x] **Task V3-17:** Po kliknięciu ConfirmButton („Dalej”) poczekaj na załadowanie wyników połączeń (zarówno w sieci JSON, jak i w DOM).
- [x] **Task V3-18:** Zapisz automatyczny zrzut pomocniczy `logs/debug_snapshots/step2_connections_list.html` przy pierwszym wyszukiwaniu, aby zawsze mieć pełny podgląd struktury DOM listy połączeń e-IC 2.0.
- [x] **Task V3-19:** Wyszukaj na ekranie „Lista połączeń” nasz pociąg (`train_number`, np. `IC 16104` lub sam numer `16104`, albo po godzinie odjazdu `departure_time`):
  - Użyj selektorów dopasowanych do CSS Modules w React (`[class*='Connection']`, `[class*='Train']`, `[data-testid*='connection']`, `[data-testid*='train']`, `li`, `article`, lub kontenerów zawierających tekst numeru pociągu / godziny odjazdu).
- [x] **Task V3-20:** Jeśli lista połączeń wymaga rozwinięcia szczegółów danego połączenia (np. kliknięcia w wiersz/kartę pociągu lub przycisk „Wybierz” / „Szczegóły” przy tym pociągu), kliknij go.
- [x] **Task V3-21:** Wymuszenie Klasy 2 (`TARGET_CLASS = 2`):
  - Wewnątrz karty wybranego pociągu (lub na ekranie wyboru klasy) znajdź przełącznik / kafelek Klasa 2 (`2 kl.`, `Klasa 2`, `[data-testid*='class-2']`, `[aria-label*='2']`, `button:has-text('2')`, `button[aria-label='Wybierz 2 klasę']`).
  - Upewnij się, że nie ma przy nim komunikatu „Brak miejsc” / „Niedostępne”.
  - Kliknij przycisk przejścia do parametryzacji biletu (`Wybierz`, `Dalej`, `[data-testid='ConfirmButton']`, `Kup bilet` – ale TYLKO wewnątrz obszaru wyników wyszukiwania, NIGDY na stopce `.SeasonTicketBar`!).
- [x] **Task V3-22:** Na ekranie „Twoja podróż” (parametryzacja biletu: 1 pasażer, bilet normalny):
  - Zapisz zrzut pomocniczy `logs/debug_snapshots/step3_your_journey.html`.
  - Zamknij ewentualne modale informacyjne (`dismiss_intermediate_modals`).
- [x] **Task V3-23:** Na ekranie „Twoja podróż” znajdź opcję wyboru miejsca ze schematu:
  - Szukaj przycisków / linków / przełączników zawierających tekst lub aria-label / data-testid:
    - Wybierz miejsce ze schematu
    - Wskazanie miejsca
    - Schemat wagonu
    - Wybór miejsca
    - `[data-testid*='seat']`, `[data-testid*='schema']`, `[data-testid*='wagon']`, `[class*='Seat']`, `[class*='Schema']`, `button[class*='PreferedPlace_PreferedPlace_grmButton']`.
- [x] **Task V3-24:** Kliknij przycisk otwierający schemat wagonu i poczekaj na załadowanie danych schematu (odpowiedź JSON z sieci oraz wyrenderowanie modala ze schematem SVG/DOM).
- [x] **Task V3-25:** Zapisz zrzut `logs/debug_snapshots/step4_seatmap_modal.html` oraz `step4_seatmap_modal.png` w momencie otwarcia schematu wagonu.

### ETAP 4: Ekstrakcja Wszystkich Wagonów Klasy 2 i Zielonych Miejsc (Tasks V3-26 – V3-35)

- [x] **Task V3-26:** Priorytet 1 – Ekstrakcja bezpośrednio z przechwyconego JSON-a API e-IC 2.0:
  - Przeanalizuj wszystkie przechwycone w `captured_network_json` obiekty JSON.
  - Rozszerz `_extract_seats_from_json_list` tak, aby rekurencyjnie przeszukiwał strukturę JSON niezależnie od nazw kluczy (zarówno carriages/wagony/cars/vehicles, jak i seats/miejsca/places/elements).
  - Jeśli JSON zawiera kompletną listę wagonów 2. klasy wraz ze statusem miejsc (wolne/zajęte), wyciągnij je natychmiast bez konieczności ręcznego przeklikiwania każdego wagonu.
- [x] **Task V3-27:** Priorytet 2 – Przeklikiwanie zakładek wagonów w DOM/SVG:
  - Rozszerz selektory zakładek wagonów o wzorce CSS Modules z e-IC 2.0:
    ```python
    tab_selectors = [
        "[data-testid*='carriage']",
        "[data-testid*='wagon']",
        "[class*='Carriage']",
        "[class*='Wagon']",
        "button[aria-label*='wagon' i]",
        "button[aria-label*='Wagon']",
        ".carriage-tab",
        ".wagon-tab",
        "button:has-text('Wagon')",
    ]
    ```
- [x] **Task V3-28:** Dla każdego wagonu 2. klasy (pomijając wagony 1. klasy, WARS/WR, sypialne WL i kuszetki Bc) kliknij jego zakładkę, poczekaj 300-500ms na odświeżenie widoku SVG/DOM i odczytaj wszystkie wolne (zielone / aktywne) miejsca.
- [x] **Task V3-29:** Rozszerz selektory miejsc w `_find_seats_in_iframe` o wzorce e-IC 2.0 (SVG `<g>`, `<path>`, `<rect>`, `<text>` z `text_content()` oraz przyciski z aria-label np. "Miejsce 82, wolne" lub klasami `[class*='Seat'][class*='free' i]`, `[class*='Seat'][class*='available' i]`, `button:not([disabled])`).
- [x] **Task V3-30:** Upewnij się, że wyekstrahowane numery miejsc są poprawnie przypisane do rzeczywistych numerów wagonów (np. W3, W4, W5, W6, W7, W8) i posortowane rosnąco.
- [x] **Task V3-31:** Napisz skrypt diagnostyczny `scratch/test_ebilet_v3_flow.py`, który uruchamia Playwrighta na jednym odcinku (np. Koluszki -> Piotrków Trybunalski lub Lubliniec -> Opole Główne), wykonuje kroki 1–4 i wypisuje w konsoli:
  - Wartość wpisaną w #stationFrom i #stationTo.
  - Status przycisku [data-testid="ConfirmButton"] (enabled: True).
  - Listę przechwyconych adresów URL JSON z backendu ebilet.intercity.pl.
  - Wykryte wagony i listę wolnych miejsc.
- [x] **Task V3-32:** Uruchom `python scratch/test_ebilet_v3_flow.py` i zweryfikuj, że formularz poprawnie wypełnia `#stationFrom` oraz `#stationTo`, zamyka `#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll` i klika aktywny przycisk `#ConfirmButton` („Dalej”).
- [x] **Task V3-33:** Jeśli na ekranie listy połączeń lub schematu wagonu w e-IC 2.0 pojawią się nowe klasy CSS Modules, odczytaj je ze zapisanych plików `step2_connections_list.html` / `step3_your_journey.html` / `step4_seatmap_modal.html` oraz `network_api_log.jsonl` i dopisz dokładne selektory do `ebilet_seat_scraper.py`.
- [x] **Task V3-34:** Uruchom wszystkie testy jednostkowe (`pytest`) i upewnij się, że cała istniejąca logika optymalizatora DAG przechodzi w 100%.
- [x] **Task V3-35:** Przetestuj pełny przepływ z poziomu UI (`http://localhost:8000`) z zaznaczonym checkboxem *Pokaż okno przeglądarki podczas skanowania*.
