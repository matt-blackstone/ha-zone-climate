"""Occupancy resolution and setback adjustment.

Pure functions, no Home Assistant imports. The coordinator translates HA
state objects (``person``, ``device_tracker``, motion ``binary_sensor``,
``calendar`` events, manual ``input_boolean`` overrides) into
:class:`~.models.OccupancySignalReading` instances and then calls
:func:`resolve_occupancy` to collapse them into a single
:class:`~.models.OccupancyState`.

State mapping rules:

- A configured zone with no sources is treated as **always CONFIRMED**.
  This keeps Phase 1/2 zones (no occupancy config) running comfort
  setpoints.
- ``confidence`` is the weighted fraction of active sources, considering
  every kind together. A non-zero ``MANUAL_OVERRIDE`` short-circuits
  straight to ``CONFIRMED`` when active.
- A presence-class signal active right now elevates a high-confidence
  zone to ``CONFIRMED``; without one, even a high-confidence zone stays
  in ``EXPECTED`` (calendar/schedule alone never imply *current*
  presence).
- ``RECENT`` is reached when the zone was confirmed within the last
  ``linger_minutes`` window even if no signal is active right now.
- Otherwise the zone is ``UNOCCUPIED``.

The setback adjustment shifts the target temperature toward the
configured offset when the zone is ``UNOCCUPIED``. ``RECENT`` and
``EXPECTED`` keep the comfort setpoint (Phase 4 will add explicit
pre-conditioning math).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from .models import (
    HVACMode,
    OccupancyConfig,
    OccupancyResolution,
    OccupancySignalKind,
    OccupancySignalReading,
    OccupancyState,
    SetbackPolicy,
    ZoneIntent,
)


def resolve_occupancy(
    config: OccupancyConfig,
    readings: tuple[OccupancySignalReading, ...],
    last_confirmed_at: datetime | None,
    now: datetime,
) -> OccupancyResolution:
    """Resolve the four-state occupancy for one zone."""
    if not config.sources:
        return OccupancyResolution(
            state=OccupancyState.CONFIRMED,
            confidence=1.0,
            active_sources=(),
        )

    active = [r for r in readings if r.is_active]
    active_ids = tuple(r.source.entity_id for r in active)

    if any(r.source.kind is OccupancySignalKind.MANUAL_OVERRIDE for r in active):
        return OccupancyResolution(
            state=OccupancyState.CONFIRMED,
            confidence=1.0,
            active_sources=active_ids,
        )

    total_weight = sum(s.weight for s in config.sources if s.weight > 0)
    if total_weight <= 0:
        confidence = 0.0
    else:
        active_weight = sum(r.source.weight for r in active if r.source.weight > 0)
        confidence = active_weight / total_weight

    has_presence_now = any(
        r.source.kind is OccupancySignalKind.PRESENCE for r in active
    )

    if confidence >= config.confirmed_threshold and has_presence_now:
        return OccupancyResolution(
            state=OccupancyState.CONFIRMED,
            confidence=confidence,
            active_sources=active_ids,
        )

    if confidence >= config.expected_threshold:
        return OccupancyResolution(
            state=OccupancyState.EXPECTED,
            confidence=confidence,
            active_sources=active_ids,
        )

    if last_confirmed_at is not None:
        linger = timedelta(minutes=config.linger_minutes)
        if now - last_confirmed_at <= linger:
            return OccupancyResolution(
                state=OccupancyState.RECENT,
                confidence=confidence,
                active_sources=active_ids,
            )

    return OccupancyResolution(
        state=OccupancyState.UNOCCUPIED,
        confidence=confidence,
        active_sources=active_ids,
    )


def setback_adjust(
    intent: ZoneIntent,
    occupancy: OccupancyState,
    policy: SetbackPolicy,
) -> ZoneIntent:
    """Return an intent whose target temperature reflects setback when unoccupied.

    ``CONFIRMED``/``EXPECTED``/``RECENT`` pass through unchanged.
    ``UNOCCUPIED`` shifts the target away from comfort by the configured
    offset (down for heating, up for cooling). Modes that do not have a
    meaningful target (OFF/AUTO/FAN_ONLY/DRY) are passed through.
    """
    if occupancy is not OccupancyState.UNOCCUPIED:
        return intent
    if intent.target_temperature is None:
        return intent
    if intent.hvac_mode is HVACMode.HEAT:
        new_target = intent.target_temperature - policy.setback_offset_heat
    elif intent.hvac_mode is HVACMode.COOL:
        new_target = intent.target_temperature + policy.setback_offset_cool
    else:
        return intent
    return ZoneIntent(
        zone_id=intent.zone_id,
        hvac_mode=intent.hvac_mode,
        target_temperature=new_target,
    )


def occupancy_comfort_weight(
    state: OccupancyState, policy: SetbackPolicy
) -> float:
    """Multiplier applied to the comfort component of the priority score."""
    if state is OccupancyState.UNOCCUPIED:
        return policy.unoccupied_comfort_weight
    return 1.0
