"""Tests for sensor_fusion: head_only and external_preferred strategies."""

from __future__ import annotations

import pytest

from custom_components.multisplit_zone_controller.models import (
    FusionConfig,
    FusionStrategy,
    SensorReading,
    SourceQuality,
)
from custom_components.multisplit_zone_controller.sensor_fusion import fuse


def _r(entity_id: str, value: float | None) -> SensorReading:
    return SensorReading(entity_id=entity_id, value=value)


# --- head_only -----------------------------------------------------------------


def test_head_only_uses_head_sensors() -> None:
    cfg = FusionConfig(strategy=FusionStrategy.HEAD_ONLY)
    out = fuse(cfg, _r("sensor.head_t", 20.5), _r("sensor.head_h", 45.0), (), ())
    assert out.temperature == 20.5
    assert out.humidity == 45.0
    assert out.temperature_source == "head"
    assert out.humidity_source == "head"
    assert out.quality is SourceQuality.OK


def test_head_only_ignores_externals() -> None:
    cfg = FusionConfig(strategy=FusionStrategy.HEAD_ONLY)
    out = fuse(
        cfg,
        _r("sensor.head_t", 20.0),
        None,
        (_r("sensor.ext_t", 18.0),),
        (_r("sensor.ext_h", 60.0),),
    )
    assert out.temperature == 20.0
    assert out.humidity is None
    assert out.temperature_source == "head"
    assert out.humidity_source == "none"


def test_head_only_unavailable_when_head_missing() -> None:
    cfg = FusionConfig(strategy=FusionStrategy.HEAD_ONLY)
    out = fuse(cfg, None, None, (), ())
    assert out.temperature is None
    assert out.quality is SourceQuality.UNAVAILABLE


# --- external_preferred --------------------------------------------------------


def test_external_preferred_uses_external_when_available() -> None:
    cfg = FusionConfig(strategy=FusionStrategy.EXTERNAL_PREFERRED)
    out = fuse(
        cfg,
        _r("sensor.head_t", 22.0),
        _r("sensor.head_h", 50.0),
        (_r("sensor.room_t", 19.5),),
        (_r("sensor.room_h", 42.0),),
    )
    assert out.temperature == 19.5
    assert out.humidity == 42.0
    assert out.temperature_source == "external:sensor.room_t"
    assert out.humidity_source == "external:sensor.room_h"
    assert out.quality is SourceQuality.OK


def test_external_preferred_falls_back_to_head_when_external_missing() -> None:
    cfg = FusionConfig(strategy=FusionStrategy.EXTERNAL_PREFERRED)
    out = fuse(
        cfg,
        _r("sensor.head_t", 22.0),
        None,
        (_r("sensor.room_t", None),),
        (),
    )
    assert out.temperature == 22.0
    assert out.temperature_source == "head"
    # External was configured but unavailable -> degraded
    assert out.quality is SourceQuality.DEGRADED


def test_external_preferred_no_externals_configured_is_ok_quality() -> None:
    cfg = FusionConfig(strategy=FusionStrategy.EXTERNAL_PREFERRED)
    out = fuse(cfg, _r("sensor.head_t", 22.0), None, (), ())
    assert out.temperature == 22.0
    assert out.temperature_source == "head"
    assert out.quality is SourceQuality.OK


def test_external_preferred_picks_first_valid_external() -> None:
    cfg = FusionConfig(strategy=FusionStrategy.EXTERNAL_PREFERRED)
    out = fuse(
        cfg,
        _r("sensor.head_t", 22.0),
        None,
        (_r("sensor.first", None), _r("sensor.second", 19.0)),
        (),
    )
    assert out.temperature == 19.0
    assert out.temperature_source == "external:sensor.second"


def test_external_preferred_unavailable_when_everything_missing() -> None:
    cfg = FusionConfig(strategy=FusionStrategy.EXTERNAL_PREFERRED)
    out = fuse(cfg, None, None, (_r("sensor.x", None),), ())
    assert out.temperature is None
    assert out.quality is SourceQuality.UNAVAILABLE


# --- calibration --------------------------------------------------------------


def test_calibration_offset_is_applied_when_present() -> None:
    cfg = FusionConfig(strategy=FusionStrategy.EXTERNAL_PREFERRED)
    biased = SensorReading(
        entity_id="sensor.room_t", value=20.0, calibration_offset=-1.5
    )
    out = fuse(cfg, _r("sensor.head_t", 22.0), None, (biased,), ())
    assert out.temperature == pytest.approx(18.5)


# --- weighted_blend -----------------------------------------------------------


def _weighted(entity_id: str, value: float | None, weight: float) -> SensorReading:
    return SensorReading(entity_id=entity_id, value=value, weight=weight)


def test_weighted_blend_combines_head_and_externals() -> None:
    cfg = FusionConfig(strategy=FusionStrategy.WEIGHTED_BLEND, head_weight=1.0)
    out = fuse(
        cfg,
        _r("sensor.head_t", 22.0),
        None,
        (_weighted("sensor.room_t", 20.0, 3.0),),
        (),
    )
    # (22 * 1 + 20 * 3) / (1 + 3) = 82 / 4 = 20.5
    assert out.temperature == pytest.approx(20.5)
    assert out.temperature_source == "blend(sensor.head_t,sensor.room_t)"
    assert out.quality is SourceQuality.OK


