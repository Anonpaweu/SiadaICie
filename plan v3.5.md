# Plan Naprawczy V3 – Architektura 6 Kroków i Śledzenie Pociągu (IccBagno)

Dokument zawiera kompletną analizę przebiegu rezerwacji oraz wytyczne wdrożeniowe na podstawie zrzuconych widoków (`1.png` – `6.png`) oraz plików źródłowych DOM:
1. `1-kup bilet - PKP Intercity.html`
2. `2-e-IC2 - Internetowa rezerwacja miejsc.html`
3. `3-e-IC2 - Internetowa rezerwacja miejsc.html`
4. `4-e-IC2 - Internetowa rezerwacja miejsc.html`
5. `5-e-IC2 - Internetowa rezerwacja miejsc.html`
6. `6-e-IC2 - Internetowa rezerwacja miejsc.html`

---

## 1. Architektura Przepływu i Zarządzanie Sesją (Przejście Krok 1 $\rightarrow$ Krok 2–6)

Głównym problemem stabilności w dotychczasowej wersji (IccBagno) było gubienie kontekstu przy przejściu z nowej wyszukiwarki frontowej (`intercity.pl`) do właściwego silnika rezerwacyjnego **e-IC2** (`ebilet.intercity.pl`).

* **Utrzymanie Ciasteczek i Tokenów:** Przejście z Kroku 1 do Kroku 2 odbywa się poprzez wysłanie formularza lub przekierowanie z parametrami sesyjnymi. Scraper/automat musi współdzielić jeden kontekst przeglądarki (`BrowserContext` / `CookieJar`), uwzględniając tokeny CSRF oraz identyfikator sesji e-IC2.
* **Śledzenie Pociągu (Train Tracking):** Identyfikacja konkretnego składu nie może opierać się wyłącznie na godzinie odjazdu (ze względu na opóźnienia i zmiany w rozkładzie). Kluczem głównym śledzenia w krokach 2–6 jest kombinacja: **Numer pociągu (np. IC/EIC/EIP) + Relacja + Data wyjazdu**.

---

## 2. Szczegółowa Specyfikacja 6 Kroków (Analiza HTML + PNG)

### Krok 1: Wyszukiwanie połączenia (`1.png` / `1-kup bilet - PKP Intercity.html`)
Widok startowy wyszukiwarki połączeń.
* **Elementy DOM i dane wejściowe:**
  * Pola stacji: `stname[0]` (stacja wyjazdu) oraz `stname[1]` (stacja przyjazdu) wraz z ukrytymi polami ID stacji (`stid[0]`, `stid[1]`), które są wypełniane przez autouzupełnianie (AJAX).
  * Data i czas: pole `date` (format `YYYY-MM-DD`) oraz `time` (`HH:MM`).
* **Akcja naprawcza:** Automat nie może tylko wpisać tekstu w pole stacji i kliknąć „Szukaj” – musi poczekać na załadowanie listy podpowiedzi lub bezpośrednio wstrzyknąć kod EVA/ID stacji do ukrytego inputu, a następnie wywołać akcję submit formularza wyszukiwarki.

### Krok 2: Lista połączeń i wybór pociągu (`2.png` / `2-e-IC2 - Internetowa rezerwacja miejsc.html`)
Przejście do systemu **e-IC2**. Widok prezentuje tabelę dostępnych połączeń dla zadanej relacji.
* **Elementy DOM i logika wyboru:**
  * Wiersze z pociągami zawierają szczegółowe metadane: numer pociągu, kategorię (EIP, EIC, IC, TLK), czas podróży oraz status dostępności miejsc.
  * Przycisk wyboru połączenia inicjuje załadowanie szczegółów taryfowych dla wybranego wiersza.
* **Śledzenie pociągu (IccBagno Fix):** Zamiast klikać pierwszy lepszy wynik z brzegu po indeksie wiersza (`tr:nth-child(1)`), skrypt musi parsować tekst z numerem pociągu w wierszu, dopasować go do szukanego ID składu i kliknąć przypisany do niego przycisk przejścia dalej („Wybierz” / rozwinięcie szczegółów).

### Krok 3: Konfiguracja parametrów biletu, klasy i zniżek (`3.png` / `3-e-IC2 - Internetowa rezerwacja miejsc.html`)
Widok konfiguracji oferty po wybraniu konkretnego pociągu w e-IC2.
* **Elementy DOM i dane wejściowe:**
  * Wybór klasy wagonu: przełączniki / radio buttony (`1 klasa` / `2 klasa`).
  * Liczba podróżnych i ulgi: elementy `<select>` odpowiadające za wybór taryfy (Normalny vs zniżki ustawowe: Studencka 51%, Uczniowska 37% itd.).
  * Rodzaj miejsca / preferencje: wybór typu wagonu (przedziałowy, bezprzedziałowy, strefa ciszy, miejsce dla roweru).
* **Akcja naprawcza:** Po każdej zmianie w `<select>` (np. dodanie ulgi) system e-IC2 wykonuje przeładowanie częściowe (AJAX) w celu przeliczenia dostępności i ceny. Skrypt musi bezwzględnie czekać na zakończenie requestu sieciowego (`networkidle` / zniknięcie spinnera ładowania) przed kliknięciem przycisku przejścia do wyboru miejsc.

