"""Sensor fusion: collapse raw sensor readings into per-zone effective values.

Phases 1 and 2 implement four strategies:

- ``head_only``: use the mini-split head's sensors only.
- ``external_preferred``: use the first available external sensor; fall
  back to the head sensor if no external sensor is reporting a value.
- ``weighted_blend``: weighted average of head + externals using each
  sensor's configured ``weight``. Sensors whose value is ``None`` are
  dropped from the average; if every source is missing the result is
  ``None``.
- ``average_of_externals``: arithmetic mean of valid external sensors;
  falls back to the head only if no external sensor is reporting.

Per-sensor calibration offsets are applied *before* fusion so a biased
sensor never poisons the average. The conservative safety temperature
(coldest reading for floor checks, hottest for ceiling checks) is
computed across **every** valid source regardless of strategy so a
single anomalous reading still triggers safety protection.

``occupancy_weighted`` is reserved for Phase 5 and raises
``NotImplementedError`` until then.
"""

from __future__ import annotations

from typing import Iterable

from .models import (
    EffectiveReadings,
    FusionConfig,
    FusionStrategy,
    SensorReading,
    SourceQuality,
)


def _first_valid(readings: Iterable[SensorReading]) -> SensorReading | None:
    for r in readings:
        if r.value is not None:
            return r
    return None


def _calibrated(reading: SensorReading) -> float:
    assert reading.value is not None
    return reading.value + reading.calibration_offset


def _valid_calibrated(
    readings: Iterable[SensorReading],
) -> list[tuple[SensorReading, float]]:
    """Filter to readings with values and apply calibration."""
    return [(r, _calibrated(r)) for r in readings if r.value is not None]


def fuse(
    config: FusionConfig,
    head_temp: SensorReading | None,
    head_humidity: SensorReading | None,
    external_temps: tuple[SensorReading, ...],
    external_humidities: tuple[SensorReading, ...],
) -> EffectiveReadings:
    """Compute fused effective readings for a single zone."""
    if config.strategy is FusionStrategy.HEAD_ONLY:
        primary = _fuse_head_only(head_temp, head_humidity)
    elif config.strategy is FusionStrategy.EXTERNAL_PREFERRED:
        primary = _fuse_external_preferred(
            head_temp, head_humidity, external_temps, external_humidities
        )
    elif config.strategy is FusionStrategy.WEIGHTED_BLEND:
        primary = _fuse_weighted_blend(
            config, head_temp, head_humidity, external_temps, external_humidities
        )
    elif config.strategy is FusionStrategy.AVERAGE_OF_EXTERNALS:
        primary = _fuse_average_of_externals(
            head_temp, head_humidity, external_temps, external_humidities
        )
    elif config.strategy is FusionStrategy.OCCUPANCY_WEIGHTED:
        # Same blend math; coordinator pre-boosts SensorReading.weight
        # for sensors paired with active occupancy sources before passing
        # into fuse(). When no source is active, this collapses to the
        # static weighted blend.
        primary = _fuse_weighted_blend(
            config, head_temp, head_humidity, external_temps, external_humidities
        )
    else:
        raise NotImplementedError(
            f"Fusion strategy {config.strategy.value!r} is not implemented yet"
        )

    floor_temp, ceiling_temp, contributors = _safety_envelope(
        head_temp, external_temps
    )
    return EffectiveReadings(
        temperature=primary.temperature,
        humidity=primary.humidity,
        temperature_source=primary.temperature_source,
        humidity_source=primary.humidity_source,
        quality=primary.quality,
        safety_floor_temp=floor_temp,
        safety_ceiling_temp=ceiling_temp,
        contributing_sources=contributors,
    )


def _safety_envelope(
    head_temp: SensorReading | None,
    external_temps: tuple[SensorReading, ...],
) -> tuple[float | None, float | None, tuple[str, ...]]:
    """Conservative safety bounds across every valid temperature source."""
    sources: list[SensorReading] = []
    if head_temp is not None and head_temp.value is not None:
        sources.append(head_temp)
    for r in external_temps:
        if r.value is not None:
            sources.append(r)
    if not sources:
        return None, None, ()
    calibrated = [_calibrated(r) for r in sources]
    return min(calibrated), max(calibrated), tuple(r.entity_id for r in sources)


# --- strategy implementations -------------------------------------------------


def _fuse_head_only(
    head_temp: SensorReading | None,
    head_humidity: SensorReading | None,
) -> EffectiveReadings:
    temp = (
        _calibrated(head_temp)
        if head_temp is not None and head_temp.value is not None
        else None
    )
    hum = (
        _calibrated(head_humidity)
        if head_humidity is not None and head_humidity.value is not None
        else None
    )
    quality = SourceQuality.OK if temp is not None else SourceQuality.UNAVAILABLE
    return EffectiveReadings(
        temperature=temp,
        humidity=hum,
        temperature_source="head" if temp is not None else "none",
        humidity_source="head" if hum is not None else "none",
        quality=quality,
    )


