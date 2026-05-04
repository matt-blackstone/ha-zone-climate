"""Pure-logic tests for the unit normalisation helpers."""

from __future__ import annotations

import pytest

from custom_components.multisplit_zone_controller.units import (
    c_to_f,
    f_to_c,
    from_celsius,
    is_celsius,
    is_fahrenheit,
    state_temperature_in_celsius,
    to_celsius,
)


class FakeState:
    """Minimal stand-in for an HA State object."""

    def __init__(self, state, attributes=None):
        self.state = state
        self.attributes = dict(attributes or {})


# ---------------------------------------------------------------------------
# Unit token recognition
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("token", ["°C", "c", "Celsius", " °c "])
def test_is_celsius_accepts_common_tokens(token):
    assert is_celsius(token)


@pytest.mark.parametrize("token", ["°F", "f", "FAHRENHEIT"])
def test_is_fahrenheit_accepts_common_tokens(token):
    assert is_fahrenheit(token)


@pytest.mark.parametrize("token", [None, "", "%", "kg"])
def test_unknown_unit_is_neither(token):
    assert not is_celsius(token)
    assert not is_fahrenheit(token)


# ---------------------------------------------------------------------------
# Numerical conversion
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fahrenheit,expected_celsius",
    [(32, 0), (212, 100), (-40, -40), (68, 20), (28, pytest.approx(-2.222, abs=0.01))],
)
def test_fahrenheit_to_celsius(fahrenheit, expected_celsius):
    assert f_to_c(fahrenheit) == expected_celsius


@pytest.mark.parametrize(
    "celsius,expected_f",
    [(0, 32), (100, 212), (-40, -40), (20, 68), (22, pytest.approx(71.6, abs=0.01))],
)
def test_celsius_to_fahrenheit(celsius, expected_f):
    assert c_to_f(celsius) == expected_f


# ---------------------------------------------------------------------------
# to_celsius / from_celsius behaviour
# ---------------------------------------------------------------------------


def test_to_celsius_passes_through_celsius():
    assert to_celsius(22.0, "°C") == 22.0


def test_to_celsius_converts_fahrenheit():
    assert to_celsius(68.0, "°F") == pytest.approx(20.0)


def test_to_celsius_unknown_unit_assumed_celsius():
    # We deliberately don't crash on misconfigured sensors.
    assert to_celsius(15.5, "K") == 15.5


def test_to_celsius_none_passes_through():
    assert to_celsius(None, "°C") is None
    assert to_celsius(None, "°F") is None


def test_from_celsius_passes_through_celsius():
    assert from_celsius(22.0, "°C") == 22.0


def test_from_celsius_converts_to_fahrenheit():
    assert from_celsius(20.0, "°F") == pytest.approx(68.0)


def test_from_celsius_none_passes_through():
    assert from_celsius(None, "°F") is None


def test_round_trip_celsius_to_fahrenheit_to_celsius():
    for c in (-40, 0, 20, 22.5, 100):
        round_tripped = to_celsius(from_celsius(c, "°F"), "°F")
        assert round_tripped == pytest.approx(c, abs=0.001)


# ---------------------------------------------------------------------------
# state_temperature_in_celsius — sensors and climate entities
# ---------------------------------------------------------------------------


def test_sensor_state_in_celsius_returned_unchanged():
    state = FakeState("22.5", {"unit_of_measurement": "°C"})
    assert state_temperature_in_celsius(state) == 22.5


def test_sensor_state_in_fahrenheit_converted():
    state = FakeState("68", {"unit_of_measurement": "°F"})
    assert state_temperature_in_celsius(state) == pytest.approx(20.0)


def test_climate_attribute_in_fahrenheit_converted():
    """Climate entity exposes current_temperature converted to user unit."""
    state = FakeState(
        "heat",
        {
            "current_temperature": 68.0,
            "temperature_unit": "°F",
        },
    )
    assert state_temperature_in_celsius(state) == pytest.approx(20.0)


def test_climate_attribute_unit_falls_back_to_unit_of_measurement():
    state = FakeState(
        "heat",
        {
            "current_temperature": 22.0,
            "unit_of_measurement": "°C",
        },
    )
    assert state_temperature_in_celsius(state) == 22.0


def test_unknown_state_returns_none():
    assert state_temperature_in_celsius(FakeState("unknown", {})) is None
    assert state_temperature_in_celsius(FakeState("unavailable", {})) is None
    assert state_temperature_in_celsius(None) is None


def test_non_numeric_state_returns_none():
    state = FakeState("not_a_number", {"unit_of_measurement": "°C"})
    assert state_temperature_in_celsius(state) is None


def test_climate_off_state_still_reads_current_temperature():
    """A climate entity in off mode still reports current_temperature."""
    state = FakeState(
        "off",
        {"current_temperature": 21.5, "temperature_unit": "°C"},
    )
    assert state_temperature_in_celsius(state) == 21.5


def test_climate_without_unit_attr_uses_default_unit():
    """ClimateEntity serialises ``current_temperature`` already converted
    to HA's user-display unit but doesn't publish ``temperature_unit``.

    The reader must therefore fall back to the explicitly-passed
    ``default_unit`` (intended to be ``hass.config.units.temperature_unit``)
    rather than silently treating the value as Celsius.
    """
    state = FakeState("heat", {"current_temperature": 72.0})
    assert state_temperature_in_celsius(state, default_unit="°F") == pytest.approx(
        22.222, abs=0.01
    )
    # Without a default_unit hint we keep the legacy behaviour (treat as
    # Celsius) so existing callers that don't have access to ``hass``
    # are unaffected.
    assert state_temperature_in_celsius(state) == 72.0


def test_sensor_without_unit_attr_uses_default_unit():
    state = FakeState("60.8", {})
    assert state_temperature_in_celsius(state, default_unit="°F") == pytest.approx(
        16.0, abs=0.05
    )


def test_explicit_state_unit_wins_over_default():
    """If the state advertises its own unit we trust that, not the fallback."""
    state = FakeState("22", {"unit_of_measurement": "°C"})
    assert state_temperature_in_celsius(state, default_unit="°F") == 22.0
