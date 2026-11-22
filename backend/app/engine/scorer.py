"""
Scoring Functions and Weight Profiles for PKP Seat Optimizer.

Implements mathematical penalty/reward formulas:
- BlockCost with convex continuity reward (gamma = 1.6) and final leg bonus (W_final)
- TransferPenalty with quadratic carriage distance and physical obstacles
- 3 distinct optimization profiles (Balanced, Minimal Walking, Minimal Transfers)
"""
from typing import Optional
from backend.app.models.domain import Seat, TrainTopology, OptimizationProfile
from backend.app.engine.topology import calculate_physical_distance

W_TICKET: float = 1000.0

PROFILE_MULTIPLIERS = {
    OptimizationProfile.BALANCED: (1.0, 1.0),
    OptimizationProfile.MINIMAL_WALKING: (4.0, 1.0),
    OptimizationProfile.MINIMAL_TRANSFERS: (0.5, 3.0),
}


def get_profile_multipliers(profile: OptimizationProfile) -> tuple[float, float]:
    """Return (distance_multiplier, base_transfer_multiplier) for the given optimization profile."""
    return PROFILE_MULTIPLIERS.get(profile, (1.0, 1.0))


class ScorerConfig:
    def __init__(
        self,
        profile: OptimizationProfile = OptimizationProfile.BALANCED,
        w_cont: float = 1.0,
        w_final: float = 1.5,
        w_stand: float = 50.0,
        p_base: float = 120.0,
        gamma: float = 1.6,
    ):
        self.profile = profile
        self.w_cont = w_cont
        self.w_final = w_final
        self.w_stand = w_stand
        self.p_base = p_base
        self.gamma = gamma
        self.distance_multiplier, self.base_transfer_multiplier = get_profile_multipliers(profile)


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

    if seat.is_standing_virtual:
        return config.w_stand * t * (1.0 + t / 30.0)

    continuity_reward = config.w_cont * t_total * ((t / t_total) ** config.gamma)
    final_bonus = (config.w_final * t) if is_final_leg else 0.0

    return -continuity_reward - final_bonus


def calculate_transfer_penalty(
    seat_a: Seat,
    seat_b: Seat,
    topology: Optional[TrainTopology],
    config: ScorerConfig,
) -> float:
    """Computes cost of physical seat transfer."""
    if seat_a.id == seat_b.id:
        return 0.0

    base_penalty = config.p_base * config.base_transfer_multiplier
    distance_cost = calculate_physical_distance(seat_a, seat_b, topology) * config.distance_multiplier

    return base_penalty + distance_cost