def test_weighted_blend_drops_unavailable_sensors() -> None:
    cfg = FusionConfig(strategy=FusionStrategy.WEIGHTED_BLEND, head_weight=2.0)
    out = fuse(
        cfg,
        _r("sensor.head_t", 22.0),
        None,
        (
            _weighted("sensor.room_a", None, 1.0),
            _weighted("sensor.room_b", 18.0, 2.0),
        ),
        (),
    )
    # head 22 * 2 + room_b 18 * 2 / (2+2) = 80/4 = 20
    assert out.temperature == pytest.approx(20.0)


def test_weighted_blend_zero_weight_ignored() -> None:
    cfg = FusionConfig(strategy=FusionStrategy.WEIGHTED_BLEND, head_weight=0.0)
    out = fuse(
        cfg,
        _r("sensor.head_t", 30.0),
        None,
        (_weighted("sensor.room", 20.0, 1.0),),
        (),
    )
    assert out.temperature == pytest.approx(20.0)


def test_weighted_blend_unavailable_when_all_missing() -> None:
    cfg = FusionConfig(strategy=FusionStrategy.WEIGHTED_BLEND, head_weight=1.0)
    out = fuse(cfg, _r("sensor.head_t", None), None, (), ())
    assert out.temperature is None
    assert out.quality is SourceQuality.UNAVAILABLE


# --- average_of_externals -----------------------------------------------------


def test_average_of_externals_averages_only_externals() -> None:
    cfg = FusionConfig(strategy=FusionStrategy.AVERAGE_OF_EXTERNALS)
    out = fuse(
        cfg,
        _r("sensor.head_t", 30.0),  # ignored
        None,
        (_r("sensor.a", 20.0), _r("sensor.b", 22.0)),
        (),
    )
    assert out.temperature == pytest.approx(21.0)
    assert "sensor.a" in out.temperature_source
    assert "sensor.b" in out.temperature_source


def test_average_of_externals_falls_back_to_head_when_none_valid() -> None:
    cfg = FusionConfig(strategy=FusionStrategy.AVERAGE_OF_EXTERNALS)
    out = fuse(
        cfg,
        _r("sensor.head_t", 22.5),
        None,
        (_r("sensor.a", None),),
        (),
    )
    assert out.temperature == 22.5
    assert out.temperature_source == "head"
    assert out.quality is SourceQuality.DEGRADED


def test_average_of_externals_unavailable_when_nothing_works() -> None:
    cfg = FusionConfig(strategy=FusionStrategy.AVERAGE_OF_EXTERNALS)
    out = fuse(cfg, None, None, (_r("sensor.a", None),), ())
    assert out.temperature is None
    assert out.quality is SourceQuality.UNAVAILABLE


# --- safety envelope (conservative min/max) ----------------------------------


def test_safety_envelope_uses_min_and_max_across_all_sources() -> None:
    cfg = FusionConfig(strategy=FusionStrategy.EXTERNAL_PREFERRED)
    out = fuse(
        cfg,
        _r("sensor.head_t", 22.0),
        None,
        (_r("sensor.cold_corner", 5.0), _r("sensor.warm_corner", 28.0)),
        (),
    )
    # Control reading uses external_preferred (first valid external = 5.0)
    assert out.temperature == 5.0
    # Safety envelope is across head + both externals.
    assert out.safety_floor_temp == 5.0
    assert out.safety_ceiling_temp == 28.0
    assert set(out.contributing_sources) == {
        "sensor.head_t",
        "sensor.cold_corner",
        "sensor.warm_corner",
    }


def test_safety_envelope_applies_calibration() -> None:
    cfg = FusionConfig(strategy=FusionStrategy.HEAD_ONLY)
    biased = SensorReading(
        entity_id="sensor.head_t", value=20.0, calibration_offset=-2.0
    )
    out = fuse(cfg, biased, None, (), ())
    assert out.temperature == pytest.approx(18.0)
    assert out.safety_floor_temp == pytest.approx(18.0)
    assert out.safety_ceiling_temp == pytest.approx(18.0)


def test_safety_envelope_none_when_no_sources() -> None:
    cfg = FusionConfig(strategy=FusionStrategy.HEAD_ONLY)
    out = fuse(cfg, None, None, (), ())
    assert out.safety_floor_temp is None
    assert out.safety_ceiling_temp is None
    assert out.contributing_sources == ()


# --- occupancy_weighted (still unimplemented) --------------------------------


def test_occupancy_weighted_collapses_to_weighted_blend() -> None:
    """With static (non-boosted) weights, occupancy_weighted == weighted_blend."""
    cfg = FusionConfig(strategy=FusionStrategy.OCCUPANCY_WEIGHTED, head_weight=1.0)
    out = fuse(
        cfg,
        _r("sensor.head_t", 22.0),
        None,
        (_weighted("sensor.room_t", 20.0, 3.0),),
        (),
    )
    # Same as weighted_blend test: (22*1 + 20*3)/4 = 20.5
    assert out.temperature == pytest.approx(20.5)


def test_occupancy_weighted_uses_boosted_weight_when_provided() -> None:
    """Coordinator pre-multiplies weight; ensure the fused value reflects that."""
    cfg = FusionConfig(strategy=FusionStrategy.OCCUPANCY_WEIGHTED, head_weight=1.0)
    # Imagine the coordinator boosted sensor.kitchen by 3x because someone is in the kitchen.
    out = fuse(
        cfg,
        _r("sensor.head_t", 22.0),
        None,
        (
            _weighted("sensor.kitchen", 24.0, 3.0),  # boosted
            _weighted("sensor.living", 20.0, 1.0),  # not boosted
        ),
        (),
    )
    # (22*1 + 24*3 + 20*1) / 5 = 114/5 = 22.8
    assert out.temperature == pytest.approx(22.8)
