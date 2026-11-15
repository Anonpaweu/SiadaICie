# Plan Naprawczy V2: Głęboka Analiza Procesu PKP Intercity i Naprawa Krytycznych Błędów (IccBagno)

Ten dokument jest bezpośrednią diagnozą problemów zgłoszonych po pierwszej fali poprawek oraz kompleksową instrukcją dla środowiska **Antigravity**. Zawiera pełną anatomię procesu rezerwacji na stronach PKP Intercity (`intercity.pl` / `ebilet.intercity.pl`) oraz twarde wytyczne techniczne rozwiązujące problem niedziałającego trybu widocznego (`headless: false`), zawieszania się procesu na **85%** oraz wymuszające bezbłędny wybór **Klasy 2**.

---

## CZĘŚĆ 1: Diagnoza Krytycznych Błędów ("Dlaczego to nadal nie działa?")

### 1. Dlaczego zaznaczenie "Pokaż okno przeglądarki" nic nie wyświetla?
W aplikacjach opartych na architekturze klient-serwer (lub Electron/Python GUI + backend), checkbox w interfejsie użytkownika zazwyczaj nie jest prawidłowo spięty z faktyczną instancją `BrowserType.launch()`.
* **Błąd 1 (Brak przekazania flagi w Payloadzie):** Frontend ma checkbox, ale przy wysyłaniu żądania POST/WebSocket rozpoczynającego szukanie, parametr `showBrowser: true` (lub `headless: false`) nie jest dodawany do JSON-a, albo backend go ignoruje, mając zahardkodowane `headless=True`.
* **Błąd 2 (Użycie kontekstu zbuforowanego / Singletona):** Jeśli backend inicjalizuje przeglądarkę przy starcie całej aplikacji (w tle, jako `headless: true`), to późniejsze kliknięcie checkboxa w UI i rozpoczęcie scrapowania otwiera jedynie nową kartę (`new_page()`) w już istniejącej, **niewidocznej** instancji przeglądarki! Przeglądarka musi być inicjalizowana (lub restartowana) **per zadanie scrapowania** z odczytanym na świeżo parametrem `headless = not user_wants_to_see_browser`.
* **Błąd 3 (Flagi środowiskowe w Windows/Linux):** Czasami okno otwiera się poza ekranem (np. współrzędne `-32000, -32000`) lub proces potomny nie ma dostępu do ekranu głównego wątku UI.

### 2. Dlaczego pasek postępu staje dokładnie na 85%, a po kilku minutach wyrzuca błąd?
Wartość **85%** w logice obecnego scrapera odpowiada momentowi **po załadowaniu listy połączeń i wyborze pociągu, a tuż przed załadowaniem graficznego schematu wagonu (mapy miejsc)**. 
Zatrzymanie na kilka minut i błąd to klasyczny **`TimeoutError` (domyślnie 30 000 ms - 180 000 ms)** wynikający z niezrozumienia architektury strony PKP Intercity:
1. **Brak obsługi modali pośrednich:** Po kliknięciu "Wybierz" przy pociągu, PKP IC często wyświetla ukryte kroki: ostrzeżenie o obowiązkowej rezerwacji, pytanie o zniżki, komunikat o zastępczej komunikacji autobusowej (ZKA) lub informację o braku miejsc siedzących. Scraper czeka na przycisk "Wybierz miejsce z graficznego schematu", który nigdy się nie pojawia, bo zasłania go modal lub strona oczekuje na najpierw wybranie rodzaju biletu (Normalny, 1 szt.).
2. **Stary system (`bilet.intercity.pl`) vs Nowy system (`ebilet.intercity.pl` - e-IC 2.0):** Nowy system ładuje schematy wagonów jako dynamiczne komponenty SVG/Canvas wewnątrz asynchronicznych kontenerów (często po kliknięciu "Wskaż miejsce na schemacie"). W starym systemie schemat otwierał się w **ramce `<iframe>`** lub osobnym oknie popup! Jeśli scraper szuka selektora wagonu bezpośrednio w głównym `page`, a wagon jest w `iframe`, zawiesi się na 85% aż do wygaśnięcia czasu (Timeout).
3. **Anty-bot (Cloudflare / Akamai / Imperva):** Jeśli okno działa w tle bez flag maskujących (`stealth`), przejście do koszyka/schematu miejsc (najbardziej chroniony endpoint w PKP IC, bo blokuje miejsca w bazie Kurs90) zostaje zablokowane przez niewidzialny challenge CAPTCHA.

