# PKP Seat Optimizer
> *Koniec z koczowaniem na korytarzu przy toalecie. Usiądź jak człowiek.*

[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg)](https://fastapi.tiangolo.com)
[![Playwright](https://img.shields.io/badge/Playwright-Async-45ba4b.svg)](https://playwright.dev/python/)
[![License: MIT](https://img.shields.io/badge/license-MIT-orange.svg)]()

---

## O co w tym w ogóle chodzi?

Znasz to uczucie? Jest niedzielne popołudnie, koniec długiego weekendu albo święta. Chcesz kupić bilet na pociąg przez pół Polski (np. z Warszawy do Wrocławia przez Koluszki, Piotrków, Częstochowę i Opole). Wchodzisz do systemu, a tam czerwony komunikat: **„Brak wolnych miejsc do siedzenia”**. Do wyboru masz bilet bez gwarancji miejsca (czyli 5 godzin siedzenia na walizce obok wagonowego kibla) albo rezygnację z podróży.

**Ale kolej to nie samolot!** Pociąg zatrzymuje się po drodze na kilkunastu stacjach. Ludzie nieustannie wsiadają i wysiadają:
- Ktoś wysiada w Koluszkach – jego fotel zwalnia się na resztę trasy.
- Ktoś inny wsiada dopiero w Opolu – do Opola to miejsce stoi puste.

System kolejowy przy próbie zakupu biletu na **całą relację** mówi „nie ma miejsc”, bo żaden pojedynczy fotel nie jest wolny od stacji początkowej do samego końca. Ale wystarczy kupić **dwa lub trzy bilety cząstkowe** na kolejne odcinki i po prostu w trakcie podróży przesiąść się na sąsiedni fotel – i całą trasę pokonujesz wygodnie na siedząco.

**PKP Seat Optimizer** robi za Ciebie całą czarną robotę, której nikomu nie chciałoby się przeklikiwać ręcznie: w kilkadziesiąt sekund skanuje rozkład, wchodzi na schematy wszystkich wagonów, zdejmuje wolne fotele i układa matematycznie optymalną podróż z minimalną liczbą przesiadek.

---

## Co potrafi aplikacja?

- **Szybkie skanowanie na żywo (Playwright + in-page V8 JS):**
  Zamiast powolnego klikania, scraper wyciąga mapę wolnych foteli bezpośrednio w silniku JavaScript V8 przeglądarki. Parsowanie całego wagonu trwa **mniej niż 5 milisekund**, a wieloetapowa trasa przez pół kraju analizowana jest równolegle w około 40 sekund. Obsługuje wagony **Klasy 2** oraz **Klasy 1**.
- **Matematyczny silnik optymalizacji (DAG + Programowanie Dynamiczne):**
  Algorytm nie rzuca losowych foteli. Buduje skierowany graf acykliczny (DAG) i optymalizuje podróż według realnej wygody:
  - **Maksymalny bonus** za siedzenie na jednym fotelu przez całą trasę (zero przesiadek).
  - **Minimalny koszt spaceru:** gdy przesiadka jest konieczna, algorytm szuka wolnego miejsca w **tym samym przedziale** lub **tym samym wagonie**, żebyś nie musiał biegać z bagażami przez 8 wagonów.
  - **3 profile optymalizacji do wyboru:** *Zrównoważona*, *Najmniej przesiadek* oraz *Najkrótsze spacery*.
  - Wykrywanie rozłącznych jednostek EZT (np. Dart / Flirt) – algorytm wie, kiedy przejście między członami jest fizycznie niemożliwe.
- **Gotowy „Plan Zakupu Biletów”:**
  Aplikacja wyświetla jasną instrukcję krok po kroku dla wybranego wariantu:
  *„Bilet 1: Warszawa -> Częstochowa (Wagon 5, Miejsce 82) • Bilet 2: Częstochowa -> Wrocław (Wagon 5, Miejsce 88) — przesiadka na sąsiedni fotel w tym samym wagonie”*.
- **Wizualna tablica dostępności (Wykres Gantta):**
  Przejrzysta siatka wszystkich foteli i wagonów z podziałem na przedziały oraz odcinki trasy. Zobacz na własne oczy, gdzie były wolne miejsca i jak przebiega wybrany wariant.
- **Radar nocnej przerwy serwisowej:**
  Automatycznie wykrywa codzienne okno konserwacyjne systemów kolejowych (**23:45 – 00:30**, a na przełomie miesięcy do **01:00**) i dyskretnie ostrzega użytkownika, gdy serwery mogą mieć przerwę techniczną.
- **Tryb edytora tekstowego (Offline):**
  Możesz wkleić własną rozpiskę wolnych foteli z notatek i natychmiast przeliczyć optymalną trasę bez łączenia się ze stroną www.

---

## Architektura projektu

```text
IccBagno/
├── backend/
│   ├── app/
│   │   ├── engine/          # Parsowanie dostępności, profile wag i topologia pociągu
│   │   ├── optimizer/       # Solwer grafowy DAG + Programowanie Dynamiczne
│   │   ├── scraper/         # Playwright scraper (silnik V8 JS, radar błędów, cache)
│   │   ├── db/              # Baza SQLite (auto-inicjalizacja i seed stacji przy starcie)
│   │   ├── models/          # Schematy Pydantic i modele domenowe
│   │   └── main.py          # Aplikacja FastAPI (REST API + serwowanie UI)
│   ├── static/
│   │   └── index.html       # Interfejs (Tailwind CSS, Vanilla JS)
│   ├── tests/               # Zestaw testów jednostkowych i integracyjnych (pytest)
│   └── requirements.txt     # Zależności Pythona
├── start_server.bat         # Launcher serwera dla systemu Windows
└── README.md
```

---

## Szybki start (Instalacja i uruchomienie)

### Wymagania:
- **Python 3.10+** (zalecany Python 3.12)
- Zainstalowana przeglądarka Chromium dla Playwrighta

### 1. Sklonuj repozytorium:
```bash
git clone https://github.com/Anonpaweu/Icc-Seat-Finder.git
cd Icc-Seat-Finder
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

### 3. Odpal aplikację
- **Na Windows:** po prostu kliknij dwukrotnie w plik:
  ```text
  start_server.bat
  ```
- **Albo ręcznie z konsoli:**
  ```bash
  python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
  ```

Przy pierwszym uruchomieniu serwer automatycznie utworzy bazę SQLite i pobierze słownik oficjalnych stacji.
Otwórz w przeglądarce: **[http://localhost:8000](http://localhost:8000)**.

---

## Uruchamianie testów

Wszystkie kluczowe moduły (algorytm optymalizatora, profile, topologia wagonów, parsowanie DOM, odporność na błędy serwera, cache SQLite) są pokryte testami:

```bash
pytest backend/tests/ -v
```

---

## Jak tego używać?

1. **Wpisz stację odjazdu i docelową** – np. *Koluszki* do *Opole Główne* (inteligentna wyszukiwarka podpowie oficjalne nazwy węzłów).
2. **Wybierz datę i godzinę** – system domyślnie podpowiada aktualny czas z Twojego komputera i blokuje omyłkowy wybór dat z przeszłości.
3. **Kliknij „Szukaj połączeń”** – lista automatycznie wybierze najbliższy pociąg po wskazanej godzinie (np. 1646).
4. **Wybierz klasę (2 lub 1) oraz strategię optymalizacji** i kliknij **„Wyszukaj i optymalizuj miejsca”**:
   - Aplikacja natychmiast pokaże listę stacji pośrednich z godzinami.
   - Skaner sprawdzi wagony wybranej klasy na każdym etapie podróży.
   - Na koniec otrzymasz 3 zróżnicowane warianty podróży, gotowy plan zakupu biletów oraz kolorową mapę zajętości miejsc.
5. Idziesz na oficjalną stronę przewoźnika lub do kasy biletowej, kupujesz bilety według wygenerowanej listy i podróżujesz na siedząco.

---

## Zastrzeżenie prawne (Disclaimer)

Projekt ma charakter wyłącznie edukacyjny, badawczy i hobbystyczny. Program służy do wspomagania planowania podróży i **nie dokonuje zakupu biletów ani nie rezerwuje miejsc na stałe** (działa w 100% w trybie odczytu / read-only). Projekt nie jest w żaden sposób powiązany z żadnym przewoźnikiem kolejowym.
