"""Tests for the safety floor/ceiling clamp."""

from __future__ import annotations

from custom_components.multisplit_zone_controller.models import (
    EffectiveReadings,
    FusionConfig,
    HVACMode,
    SafetyLimits,
    SourceQuality,
    ZoneConfig,
    ZoneIntent,
)
from custom_components.multisplit_zone_controller.safety import apply_safety


def _eff(temp: float | None) -> EffectiveReadings:
    return EffectiveReadings(
        temperature=temp,
        humidity=None,
        temperature_source="head",
        humidity_source="none",
        quality=SourceQuality.OK if temp is not None else SourceQuality.UNAVAILABLE,
    )


def _zone(safety: SafetyLimits) -> ZoneConfig:
    return ZoneConfig(
        zone_id="z1",
        name="Z1",
        head_climate_entity="climate.head_z1",
        fusion=FusionConfig(),
        safety=safety,
    )


def test_within_limits_passes_through() -> None:
    zone = _zone(SafetyLimits(min_temp=5.0, max_temp=30.0))
    intent = ZoneIntent(zone_id="z1", hvac_mode=HVACMode.OFF, target_temperature=21.0)
    out = apply_safety(zone, intent, _eff(20.0))
    assert out.safety_override is False
    assert out.hvac_mode is HVACMode.OFF
    assert out.target_temperature == 21.0


def test_floor_overrides_to_heat() -> None:
    zone = _zone(SafetyLimits(min_temp=10.0))
    intent = ZoneIntent(zone_id="z1", hvac_mode=HVACMode.OFF, target_temperature=18.0)
    out = apply_safety(zone, intent, _eff(7.5))
    assert out.safety_override is True
    assert out.hvac_mode is HVACMode.HEAT
    assert out.target_temperature == 10.0
    assert out.safety_reason is not None
    assert "floor" in out.safety_reason.lower()


def test_ceiling_overrides_to_cool() -> None:
    zone = _zone(SafetyLimits(max_temp=28.0))
    intent = ZoneIntent(zone_id="z1", hvac_mode=HVACMode.OFF, target_temperature=21.0)
    out = apply_safety(zone, intent, _eff(31.0))
    assert out.safety_override is True
    assert out.hvac_mode is HVACMode.COOL
    assert out.target_temperature == 28.0
    assert "ceiling" in (out.safety_reason or "").lower()


def test_no_temperature_means_no_override() -> None:
    zone = _zone(SafetyLimits(min_temp=10.0, max_temp=28.0))
    intent = ZoneIntent(zone_id="z1", hvac_mode=HVACMode.HEAT, target_temperature=20.0)
    out = apply_safety(zone, intent, _eff(None))
    assert out.safety_override is False


def test_no_limits_configured_passes_through() -> None:
    zone = _zone(SafetyLimits())
    intent = ZoneIntent(zone_id="z1", hvac_mode=HVACMode.HEAT, target_temperature=20.0)
    out = apply_safety(zone, intent, _eff(-5.0))
    assert out.safety_override is False


def test_floor_overrides_user_cool_request() -> None:
    """Even a user explicitly asking for cooling cannot defeat the safety floor."""
    zone = _zone(SafetyLimits(min_temp=10.0))
    intent = ZoneIntent(zone_id="z1", hvac_mode=HVACMode.COOL, target_temperature=18.0)
    out = apply_safety(zone, intent, _eff(8.0))
    assert out.safety_override is True
    assert out.hvac_mode is HVACMode.HEAT


def test_conservative_floor_uses_safety_floor_temp() -> None:
    """Floor check must use the coldest sensor, not the control temperature."""
    zone = _zone(SafetyLimits(min_temp=10.0))
    eff = EffectiveReadings(
        temperature=21.0,  # control reading (e.g. averaged) is fine
        humidity=None,
        temperature_source="average(...)",
        humidity_source="none",
        quality=SourceQuality.OK,
        safety_floor_temp=8.0,  # one corner sensor reports cold
        safety_ceiling_temp=24.0,
    )
    intent = ZoneIntent(zone_id="z1", hvac_mode=HVACMode.OFF)
    out = apply_safety(zone, intent, eff)
    assert out.safety_override is True
    assert out.hvac_mode is HVACMode.HEAT


def test_conservative_ceiling_uses_safety_ceiling_temp() -> None:
    zone = _zone(SafetyLimits(max_temp=28.0))
    eff = EffectiveReadings(
        temperature=24.0,
        humidity=None,
        temperature_source="average(...)",
        humidity_source="none",
        quality=SourceQuality.OK,
        safety_floor_temp=22.0,
        safety_ceiling_temp=31.0,
    )
    intent = ZoneIntent(zone_id="z1", hvac_mode=HVACMode.OFF)
    out = apply_safety(zone, intent, eff)
    assert out.safety_override is True
    assert out.hvac_mode is HVACMode.COOL