### 3. Problem wyboru Klasy 2 (Domyślne pułapki)
W systemie PKP Intercity każdy pociąg na liście wyników ma osobne przyciski lub zakładki wyboru ceny dla **Klasy 1** i **Klasy 2**. 
* Jeśli w klasie 2 skończyły się miejsca siedzące (lub są droższe niż promocyjne w 1 klasie), pierwszy dostępny przycisk "Kup bilet / Wybierz" w wierszu danego pociągu może należeć do **Klasy 1**!
* Scraper, który klika po prostu `.train-row .buy-button:first-of-type`, wejdzie w konfigurację 1. klasy, gdzie układy wagonów są zupełnie inne (np. przedziały 6-osobowe zamiast 8-osobowych, inna numeracja wagonów), co całkowicie psuje dalsze mapowanie miejsc.

---

## CZĘŚĆ 2: Anatomia Procesu Rezerwacji PKP Intercity (Krok po Kroku dla Antigravity)

Aby aplikacja działała niezawodnie, kod scrapera w Antigravity musi zostać przepisany tak, aby ściśle odwzorowywał poniższy proces i reagował na każdy z wariantów DOM na stronie PKP Intercity.

### KROK A: Wejście na stronę i zgody RODO (0% -> 15%)
1. Wejście na główną stronę wyszukiwarki (`https://ebilet.intercity.pl/` lub przekierowanie z `intercity.pl`).
2. **Natychmiastowe przechwycenie banera Cookies (Onetrust / własny modal PKP):**
   * Szukaj przycisków zawierających tekst: `"Zaakceptuj wszystkie"`, `"Zgadzam się"`, `"Akceptuję"`, lub ID `#onetrust-accept-btn-handler`, `.cookie-accept`.
   * Jeśli nie zamkniesz tego banera, zasłoni on kalendarz lub listę autouzupełniania stacji!

### KROK B: Wyszukiwarka połączeń – Autocomplete i Daty (15% -> 35%)
Pola "Stacja początkowa" (Od) i "Stacja końcowa" (Do) na PKP IC **nie są zwykłymi polami tekstowymi**. Zwykłe wpisanie tekstu (`page.fill()`) i naciśnięcie Szukaj spowoduje wyczyszczenie pola lub błąd walidacji, ponieważ pod spodem musi zapisać się wewnętrzny kod stacji (tzw. kod HAFAS/EVA, np. `5100065` dla Warszawa Centralna).
1. Kliknij w pole stacji "Od".
2. Wpisuj nazwę stacji z lekkim opóźnieniem (`delay=50ms`).
3. **Poczekaj na pojawienie się listy podpowiedzi (dropdown/autocomplete)** – czekaj na selektor listy podpowiedzi (np. `ul[role="listbox"] li`, `.autocomplete-suggestions div`).
4. Kliknij dokładnie pierwszy pasujący element z listy podpowiedzi (lub wciśnij `ArrowDown` + `Enter`, upewniając się, że pole ma przypisaną wartość z bazy).
5. Powtórz dokładnie to samo dla stacji "Do".
6. **Wybór daty i godziny:** Wpisz datę w formacie wymaganym przez input (często `DD.MM.RRRR` lub przez kliknięcie w datepicker) i zatwierdź klawiszem `Escape` lub kliknięciem w tło, aby zamknąć kalendarz, który zasłania przycisk "Szukaj".

