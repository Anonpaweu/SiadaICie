"""
Scoring Functions and Weight Profiles for PKP Seat Optimizer.

Implements mathematical penalty/reward formulas from architecture documentation:
- BlockCost with convex continuity reward (gamma = 1.6) and final leg bonus (W_final)
- TransferPenalty with quadratic carriage distance and physical obstacles
- 3 distinct optimization profiles (Balanced, Minimal Walking, Minimal Transfers)
"""
from typing import Optional
from backend.app.models.domain import Seat, TrainTopology, OptimizationProfile
from backend.app.engine.topology import calculate_physical_distance


class ScorerConfig:
    def __init__(
        self,
        profile: OptimizationProfile = OptimizationProfile.BALANCED,
        w_cont: float = 1.0,
        w_final: float = 1.5,
        w_stand: float = 50.0,
        p_base: float = 120.0,
        gamma: float = 1.6,
        prefer_window: bool = False,
    ):
        self.profile = profile
        self.w_cont = w_cont
        self.w_final = w_final
        self.w_stand = w_stand
        self.p_base = p_base
        self.gamma = gamma
        self.prefer_window = prefer_window

        # Apply profile multipliers
        if profile == OptimizationProfile.MINIMAL_WALKING:
            self.distance_multiplier = 4.0
            self.base_transfer_multiplier = 1.0
        elif profile == OptimizationProfile.MINIMAL_TRANSFERS:
            self.distance_multiplier = 0.5
            self.base_transfer_multiplier = 3.0
        else:  # BALANCED
            self.distance_multiplier = 1.0
            self.base_transfer_multiplier = 1.0


def calculate_block_cost(
    seat: Seat,
    duration_minutes: int,
    total_trip_minutes: int,
    is_final_leg: bool,
    config: ScorerConfig,
) -> float:
    """
    Computes total penalty/reward for occupying a seat continuously for duration_minutes.
    Negative values indicate high desirability (reward).
    """
    t = float(duration_minutes)
    t_total = max(1.0, float(total_trip_minutes))

    # Standing corridor case
    if seat.is_standing_virtual:
        return config.w_stand * t * (1.0 + t / 30.0)

    # Base preference penalty
    pref_penalty = 0.0
    if config.prefer_window and seat.position_type.value != "WINDOW":
        pref_penalty = 0.2 * t

    # Continuity reward with exponent gamma = 1.6
    continuity_reward = config.w_cont * t_total * ((t / t_total) ** config.gamma)

    # Final destination bonus (peace of mind till destination)
    final_bonus = (config.w_final * t) if is_final_leg else 0.0

    return pref_penalty - continuity_reward - final_bonus


def calculate_transfer_penalty(
    seat_a: Seat,
    seat_b: Seat,
    topology: Optional[TrainTopology],
    config: ScorerConfig,
) -> float:
    """
    Computes cost of physical seat transfer.
    """
    if seat_a.id == seat_b.id:
        return 0.0

    base_penalty = config.p_base * config.base_transfer_multiplier
    distance_cost = calculate_physical_distance(seat_a, seat_b, topology) * config.distance_multiplier

    return base_penalty + distance_cost

