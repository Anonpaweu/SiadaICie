"""Tests for TrainTopology and physical distance calculator."""
import pytest
from backend.app.models.domain import Seat, TrainTopology, Carriage
from backend.app.engine.topology import calculate_physical_distance


def test_same_compartment_minimal_penalty():
    s1 = Seat(id="W8_M11", carriage_number=8, seat_number="11", compartment_id=1)
    s2 = Seat(id="W8_M12", carriage_number=8, seat_number="12", compartment_id=1)
    dist = calculate_physical_distance(s1, s2)
    assert dist == 15.0


def test_same_carriage_different_compartment():
    s1 = Seat(id="W8_M11", carriage_number=8, seat_number="11", compartment_id=1)
    s2 = Seat(id="W8_M55", carriage_number=8, seat_number="55", compartment_id=5)
    dist = calculate_physical_distance(s1, s2)
    assert dist == 40.0 + 3.0 * 4  # 52.0


def test_adjacent_carriage_distance():
    s1 = Seat(id="W8_M11", carriage_number=8, seat_number="11")
    s2 = Seat(id="W7_M24", carriage_number=7, seat_number="24")
    dist = calculate_physical_distance(s1, s2)
    # delta_c = 1: 85 * 1 + 25 * 1 = 110.0
    assert dist == 110.0


def test_disconnected_ezt_units():
    topology = TrainTopology(
        train_number="ED160",
        travel_date="2026-11-15",
        carriages=[
            Carriage(carriage_number=8, sequence_index=7, unit_group_id=1),
            Carriage(carriage_number=9, sequence_index=8, unit_group_id=2),
        ],
        disconnected_carriages=[(8, 9)],
    )
    s1 = Seat(id="W8_M11", carriage_number=8, seat_number="11")
    s2 = Seat(id="W9_M24", carriage_number=9, seat_number="24")
    dist = calculate_physical_distance(s1, s2, topology)
    assert dist == 600.0  # Massive penalty requiring walking via station platform


def test_pkp_compartment_numbering():
    """
    Task 030: Checks that seats 81, 82, 83, 88 belong to compartment 8,
    and seat 106 belongs to compartment 10.
    """
    from backend.app.engine.topology import get_compartment

    for s in ["81", "82", "83", "88", 81, 82, 83, 88]:
        assert get_compartment(s) == 8, f"Seat {s} should be in compartment 8"

    assert get_compartment("106") == 10
    assert get_compartment(106) == 10
    assert get_compartment("15") == 1
    assert get_compartment("53") == 5

