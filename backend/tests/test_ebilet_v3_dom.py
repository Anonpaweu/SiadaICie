"""
Unit and integration tests for Plan V3 / V3.5 DOM and seatmap parsing.
Uses the real HTML snapshots from 'screeny i html krok po kroku strony icc'.
"""
import bs4
import re
from pathlib import Path
import pytest

from backend.app.scraper.ebilet_seat_scraper import EbiletSeatScraper

SNAPSHOTS_DIR = Path("screeny i html krok po kroku strony icc")


class TestEbiletV3RealDOM:
    """Tests parsing on the user-provided real e-IC 2.0 HTML snapshots."""

    @pytest.fixture
    def scraper(self):
        return EbiletSeatScraper(headless=True)

    def test_step2_connections_list_parsing(self):
        """Verifies finding train cards and buy buttons on real Step 2 HTML."""
        fpath = SNAPSHOTS_DIR / "2-e-IC2 - Internetowa rezerwacja miejsc.html"
        assert fpath.exists()
        soup = bs4.BeautifulSoup(fpath.read_text(encoding="utf-8", errors="ignore"), "html.parser")
        
        cards = soup.select("li[class*='TripPropositionDesktop_tripPropositionDesktop__container']")
        assert len(cards) > 0
        
        # Train card should contain departure time 12:17 and buy button
        first_card = cards[0]
        card_text = first_card.text
        assert "12:17" in card_text
        
        buy_btn = first_card.select_one("button[data-testid='OutlineButton'], button[aria-label*='Kup bilet']")
        assert buy_btn is not None
        assert "Kup bilet" in buy_btn.get("aria-label", "")

    def test_step3_class_2_selection(self):
        """Verifies finding the Class 2 selection button on real Step 3 HTML."""
        fpath = SNAPSHOTS_DIR / "3-e-IC2 - Internetowa rezerwacja miejsc.html"
        assert fpath.exists()
        soup = bs4.BeautifulSoup(fpath.read_text(encoding="utf-8", errors="ignore"), "html.parser")
        
        c2_btns = [b for b in soup.find_all("button") if "2 klas" in b.text.lower() or "2 klas" in str(b.get("aria-label", "")).lower()]
        assert len(c2_btns) > 0
        btn = c2_btns[0]
        assert "Wybierz 2 klasę" in btn.text or "Wybierz 2 klasę" in btn.get("aria-label", "")

    def test_step4_schema_button(self):
        """Verifies finding the button opening seatmap on real Step 4 HTML."""
        fpath = SNAPSHOTS_DIR / "4-e-IC2 - Internetowa rezerwacja miejsc.html"
        assert fpath.exists()
        soup = bs4.BeautifulSoup(fpath.read_text(encoding="utf-8", errors="ignore"), "html.parser")
        
        schema_btns = [b for b in soup.find_all("button") if "miejsce" in b.text.lower() or "schemat" in b.text.lower()]
        assert len(schema_btns) > 0
        assert any("Wybierz miejsce" in b.text for b in schema_btns)

    def test_step6_wagon_slides_and_class_filtering(self):
        """Verifies identifying all 8 wagons and filtering Class 2 sitting cars."""
        fpath = SNAPSHOTS_DIR / "6-e-IC2 - Internetowa rezerwacja miejsc.html"
        assert fpath.exists()
        soup = bs4.BeautifulSoup(fpath.read_text(encoding="utf-8", errors="ignore"), "html.parser")
        
        slides = soup.select("div[class*='Carriage_carriageSlide']")
        assert len(slides) == 8

        class2_wagons = []
        for s in slides:
            k_el = s.select_one("p[class*='Carriage_carriageKlasa']")
            n_el = s.select_one("p[class*='Carriage_carriageNumberDesktop']")
            klasa = k_el.text.strip() if k_el else ""
            num = n_el.text.strip() if n_el else ""
            if "klasa 2" in klasa.lower():
                m = re.search(r'\d+', num)
                if m:
                    class2_wagons.append(m.group(0))

        # Wagons 3, 4, 5, 6, 7, 8 are Class 2 (Wagons 1 & 2 are Class 1)
        assert class2_wagons == ["3", "4", "5", "6", "7", "8"]

    def test_seatmap_iframe_free_seats_extraction(self):
        """Verifies extracting exact available seat numbers from saved seatmap iframe."""
        fpath = SNAPSHOTS_DIR / "6-e-IC2 - Internetowa rezerwacja miejsc_files" / "saved_resource(1).html"
        assert fpath.exists()
        soup = bs4.BeautifulSoup(fpath.read_text(encoding="utf-8", errors="ignore"), "html.parser")
        
        free_groups = [g for g in soup.find_all("g") if "wolne" in str(g.get("aria-label", "")).lower()]
        assert len(free_groups) == 7

        seats = []
        for g in free_groups:
            aria = g.get("aria-label", "")
            t_el = g.find("text")
            num = t_el.text.strip() if t_el else ""
            if not num:
                m = re.search(r'miejsce\s+(\d+)', aria, re.IGNORECASE)
                if m: num = m.group(1)
            seats.append(num)

        assert sorted(seats, key=lambda x: int(x)) == ["14", "17", "24", "28", "38", "43", "48"]

    def test_json_recursive_extractor(self, scraper):
        """Verifies _extract_seats_from_json_list with nested PKP IC payload."""
        payload = [{
            "data": {
                "carriages": [
                    {
                        "number": "3",
                        "class": "2",
                        "type": "B10bmnouz",
                        "seats": [
                            {"number": "14", "status": "FREE"},
                            {"number": "17", "status": "AVAILABLE"},
                            {"number": "18", "status": "OCCUPIED"},
                        ]
                    },
                    {
                        "number": "1",
                        "class": "1",
                        "seats": [{"number": "11", "status": "FREE"}]
                    },
                    {
                        "number": "5",
                        "class": "2",
                        "type": "WR",  # Restaurant car to skip
                        "seats": [{"number": "21", "status": "FREE"}]
                    }
                ]
            }
        }]

        result = scraper._extract_seats_from_json_list(payload)
        # Carriage 1 (Class 1) and 5 (WR) must be omitted
        assert "1" not in result
        assert "5" not in result
        assert "3" in result
        assert result["3"] == ["14", "17"]
