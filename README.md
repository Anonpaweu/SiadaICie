<p align="center">
  <img src="docs/logo.jpg" alt="SiadaICie" width="160" />
</p>

<h1 align="center">SiadaICie</h1>

<p align="center">
  <em>Koniec z koczowaniem na korytarzu przy toalecie. Usiądź jak człowiek.</em>
</p>

<p align="center">
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.12-blue.svg" alt="Python 3.12" /></a>
  <a href="https://fastapi.tiangolo.com"><img src="https://img.shields.io/badge/FastAPI-0.115+-009688.svg" alt="FastAPI" /></a>
  <a href="https://playwright.dev/python/"><img src="https://img.shields.io/badge/Playwright-Async-45ba4b.svg" alt="Playwright" /></a>
  <img src="https://img.shields.io/badge/Android-Kotlin%20%2F%20Jetpack%20Compose-3DDC84.svg" alt="Android Kotlin Jetpack Compose" />
  <img src="https://img.shields.io/badge/license-MIT-orange.svg" alt="License MIT" />
  <a href="https://buycoffee.to/anonpaweu"><img src="https://img.shields.io/badge/Buy%20me%20a%20coffee-buycoffee.to-f36f21.svg" alt="Pomogłem? Buy me a coffee" /></a>
</p>

---

## Jak znaleźć wolne miejsce siedzące w przepełnionym pociągu PKP Intercity?

**SiadaICie** to darmowa aplikacja (na Androida oraz PC Web), która znajduje wolne miejsca siedzące w przepełnionych pociągach PKP Intercity (IC, TLK, EIC, EIP), nawet gdy system e-IC pokazuje brak miejsc na całą trasę. Aplikacja sprawdza schematy wagonów odcinek po odcinku i układa przejazd z przesiadką między wolnymi fotelami w tym samym pociągu albo wskazuje niezarezerwowane miejsca w trakcie jazdy z biletem bez gwarancji.

Znasz to uczucie? Jest niedzielne popołudnie, koniec długiego weekendu albo święta. Chcesz kupić bilet na pociąg przez pół Polski (np. z Warszawy do Wrocławia przez Koluszki, Piotrków, Częstochowę i Opole). Wchodzisz do systemu, a tam komunikat: **„Brak wolnych miejsc do siedzenia”**. Do wyboru masz bilet bez gwarancji miejsca albo rezygnację z podróży.

**Kolej to nie samolot.** Pociąg zatrzymuje się po drodze na kilkunastu stacjach, więc ludzie nieustannie wsiadają i wysiadają:
- Ktoś wysiada w Koluszkach — jego fotel zwalnia się na resztę trasy.
- Ktoś inny wsiada dopiero w Opolu — do Opola to miejsce stoi puste.

System kolejowy przy próbie zakupu biletu na **całą relację** mówi „nie ma miejsc”, bo żaden pojedynczy fotel nie jest wolny od stacji początkowej do samego końca. Wystarczy kupić **dwa lub trzy bilety cząstkowe** na kolejne odcinki i w trakcie podróży przesiąść się na inny wolny fotel — i całą trasę pokonujesz wygodnie na siedząco.

**Prosty przykład z życia:** chcesz jechać ze stacji **A** do **Z**. Akurat między przystankami **G** i **H** (jeden 20-minutowy odcinek w środku trasy) wsiada duża grupa i zajmuje wszystkie fotele w całym pociągu. System PKP IC powie Ci wtedy krótko: *„brak gwarancji miejsca siedzącego na całą trasę A–Z”* i w ogóle nie podpowie alternatywy — choć w praktyce od **A do G** możesz jechać na siedząco, na tym jednym odcinku **G–H** postać 20 minut, a od **H do Z** znów usiąść na gwarantowanym miejscu. SiadaICie wyłapuje dokładnie takie sytuacje.

### Jak znaleźć wolne miejsce w pociągu PKP Intercity z biletem bez gwarancji miejsca?

**Jadąc pociągiem PKP Intercity z biletem bez gwarancji miejsca, możesz w aplikacji SiadaICie sprawdzić aktualnie niezarezerwowane fotele na pozostałej części trasy.** Wystarczy rozwinąć przystanki swojego pociągu, wskazać najbliższą stację przed sobą i uruchomić skan. SiadaICie odczyta bieżący stan rezerwacji w systemie PKP IC i pokaże na siatce miejsc, w którym wagonie oraz na których numerach nikt nie ma wykupionej miejscówki (system widzi oczywiście brak rezerwacji, a nie to, czy fizycznie nie usiadł tam już ktoś inny bez miejscówki — ale i tak oszczędza sporo bezsensownego chodzenia po składzie).

