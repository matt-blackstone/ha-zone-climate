"""Unit tests for display thermostat inbound intent handling."""

from __future__ import annotations

import pytest

from custom_components.multisplit_zone_controller.display_echo import DisplayEchoGuard
from custom_components.multisplit_zone_controller.display_listener import (
    DisplayListener,
    display_target_temperature_celsius,
    display_user_intent_changed,
)
from custom_components.multisplit_zone_controller.models import (
    DisplayThermostatConfig,
    FusionConfig,
    GroupConfig,
    HVACMode,
    ZoneConfig,
    ZoneIntent,
)


class _State:
    def __init__(self, state: str, attrs: dict | None = None) -> None:
        self.state = state
        self.attributes = attrs or {}


class _Event:
    def __init__(self, old_state: _State, new_state: _State) -> None:
        self.data = {"old_state": old_state, "new_state": new_state}


class _Hass:
    config = type(
        "Cfg",
        (),
        {"units": type("Units", (), {"temperature_unit": "°F"})()},
    )()


def _display() -> DisplayThermostatConfig:
    return DisplayThermostatConfig(entity_id="climate.t6")


def test_user_intent_changed_detects_fan_mode_change() -> None:
    old = _State("off", {"fan_mode": "Auto low", "temperature": None})
    new = _State("off", {"fan_mode": "Low", "temperature": None})
    assert display_user_intent_changed(old, new, _display())


def test_target_temperature_reads_fahrenheit_climate_attr() -> None:
    state = _State("heat", {"temperature": 82})
    assert display_target_temperature_celsius(state, "°F") == pytest.approx(
        27.777,
        abs=0.01,
    )


def test_handler_maps_heat_setpoint_to_zone_intent() -> None:
    cfg = _display()
    group = GroupConfig(
        group_id="g",
        name="G",
        zones=(
            ZoneConfig(
                zone_id="z",
                name="Z",
                head_climate_entity="climate.head",
                fusion=FusionConfig(),
                display_thermostats=(cfg,),
            ),
        ),
    )
    intents = {"z": ZoneIntent("z", HVACMode.OFF, 21.0)}

    listener = DisplayListener(
        _Hass(),
        group,
        DisplayEchoGuard(),
        lambda zone_id: intents[zone_id],
        lambda intent: intents.__setitem__(intent.zone_id, intent),
    )
    handler = listener._make_handler("z", cfg)

    handler(
        _Event(
            _State("off", {"fan_mode": "Auto low", "temperature": None}),
            _State("heat", {"fan_mode": "Auto low", "temperature": 82}),
        )
    )

    assert intents["z"].hvac_mode is HVACMode.HEAT
    assert intents["z"].target_temperature == pytest.approx(27.777, abs=0.01)


def test_handler_maps_t6_off_low_to_fan_only_preserving_target() -> None:
    cfg = _display()
    group = GroupConfig(group_id="g", name="G", zones=())
    intents = {"z": ZoneIntent("z", HVACMode.OFF, 22.0)}
    listener = DisplayListener(
        _Hass(),
        group,
        DisplayEchoGuard(),
        lambda zone_id: intents[zone_id],
        lambda intent: intents.__setitem__(intent.zone_id, intent),
    )

    listener._make_handler("z", cfg)(
        _Event(
            _State("off", {"fan_mode": "Auto low", "temperature": None}),
            _State("off", {"fan_mode": "Low", "temperature": None}),
        )
    )

    assert intents["z"] == ZoneIntent("z", HVACMode.FAN_ONLY, 22.0)


def test_handler_ignores_recent_echo_write() -> None:
    cfg = _display()
    guard = DisplayEchoGuard()
    guard.mark_write(cfg.entity_id)
    intents = {"z": ZoneIntent("z", HVACMode.OFF, 21.0)}
    listener = DisplayListener(
        _Hass(),
        GroupConfig(group_id="g", name="G", zones=()),
        guard,
        lambda zone_id: intents[zone_id],
        lambda intent: intents.__setitem__(intent.zone_id, intent),
    )

    listener._make_handler("z", cfg)(
        _Event(
            _State("off", {"fan_mode": "Auto low", "temperature": None}),
            _State("heat", {"fan_mode": "Auto low", "temperature": 82}),
        )
    )

    assert intents["z"] == ZoneIntent("z", HVACMode.OFF, 21.0)
