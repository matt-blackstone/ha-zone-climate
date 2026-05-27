"""Mode and fan-mode mapping for physical display thermostats."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .models import DisplayThermostatConfig, HVACMode


@dataclass(frozen=True)
class DisplayModeCommand:
    """Desired thermostat-facing mode/fan tuple.

    ``hvac_mode`` and ``fan_mode`` are strings because display thermostats
    expose vendor/platform-specific fan mode values, even though HVAC modes
    broadly follow Home Assistant's climate constants.
    """

    hvac_mode: str | None
    fan_mode: str | None


def _norm(value: object) -> str:
    return str(value).strip().lower().replace("_", " ")


def _first_supported(
    preferred: Iterable[str],
    supported: Iterable[str] | None,
) -> str | None:
    preferred_values = [str(v) for v in preferred if str(v).strip()]
    supported_values = [str(v) for v in (supported or []) if str(v).strip()]
    if not supported_values:
        return preferred_values[0] if preferred_values else None

    supported_by_norm = {_norm(v): v for v in supported_values}
    for value in preferred_values:
        match = supported_by_norm.get(_norm(value))
        if match is not None:
            return match
    return None


def choose_auto_fan_mode(
    cfg: DisplayThermostatConfig,
    supported_fan_modes: Iterable[str] | None,
) -> str | None:
    """Pick the fan-mode value representing normal thermostat auto fan."""
    return _first_supported(
        (
            cfg.auto_fan_mode,
            "Auto low",
            "Auto",
            "auto",
        ),
        supported_fan_modes,
    )


def choose_fan_only_fan_mode(
    cfg: DisplayThermostatConfig,
    supported_fan_modes: Iterable[str] | None,
) -> str | None:
    """Pick the fan-mode value representing continuous fan."""
    return _first_supported(
        (
            cfg.fan_only_fan_mode,
            "Low",
            "on",
            "On",
            cfg.circulate_fan_mode,
            "Circulation",
            "circulate",
        ),
        supported_fan_modes,
    )


def fan_mode_is_auto(cfg: DisplayThermostatConfig, fan_mode: str | None) -> bool:
    if fan_mode is None:
        return True
    return _norm(fan_mode) in {
        _norm(cfg.auto_fan_mode),
        "auto low",
        "auto",
    }


def fan_mode_is_fan_only(
    cfg: DisplayThermostatConfig,
    fan_mode: str | None,
) -> bool:
    if fan_mode is None:
        return False
    return _norm(fan_mode) in {
        _norm(cfg.fan_only_fan_mode),
        _norm(cfg.circulate_fan_mode),
        "low",
        "on",
        "circulation",
        "circulate",
    }


def managed_mode_to_display(
    mode: HVACMode,
    cfg: DisplayThermostatConfig,
    supported_fan_modes: Iterable[str] | None = None,
) -> DisplayModeCommand:
    """Map a managed-zone mode to the display thermostat's two knobs."""
    auto_fan = choose_auto_fan_mode(cfg, supported_fan_modes)
    fan_only_fan = choose_fan_only_fan_mode(cfg, supported_fan_modes)

    if mode is HVACMode.OFF:
        return DisplayModeCommand("off", auto_fan)
    if mode is HVACMode.HEAT:
        return DisplayModeCommand("heat", auto_fan)
    if mode is HVACMode.COOL:
        return DisplayModeCommand("cool", auto_fan)
    if mode is HVACMode.AUTO:
        return DisplayModeCommand("auto", auto_fan)
    if mode is HVACMode.FAN_ONLY:
        # Honeywell T6 exposes continuous fan as HVAC off + fan Low.
        return DisplayModeCommand("off", fan_only_fan)
    if mode is HVACMode.DRY:
        # T6 has no dry mode; cool is the closest display-side semantic.
        return DisplayModeCommand("cool", auto_fan)
    return DisplayModeCommand(None, None)


def display_state_to_managed_mode(
    hvac_state: str | None,
    fan_mode: str | None,
    cfg: DisplayThermostatConfig,
) -> HVACMode | None:
    """Map a physical thermostat state/fan pair back to managed intent."""
    state = _norm(hvac_state or "")
    if state in ("", "unknown", "unavailable"):
        return None
    if state == "off":
        return HVACMode.FAN_ONLY if fan_mode_is_fan_only(cfg, fan_mode) else HVACMode.OFF
    if state == "heat":
        return HVACMode.HEAT
    if state == "cool":
        return HVACMode.COOL
    if state == "auto":
        return HVACMode.AUTO
    if state == "dry":
        return HVACMode.DRY
    if state in ("em heat", "emergency heat", "aux heat"):
        # Let the integration's own aux-heat policy decide whether aux fires.
        return HVACMode.HEAT
    return None
