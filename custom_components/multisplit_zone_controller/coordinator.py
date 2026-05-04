"""Per-group DataUpdateCoordinator.

Owns user intent for every zone in the group, gathers upstream state,
runs sensor fusion, applies safety, arbitrates the legal subset, and
dispatches the outcome to the upstream head climate entities.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Mapping

from homeassistant.core import HomeAssistant, State
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .arbitration import arbitrate
from .aux_heat import AuxHeatRuntime, evaluate_aux_heat
from .aux_heat_dispatch import AuxHeatDispatcher
from .comfort import apparent_temp_credit, fan_command_for
from .dispatch import HeadDispatcher
from .fan_proxy import FanDispatcher
from .invariants import incompatible_mode_violations
from .models import (
    AuxHeatInputs,
    AuxHeatTrigger,
    EffectiveReadings,
    FanCommand,
    FusionStrategy,
    GroupConfig,
    GroupDecision,
    HVACMode,
    OccupancyResolution,
    OccupancySignalKind,
    OccupancySignalReading,
    OccupancyState,
    ResolvedIntent,
    SensorReading,
    SensorRef,
    ZoneConfig,
    ZoneIntent,
)
from .occupancy import resolve_occupancy, setback_adjust
from .preconditioning import (
    RateEstimator,
    expected_timed_out,
    lead_time_minutes,
)
from .psychrometrics import effective_comfort_temp
from .safety import apply_safety
from .sensor_fusion import fuse
from .units import hass_temperature_unit, state_temperature_in_celsius

_LOGGER = logging.getLogger(__name__)


class GroupCoordinator(DataUpdateCoordinator[GroupDecision]):
    """One coordinator per compressor group.

    User intent is held in ``self.intents`` and mutated by managed
    ``ClimateEntity`` instances via :meth:`set_intent`. Each tick produces
    a fresh ``GroupDecision`` exposed via ``self.data`` and consumed by
    the climate and diagnostic entities.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        group: GroupConfig,
        dispatcher: HeadDispatcher | None = None,
        fan_dispatcher: FanDispatcher | None = None,
        aux_dispatcher: AuxHeatDispatcher | None = None,
        config_entry: object | None = None,
    ) -> None:
        # ``config_entry`` is optional: YAML setup has no entry while
        # config-entry setup needs it so DataUpdateCoordinator can call
        # ``async_config_entry_first_refresh`` without raising. We
        # forward it to the base class only when it's actually present
        # to preserve back-compat with HA versions that don't accept
        # the kwarg yet.
        base_kwargs: dict[str, object] = {
            "name": f"multisplit_zone_controller[{group.group_id}]",
            "update_interval": group.update_interval,
        }
        if config_entry is not None:
            base_kwargs["config_entry"] = config_entry
        super().__init__(hass, _LOGGER, **base_kwargs)
        self.group = group
        self.intents: dict[str, ZoneIntent] = {
            z.zone_id: ZoneIntent(
                zone_id=z.zone_id,
                hvac_mode=HVACMode.OFF,
                target_temperature=z.default_target_temperature,
            )
            for z in group.zones
        }
        self._dispatcher = dispatcher or HeadDispatcher(hass)
        self._fan_dispatcher = fan_dispatcher or FanDispatcher(hass)
        self._aux_dispatcher = aux_dispatcher or AuxHeatDispatcher(hass)
        self._aux_runtimes: dict[str, AuxHeatRuntime] = {
            z.zone_id: AuxHeatRuntime(z.aux_heat) for z in group.zones
        }
        self._zones_by_id: dict[str, ZoneConfig] = {
            z.zone_id: z for z in group.zones
        }
        self._last_confirmed_at: dict[str, datetime | None] = {
            z.zone_id: None for z in group.zones
        }
        self._expected_since: dict[str, datetime | None] = {
            z.zone_id: None for z in group.zones
        }
        self._rate_estimators: dict[str, RateEstimator] = {
            z.zone_id: RateEstimator(z.pre_conditioning) for z in group.zones
        }
        self._last_dispatched_mode: dict[str, HVACMode] = {
            z.zone_id: HVACMode.OFF for z in group.zones
        }
        # Suppression cache for the post-dispatch incompatible-mode
        # invariant log. Keyed by ``(zone_a, mode_a, zone_b, mode_b)``
        # so a violation that flips between modes still gets logged
        # once per distinct pairing rather than spamming every tick.
        self._invariant_warned: set[tuple[str, str, str, str]] = set()
        # Suppression cache for the one-time startup capability
        # warning, keyed by ``(zone_id, missing_mode)``.
        self._capability_warned: set[tuple[str, str]] = set()

    def set_intent(self, intent: ZoneIntent) -> None:
        """Replace the stored intent for one zone.

        The caller is expected to ``await coordinator.async_request_refresh()``
        afterwards if it wants the change to take effect immediately rather
        than waiting for the next scheduled tick.
        """
        if intent.zone_id not in self._zones_by_id:
            raise KeyError(intent.zone_id)
        self.intents[intent.zone_id] = intent

    async def _async_update_data(self) -> GroupDecision:
        now = datetime.now(tz=timezone.utc)

        effective_by_zone = {
            z.zone_id: self._fuse_zone(z) for z in self.group.zones
        }

        # Feed the rate estimators with the latest observation under the
        # mode we last dispatched. This must happen before pre-conditioning
        # so the lead-time math uses the freshest learned rate.
        for z in self.group.zones:
            self._rate_estimators[z.zone_id].observe(
                now,
                effective_by_zone[z.zone_id].temperature,
                self._last_dispatched_mode[z.zone_id],
            )

        occupancy_by_zone: dict[str, OccupancyResolution] = {}
        for z in self.group.zones:
            readings = self._read_occupancy_signals(z)
            resolution = resolve_occupancy(
                z.occupancy,
                readings,
                self._last_confirmed_at[z.zone_id],
                now,
            )
            resolution = self._apply_expected_timeout(z, resolution, now)
            if resolution.state is OccupancyState.CONFIRMED:
                self._last_confirmed_at[z.zone_id] = now
            occupancy_by_zone[z.zone_id] = resolution

        comfort_setpoints = {
            z.zone_id: self.intents[z.zone_id].target_temperature
            for z in self.group.zones
        }

        adjusted_intents = {
            z.zone_id: setback_adjust(
                self.intents[z.zone_id],
                occupancy_by_zone[z.zone_id].state,
                z.setback,
            )
            for z in self.group.zones
        }

        resolved = {
            z.zone_id: apply_safety(
                z, adjusted_intents[z.zone_id], effective_by_zone[z.zone_id]
            )
            for z in self.group.zones
        }

        fan_offsets = {
            z.zone_id: self._fan_offset_for(
                z, resolved[z.zone_id], effective_by_zone[z.zone_id], occupancy_by_zone[z.zone_id].state
            )
            for z in self.group.zones
        }

        comfort_temps = self._comfort_temps_if_enabled(
            effective_by_zone, occupancy_by_zone
        )

        decision = arbitrate(
            self.group,
            resolved,
            effective_by_zone,
            occupancy_by_zone,
            comfort_setpoints,
            fan_offsets,
            comfort_temps,
        )
        decision = self._enrich_with_preconditioning(decision, effective_by_zone)
        decision = self._enrich_with_fan_commands(decision, effective_by_zone, occupancy_by_zone)
        decision = self._enrich_with_aux_heat(decision, effective_by_zone, now)
        # Startup capability sanity check: warn loudly the first time
        # we see a head whose advertised hvac_modes don't include the
        # full set the integration's managed climate exposes. Catches
        # most "I picked the wrong head_climate entity" misconfigs
        # before a user encounters a runtime ServiceValidationError.
        # Cheap (just attribute reads), runs every tick because heads
        # may register late; the suppression cache makes it idempotent.
        self._check_head_capabilities()

        await self._dispatcher.apply(self._zones_by_id, decision)
        await self._fan_dispatcher.apply(self._zones_by_id, decision)
        await self._aux_dispatcher.apply(self._zones_by_id, decision)

        # Post-dispatch invariant: if the arbiter ever produces a
        # decision where two zones run in mutually-incompatible modes,
        # that's a bug in arbitration (or a corrupted compat config) we
        # want to surface immediately. ERROR-level so it shows up red
        # in the UI's log panel.
        self._check_dispatch_invariants(decision)

        for zone_id, zone_decision in decision.zones.items():
            self._last_dispatched_mode[zone_id] = zone_decision.dispatched_mode

        return decision

    def _check_dispatch_invariants(self, decision: GroupDecision) -> None:
        """Log a loud ERROR if arbitration emitted an incompatible
        mode set. The check itself is pure logic in ``invariants`` so
        it is fully unit-tested without a HomeAssistant instance.
        """
        violations = incompatible_mode_violations(
            decision, self.group.incompatible_mode_pairs
        )
        observed: set[tuple[str, str, str, str]] = set()
        for zone_a, zone_b, mode_a, mode_b in violations:
            key = (zone_a, mode_a.value, zone_b, mode_b.value)
            observed.add(key)
            if key in self._invariant_warned:
                continue
            _LOGGER.error(
                "Group %s: arbitration produced an incompatible mode "
                "combination — zone %r dispatched %s while zone %r "
                "dispatched %s. This pair is in incompatible_mode_pairs "
                "and should never run concurrently. This indicates an "
                "arbitration bug or a corrupted compatibility config; "
                "please open an issue with the coordinator's debug log.",
                self.group.group_id,
                zone_a,
                mode_a.value,
                zone_b,
                mode_b.value,
            )
            self._invariant_warned.add(key)
        # Recovery: clear suppression entries that no longer apply, so
        # if the same violation reappears later it gets logged again.
        self._invariant_warned &= observed

    def _check_head_capabilities(self) -> None:
        """Warn once per (zone, missing-mode) when a head doesn't
        advertise a mode the managed climate entity exposes.

        The integration's managed entity always offers
        {OFF, HEAT, COOL, AUTO, FAN_ONLY, DRY} so the user CAN ask
        for any of them; we want to flag heads that physically can't
        receive certain modes before the user discovers it the hard
        way (rejected service call).
        """
        # Modes the managed climate entity may pass to dispatch — kept
        # in sync with ManagedZoneClimate._attr_hvac_modes. OFF is
        # universally supported so we don't bother flagging that.
        required = {
            HVACMode.HEAT,
            HVACMode.COOL,
            HVACMode.AUTO,
            HVACMode.FAN_ONLY,
            HVACMode.DRY,
        }
        for zone in self.group.zones:
            head_entity = zone.head_climate_entity
            state = self.hass.states.get(head_entity)
            if state is None:
                continue
            advertised = state.attributes.get("hvac_modes") or []
            if not advertised:
                continue
            advertised_set = {str(m) for m in advertised}
            missing = sorted(
                m.value for m in required if m.value not in advertised_set
            )
            if not missing:
                # Recovery: a head that previously flagged but now
                # supports everything required clears its warnings so
                # later regressions get re-logged.
                self._capability_warned = {
                    (zid, mv)
                    for (zid, mv) in self._capability_warned
                    if zid != zone.zone_id
                }
                continue
            for mode_value in missing:
                key = (zone.zone_id, mode_value)
                if key in self._capability_warned:
                    continue
                _LOGGER.warning(
                    "Zone %r is wired to head %s, which advertises "
                    "hvac_modes=%s. The managed climate entity exposes "
                    "%r, so any user request for that mode will be "
                    "skipped (with a per-call warning) by the dispatcher. "
                    "If this is intentional, ignore. Otherwise replace "
                    "the head_climate entity with one that supports the "
                    "modes you want to expose.",
                    zone.zone_id,
                    head_entity,
                    sorted(advertised_set),
                    mode_value,
                )
                self._capability_warned.add(key)

    def _enrich_with_aux_heat(
        self,
        decision: GroupDecision,
        effective_by_zone: dict,
        now: datetime,
    ) -> GroupDecision:
        from dataclasses import replace

        enriched: dict = {}
        for zone_id, zone_decision in decision.zones.items():
            zone_cfg = self._zones_by_id[zone_id]
            cfg = zone_cfg.aux_heat
            runtime = self._aux_runtimes[zone_id]
            eff = effective_by_zone[zone_id]
            outdoor_temp = self._read_outdoor_temp(cfg.outdoor_temp_sensor)
            head_available = self._head_available(zone_cfg.head_climate_entity)
            inputs = AuxHeatInputs(
                intent_mode=self.intents[zone_id].hvac_mode,
                effective_temp=eff.temperature,
                safety_floor=zone_cfg.safety.min_temp,
                outdoor_temp=outdoor_temp,
                head_available=head_available,
                currently_active=runtime.actual_active,
            )
            policy_decision = evaluate_aux_heat(cfg, inputs)
            outcome = runtime.step(policy_decision, now)
            new_mode = zone_decision.dispatched_mode
            new_target = zone_decision.dispatched_target
            new_block_reason = zone_decision.block_reason
            if outcome.force_head_off:
                new_mode = HVACMode.OFF
                new_target = None
                if outcome.aux_on:
                    new_block_reason = (
                        outcome.decision.status_message or new_block_reason
                    )
                elif outcome.in_settling:
                    new_block_reason = "Aux heat settling — head off temporarily"
            enriched[zone_id] = replace(
                zone_decision,
                dispatched_mode=new_mode,
                dispatched_target=new_target,
                block_reason=new_block_reason,
                aux_heat_active=outcome.aux_on,
                aux_heat_trigger=outcome.decision.trigger,
                aux_heat_status=outcome.decision.status_message,
            )
        return GroupDecision(group_id=decision.group_id, zones=enriched)

    def _read_outdoor_temp(self, entity_id: str | None) -> float | None:
        if entity_id is None:
            return None
        state = self.hass.states.get(entity_id)
        if state is None:
            return None
        return _coerce_state_value(state, hass_temperature_unit(self.hass))

    def _head_available(self, entity_id: str) -> bool:
        state = self.hass.states.get(entity_id)
        if state is None:
            return False
        return state.state not in ("unavailable", "unknown", None, "")

    def _comfort_temps_if_enabled(
        self,
        effective_by_zone: dict,
        occupancy_by_zone: dict,
    ) -> dict[str, float | None] | None:
        """Compute effective_comfort_temp per zone when the flag is set, else None.

        Returning ``None`` lets ``arbitrate`` skip the override entirely
        and use raw dry-bulb temperatures, preserving the default
        behaviour from earlier phases.
        """
        if not self.group.use_psychrometric_scoring:
            return None
        comfort: dict[str, float | None] = {}
        for z in self.group.zones:
            eff = effective_by_zone[z.zone_id]
            occ = occupancy_by_zone[z.zone_id].state
            occupied = occ in (
                OccupancyState.CONFIRMED,
                OccupancyState.RECENT,
                OccupancyState.EXPECTED,
            )
            comfort[z.zone_id] = effective_comfort_temp(
                eff.temperature,
                eff.humidity,
                None,  # fan speed populated post-arbitration; first-cut uses 0
                occupied,
            )
        return comfort

    def _fan_offset_for(
        self,
        zone: ZoneConfig,
        intent: ResolvedIntent,
        effective: EffectiveReadings,
        occupancy: OccupancyState,
    ) -> float:
        if intent.target_temperature is None or effective.temperature is None:
            return 0.0
        deviation = effective.temperature - intent.target_temperature
        return apparent_temp_credit(
            deviation, zone.fan, occupancy, self._fan_locked_out(zone)
        )

    def _fan_locked_out(self, zone: ZoneConfig) -> bool:
        if zone.fan.lockout_entity_id is None:
            return False
        state = self.hass.states.get(zone.fan.lockout_entity_id)
        return _coerce_occupancy_active(state, OccupancySignalKind.PRESENCE)

    def _enrich_with_fan_commands(
        self,
        decision: GroupDecision,
        effective_by_zone: dict,
        occupancy_by_zone: dict,
    ) -> GroupDecision:
        from dataclasses import replace

        enriched: dict = {}
        for zone_id, zone_decision in decision.zones.items():
            zone_cfg = self._zones_by_id[zone_id]
            eff = effective_by_zone[zone_id]
            target = zone_decision.dispatched_target
            if target is None or eff.temperature is None:
                deviation = 0.0
            else:
                deviation = eff.temperature - target
            cmd = fan_command_for(
                zone_decision.dispatched_mode,
                deviation,
                zone_cfg.fan,
                occupancy_by_zone[zone_id].state,
                self._fan_locked_out(zone_cfg),
            )
            enriched[zone_id] = replace(zone_decision, fan_command=cmd)
        return GroupDecision(group_id=decision.group_id, zones=enriched)

    def _apply_expected_timeout(
        self,
        zone: ZoneConfig,
        resolution: OccupancyResolution,
        now: datetime,
    ) -> OccupancyResolution:
        """Track ``EXPECTED`` duration and downgrade to ``UNOCCUPIED`` on timeout."""
        if not zone.pre_conditioning.enabled:
            self._expected_since[zone.zone_id] = None
            return resolution
        if resolution.state is OccupancyState.EXPECTED:
            if self._expected_since[zone.zone_id] is None:
                self._expected_since[zone.zone_id] = now
            if expected_timed_out(
                self._expected_since[zone.zone_id], now, zone.pre_conditioning
            ):
                return OccupancyResolution(
                    state=OccupancyState.UNOCCUPIED,
                    confidence=resolution.confidence,
                    active_sources=resolution.active_sources,
                )
        else:
            self._expected_since[zone.zone_id] = None
        return resolution

    def _enrich_with_preconditioning(
        self,
        decision: GroupDecision,
        effective_by_zone: dict,
    ) -> GroupDecision:
        """Attach lead-time + learned-rate diagnostics to the decision."""
        from dataclasses import replace

        enriched: dict = {}
        for zone_id, zone_decision in decision.zones.items():
            zone_cfg = self._zones_by_id[zone_id]
            est = self._rate_estimators[zone_id]
            current_temp = effective_by_zone[zone_id].temperature
            lead = lead_time_minutes(
                current_temp,
                zone_decision.comfort_setpoint,
                est.heating_rate,
                est.cooling_rate,
                zone_cfg.pre_conditioning.max_lead_minutes,
            )
            enriched[zone_id] = replace(
                zone_decision,
                predicted_lead_time_minutes=lead,
                learned_heating_rate=est.learned_heating_rate,
                learned_cooling_rate=est.learned_cooling_rate,
            )
        return GroupDecision(group_id=decision.group_id, zones=enriched)

    def _read_occupancy_signals(
        self, zone: ZoneConfig
    ) -> tuple[OccupancySignalReading, ...]:
        readings: list[OccupancySignalReading] = []
        for source in zone.occupancy.sources:
            state = self.hass.states.get(source.entity_id)
            is_active = _coerce_occupancy_active(state, source.kind)
            readings.append(
                OccupancySignalReading(source=source, is_active=is_active)
            )
        return tuple(readings)

    def _fuse_zone(self, zone: ZoneConfig) -> EffectiveReadings:
        stale_after = zone.fusion.stale_after_seconds
        head_temp = self._read_optional(zone.fusion.head_temp_sensor, stale_after)
        head_hum = self._read_optional(zone.fusion.head_humidity_sensor, stale_after)
        ext_temps = tuple(
            self._read_ref(ref, stale_after)
            for ref in zone.fusion.external_temp_sensors
        )
        ext_hums = tuple(
            self._read_ref(ref, stale_after)
            for ref in zone.fusion.external_humidity_sensors
        )
        if zone.fusion.strategy is FusionStrategy.OCCUPANCY_WEIGHTED:
            ext_temps = self._apply_occupancy_boost(zone, ext_temps)
            ext_hums = self._apply_occupancy_boost(zone, ext_hums)
        return fuse(zone.fusion, head_temp, head_hum, ext_temps, ext_hums)

    def _apply_occupancy_boost(
        self, zone: ZoneConfig, readings: tuple[SensorReading, ...]
    ) -> tuple[SensorReading, ...]:
        boost = zone.fusion.occupancy_boost
        if boost <= 1.0:
            return readings
        # Map sensor_entity_id -> occupancy_source via the original SensorRef list.
        ref_lookup: dict[str, SensorRef] = {}
        for ref in (
            *zone.fusion.external_temp_sensors,
            *zone.fusion.external_humidity_sensors,
        ):
            ref_lookup[ref.entity_id] = ref
        boosted: list[SensorReading] = []
        for reading in readings:
            ref = ref_lookup.get(reading.entity_id)
            if ref is None or ref.occupancy_source is None:
                boosted.append(reading)
                continue
            state = self.hass.states.get(ref.occupancy_source)
            if state is None or not _coerce_occupancy_active(
                state, OccupancySignalKind.PRESENCE
            ):
                boosted.append(reading)
                continue
            boosted.append(
                SensorReading(
                    entity_id=reading.entity_id,
                    value=reading.value,
                    weight=reading.weight * boost,
                    calibration_offset=reading.calibration_offset,
                )
            )
        return tuple(boosted)

    def _read_optional(
        self, entity_id: str | None, stale_after_seconds: float | None
    ) -> SensorReading | None:
        if entity_id is None:
            return None
        return self._read_ref(SensorRef(entity_id=entity_id), stale_after_seconds)

    def _read_ref(
        self, ref: SensorRef, stale_after_seconds: float | None
    ) -> SensorReading:
        state = self.hass.states.get(ref.entity_id)
        if state is None:
            return SensorReading(
                entity_id=ref.entity_id,
                value=None,
                weight=ref.weight,
                calibration_offset=ref.calibration_offset,
            )
        value = _coerce_state_value(state, hass_temperature_unit(self.hass))
        if value is not None and stale_after_seconds is not None and _is_stale(
            state, stale_after_seconds
        ):
            value = None
        return SensorReading(
            entity_id=ref.entity_id,
            value=value,
            weight=ref.weight,
            calibration_offset=ref.calibration_offset,
        )


