"""Tests for psychrometric helpers (Phase 8 / Psych Level 2)."""

from __future__ import annotations

import math

import pytest

from custom_components.multisplit_zone_controller.psychrometrics import (
    cooling_effect,
    dew_point,
    effective_comfort_temp,
    enthalpy,
    humidity_ratio,
    saturation_vapor_pressure,
    simple_pmv,
    vapor_pressure,
)


# --- saturation_vapor_pressure ----------------------------------------------


def test_saturation_vapor_pressure_at_freezing() -> None:
    """ASHRAE-style reference: ~0.611 kPa at 0 °C."""
    assert saturation_vapor_pressure(0.0) == pytest.approx(0.6108, abs=0.005)


def test_saturation_vapor_pressure_at_20c() -> None:
    """Reference value ~2.34 kPa at 20 °C."""
    assert saturation_vapor_pressure(20.0) == pytest.approx(2.34, abs=0.05)


def test_saturation_vapor_pressure_monotonic_increasing() -> None:
    prev = saturation_vapor_pressure(-10.0)
    for t in range(-5, 41, 5):
        cur = saturation_vapor_pressure(float(t))
        assert cur > prev
        prev = cur


# --- vapor_pressure / humidity_ratio ----------------------------------------


def test_vapor_pressure_scales_with_rh() -> None:
    es = saturation_vapor_pressure(25.0)
    assert vapor_pressure(25.0, 100.0) == pytest.approx(es)
    assert vapor_pressure(25.0, 50.0) == pytest.approx(es * 0.5)
    assert vapor_pressure(25.0, 0.0) == 0.0


def test_humidity_ratio_typical_indoor_values() -> None:
    """At 22 °C, 50% RH, atmospheric pressure: W ≈ 0.0083 kg/kg."""
    w = humidity_ratio(22.0, 50.0)
    assert w == pytest.approx(0.0083, abs=0.001)


# --- enthalpy ----------------------------------------------------------------


def test_enthalpy_at_22c_50rh_is_around_43_kj() -> None:
    """ASHRAE moist-air enthalpy ~43 kJ/kg at 22 °C and 50% RH."""
    w = humidity_ratio(22.0, 50.0)
    h = enthalpy(22.0, w)
    assert h == pytest.approx(43.0, abs=1.5)


def test_enthalpy_increases_with_temperature() -> None:
    w = humidity_ratio(20.0, 50.0)
    assert enthalpy(20.0, w) < enthalpy(30.0, w)


# --- dew_point --------------------------------------------------------------


def test_dew_point_equals_air_temp_at_100rh() -> None:
    assert dew_point(20.0, 100.0) == pytest.approx(20.0, abs=0.1)


def test_dew_point_below_air_temp_at_partial_rh() -> None:
    td = dew_point(25.0, 50.0)
    assert td < 25.0
    # Reference: 25 °C, 50% RH -> dew point ~13.8 °C
    assert td == pytest.approx(13.8, abs=0.5)


def test_dew_point_zero_rh_is_negative_infinity() -> None:
    assert dew_point(20.0, 0.0) == float("-inf")


# --- cooling_effect ---------------------------------------------------------


def test_cooling_effect_zero_at_no_air_movement() -> None:
    assert cooling_effect(0.0) == 0.0
    assert cooling_effect(-1.0) == 0.0


def test_cooling_effect_capped() -> None:
    assert cooling_effect(100.0) == 2.0


def test_cooling_effect_grows_with_velocity_until_cap() -> None:
    a = cooling_effect(0.5)
    b = cooling_effect(1.0)
    assert b > a > 0.0


# --- simple_pmv -------------------------------------------------------------


def test_simple_pmv_neutral_around_24c_50rh() -> None:
    """At baseline assumptions (met=1.2, clo=0.5), PMV near zero around 24 °C."""
    assert abs(simple_pmv(24.0, 50.0)) < 0.2


def test_simple_pmv_negative_when_cool() -> None:
    assert simple_pmv(18.0, 50.0) < -0.5


def test_simple_pmv_positive_when_warm() -> None:
    assert simple_pmv(30.0, 50.0) > 0.5


# --- effective_comfort_temp -------------------------------------------------


def test_effective_comfort_returns_none_when_dry_bulb_unknown() -> None:
    assert effective_comfort_temp(None, 50.0, 50, True) is None


def test_effective_comfort_passes_through_with_no_corrections() -> None:
    # Below 20 °C threshold: humidity correction not applied.
    out = effective_comfort_temp(18.0, 70.0, 0, True)
    assert out == 18.0


def test_humid_warm_environment_feels_hotter() -> None:
    base = effective_comfort_temp(28.0, 50.0, 0, True)
    humid = effective_comfort_temp(28.0, 80.0, 0, True)
    assert humid > base


def test_fan_use_when_occupied_lowers_apparent_temp() -> None:
    no_fan = effective_comfort_temp(28.0, 50.0, 0, True)
    fan_on = effective_comfort_temp(28.0, 50.0, 100, True)
    assert fan_on < no_fan


def test_fan_use_when_unoccupied_does_not_lower_apparent_temp() -> None:
    no_fan = effective_comfort_temp(28.0, 50.0, 0, False)
    fan_on = effective_comfort_temp(28.0, 50.0, 100, False)
    assert math.isclose(no_fan or 0.0, fan_on or 0.0)
