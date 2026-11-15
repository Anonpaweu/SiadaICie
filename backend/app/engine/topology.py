"""
Train Topology and Physical Distance Calculator.

Models physical train layout:
- Carriage sequence order
- Compartment distances
- Dining car (WARS) crossing penalties
- Disconnected multiple-unit trains (EZT unit separation)
"""
from typing import Optional, Any
from backend.app.models.domain import Seat, TrainTopology, Carriage


def get_compartment(seat_num: Any) -> int:
    """
    Computes PKP compartment number from seat number.
    In PKP carriages, two-digit numbers (e.g. 15, 53, 82, 84):
    tens digit is the compartment number (e.g. 81..88 -> compartment 8).
    For seats >= 100 (e.g. 106), first two digits (10) are the compartment number.
    """
    try:
        s_int = int(str(seat_num).strip())
        if s_int >= 10:
            return s_int // 10
        return 1
    except Exception:
        return 1


def calculate_distance_penalty(seat_a: Any, seat_b: Any) -> float:
    """
    Physical distance penalty between two seats:
    - Same carriage and compartment: 15.0
    - Same carriage, different compartment: 40.0
    - Different carriage: 100.0 * (delta_w ** 2)
    """
    c_a = getattr(seat_a, 'carriage_no', getattr(seat_a, 'carriage_number', 0))
    c_b = getattr(seat_b, 'carriage_no', getattr(seat_b, 'carriage_number', 0))
    comp_a = getattr(seat_a, 'compartment_no', getattr(seat_a, 'compartment_id', None))
    comp_b = getattr(seat_b, 'compartment_no', getattr(seat_b, 'compartment_id', None))
    delta_c = abs(c_a - c_b)
    if delta_c == 0:
        if comp_a is not None and comp_b is not None and comp_a == comp_b:
            return 15.0
        return 40.0
    return 100.0 * (delta_c ** 2)


def calculate_physical_distance(
    seat_a: Seat,
    seat_b: Seat,
    topology: Optional[TrainTopology] = None,
) -> float:
    """
    Calculates physical walking cost between two seats.
    """
    if seat_a.is_standing_virtual or seat_b.is_standing_virtual:
        return 100.0

    if seat_a.id == seat_b.id:
        return 0.0

    # Determine sequence indices of carriages
    seq_a = seat_a.carriage_number
    seq_b = seat_b.carriage_number
    unit_a = 1
    unit_b = 1

    if topology:
        c_a = topology.get_carriage(seat_a.carriage_number)
        c_b = topology.get_carriage(seat_b.carriage_number)
        if c_a:
            seq_a = c_a.sequence_index
            unit_a = c_a.unit_group_id
        if c_b:
            seq_b = c_b.sequence_index
            unit_b = c_b.unit_group_id

        # Check disconnected EZT units
        if unit_a != unit_b:
            return 600.0

        for disc_1, disc_2 in topology.disconnected_carriages:
            low, high = min(disc_1, disc_2), max(disc_1, disc_2)
            if min(seat_a.carriage_number, seat_b.carriage_number) <= low and max(seat_a.carriage_number, seat_b.carriage_number) >= high:
                return 600.0

    delta_c = abs(seq_a - seq_b)

    # Same carriage
    if delta_c == 0:
        comp_a = seat_a.compartment_id or 1
        comp_b = seat_b.compartment_id or 1
        if comp_a == comp_b:
            return 15.0  # Adjacent seat in same compartment/row
        return 40.0 + 3.0 * abs(comp_a - comp_b)

    # Different carriages (quadratic penalty)
    cost = 85.0 * delta_c + 25.0 * (delta_c ** 2)

    # Crossing WARS dining car
    if topology and topology.restaurant_carriage_number is not None:
        r_car = topology.restaurant_carriage_number
        if min(seat_a.carriage_number, seat_b.carriage_number) < r_car < max(seat_a.carriage_number, seat_b.carriage_number):
            cost += 50.0

    return cost

