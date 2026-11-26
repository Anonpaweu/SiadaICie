"""Unit tests for Non-GRM scanner ("tryb bez schematu wagonu")."""
import pytest

from backend.app.scraper.non_grm_scanner import (
    parse_sprawdz_ceny_entry,
    plan_non_grm_legs,
)
from backend.app.scraper.route_resolver import StationStop


class TestParseSprawdzCenyEntry:
    def test_standard_seats_available_when_komunikaty_empty(self):
        ceny = [
            {"klasa": 1, "cena": 9900, "komunikaty": [], "komunikatTekst": ""},
            {"klasa": 2, "cena": 1820, "komunikaty": [], "komunikatTekst": ""},
        ]
        res = parse_sprawdz_ceny_entry(ceny, 2)
        assert res["has_standard_seats"] is True
        assert res["standing_status"] is None
        assert res["price_pln"] == 18.20

    def test_code_47_classifies_as_special_only(self):
        ceny = [
            {
                "klasa": 2,
                "cena": 4940,
                "komunikatTekst": "Brak miejsc ogólnodostępnych. Dostępne miejsca specjalne ",
                "komunikaty": [{"komunikatKod": 47}],
            }
        ]
        res = parse_sprawdz_ceny_entry(ceny, 2)
        assert res["has_standard_seats"] is False
        assert res["standing_status"] == "SPECIAL_ONLY"
        assert res["price_pln"] == 49.40

    def test_code_48_classifies_as_available_standing(self):
        ceny = [
            {
                "klasa": 2,
                "cena": 9685,
                "komunikatTekst": "Nie ma już miejsc ogólnodostępnych. Można kupić bilet bez gwarancji miejsca siedzącego.",
                "komunikaty": [{"komunikatKod": 48}],
            }
        ]
        res = parse_sprawdz_ceny_entry(ceny, 2)
        assert res["has_standard_seats"] is False
        assert res["standing_status"] == "AVAILABLE"

    def test_missing_or_zero_price_classifies_as_overbooked(self):
        ceny = [{"klasa": 2, "cena": 0, "komunikatTekst": "Brak miejsc", "komunikaty": [{"komunikatKod": 51}]}]
        res = parse_sprawdz_ceny_entry(ceny, 2)
        assert res["has_standard_seats"] is False
        assert res["standing_status"] == "OVERBOOKED"


class TestPlanNonGrmLegs:
    def _sample_stops(self):
        return [
            StationStop(seq=0, eva_code="5100069", name="Wrocław Główny", arrival_min=0, departure_min=0, departure_time="14:37"),
            StationStop(seq=1, eva_code="5102560", name="Oława", arrival_min=18, departure_min=19, departure_time="14:55"),
            StationStop(seq=2, eva_code="5100046", name="Opole Główne", arrival_min=46, departure_min=48, departure_time="15:25"),
            StationStop(seq=3, eva_code="5100237", name="Lubliniec", arrival_min=80, departure_min=81, departure_time="15:58"),
            StationStop(seq=4, eva_code="5100007", name="Częstochowa", arrival_min=107, departure_min=110, departure_time="16:27"),
            StationStop(seq=5, eva_code="5100065", name="Warszawa Centralna", arrival_min=243, departure_min=243, arrival_time="18:40"),
        ]

    @pytest.mark.asyncio
    async def test_fast_path_full_route_single_leg(self):
        stops = self._sample_stops()
        short_codes = {s.eva_code: 100 + s.seq for s in stops}

        async def mock_check(pairs):
            return {
                pid: {"has_standard_seats": True, "standing_status": None, "price_pln": 49.40, "message": ""}
                for (pid, _, _, _) in pairs
            }

        plan = await plan_non_grm_legs(stops, short_codes, {}, "IC 6138", "IC", "2026-11-27", 2, mock_check)
        assert plan["fast_path_used"] is True
        assert len(plan["legs"]) == 1
        assert plan["legs"][0]["start_idx"] == 0
        assert plan["legs"][0]["end_idx"] == 5
        assert plan["legs"][0]["is_standing"] is False

    @pytest.mark.asyncio
    async def test_greedy_split_finds_longest_seated_legs_and_merges_gap(self):
        stops = self._sample_stops()
        short_codes = {s.eva_code: 100 + s.seq for s in stops}

        # Bottleneck on Opole (2) -> Lubliniec (3) and Lubliniec (3) -> Częstochowa (4)
        async def mock_check(pairs):
            out = {}
            for pid, _dep, f_sid, t_sid in pairs:
                f_idx = f_sid - 100
                t_idx = t_sid - 100
                # Seated iff interval does NOT overlap [2, 4]
                if t_idx <= 2 or f_idx >= 4:
                    out[pid] = {"has_standard_seats": True, "standing_status": None, "price_pln": 25.0, "message": ""}
                else:
                    out[pid] = {"has_standard_seats": False, "standing_status": "SPECIAL_ONLY", "price_pln": 30.0, "message": "special"}
            return out

        plan = await plan_non_grm_legs(stops, short_codes, {}, "IC 6138", "IC", "2026-11-27", 2, mock_check)
        assert plan["fast_path_used"] is False
        assert plan["full_route_standing_status"] == "SPECIAL_ONLY"
        legs = plan["legs"]
        assert len(legs) == 3
        assert (legs[0]["start_idx"], legs[0]["end_idx"], legs[0]["is_standing"]) == (0, 2, False)
        assert (legs[1]["start_idx"], legs[1]["end_idx"], legs[1]["is_standing"], legs[1]["standing_status"]) == (2, 4, True, "SPECIAL_ONLY")
        assert (legs[2]["start_idx"], legs[2]["end_idx"], legs[2]["is_standing"]) == (4, 5, False)
