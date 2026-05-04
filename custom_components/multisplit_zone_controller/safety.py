"""Safety floor/ceiling enforcement.

The safety layer runs after sensor fusion and before arbitration. If the
fused effective temperature has crossed a configured floor or ceiling, the
zone's user-requested intent is overridden so the coordinator will dispatch
heat or cool regardless of what the user actually asked for.

Safety is intentionally orthogonal to occupancy — even an unoccupied zone
must remain protected. Later phases extend this with humidity-aware safety
and conservative sensor fusion; the surface here is stable from Phase 1.
"""

from __future__ import annotations

from .models import (
    EffectiveReadings,
    HVACMode,
    ResolvedIntent,
    SafetyLimits,
    ZoneConfig,
    ZoneIntent,
)


def apply_safety(
    zone: ZoneConfig,
    intent: ZoneIntent,
    effective: EffectiveReadings,
) -> ResolvedIntent:
    """Clamp `intent` against `zone.safety` using conservative readings.

    The floor check uses ``effective.safety_floor_temp`` (the coldest
    valid sensor) so a single anomalously low reading still triggers
    freeze protection. The ceiling check uses ``effective.safety_ceiling_temp``
    similarly. Both fall back to ``effective.temperature`` when fusion
    did not produce a separate envelope (Phase-1 behaviour).

    Returns a ``ResolvedIntent``. If no override applies, the original
    intent is returned with ``safety_override=False``.
    """
    limits: SafetyLimits = zone.safety
    floor_temp = (
        effective.safety_floor_temp
        if effective.safety_floor_temp is not None
        else effective.temperature
    )
    ceiling_temp = (
        effective.safety_ceiling_temp
        if effective.safety_ceiling_temp is not None
        else effective.temperature
    )

    if (floor_temp is None and ceiling_temp is None) or (
        limits.min_temp is None and limits.max_temp is None
    ):
        return ResolvedIntent(
            zone_id=intent.zone_id,
            hvac_mode=intent.hvac_mode,
            target_temperature=intent.target_temperature,
            safety_override=False,
        )

    if (
        limits.min_temp is not None
        and floor_temp is not None
        and floor_temp < limits.min_temp
    ):
        return ResolvedIntent(
            zone_id=intent.zone_id,
            hvac_mode=HVACMode.HEAT,
            target_temperature=limits.min_temp,
            safety_override=True,
            safety_reason=(
                f"Safety floor active: {floor_temp:.1f} below minimum "
                f"{limits.min_temp:.1f}"
            ),
        )

    if (
        limits.max_temp is not None
        and ceiling_temp is not None
        and ceiling_temp > limits.max_temp
    ):
        return ResolvedIntent(
            zone_id=intent.zone_id,
            hvac_mode=HVACMode.COOL,
            target_temperature=limits.max_temp,
            safety_override=True,
            safety_reason=(
                f"Safety ceiling active: {ceiling_temp:.1f} above maximum "
                f"{limits.max_temp:.1f}"
            ),
        )

    return ResolvedIntent(
        zone_id=intent.zone_id,
        hvac_mode=intent.hvac_mode,
        target_temperature=intent.target_temperature,
        safety_override=False,
    )
