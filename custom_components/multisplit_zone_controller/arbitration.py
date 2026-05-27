"""Compressor-group arbitration.

Each compressor group declares a set of *incompatible mode pairs* (for
example ``{HEAT, COOL}`` when the outdoor unit cannot run any heads in
heating while another head is in cooling). Arbitration picks the
highest-scoring subset of zones whose dispatched modes are pairwise
compatible. Zones outside the winning subset are blocked: they are
dispatched as ``OFF`` with a human-readable ``block_reason``.

Safety overrides are respected. A zone whose intent was forced by the
safety layer is given a very large priority bonus so a comfort-oriented
zone never blocks safety-critical operation.
"""

from __future__ import annotations

from itertools import combinations
from typing import Iterable, Mapping

from .models import (
    EffectiveReadings,
    GroupConfig,
    GroupDecision,
    HVACMode,
    OccupancyResolution,
    OccupancyState,
    ResolvedIntent,
    ZoneConfig,
    ZoneDecision,
)
from .comfort import humidity_priority
from .occupancy import occupancy_comfort_weight


def _default_resolution() -> OccupancyResolution:
    return OccupancyResolution(
        state=OccupancyState.CONFIRMED, confidence=1.0, active_sources=()
    )

SAFETY_PRIORITY_BONUS = 1_000_000.0
THERMAL_CHANGEOVER_MODES: frozenset[HVACMode] = frozenset(
    {HVACMode.HEAT, HVACMode.COOL}
)


def score_zone(
    zone: ZoneConfig,
    intent: ResolvedIntent,
    effective: EffectiveReadings,
    occupancy: OccupancyState = OccupancyState.CONFIRMED,
    fan_comfort_offset: float = 0.0,
    comfort_temp_override: float | None = None,
) -> float:
    """Compute a per-zone priority score.

    Components:

    - **Comfort**: directional demand from the active setpoint, reduced
      by the zone's deadband and the fan apparent-temperature credit
      (``fan_comfort_offset``), then weighted by occupancy via
      :func:`occupancy.occupancy_comfort_weight`. Unoccupied zones lose
      most (but not all) priority against an occupied zone with similar
      deviation; the fan credit is already zeroed out for unoccupied
      zones by :mod:`comfort` so it cannot artificially deflate a
      vacant zone's score.
    - **Safety bonus**: a large constant added when
      ``intent.safety_override`` is set so a safety-driven zone always
      wins arbitration regardless of occupancy or fan credit.
    """
    if intent.hvac_mode is HVACMode.OFF:
        return 0.0

    control_temp = (
        comfort_temp_override
        if comfort_temp_override is not None
        else effective.temperature
    )
    demand = _mode_temperature_demand(
        intent.hvac_mode,
        control_temp,
        intent.target_temperature,
        zone.demand_deadband,
    )
    comfort = max(0.0, demand - fan_comfort_offset)
    comfort += humidity_priority(zone.humidity, intent.hvac_mode, effective.humidity)
    comfort *= occupancy_comfort_weight(occupancy, zone.setback)

    if intent.safety_override:
        return comfort + SAFETY_PRIORITY_BONUS

    return comfort


def _mode_temperature_demand(
    mode: HVACMode,
    control_temp: float | None,
    target_temp: float | None,
    deadband: float,
) -> float:
    """Return the temperature demand this mode can actually satisfy."""
    if target_temp is None or control_temp is None:
        return 0.0

    if mode is HVACMode.HEAT:
        return max(0.0, target_temp - control_temp - deadband)
    if mode is HVACMode.COOL:
        return max(0.0, control_temp - target_temp - deadband)
    if mode in (HVACMode.AUTO, HVACMode.DRY):
        return max(0.0, abs(control_temp - target_temp) - deadband)
    return 0.0


def _modes_compatible(
    modes: Iterable[HVACMode],
    incompatible_pairs: frozenset[frozenset[HVACMode]],
) -> bool:
    """Return True if no two modes in `modes` form an incompatible pair.

    ``OFF`` is always compatible with anything and is filtered out of the
    pairwise check.
    """
    active = [m for m in modes if m is not HVACMode.OFF]
    for a, b in combinations(active, 2):
        if a == b:
            continue
        if frozenset({a, b}) in incompatible_pairs:
            return False
    return True


