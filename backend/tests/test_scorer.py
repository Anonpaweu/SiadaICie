"""Tests for Scorer and optimization profiles."""
import pytest
from backend.app.models.domain import Seat, OptimizationProfile
from backend.app.engine.scorer import (
    ScorerConfig,
    calculate_block_cost,
    calculate_transfer_penalty,
)


def test_continuity_exponent_gamma_reward():
    config = ScorerConfig()
    seat = Seat(id="W8_M11", carriage_number=8, seat_number="11")
    # Single 120-min block should produce a larger negative cost (better reward)
    # than two 60-min blocks due to gamma = 1.6
    cost_120 = calculate_block_cost(seat, 120, 120, True, config)
    cost_60a = calculate_block_cost(seat, 60, 120, False, config)
    cost_60b = calculate_block_cost(seat, 60, 120, True, config)
    assert cost_120 < (cost_60a + cost_60b)


def test_minimal_walking_multiplies_distance_cost():
    balanced = ScorerConfig(profile=OptimizationProfile.BALANCED)
    minimal_walking = ScorerConfig(profile=OptimizationProfile.MINIMAL_WALKING)

    s1 = Seat(id="W8_M11", carriage_number=8, seat_number="11")
    s2 = Seat(id="W2_M55", carriage_number=2, seat_number="55")

    penalty_bal = calculate_transfer_penalty(s1, s2, None, balanced)
    penalty_min_walk = calculate_transfer_penalty(s1, s2, None, minimal_walking)

    assert penalty_min_walk > penalty_bal * 2.5

