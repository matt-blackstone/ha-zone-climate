"""Pure tests for Honeywell T6 display thermostat mode mapping."""

from __future__ import annotations

from custom_components.multisplit_zone_controller.display_mapping import (
    display_state_to_managed_mode,
    managed_mode_to_display,
)
from custom_components.multisplit_zone_controller.models import (
    DisplayThermostatConfig,
    HVACMode,
)


T6_FAN_MODES = ("Auto low", "Low", "Circulation")


def _cfg() -> DisplayThermostatConfig:
    return DisplayThermostatConfig(entity_id="climate.t6")


def test_managed_heat_maps_to_t6_heat_auto_low() -> None:
    out = managed_mode_to_display(HVACMode.HEAT, _cfg(), T6_FAN_MODES)
    assert out.hvac_mode == "heat"
    assert out.fan_mode == "Auto low"


def test_managed_fan_only_maps_to_t6_off_low() -> None:
    out = managed_mode_to_display(HVACMode.FAN_ONLY, _cfg(), T6_FAN_MODES)
    assert out.hvac_mode == "off"
    assert out.fan_mode == "Low"


def test_managed_dry_maps_to_t6_cool_auto_low() -> None:
    out = managed_mode_to_display(HVACMode.DRY, _cfg(), T6_FAN_MODES)
    assert out.hvac_mode == "cool"
    assert out.fan_mode == "Auto low"


def test_t6_heat_maps_to_managed_heat() -> None:
    assert (
        display_state_to_managed_mode("heat", "Auto low", _cfg())
        is HVACMode.HEAT
    )


def test_t6_off_auto_low_maps_to_managed_off() -> None:
    assert (
        display_state_to_managed_mode("off", "Auto low", _cfg())
        is HVACMode.OFF
    )


def test_t6_off_low_maps_to_managed_fan_only() -> None:
    assert (
        display_state_to_managed_mode("off", "Low", _cfg())
        is HVACMode.FAN_ONLY
    )


def test_t6_off_circulation_maps_to_managed_fan_only() -> None:
    assert (
        display_state_to_managed_mode("off", "Circulation", _cfg())
        is HVACMode.FAN_ONLY
    )


def test_emergency_heat_maps_to_managed_heat() -> None:
    assert (
        display_state_to_managed_mode("em_heat", "Auto low", _cfg())
        is HVACMode.HEAT
    )