### KROK C: Lista wyników i rygorystyczny wybór KLASY 2 (35% -> 60%)
Po kliknięciu "Szukaj" ładowana jest lista połączeń.
1. Odszukaj na liście właściwy pociąg (po numerze pociągu np. `IC 1524`, `EIP 3502` lub po dokładnej godzinie odjazdu).
2. **WYBÓR KLASY 2 (KRYTYCZNE):**
   * W obrębie kontenera znalezionego pociągu zlokalizuj sekcję wyboru klasy.
   * W nowym systemie (`ebilet.intercity.pl`) są to kafelki lub kolumny oznaczone wyraźnie jako **"2 klasa"** / **"Kl. 2"** (obok "1 klasa").
   * Scraper musi **jawnie przefiltrować przyciski wewnątrz wiersza pociągu**, szukając kontenera powiązanego z klasą 2 (np. po atrybucie `data-class="2"`, klasie CSS `.class-2`, lub tekście nagłówka kolumny `"2 kl."`).
   * **Walidacja dostępności:** Jeżeli w kafelku 2. klasy widnieje napis `"Brak miejsc"`, `"Sprzedaż zablokowana"` lub przycisk jest nieaktywny (`disabled`), scraper **NIE MOŻE** klikać w klasę 1! Musi natychmiast przerwać próbę dla tego odcinka/pociągu i zwrócić czysty status: `BRAK_MIEJSC_KLASA_2`.

### KROK D: Konfiguracja parametrów biletu i modale pośrednie (60% -> 80%)
To tutaj obecny scraper zaczyna gubić ścieżkę. Po wybraniu pociągu i Klasy 2, strona przechodzi do konfiguratora oferty.
1. **Sprawdzenie pop-upów ostrzegawczych:** Często wyskakuje okienko (modal): *"Wybrany pociąg objęty jest obowiązkową rezerwacją miejsc"* lub *"Na części trasy kursuje ZKA"*, lub *"Brak gwarancji miejsca do siedzenia"*. Scraper musi mieć pętlę sprawdzającą obecność przycisków `"Dalej"`, `"Rozumiem"`, `"Kontynuuj"`, `"Akceptuję"` w modalach i klikać je.
2. **Ustawienie liczby pasażerów:** Domyślnie zazwyczaj jest "1 pasażer - Bilet normalny". Upewnij się, że wybrany jest **1 bilet normalny**.
3. **Rodzaj miejsca (przed otwarciem mapy):** System ma domyślnie wybrane "Miejsce dowolne" (losowe z systemu Kurs90). Aby zobaczyć mapę, scraper musi zmienić opcję lub kliknąć przycisk:
   * Szukaj przycisku/linku: **"Wybierz miejsce z graficznego schematu"**, **"Wskaż miejsce na schemacie"**, **"Wybór miejsca na schemacie wagonu"** (w zależności od wersji front-endu e-IC).
   * *Uwaga:* Dla niektórych pociągów (np. TLK bez wagonów z miejscami numerowanymi w systemie graficznym lub przy wyczerpanej puli miejsc siedzących, gdy zostały tylko "miejsca bez gwarancji siedzenia") ten przycisk **nie istnieje lub jest wyszarzony**! Jeśli po 5 sekundach od załadowania konfiguratora przycisk schematu nie jest klikalny, scraper nie może wisieć na 85% przez 3 minuty – musi zgłosić: `SCHEMAT_NIEDOSTEPNY_DLA_POCIAGU`.

### KROK E: Graficzny schemat wagonów – Punkt Zawieszenia 85% (80% -> 95%)
Gdy scraper kliknie "Wskaż miejsce na schemacie", następuje ładowanie właściwej mapy.
1. **Detekcja kontenera (DOM vs Iframe):**
   * Sprawdź, czy na stronie pojawił się `<iframe>` ze schematem (stary e-IC) czy modal z elementami `<svg>` / `.carriage-schema` (nowy e-IC 2.0). Jeśli jest `iframe`, przełącz kontekst Playwrighta: `frame = page.frame_locator("iframe#...")`.
2. **Sprawdzenie klasy wagonu na schemacie:**
   * Na górze schematu znajduje się pasek wyboru wagonów (np. Wagon 11, 12, 14, 15).
   * Wagony Klasy 1 są na tym pasku wyszarzone, oznaczone jedynką (`1`) lub zablokowane, ponieważ w Kroku C wybraliśmy bilet na Klasę 2. Scraper musi iterować **wyłącznie po aktywnych wagonach Klasy 2** (oznaczonych cyfrą `2`).
