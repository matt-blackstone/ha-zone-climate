"""Group-level heat/cool changeover dwell protection."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Mapping

from .arbitration import arbitrate
from .models import (
    EffectiveReadings,
    GroupConfig,
    GroupDecision,
    HVACMode,
    OccupancyResolution,
    ResolvedIntent,
    ZoneDecision,
)

THERMAL_CHANGEOVER_MODES: frozenset[HVACMode] = frozenset(
    {HVACMode.HEAT, HVACMode.COOL}
)
HEAT_COOL_PAIR: frozenset[HVACMode] = frozenset({HVACMode.HEAT, HVACMode.COOL})


def dominant_thermal_mode(decision: GroupDecision) -> HVACMode | None:
    """Return the group's single dispatched heat/cool mode, if any."""
    modes = {
        zone_decision.dispatched_mode
        for zone_decision in decision.zones.values()
        if zone_decision.dispatched_mode in THERMAL_CHANGEOVER_MODES
    }
    if len(modes) != 1:
        return None
    return next(iter(modes))


def apply_changeover_dwell(
    group: GroupConfig,
    decision: GroupDecision,
    resolved: Mapping[str, ResolvedIntent],
    effective_by_zone: Mapping[str, EffectiveReadings],
    occupancy_by_zone: Mapping[str, OccupancyResolution],
    comfort_setpoints: Mapping[str, float | None],
    fan_offsets: Mapping[str, float],
    comfort_temps: Mapping[str, float | None] | None,
    previous_mode: HVACMode | None,
    previous_mode_started_at: datetime | None,
    now: datetime,
) -> GroupDecision:
    """Pin heat/cool changeovers until the configured dwell window expires."""
    dwell_minutes = group.min_changeover_dwell_minutes
    proposed_mode = dominant_thermal_mode(decision)

    if (
        dwell_minutes <= 0
        or HEAT_COOL_PAIR not in group.incompatible_mode_pairs
        or proposed_mode is None
        or previous_mode is None
        or proposed_mode is previous_mode
        or previous_mode_started_at is None
        or _safety_requires_mode(decision, proposed_mode)
    ):
        return decision

    elapsed_minutes = (now - previous_mode_started_at).total_seconds() / 60.0
    remaining_minutes = dwell_minutes - elapsed_minutes
    if remaining_minutes <= 0:
        return decision

    pinned = arbitrate(
        group,
        resolved,
        effective_by_zone,
        occupancy_by_zone,
        comfort_setpoints,
        fan_offsets,
        comfort_temps,
        allowed_thermal_mode=previous_mode,
    )
    return _mark_changeover_throttled(
        pinned,
        resolved,
        blocked_mode=proposed_mode,
        held_mode=previous_mode,
        remaining_minutes=remaining_minutes,
    )


def _safety_requires_mode(decision: GroupDecision, mode: HVACMode) -> bool:
    return any(
        zone_decision.safety_override and zone_decision.dispatched_mode is mode
        for zone_decision in decision.zones.values()
    )


def _mark_changeover_throttled(
    decision: GroupDecision,
    resolved: Mapping[str, ResolvedIntent],
    *,
    blocked_mode: HVACMode,
    held_mode: HVACMode,
    remaining_minutes: float,
) -> GroupDecision:
    reason = (
        f"Changeover locked: holding {held_mode.value} for "
        f"{remaining_minutes:.1f} more min before {blocked_mode.value}"
    )
    zones: dict[str, ZoneDecision] = {}
    for zone_id, zone_decision in decision.zones.items():
        intent = resolved[zone_id]
        should_mark = (
            intent.hvac_mode is blocked_mode
            and not intent.safety_override
            and zone_decision.dispatched_mode is not blocked_mode
        )
        zones[zone_id] = (
            replace(zone_decision, blocked=True, block_reason=reason)
            if should_mark
            else zone_decision
        )
    return GroupDecision(group_id=decision.group_id, zones=zones)
