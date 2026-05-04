"""Tests for the rate estimator, lead-time calc, and EXPECTED timeout."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from custom_components.multisplit_zone_controller.models import (
    HVACMode,
    PreConditioningConfig,
)
from custom_components.multisplit_zone_controller.preconditioning import (
    RateEstimator,
    expected_timed_out,
    lead_time_minutes,
)


_NOW = datetime(2026, 5, 2, 22, 0, tzinfo=timezone.utc)


# --- RateEstimator -----------------------------------------------------------


def test_rate_estimator_returns_defaults_until_observed() -> None:
    cfg = PreConditioningConfig(default_heating_rate=0.7, default_cooling_rate=0.5)
    est = RateEstimator(cfg)
    assert est.heating_rate == 0.7
    assert est.cooling_rate == 0.5
    assert est.learned_heating_rate is None
    assert est.learned_cooling_rate is None


def test_rate_estimator_learns_heating_rate() -> None:
    cfg = PreConditioningConfig(rate_alpha=1.0)  # no smoothing for deterministic test
    est = RateEstimator(cfg)
    est.observe(_NOW, 18.0, HVACMode.HEAT)
    est.observe(_NOW + timedelta(minutes=10), 19.0, HVACMode.HEAT)
    # 1°C in 10 min = 0.1°C/min
    assert est.learned_heating_rate == pytest.approx(0.1)
    assert est.heating_rate == pytest.approx(0.1)


def test_rate_estimator_learns_cooling_rate() -> None:
    cfg = PreConditioningConfig(rate_alpha=1.0)
    est = RateEstimator(cfg)
    est.observe(_NOW, 26.0, HVACMode.COOL)
    est.observe(_NOW + timedelta(minutes=20), 24.0, HVACMode.COOL)
    # -2°C in 20 min = -0.1°C/min observed; cooling rate stored as 0.1
    assert est.learned_cooling_rate == pytest.approx(0.1)


def test_rate_estimator_ignores_wrong_direction_observations() -> None:
    """Heating mode but temperature went down -> ignore (likely sensor anomaly)."""
    cfg = PreConditioningConfig(rate_alpha=1.0)
    est = RateEstimator(cfg)
    est.observe(_NOW, 22.0, HVACMode.HEAT)
    est.observe(_NOW + timedelta(minutes=5), 21.0, HVACMode.HEAT)
    assert est.learned_heating_rate is None


def test_rate_estimator_ignores_off_and_other_modes() -> None:
    cfg = PreConditioningConfig(rate_alpha=1.0)
    est = RateEstimator(cfg)
    est.observe(_NOW, 18.0, HVACMode.OFF)
    est.observe(_NOW + timedelta(minutes=5), 19.0, HVACMode.OFF)
    assert est.learned_heating_rate is None


def test_rate_estimator_smooths_via_ema() -> None:
    cfg = PreConditioningConfig(rate_alpha=0.5)
    est = RateEstimator(cfg)
    est.observe(_NOW, 18.0, HVACMode.HEAT)
    est.observe(_NOW + timedelta(minutes=10), 19.0, HVACMode.HEAT)  # rate 0.1
    est.observe(_NOW + timedelta(minutes=20), 20.0, HVACMode.HEAT)  # rate 0.1
    # With alpha=0.5: first sample sets to 0.1; second EMA = 0.5*0.1 + 0.5*0.1 = 0.1
    assert est.learned_heating_rate == pytest.approx(0.1)


def test_rate_estimator_skips_missing_temperatures() -> None:
    cfg = PreConditioningConfig(rate_alpha=1.0)
    est = RateEstimator(cfg)
    est.observe(_NOW, None, HVACMode.HEAT)
    est.observe(_NOW + timedelta(minutes=5), 19.0, HVACMode.HEAT)
    est.observe(_NOW + timedelta(minutes=15), 20.0, HVACMode.HEAT)
    # Only the last two valid samples count: 1°C in 10 min = 0.1
    assert est.learned_heating_rate == pytest.approx(0.1)


# --- lead_time_minutes -------------------------------------------------------


def test_lead_time_returns_none_when_temps_missing() -> None:
    assert lead_time_minutes(None, 21.0, 0.5, 0.4, 60) is None
    assert lead_time_minutes(20.0, None, 0.5, 0.4, 60) is None


def test_lead_time_zero_when_already_at_target() -> None:
    assert lead_time_minutes(21.0, 21.0, 0.5, 0.4, 60) == 0.0


def test_lead_time_uses_heating_rate_when_warming_up() -> None:
    # 3°C gap at 0.5°C/min = 6 min
    assert lead_time_minutes(18.0, 21.0, 0.5, 0.4, 60) == pytest.approx(6.0)


def test_lead_time_uses_cooling_rate_when_cooling_down() -> None:
    # 4°C gap at 0.5°C/min = 8 min
    assert lead_time_minutes(28.0, 24.0, 0.4, 0.5, 60) == pytest.approx(8.0)


def test_lead_time_capped_at_max_lead() -> None:
    assert lead_time_minutes(10.0, 30.0, 0.1, 0.1, 30) == 30.0


def test_lead_time_returns_max_when_rate_is_zero() -> None:
    assert lead_time_minutes(18.0, 21.0, 0.0, 0.4, 45) == 45.0


# --- expected_timed_out ------------------------------------------------------


def test_expected_timeout_false_when_never_started() -> None:
    cfg = PreConditioningConfig(expected_timeout_minutes=30)
    assert expected_timed_out(None, _NOW, cfg) is False


def test_expected_timeout_false_within_window() -> None:
    cfg = PreConditioningConfig(expected_timeout_minutes=30)
    assert expected_timed_out(_NOW - timedelta(minutes=10), _NOW, cfg) is False


def test_expected_timeout_true_after_window() -> None:
    cfg = PreConditioningConfig(expected_timeout_minutes=30)
    assert expected_timed_out(_NOW - timedelta(minutes=45), _NOW, cfg) is True
