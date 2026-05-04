"""Comfort helpers: fan apparent-temperature credit and fan command resolution.

Pure functions, no Home Assistant imports. The coordinator combines the
results with sensor fusion, occupancy, and arbitration outputs to
produce the per-tick fan dispatch.

Apparent-temperature credit only applies when the zone is occupied (per
the design doc: ``Confirmed``, ``Recent``, or ``Expected``). It is
zero when:

- the fan is disabled or unconfigured,
- the lockout is active (occupants who dislike air movement),
- the zone is ``Unoccupied``,
- the deviation from the active setpoint is zero.

The credit grows linearly with deviation up to ``max_apparent_temp_credit``
once the deviation reaches ``deviation_per_step``.
"""

from __future__ import annotations

from .models import (
    FanCommand,
    FanConfig,
    FanDirection,
    HumidityPolicy,
    HVACMode,
    OccupancyState,
)

OCCUPIED_STATES: frozenset[OccupancyState] = frozenset(
    {OccupancyState.CONFIRMED, OccupancyState.RECENT, OccupancyState.EXPECTED}
)


def apparent_temp_credit(
    deviation: float,
    fan: FanConfig,
    occupancy: OccupancyState,
    locked_out: bool,
) -> float:
    """Estimated apparent-temperature reduction (°C) from running the fan.

    Always returned as a non-negative magnitude; the arbitration scorer
    subtracts it from the absolute deviation.
    """
    if not fan.enabled or fan.fan_entity_id is None:
        return 0.0
    if locked_out:
        return 0.0
    if occupancy not in OCCUPIED_STATES:
        return 0.0
    if fan.deviation_per_step <= 0:
        return fan.max_apparent_temp_credit
    factor = min(abs(deviation) / fan.deviation_per_step, 1.0)
    return factor * fan.max_apparent_temp_credit


def fan_command_for(
    dispatched_mode: HVACMode,
    deviation: float,
    fan: FanConfig,
    occupancy: OccupancyState,
    locked_out: bool,
) -> FanCommand:
    """Compute the fan command for a zone given the post-arbitration mode.

    A locked-out or disabled fan returns an OFF command. ``OFF`` HVAC
    mode also turns the fan off; the design treats coordinated fan use
    as supplementary to active heating/cooling, not a stand-alone
    distribution loop.
    """
    default_dir = fan.direction_in_heat
    if not fan.enabled or fan.fan_entity_id is None or locked_out:
        return FanCommand(on=False, speed_pct=0, direction=default_dir)

    if dispatched_mode is HVACMode.OFF:
        return FanCommand(on=False, speed_pct=0, direction=default_dir)

    if dispatched_mode is HVACMode.HEAT:
        direction = fan.direction_in_heat
    else:
        direction = fan.direction_in_cool

    if occupancy in OCCUPIED_STATES:
        max_speed = fan.max_speed_pct_occupied
    else:
        max_speed = fan.max_speed_pct_unoccupied

    if fan.deviation_per_step <= 0:
        speed_pct = max_speed
    else:
        factor = min(abs(deviation) / fan.deviation_per_step, 1.0)
        speed_pct = int(round(factor * max_speed))

    return FanCommand(
        on=speed_pct > 0,
        speed_pct=speed_pct,
        direction=FanDirection(direction),
    )


def humidity_priority(
    policy: HumidityPolicy,
    intent_mode: HVACMode,
    effective_humidity: float | None,
) -> float:
    """Return the humidity-driven priority contribution (>= 0).

    Two terms, both opt-in:

    1. ``weight``: scales how far the effective humidity sits outside
       the configured comfort band ``target_humidity ± tolerance_band/2``.
       Active in any mode.

    2. ``dehumidify_bonus``: a flat bonus applied when the zone is in
       COOL and ``effective_humidity > dehumidify_threshold``. Models
       the design doc's "bias toward cool when latent load is high".

    Both terms collapse to zero when their parameters are unset, so the
    score is unchanged from Phase 6 unless explicitly configured.
    """
    contribution = 0.0
    if effective_humidity is None:
        return 0.0

    if policy.weight > 0 and policy.target_humidity is not None:
        excess = max(
            0.0,
            abs(effective_humidity - policy.target_humidity)
            - policy.tolerance_band / 2.0,
        )
        contribution += excess * policy.weight

    if (
        intent_mode is HVACMode.COOL
        and policy.dehumidify_bonus > 0
        and policy.dehumidify_threshold is not None
        and effective_humidity > policy.dehumidify_threshold
    ):
        contribution += policy.dehumidify_bonus

    return contribution