### Krok 4: Wybór miejsca ze schematu lub wskazanie wagonu (`4.png` / `4-e-IC2 - Internetowa rezerwacja miejsc.html`)
Kluczowy moduł dla funkcjonalności **IccBagno** (sprawdzanie zajętości / rezerwacja konkretnych miejsc).
* **Elementy DOM i odczyt stanu miejsc:**
  * Renderowany graficzny schemat wagonów (lub formularz wskazania miejsca obok innego).
  * Poszczególne wagony posiadają zakładki (numery wagonów), a wewnątrz nich znajdują się węzły reprezentujące pojedyncze fotele/miejsca.
  * Statusy miejsc są rozróżniane przez klasy CSS (np. miejsce wolne, zajęte, aktualnie wybrane, miejsce ze stolikiem, kierunek jazdy).
* **Akcja naprawcza (Śledzenie i Mapowanie):**
  * Aby zmapować cały pociąg (tryb skanowania zajętości), skrypt musi iterować po wszystkich dostępnych zakładkach wagonów na schemacie, parsując z DOM tablicę wolnych i zajętych numerów miejsc.
  * Przy rezerwacji konkretnego miejsca należy kliknąć element odpowiadający danemu numerowi fotela, zweryfikować czy klasa CSS zmieniła się na „wybrane” (selected) i zatwierdzić wybór przyciskiem przejścia dalej.

### Krok 5: Dane podróżnego i przypisanie do biletu (`5.png` / `5-e-IC2 - Internetowa rezerwacja miejsc.html`)
Widok wprowadzania danych osobowych (wymagany przed utworzeniem rezerwacji w koszyku/płatności).
* **Elementy DOM i dane wejściowe:**
  * Pola tekstowe na **Imię** i **Nazwisko** podróżnego (szczególnie rygorystycznie walidowane w systemie e-IC2 przed przejściem do podsumowania).
  * W przypadku kontynuacji bez logowania (jako gość) lub na koncie: pole adresu **E-mail** (do wysyłki biletu) oraz akceptacja regulaminów (checkboxy zgód).
* **Akcja naprawcza:**
  * Częstym błędem na tym etapie w automatach jest zbyt szybkie wywołanie submitu bez wyzwolenia zdarzeń `blur` / `change` na polach input, przez co walidator JS po stronie e-IC2 blokuje przejście do Kroku 6.
  * Należy wypełnić pola imienia i nazwiska, zaznaczyć wymagane checkboxy regulaminu i wywołać kliknięcie przycisku „Dalej” / „Podsumowanie”.

### Krok 6: Podsumowanie rezerwacji, blokada miejsca i płatność (`6.png` / `6-e-IC2 - Internetowa rezerwacja miejsc.html`)
Ostatni ekran procesu w systemie e-IC2 przed przekierowaniem do bramki płatniczej.
* **Elementy DOM i weryfikacja końcowa:**
  * Tabela podsumowania zawierająca: dokładną relację, datę, godzinę odjazdu/przyjazdu, numer pociągu, **przydzielony numer wagonu oraz numer miejsca**, cenę końcową oraz czas wygaśnięcia tymczasowej rezerwacji (timer blokady miejsca).
  * Wybór metody płatności i przycisk finalizujący („Kupuję i płacę” / „Zapłać”).
* **Akcja naprawcza (Logika IccBagno):**
  * **Walidacja przydziału:** Zanim skrypt uzna proces za zakończony (lub zanim wywoła blokadę miejsca), musi sparsować z widoku `6-e-IC2` sekcję przydzielonych miejsc. Jeżeli system e-IC2 (w przypadku braku wybranego miejsca ze schematu) przydzielił miejsce automatycznie, tutaj odczytujemy faktyczny numer wagonu i fotela.
  * **Obsługa blokady (Hold):** Na etapie Kroku 6 miejsce w pociągu ma już status tymczasowo zablokowanego w systemie centralnym PKP (przez określony czas widoczny w podsumowaniu). Dla modułu śledzenia pociągu jest to punkt docelowy potwierdzający 100% dostępność konkretnego zasobu.

---

## 3. Podsumowanie zmian w kodzie (Checklist Wdrożeniowy)

1. **[Parser HTML / Selektory]:** Zaktualizować selektory dla nowego układu e-IC2 zgodnie z plikami `2-e-IC2` do `6-e-IC2` (oddzielenie logiki starego formularza od kontenerów e-IC2).
2. **[State Machine - 6 Kroków]:** Rozbudować maszynę stanów w kodzie z 4 do pełnych **6 kroków**, dodając obsługę formularza danych pasażera (Krok 5) oraz parsera tabeli podsumowania i blokady (Krok 6).
3. **[Synchronizacja AJAX]:** W Krokach 3 i 4 dodać jawne oczekiwanie na odświeżenie drzewa DOM po zmianie parametrów taryfy oraz przy przełączaniu zakładek poszczególnych wagonów na schemacie.
4. **[Walidacja Końcowa]:** W Kroku 6 dodać zrzut przydzielonego wagonu/miejsca oraz kwoty do logów śledzenia pociągu, aby wyeliminować fałszywie pozytywne odczyty z wcześniejszych kroków.