"""Tests for the humidity-aware scoring helpers (Phase 7 / Psych Level 1)."""

from __future__ import annotations

import pytest

from custom_components.multisplit_zone_controller.comfort import humidity_priority
from custom_components.multisplit_zone_controller.models import (
    HumidityPolicy,
    HVACMode,
)


def test_default_policy_returns_zero() -> None:
    """Backward compat: an unconfigured humidity policy must contribute nothing."""
    policy = HumidityPolicy()
    assert humidity_priority(policy, HVACMode.COOL, 70.0) == 0.0
    assert humidity_priority(policy, HVACMode.HEAT, 30.0) == 0.0


def test_returns_zero_when_humidity_unknown() -> None:
    policy = HumidityPolicy(target_humidity=50.0, weight=1.0)
    assert humidity_priority(policy, HVACMode.COOL, None) == 0.0


def test_inside_tolerance_band_contributes_nothing() -> None:
    policy = HumidityPolicy(target_humidity=50.0, tolerance_band=10.0, weight=1.0)
    # Band is target +/- (tolerance_band/2) = 50 +/- 5 = [45, 55]
    assert humidity_priority(policy, HVACMode.COOL, 47.0) == 0.0
    assert humidity_priority(policy, HVACMode.COOL, 53.0) == 0.0


def test_excess_outside_band_scaled_by_weight() -> None:
    policy = HumidityPolicy(target_humidity=50.0, tolerance_band=10.0, weight=0.2)
    # Humidity 60 -> deviation 10, excess beyond band/2=5 is 5; scaled by weight 0.2 = 1.0
    assert humidity_priority(policy, HVACMode.COOL, 60.0) == pytest.approx(1.0)


def test_dehumidify_bonus_only_in_cool_mode() -> None:
    policy = HumidityPolicy(
        dehumidify_threshold=60.0, dehumidify_bonus=2.0
    )
    assert humidity_priority(policy, HVACMode.COOL, 70.0) == pytest.approx(2.0)
    assert humidity_priority(policy, HVACMode.HEAT, 70.0) == 0.0


def test_dehumidify_bonus_inactive_below_threshold() -> None:
    policy = HumidityPolicy(dehumidify_threshold=60.0, dehumidify_bonus=2.0)
    assert humidity_priority(policy, HVACMode.COOL, 55.0) == 0.0


def test_combined_weight_and_bonus_add() -> None:
    policy = HumidityPolicy(
        target_humidity=50.0,
        tolerance_band=10.0,
        weight=0.2,           # excess scoring active
        dehumidify_threshold=60.0,
        dehumidify_bonus=2.0,  # cool-mode bias active
    )
    # Humidity 65 in COOL: excess (65-50-5)=10 * 0.2 = 2.0, plus 2.0 bonus = 4.0
    assert humidity_priority(policy, HVACMode.COOL, 65.0) == pytest.approx(4.0)


def test_does_not_go_negative_below_target() -> None:
    """A humidity drier than target should not subtract from comfort priority."""
    policy = HumidityPolicy(target_humidity=50.0, tolerance_band=10.0, weight=1.0)
    # Even far below target, humidity_priority is non-negative.
    val = humidity_priority(policy, HVACMode.HEAT, 30.0)
    assert val >= 0.0
    # In fact, |30-50|=20, excess beyond 5 = 15, scaled by 1.0 = 15. Verifies symmetry.
    assert val == pytest.approx(15.0)