def _conflict_summary(
    winning: Mapping[str, HVACMode],
    blocked_zone: str,
    blocked_mode: HVACMode,
    incompatible_pairs: frozenset[frozenset[HVACMode]],
    zone_name_lookup: Mapping[str, str],
) -> str:
    """Human-readable explanation of why a zone was blocked."""
    conflicts: list[str] = []
    for other_id, other_mode in winning.items():
        if other_id == blocked_zone or other_mode is HVACMode.OFF:
            continue
        if frozenset({blocked_mode, other_mode}) in incompatible_pairs:
            other_name = zone_name_lookup.get(other_id, other_id)
            conflicts.append(f"{other_name} is in {other_mode.value}")
    if not conflicts:
        return (
            f"Compressor group cannot run mode {blocked_mode.value} "
            "given current group state"
        )
    return (
        f"Compressor group conflict: requested {blocked_mode.value} but "
        + ", ".join(conflicts)
    )


def arbitrate(
    group: GroupConfig,
    intents: Mapping[str, ResolvedIntent],
    effective_by_zone: Mapping[str, EffectiveReadings],
    occupancy_by_zone: Mapping[str, OccupancyResolution] | None = None,
    comfort_setpoints: Mapping[str, float | None] | None = None,
    fan_offsets: Mapping[str, float] | None = None,
    comfort_temps: Mapping[str, float | None] | None = None,
    allowed_thermal_mode: HVACMode | None = None,
) -> GroupDecision:
    """Select the highest-priority compatible subset of zones for the group.

    Algorithm: enumerate every subset of zones that requested a non-OFF
    mode. For each subset, check pairwise mode compatibility. Pick the
    valid subset whose total priority score is highest. Zones outside the
    winning subset are dispatched OFF with a ``block_reason``. Zones whose
    intent is already OFF pass through unchanged with score 0.

    ``allowed_thermal_mode`` is an optional changeover-safety constraint used
    by the coordinator. When set to ``HEAT`` or ``COOL``, subsets containing
    the opposite thermal mode are excluded while non-thermal modes remain
    eligible if otherwise compatible.

    For typical multi-split installs (2-5 heads per outdoor unit) the
    2^N enumeration is trivial. We cap at 16 zones per group as a safety
    measure; larger groups would need a smarter solver.

    TODO(changeover-throttle): this function is stateless across ticks
    and will happily flip the group's dominant mode (HEAT ↔ COOL) every
    update interval if the per-tick scores oscillate around a
    boundary. Real heat-pump reversing valves are mechanical and rated
    for a finite number of cycles per hour; rapid changeover also
    stresses the compressor (oil migration, liquid slugging risk). We
    need a per-group "changeover throttle" layered on top of arbitration
    that:

      1. Tracks the group's currently-dispatched dominant mode and the
         timestamp of the last changeover (carried on the coordinator,
         not on this pure function).
      2. Adds a configurable ``min_changeover_dwell_minutes`` to
         ``GroupConfig``. The *default* needs research before
         shipping — see TODO(changeover-dwell-default) below. Picking
         too high penalises real comfort transitions (e.g. a cool
         day suddenly turning warm); too low defeats the protection.

         TODO(changeover-dwell-default): survey real residential
         mini-split manufacturer specs (Mitsubishi M-Series, Daikin
         Aurora, Fujitsu Halcyon, LG LMU, Senville, Pioneer, MrCool
         DIY) for documented minimum *changeover* (mode-flip)
         intervals — distinct from minimum on-time / off-time
         compressor cycle floors. Inverter-driven mini-splits are
         meaningfully more tolerant than the older single-stage
         compressors that 5-15 min figures were calibrated for; the
         real number may be 2-5 min for modern residential
         multi-splits. Cross-reference ASHRAE / AHRI guidance and
         field reports from r/HVACAdvice, /r/heatpumps, and the HVAC
         pro forums (HVAC-Talk, ContractorTalk) before committing
         to a default. Also worth checking whether HA's
         ``generic_thermostat.min_cycle_duration`` community has a
         settled rule of thumb for similar concerns.
      3. When ``arbitrate()`` proposes a winning subset whose dominant
         mode differs from the previous tick's, the coordinator should
         either (a) re-run arbitration with the previous dominant mode
         pinned (i.e. exclude subsets that would flip it) until the
         dwell window expires, OR (b) only allow the flip when the new
         winning score exceeds the previous-mode-pinned score by a
         configurable hysteresis margin.
      4. Surface the throttled state via ``block_reason``
         ("changeover_throttled") on zones whose preferred mode lost
         the tiebreak, and via a new ``sensor.<group>_changeover_locked``
         diagnostic so users can see when the throttle is engaging.
      5. Always allow safety-driven changeovers (safety floor / ceiling
         engaged) to bypass the throttle — protecting the room takes
         priority over protecting the equipment when both are at
         stake. Aux-heat activation is the existing escape hatch for
         the heat side; cool side has no equivalent today and may
         need one.

    See also the README "Hardware-safety roadmap" section.
    """
    if len(group.zones) > 16:
        raise ValueError(
            f"Group {group.group_id!r} has {len(group.zones)} zones; "
            "brute-force arbitration is capped at 16. Split the group or "
            "introduce a heuristic solver."
        )

    name_lookup = {z.zone_id: z.name for z in group.zones}
    config_lookup = {z.zone_id: z for z in group.zones}
    occ_lookup = occupancy_by_zone or {}
    comfort_lookup = comfort_setpoints or {}
    fan_lookup = fan_offsets or {}
    comfort_temp_lookup = comfort_temps or {}

    scores: dict[str, float] = {}
    for zone_id, intent in intents.items():
        zone_cfg = config_lookup[zone_id]
        eff = effective_by_zone.get(zone_id, EffectiveReadings.unavailable())
        resolution = occ_lookup.get(zone_id, _default_resolution())
        fan_offset = fan_lookup.get(zone_id, 0.0)
        comfort_temp = comfort_temp_lookup.get(zone_id)
        scores[zone_id] = score_zone(
            zone_cfg, intent, eff, resolution.state, fan_offset, comfort_temp
        )

    active_zone_ids = [
        zid for zid, intent in intents.items() if intent.hvac_mode is not HVACMode.OFF
    ]

    best_subset: tuple[str, ...] = ()
    best_rank = (-1.0, -1)
    for r in range(len(active_zone_ids) + 1):
        for subset in combinations(active_zone_ids, r):
            modes = [intents[zid].hvac_mode for zid in subset]
            if not _modes_compatible(modes, group.incompatible_mode_pairs):
                continue
            if not _matches_allowed_thermal_mode(modes, allowed_thermal_mode):
                continue
            subset_score = sum(scores[zid] for zid in subset)
            rank = (subset_score, len(subset))
            if rank > best_rank:
                best_rank = rank
                best_subset = subset

    winning_modes = {zid: intents[zid].hvac_mode for zid in best_subset}

    decisions: dict[str, ZoneDecision] = {}
    for zone_id, intent in intents.items():
        eff = effective_by_zone.get(zone_id, EffectiveReadings.unavailable())
        resolution = occ_lookup.get(zone_id, _default_resolution())
        comfort_setpoint = comfort_lookup.get(zone_id)
        humidity_contrib = humidity_priority(
            config_lookup[zone_id].humidity, intent.hvac_mode, eff.humidity
        )
        common: dict = {
            "zone_id": zone_id,
            "safety_override": intent.safety_override,
            "priority_score": scores[zone_id],
            "effective": eff,
            "occupancy_state": resolution.state,
            "occupancy_confidence": resolution.confidence,
            "comfort_setpoint": comfort_setpoint,
            "active_setpoint": intent.target_temperature,
            "fan_comfort_offset": fan_lookup.get(zone_id, 0.0),
            "humidity_priority_contribution": humidity_contrib,
            "effective_comfort_temperature": comfort_temp_lookup.get(zone_id),
        }
        if intent.hvac_mode is HVACMode.OFF:
            decisions[zone_id] = ZoneDecision(
                **common,
                dispatched_mode=HVACMode.OFF,
                dispatched_target=None,
                blocked=False,
                block_reason=None,
            )
            continue

        if zone_id in best_subset:
            decisions[zone_id] = ZoneDecision(
                **common,
                dispatched_mode=intent.hvac_mode,
                dispatched_target=intent.target_temperature,
                blocked=False,
                block_reason=intent.safety_reason,
            )
        else:
            reason = _conflict_summary(
                winning_modes,
                zone_id,
                intent.hvac_mode,
                group.incompatible_mode_pairs,
                name_lookup,
            )
            decisions[zone_id] = ZoneDecision(
                **common,
                dispatched_mode=HVACMode.OFF,
                dispatched_target=None,
                blocked=True,
                block_reason=reason,
            )

    return GroupDecision(group_id=group.group_id, zones=decisions)


def _matches_allowed_thermal_mode(
    modes: Iterable[HVACMode],
    allowed_thermal_mode: HVACMode | None,
) -> bool:
    if allowed_thermal_mode is None:
        return True
    if allowed_thermal_mode not in THERMAL_CHANGEOVER_MODES:
        return True
    return all(
        mode not in THERMAL_CHANGEOVER_MODES or mode is allowed_thermal_mode
        for mode in modes
    )
