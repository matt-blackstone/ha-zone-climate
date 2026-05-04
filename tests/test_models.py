"""Smoke tests for the typed domain models."""

from __future__ import annotations

from datetime import timedelta

from custom_components.multisplit_zone_controller.models import (
    EffectiveReadings,
    FusionConfig,
    FusionStrategy,
    GroupConfig,
    HVACMode,
    SafetyLimits,
    SensorRef,
    SourceQuality,
    ZoneConfig,
)


def _zone(zid: str = "z1") -> ZoneConfig:
    return ZoneConfig(
        zone_id=zid,
        name=zid.upper(),
        head_climate_entity=f"climate.head_{zid}",
        fusion=FusionConfig(strategy=FusionStrategy.HEAD_ONLY),
    )


def test_zone_config_defaults_are_sane() -> None:
    z = _zone()
    assert z.min_temp == 16.0
    assert z.max_temp == 30.0
    assert z.target_temp_step == 0.5
    assert z.default_target_temperature == 21.0
    assert z.safety == SafetyLimits()
    assert z.fusion.strategy is FusionStrategy.HEAD_ONLY


def test_group_config_zone_lookup() -> None:
    z1 = _zone("z1")
    z2 = _zone("z2")
    g = GroupConfig(
        group_id="g",
        name="G",
        zones=(z1, z2),
        update_interval=timedelta(seconds=30),
    )
    assert g.zone("z1") is z1
    assert g.zone("z2") is z2

    try:
        g.zone("missing")
    except KeyError as err:
        assert "missing" in str(err)
    else:  # pragma: no cover
        raise AssertionError("expected KeyError")


def test_unavailable_effective_readings() -> None:
    e = EffectiveReadings.unavailable()
    assert e.temperature is None
    assert e.humidity is None
    assert e.quality is SourceQuality.UNAVAILABLE


def test_sensor_ref_defaults() -> None:
    ref = SensorRef(entity_id="sensor.foo")
    assert ref.weight == 1.0
    assert ref.calibration_offset == 0.0


def test_hvac_mode_round_trip_via_enum_value() -> None:
    assert HVACMode("heat") is HVACMode.HEAT
    assert HVACMode.OFF.value == "off"