_PRESENCE_TRUTHY: frozenset[str] = frozenset(
    {"on", "home", "true", "1", "occupied", "active"}
)


def _coerce_occupancy_active(
    state: State | None, kind: OccupancySignalKind
) -> bool:
    """Translate an HA state into a boolean 'this signal is active' flag.

    All currently supported kinds (``presence``, ``calendar``, ``schedule``,
    ``manual_override``) are interpreted as boolean truthiness. ``person``
    entities use ``home`` for the active state; ``calendar`` entities use
    ``on`` while an event is active; ``input_boolean`` and ``binary_sensor``
    use ``on``.
    """
    if state is None or state.state in (None, "unknown", "unavailable", ""):
        return False
    return state.state.lower() in _PRESENCE_TRUTHY


def _is_stale(state: State, stale_after_seconds: float) -> bool:
    """Return True when the state has not been updated within the threshold."""
    last = state.last_updated
    if last is None:
        return False
    age = (datetime.now(tz=timezone.utc) - last).total_seconds()
    return age > stale_after_seconds


def _coerce_state_value(
    state: State, default_unit: str | None = None
) -> float | None:
    """Interpret an HA state object as a Celsius float reading.

    Delegates to :func:`units.state_temperature_in_celsius`. The
    ``default_unit`` argument should be HA's user-display unit
    (``hass.config.units.temperature_unit``); it's used as the fallback
    when the source entity does not advertise its unit on the state
    (notably ``ClimateEntity``, which serialises ``current_temperature``
    already-converted to the user-display unit but without a
    ``temperature_unit`` attribute).
    """
    return state_temperature_in_celsius(state, default_unit)


def by_group_id(
    coordinators: Mapping[str, GroupCoordinator], group_id: str
) -> GroupCoordinator:
    return coordinators[group_id]