**SiadaICie** (dostępne jako **aplikacja PC Web** oraz natywna **aplikacja na Androida**) robi za Ciebie całą czarną robotę: pobiera rozkład, sprawdza schematy wagonów, zbiera wolne fotele na poszczególnych odcinkach i układa matematycznie optymalną podróż z minimalną liczbą przesiadek.

---

## Co potrafi SiadaICie?

- **Dwie wersje do wyboru — PC Web oraz Android:**
  - **PC Web (FastAPI + Playwright):** lokalny serwer z interfejsem w przeglądarce (`http://localhost:8000`) i równoległym skanowaniem w kontekstach Chromium.
  - **Android (Kotlin + Jetpack Compose):** natywna aplikacja w Material 3 (jasny i ciemny motyw) z wbudowaną bazą stacji offline, usługą skanowania działającą **w tle** (możesz zminimalizować aplikację w trakcie analizy), historią **3 ostatnich analiz (wygasających po 1 godzinie)**, interaktywną siatką wolnych miejsc oraz adaptacyjnym dopasowaniem równoległości do RAM/CPU telefonu.
- **Wielowarstwowe przyspieszanie skanowania:**
  - **Fast-Path A -> B:** najpierw błyskawicznie sprawdza bezpośrednią dostępność na całej trasie — jeśli jest choć jedno wolne miejsce na 100% drogi, wynik wraca od razu (z możliwością ręcznego kliknięcia **„Wymuś pełne skanowanie”**, jeśli mimo wszystko chcesz zobaczyć pełną siatkę wolnych miejsc na odcinkach pośrednich).
  - **Adaptive Split Fast-Path:** na długich trasach (8+ odcinków), gdzie system blokuje schemat dla całej relacji, dzieli trasę na mniejsze odcinki zbiorcze zanim przejdzie do pełnego skanu stacja po stacji.
  - **Bezpośredni odczyt API GRM (`/grm/sklad/wbnet/` + `/grm/wagon/svg/wbnet/`):** przechwytuje sesję z e-IC i pobiera czysty XML schematów wagonów bezpośrednio przez API bez powolnego przeklikiwania karuzeli DOM.
- **Obsługa pociągów bez schematu wagonów (np. słynna, „wiecznie przepełniona” IC Orzeszkowa):**
  - Część popularnych składów PKP Intercity w ogóle nie ma w e-IC graficznej mapki wyboru miejsc — normalnie trzeba wtedy strzelać w ciemno po kolejnych odcinkach w wyszukiwarce.
  - SiadaICie automatycznie rozpoznaje taki pociąg i bez przechodzenia do płatności ani blokowania biletów wyszukuje najdłuższe odcinki z gwarantowanym miejscem siedzącym (przydzielanym automatycznie przez e-IC) oraz jasno wskazuje, na którym fragmencie trasy pojawia się wąskie gardło.
- **Filtry miejsc specjalnych, statusy e-IC i hierarchia priorytetów:**
  - Domyślna hierarchia wygody: **zwykłe miejsca / Strefa ciszy** (najwyższy priorytet) > **miejsce z rowerem** (domyślnie włączone, a po wyłączeniu aplikacja podpowie, gdy rower ratuje przed staniem) > opcjonalne filtry (**kobieta w ciąży**, **osoba z dzieckiem**, **senior**, **niepełnosprawność bez wózka**, **osoba na wózku**).
  - Rozróżnia wszystkie stany sprzedaży e-IC: bilet bez gwarancji, sprzedaż wyłącznie miejsc specjalnych (z podaniem konkretnych dostępnych kategorii w nawiasie), dostępność biletów wyłącznie w 1. klasie oraz całkowitą blokadę sprzedaży.
  - Zmiana filtrów po zakończeniu skanu przelicza warianty **natychmiast lokalnie** (`/api/v1/recompute` w Web / przeliczenie w pamięci na Androidzie) bez ponownego skanowania.
- **Matematyczny silnik optymalizacji (DAG + Programowanie Dynamiczne):**
  - **3 profile do wyboru:** *Zrównoważona*, *Najmniej przesiadek* oraz *Najkrótsze spacery*.
  - Preferuje przesiadki w **tym samym przedziale** lub **tym samym wagonie** oraz uwzględnia nieprzechodnie człony EZT (np. podwójne składy).
  - Obsługuje wagony **Klasy 2** oraz **Klasy 1**, przekreśla odjechane już dziś pociągi i stacje pośrednie (podświetlając najbliższy dostępny przystanek) oraz oferuje filtr odjazdu po wybranej godzinie.