3. **Odczyt stanów miejsc (Wolne vs Zajęte):**
   * Każde miejsce na schemacie ma swój numer (np. `11`, `12`, `86`) oraz klasę CSS lub kolor wypełnienia SVG określający stan:
     * **Wolne:** zazwyczaj kolor zielony lub biały z aktywnym obramowaniem (klasy typu `.seat-free`, `.available`, brak atrybutu `disabled`).
     * **Zajęte / Niedostępne:** kolor szary, czerwony lub przekreślony (klasy `.seat-taken`, `.occupied`, `.unavailable`, `.disabled`).
   * Podczas przełączania między kolejnymi wagonami (np. kliknięcie na "Wagon 12", potem "Wagon 14"), strona wykonuje zapytanie XHR/Fetch do backendu PKP. Scraper musi zaczekać, aż zniknie spinner ładowania wagonu, zanim sczyta miejsca!

### KROK F: Zwalnianie sesji i wyjście (95% -> 100%)
* **KRYTYCZNE:** Samo kliknięcie na miejsce i przejście dalej rezerwuje je w koszyku na 15-20 minut! Scraper służący do szukania kombinacji (przesiadek/wolnych miejsc) **NIE MOŻE** dodawać biletów do koszyka bez ich usuwania, bo sam sobie zablokuje miejsca w pociągu przy kolejnym zapytaniu!
* Scraper ma jedynie **odczytać dostępność miejsc z DOM/SVG lub z odpowiedzi sieciowej JSON (Network Interception)** dla danego odcinka i zamknąć schemat bez blokowania miejsca w systemie Kurs90.

---

## CZĘŚĆ 3: Lista Zadań Naprawczych dla Antigravity (Szczegółowy Plan Wdrożenia)

Poniższe punkty należy wdrożyć w kodzie projektu `IccBagno` w odpowiednich modułach.

### Moduł 1: Naprawa okna przeglądarki (`headless: false`) i Zarządzania Procesem
1. **Przekazywanie stanu Checkboxa (UI -> Backend):**
   * Zweryfikuj w kodzie frontendu (lub głównego okna aplikacji), jak nazywa się zmienna powiązana z checkboxem *"Pokaż okno przeglądarki"* (np. `show_browser`, `debug_mode`, `headless_toggle`).
   * Upewnij się, że w momencie kliknięcia przycisku "Start / Szukaj", aktualna wartość tego checkboxa jest odczytywana i przekazywana bezpośrednio jako argument funkcji uruchamiającej scraper.
2. **Wymuszenie świeżej instancji przeglądarki:**
   * Zlikwiduj globalną, ukrytą instancję przeglądarki tworzoną przy starcie programu.
   * W funkcji startującej zadanie scrapowania użyj jawnej deklaracji:
     ```python
     # Przykład dla Python Playwright (analogicznie w Node.js)
     is_headless = not bool(config.get("show_browser", False))
     browser = await playwright.chromium.launch(
         headless=is_headless,
         slow_mo=150 if not is_headless else 0, # Zwolnij akcje w trybie widocznym, by użytkownik widział co się dzieje!
         args=[
             "--start-maximized",
             "--disable-blink-features=AutomationControlled",
             "--window-position=50,50" # Gwarantuje, że okno nie otworzy się poza ekranem
         ]
     )
     context = await browser.new_context(no_viewport=True if not is_headless else False)
     ```
3. **Logowanie otwarcia okna:**
   * Zaraz po wywołaniu `browser.new_page()` wyślij do logów w UI komunikat: `[DEBUG] Uruchomiono przeglądarkę (Tryb widoczny: TAK/NIE)`. Jeśli użytkownik zaznaczył okienko, a w logu jest `NIE`, błąd leży w bindowaniu UI.

### Moduł 2: Naprawa Zawieszania na 85% (Eliminacja Martwego Punktu)
1. **Rozbicie kroku 85% na mikro-kroki z dokładnym logowaniem:**
   * Zamiast jednego wielkiego kroku na 85%, rozbij go na etapy paska postępu i loguj każdy z nich w konsoli aplikacji:
     * `70%` - Znaleziono pociąg na liście, wybieram Klasę 2.
     * `75%` - Załadowano konfigurator biletu, zamykam ewentualne pop-upy.
     * `80%` - Klikam przycisk "Wybierz miejsce ze schematu".
     * `85%` - Oczekiwanie na wyrenderowanie grafiki wagonu.
     * `90%` - Odczytywanie miejsc z wagonów Klasy 2.
     * `95%` - Analiza kombinacji przesiadek.