def _fuse_external_preferred(
    head_temp: SensorReading | None,
    head_humidity: SensorReading | None,
    external_temps: tuple[SensorReading, ...],
    external_humidities: tuple[SensorReading, ...],
) -> EffectiveReadings:
    ext_temp = _first_valid(external_temps)
    ext_hum = _first_valid(external_humidities)

    if ext_temp is not None:
        temp_val: float | None = _calibrated(ext_temp)
        temp_source = f"external:{ext_temp.entity_id}"
    elif head_temp is not None and head_temp.value is not None:
        temp_val = _calibrated(head_temp)
        temp_source = "head"
    else:
        temp_val = None
        temp_source = "none"

    if ext_hum is not None:
        hum_val: float | None = _calibrated(ext_hum)
        hum_source = f"external:{ext_hum.entity_id}"
    elif head_humidity is not None and head_humidity.value is not None:
        hum_val = _calibrated(head_humidity)
        hum_source = "head"
    else:
        hum_val = None
        hum_source = "none"

    if temp_val is None:
        quality = SourceQuality.UNAVAILABLE
    elif ext_temp is None:
        quality = SourceQuality.DEGRADED if external_temps else SourceQuality.OK
    else:
        quality = SourceQuality.OK

    return EffectiveReadings(
        temperature=temp_val,
        humidity=hum_val,
        temperature_source=temp_source,
        humidity_source=hum_source,
        quality=quality,
    )


def _weighted_average(
    head: SensorReading | None,
    head_weight: float,
    externals: tuple[SensorReading, ...],
) -> tuple[float | None, list[str], bool]:
    """Return (average, contributing entity_ids, head_included)."""
    weighted_sum = 0.0
    weight_total = 0.0
    contributors: list[str] = []
    head_included = False
    if head is not None and head.value is not None and head_weight > 0:
        weighted_sum += _calibrated(head) * head_weight
        weight_total += head_weight
        contributors.append(head.entity_id)
        head_included = True
    for ext in externals:
        if ext.value is None or ext.weight <= 0:
            continue
        weighted_sum += _calibrated(ext) * ext.weight
        weight_total += ext.weight
        contributors.append(ext.entity_id)
    if weight_total == 0.0:
        return None, contributors, head_included
    return weighted_sum / weight_total, contributors, head_included


def _fuse_weighted_blend(
    config: FusionConfig,
    head_temp: SensorReading | None,
    head_humidity: SensorReading | None,
    external_temps: tuple[SensorReading, ...],
    external_humidities: tuple[SensorReading, ...],
) -> EffectiveReadings:
    temp_val, temp_contribs, _ = _weighted_average(
        head_temp, config.head_weight, external_temps
    )
    hum_val, hum_contribs, _ = _weighted_average(
        head_humidity, config.head_weight, external_humidities
    )

    temp_source = (
        "blend(" + ",".join(temp_contribs) + ")" if temp_contribs else "none"
    )
    hum_source = (
        "blend(" + ",".join(hum_contribs) + ")" if hum_contribs else "none"
    )

    if temp_val is None:
        quality = SourceQuality.UNAVAILABLE
    elif external_temps and not any(r.value is not None for r in external_temps):
        # All externals were unavailable; head carried the result alone.
        quality = SourceQuality.DEGRADED
    else:
        quality = SourceQuality.OK

    return EffectiveReadings(
        temperature=temp_val,
        humidity=hum_val,
        temperature_source=temp_source,
        humidity_source=hum_source,
        quality=quality,
    )


def _fuse_average_of_externals(
    head_temp: SensorReading | None,
    head_humidity: SensorReading | None,
    external_temps: tuple[SensorReading, ...],
    external_humidities: tuple[SensorReading, ...],
) -> EffectiveReadings:
    valid_temps = _valid_calibrated(external_temps)
    valid_hums = _valid_calibrated(external_humidities)

    if valid_temps:
        temp_val: float | None = sum(v for _, v in valid_temps) / len(valid_temps)
        temp_source = "average(" + ",".join(r.entity_id for r, _ in valid_temps) + ")"
        temp_quality = SourceQuality.OK
    elif head_temp is not None and head_temp.value is not None:
        temp_val = _calibrated(head_temp)
        temp_source = "head"
        temp_quality = SourceQuality.DEGRADED if external_temps else SourceQuality.OK
    else:
        temp_val = None
        temp_source = "none"
        temp_quality = SourceQuality.UNAVAILABLE

    if valid_hums:
        hum_val: float | None = sum(v for _, v in valid_hums) / len(valid_hums)
        hum_source = "average(" + ",".join(r.entity_id for r, _ in valid_hums) + ")"
    elif head_humidity is not None and head_humidity.value is not None:
        hum_val = _calibrated(head_humidity)
        hum_source = "head"
    else:
        hum_val = None
        hum_source = "none"

    return EffectiveReadings(
        temperature=temp_val,
        humidity=hum_val,
        temperature_source=temp_source,
        humidity_source=hum_source,
        quality=temp_quality,
    )