- **Gotowy „Plan Zakupu Biletów” i siatka miejsc:**
  - Czytelna oś czasu podróży oraz lista biletów cząstkowych krok po kroku z bezpośrednim przejściem do e-IC.
  - Interaktywna siatka dostępności wszystkich wagonów i miejsc na każdym odcinku (z pomarańczowym oznaczeniem i ikonami miejsc specjalnych).
- **Radar nocnej przerwy serwisowej:**
  - Ostrzega o codziennym oknie konserwacyjnym systemów kolejowych (**23:45 – 00:30**, a na przełomie miesięcy do **01:00**).

---

## Aplikacja na Androida

Gotowy plik `.apk` (Android 10+ / API 29+) możesz pobrać bezpośrednio z zakładki **[GitHub Releases](https://github.com/Anonpaweu/SiadaICie/releases)** (kod źródłowy wersji mobilnej znajduje się na gałęzi `android-dev`).

### Instalacja na telefonie z pliku APK:
1. Pobierz najnowszy plik `.apk` z **[Releases](https://github.com/Anonpaweu/SiadaICie/releases)** bezpośrednio na telefon.
2. Zezwól na instalację z nieznanych źródeł dla przeglądarki lub Menedżera plików (oraz wybierz *„Zainstaluj mimo to”*, jeśli Google Play Protect poprosi o potwierdzenie dla aplikacji spoza Sklepu Play).

### Budowanie lokalne z kodu (dla deweloperów):
```bash
# Przełącz się na gałąź android-dev
git checkout android-dev

# Uruchom testy i zbuduj podpisany plik release APK (wymagany JDK 17 i Android SDK)
cd android
./gradlew testDebugUnitTest assembleRelease
```
Na Windowsie do szybkiego wgrania zbudowanej aktualizacji na telefon przez ADB służy skrypt `android/install_on_android.bat` (zachowuje historię i ustawienia bez odinstalowywania poprzedniej wersji).

---

## Architektura projektu

```text
SiadaICie/
├── backend/                     # Aplikacja PC Web (FastAPI + Playwright)
│   ├── app/
│   │   ├── engine/              # Parsowanie dostępności, profile wag i topologia pociągu
│   │   ├── optimizer/           # Solwer grafowy DAG + Programowanie Dynamiczne
│   │   ├── scraper/             # Playwright scraper + parser SVG GRM + radar błędów
│   │   ├── db/                  # Baza SQLite (auto-inicjalizacja i seed stacji przy starcie)
│   │   ├── models/              # Schematy Pydantic i modele domenowe
│   │   └── main.py              # Aplikacja FastAPI (REST API + /api/v1/recompute)
│   ├── static/
│   │   └── index.html           # Interfejs Web (Tailwind CSS, Vanilla JS)
│   ├── tests/                   # Zestaw testów jednostkowych i integracyjnych (pytest)
│   └── requirements.txt         # Zależności Pythona
├── android/                     # Natywna aplikacja Android (Kotlin + Jetpack Compose, gałąź android-dev)
│   ├── app/src/main/java/pl/siadaicie/app/
│   │   ├── data/                # Baza stacji offline (stations.db) + historia 3 skanów (1h TTL)
│   │   ├── optimizer/           # Solwer DAG w Kotlinie z hierarchią priorytetów miejsc
│   │   ├── scraper/             # Skaner WebView + bezpośrednie zapytania GRM API
│   │   ├── service/             # ScanForegroundService (skanowanie w tle)
│   │   └── ui/                  # Ekrany Compose (Wyszukiwanie, Filtry, Wyniki, Ustawienia)
│   └── install_on_android.bat   # Skrypt aktualizacji APK przez ADB
├── docs/
│   └── logo.jpg                 # Logo projektu
├── start_server.bat             # Launcher serwera PC Web dla systemu Windows
└── README.md
```

---

## Szybki start (PC Web)

### Wymagania:
- **Python 3.10+** (zalecany Python 3.12)
- Zainstalowana przeglądarka Chromium dla Playwrighta

### 1. Sklonuj repozytorium:
```bash
git clone https://github.com/Anonpaweu/SiadaICie.git
cd SiadaICie
```

### 2. Przygotuj wirtualne środowisko i zainstaluj zależności:
```bash
# Utwórz venv
python -m venv venv

# Aktywuj środowisko:
# Na Windows (PowerShell):
.\venv\Scripts\Activate.ps1
# Na Windows (CMD):
.\venv\Scripts\activate.bat
# Na Linux / macOS:
source venv/bin/activate

# Zainstaluj pakiety
pip install -r backend/requirements.txt

# Pobierz przeglądarkę Playwright
playwright install chromium
```

### 3. Odpal aplikację:
- **Na Windows:** kliknij dwukrotnie w plik `start_server.bat`.
- **Albo ręcznie z konsoli:**
  ```bash
  python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
  ```

Przy pierwszym uruchomieniu serwer automatycznie utworzy bazę SQLite i przygotuje słownik oficjalnych stacji.
Otwórz w przeglądarce: **[http://localhost:8000](http://localhost:8000)**.

---

## Uruchamianie testów

- **PC Web / Backend (`pytest`):**
  ```bash
  pytest backend/tests/ -v
  ```
- **Android (testy jednostkowe JUnit):**
  ```bash
  cd android
  .\gradlew.bat testDebugUnitTest
  ```

---

## Jak korzystać z SiadaICie?

Wyszukanie wolnych miejsc siedzących w pociągu PKP Intercity z pomocą SiadaICie zajmuje cztery kroki:

1. **Wpisz stację odjazdu i docelową** — np. *Warszawa Centralna* -> *Wrocław Główny* (autouzupełnianie podpowie oficjalne nazwy węzłów).
2. **Wybierz datę, opcjonalnie minimalną godzinę odjazdu, klasę (2 lub 1) oraz filtry miejsc** (domyślnie aktywne są zwykłe fotele, Strefa ciszy oraz miejsca z rowerem).
3. **Wyszukaj połączenia i uruchom skanowanie pociągu** — aplikacja automatycznie uruchomi Fast-Path albo przeskanuje kolejne odcinki trasy.
4. **Wybierz najwygodniejszy wariant podróży** (np. *Zrównoważona* lub *Najmniej przesiadek*), przejrzyj instrukcję zakupu biletów i kup wskazane bilety cząstkowe w oficjalnym serwisie e-IC lub w kasie.

---

## Najczęstsze pytania (FAQ)

### Dlaczego PKP Intercity pokazuje brak miejsc siedzących, skoro po drodze są wolne fotele?

System rezerwacyjny PKP Intercity (e-IC) przy zakupie jednego biletu na całą relację szuka pojedynczego fotela wolnego bez przerwy od stacji początkowej do końcowej. Gdy każdy fotel w pociągu jest zajęty choćby na jednym krótkim odcinku w środku trasy, e-IC oferuje wyłącznie bilet bez gwarancji miejsca — nawet jeśli po podzieleniu podróży na dwa bilety cząstkowe można przejechać całą trasę na siedząco z jedną przesiadką wewnątrz pociągu.

### Czy SiadaICie działa dla pociągów bez graficznego schematu wagonów (np. IC Orzeszkowa)?

Tak — SiadaICie obsługuje również pociągi PKP Intercity bez graficznej mapki miejsc w e-IC, takie jak popularny IC Orzeszkowa. Aplikacja automatycznie przełącza się w tryb odcinkowy, sprawdza dostępność bez blokowania biletów i wyznacza najdłuższe fragmenty trasy z gwarantowanym miejscem siedzącym przydzielanym automatycznie przez e-IC.

### Czy SiadaICie jest darmowe i czy samo rezerwuje lub blokuje bilety?

SiadaICie jest w pełni darmowym projektem open-source (licencja MIT) dostępnym na Androida (APK z GitHub Releases) oraz na PC Web. Aplikacja działa wyłącznie w trybie odczytu (read-only) — nie kupuje biletów, nie blokuje miejsc w systemie przewoźnika i po zakończeniu analizy odsyła bezpośrednio do oficjalnego serwisu e-IC.

### Dlaczego nie da się przeskanować samego odcinka Warszawa Zachodnia – Warszawa Centralna?

PKP Intercity w systemie e-IC nie prowadzi sprzedaży biletów na krótkie przejazdy wewnątrz tej samej aglomeracji (np. Warszawa Zachodnia – Warszawa Centralna lub Gdańsk Wrzeszcz – Gdańsk Główny). Na dłuższych trasach SiadaICie automatycznie łączy taki graniczny odcinek z sąsiednią stacją, a przy pojedynczym skanie wewnątrz aglomeracji informuje o ograniczeniu sprzedaży.

---

## Wsparcie projektu

Jeśli aplikacja pomogła Ci uniknąć stania na korytarzu — **Pomogłem? [Buy me a coffee (BLIK / karta)](https://buycoffee.to/anonpaweu)**.

---

## Zastrzeżenie prawne (Disclaimer)

Projekt ma charakter wyłącznie edukacyjny, badawczy i hobbystyczny. Program służy do wspomagania planowania podróży i **nie dokonuje zakupu biletów ani nie rezerwuje miejsc na stałe** (działa w trybie odczytu / read-only). Projekt nie jest w żaden sposób powiązany z żadnym przewoźnikiem kolejowym.