2. **Skrócenie Timeoutów i obsługa błędów (Fail-Fast):**
   * Zmniejsz domyślny timeout oczekiwania na pojedynczy element z 30-180 sekund na **10-12 sekund**.
   * Jeśli na poziomie `80%` lub `85%` element się nie pojawi, **NIE ZAWIESZAJ** aplikacji na kilka minut! Przechwyć wyjątek `TimeoutError` i wykonaj automatyczną diagnostykę:
     1. Zrób zrzut ekranu (`await page.screenshot(path="error_85_percent.png")`) i zapisz obecny HTML strony do pliku `debug_85.html`.
     2. Sprawdź, czy na ekranie widnieje tekst `"Brak miejsc"` lub `"Przerwa techniczna"` albo zabezpieczenie Cloudflare.
     3. Wyświetl użytkownikowi natychmiastowy, czytelny błąd w UI (np. *"Nie udało się otworzyć schematu wagonu na 85% – zapisano zrzut ekranu do error_85_percent.png"*).
3. **Przechwytywanie Ruchu Sieciowego (Network Interception) zamiast ślepego klikania w SVG:**
   * To jest **najważniejsza wskazówka dla Antigravity**: Gdy strona PKP Intercity otwiera schemat wagonu na 85%, przeglądarka wysyła w tle zapytanie AJAX/Fetch (zwracające czysty plik **JSON** z listą wagonów, ich klasą oraz tablicą wszystkich miejsc i ich statusem: wolne/zajęte!).
   * Zamiast męczyć się z parsowaniem skomplikowanych elementów graficznych SVG na ekranie, podepnij nasłuchiwanie odpowiedzi sieciowych:
     ```python
     async def handle_response(response):
         if "schema" in response.url or "seats" in response.url or "wagon" in response.url:
             try:
                 data = await response.json()
                 # Tutaj mamy gotowy JSON ze wszystkimi miejscami w ułamku sekundy!
             except:
                 pass
     page.on("response", handle_response)
     ```
   * Dzięki temu, gdy tylko klikniesz "Wskaż miejsce na schemacie", scraper natychmiast przechwytuje surowy JSON z serwera PKP IC i nie musi czekać, aż cały interfejs graficzny poprawnie się narysuje!

### Moduł 3: Rygorystyczne Wymuszenie Klasy 2 (Hardcoding & UI Selection)
1. **Parametr w konfiguracji:**
   * Ustaw w całym kodzie sztywne domyślne ustawienie: `TARGET_CLASS = 2` (z opcją przełączenia w UI na Klasę 1 tylko wtedy, gdy użytkownik wyraźnie tego zażąda, ale domyślnie zawsze zaznaczona **Klasa 2**).
2. **Selektor wyboru klasy na liście połączeń:**
   * Zakaż scraperowi klikania w pierwszy lepszy przycisk `"Wybierz"` / `"Kup bilet"`.
   * Napisz funkcję pomocniczą `select_second_class_for_train(train_row_locator)`:
     * Wewnątrz wiersza wybranego pociągu znajdź element zawierający tekst `"2 kl"` lub `"Klasa 2"`.
     * Sprawdź, czy wewnątrz tego kontenera lub bezpośrednio pod nim znajduje się cena i aktywny przycisk wyboru (radio button / kafelek / przycisk).
     * Kliknij najpierw w kafelek **2 klasy**, poczekaj `500ms` na odświeżenie ceny na przycisku podsumowania, i dopiero wtedy kliknij przycisk przejścia dalej (`"Wybierz"`).
3. **Weryfikacja na etapie konfiguracji biletu (Assertion):**
   * Po przejściu do ekranu wyboru ulg i schematu (ok. 75% postępu), sprawdź tekst podsumowania na stronie. Musi się tam znajdować informacja `"Klasa 2"` lub `"2 kl."`.
   * Jeśli scraper wykryje tam `"Klasa 1"`, musi natychmiast cofnąć się lub rzucić błąd logiki selektora, zamiast skanować wagony pierwszej klasy!
4. **Filtrowanie wagonów na schemacie:**
   * Podczas iteracji po zakładkach wagonów na schemacie (np. Wagon 9, 10, 11, 12), przed kliknięciem w dany wagon sprawdź jego oznaczenie klasy. Pomijaj wszystkie wagony oznaczone jako klasa `1`, `WR` (wagon restauracyjny WARS) lub wagony sypialne/kuszetki (`WL`, `Bc`), skupiając się wyłącznie na wagonach siedzących klasy `2` (oraz strefie ciszy w klasie 2, jeśli jest dostępna).

### Moduł 4: Anty-Bot, Stealth i Stabilność Sesji na PKP IC
1. **Zastosowanie `playwright-stealth` (lub odpowiednika):**
   * Serwery PKP Intercity używają systemów WAF (Web Application Firewall), które wykrywają domyślnego Playwrighta po fladze `navigator.webdriver = true` w momencie przejścia do rezerwacji miejsca (dokładnie na etapie 80-85%!).
   * Dodaj skrypt inicjalizujący przed załadowaniem strony:
     ```javascript
     Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
     ```
2. **Realistyczny User-Agent i nagłówki:**
   * Ustaw aktualny User-Agent zwykłej przeglądarki Chrome na systemie Windows 10/11 (bez słowa `HeadlessChrome`!).
   * Ustaw język przeglądarki na `pl-PL` oraz strefę czasową `Europe/Warsaw`.
3. **Ograniczenie szybkości zapytań (Rate Limiting przy szukaniu przesiadek):**
   * Jeśli aplikacja szuka miejsca z 1 przesiadką (czyli musi sprawdzić odcinek A -> B oraz B -> C, albo odpytać kilka stacji pośrednich), **nie otwieraj 10 kart jednocześnie z tego samego IP!**
   * System PKP IC natychmiast nałoży blokadę sesji (błąd HTTP 429 lub nieskończone kręcenie się kółka ładowania na 85%).
   * Sprawdzaj odcinki sekwencyjnie w jednej karcie lub maksymalnie w 2 kartach z losowym opóźnieniem `1.5 - 3.0 sekundy` między zapytaniami.

---

## CZĘŚĆ 4: Podsumowanie i Checklista Testowa (Co sprawdzić po wdrożeniu w Antigravity)

Po zaaplikowaniu powyższego planu naprawczego w Antigravity, przetestuj aplikację według poniższych 4 kroków weryfikacyjnych:

1. **Test Okna Przeglądarki:**
   * Zaznacz checkbox *"Pokaż okno przeglądarki"* i kliknij Szukaj.
   * **Oczekiwany efekt:** W ciągu 2 sekund na ekranie pojawia się zmaksymalizowane okno Chromium, w którym widać na żywo (dzięki `slow_mo`), jak zamyka się baner cookies, wpisują się stacje i wybierana jest **2 klasa**.
2. **Test Klasy 2:**
   * Wyszukaj połączenie pociągiem obsługującym obie klasy (np. EIP lub IC na trasie Warszawa - Kraków / Gdańsk).
   * **Oczekiwany efekt:** W widocznym oknie przeglądarki wyraźnie widać kliknięcie w kolumnę/kafelek **Klasy 2**, a na otwartym schemacie wagonów skanowane są wyłącznie wagony oznaczone cyfrą `2`.
3. **Test Przejścia Przez 85%:**
   * Obserwuj pasek postępu i logi w okolicach 75% - 95%.
   * **Oczekiwany efekt:** Pasek nie staje na 85%. Po wybraniu Klasy 2 i kliknięciu wyboru miejsca ze schematu, otwiera się mapa wagonu, scraper przechodzi przez dostępne wagony 2. klasy i zwraca dokładne numery wolnych miejsc (lub kombinację przesiadkową, np. *Wagon 12 miejsce 45 od stacji A do B, a następnie Wagon 14 miejsce 21 od B do C*).
4. **Test Zrzutu Błędu (Fail-Safe):**
   * Wyszukaj pociąg, w którym na 100% nie ma już miejsc lub nie ma schematu graficznego.
   * **Oczekiwany efekt:** Zamiast wisieć przez kilka minut na 85%, aplikacja w ciągu maksymalnie 10 sekund informuje, że dla tego pociągu schemat graficzny w 2. klasie jest niedostępny, zapisuje zrzut ekranu do folderu diagnostycznego i płynnie przechodzi do sprawdzania kolejnego połączenia.