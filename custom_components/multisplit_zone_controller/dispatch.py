"""Dispatch ZoneDecisions to upstream Home Assistant climate entities.

This module isolates the side-effecting calls into ``hass.services`` so
that the coordinator stays focused on decision-making and so we can stub
the dispatcher easily in integration tests.
"""

from __future__ import annotations

import logging
from typing import Mapping

from homeassistant.const import ATTR_ENTITY_ID, ATTR_TEMPERATURE
from homeassistant.core import HomeAssistant

from .models import GroupDecision, HVACMode, ZoneConfig
from .units import from_celsius, hass_temperature_unit

_LOGGER = logging.getLogger(__name__)

CLIMATE_DOMAIN = "climate"
SERVICE_SET_HVAC_MODE = "set_hvac_mode"
SERVICE_SET_TEMPERATURE = "set_temperature"


class HeadDispatcher:
    """Dispatches changes to upstream head climate entities.

    Behaviour is controlled per zone by ``ZoneConfig.always_assert_head_state``:

    * **False (default)** — de-duplicate against the last ``(mode, target)``
      sent to each head. Quiet, doesn't trigger an IR beep on every
      coordinator tick, but if someone retargets the head with the
      physical remote the integration won't notice until our next
      *intentional* state change.
    * **True** — re-issue ``set_hvac_mode`` (and ``set_temperature`` when
      the mode is non-OFF) on every tick, so an out-of-band remote press
      is corrected within one update interval. Use only on heads that
      don't beep on every IR command.

    Resilience: every per-zone dispatch is wrapped in try/except so that
    a single misbehaving head (rejected mode, offline, vendor-integration
    bug) cannot abort the coordinator update for the whole group. On
    failure the last-sent cache is *not* updated so the same command
    will be retried next tick. The error is logged with full context
    once per (entity, mode/target) tuple to avoid spamming the log.

    Pre-flight capability check: if the head advertises an
    ``hvac_modes`` attribute and the desired mode isn't in it, the
    dispatcher logs a warning and skips the call entirely instead of
    triggering a ``ServiceValidationError`` from the climate platform.

    TODO: a smarter implementation would listen for upstream
    ``state_changed`` events on each head entity, ignore changes whose
    context-id matches one of our own writes, and re-dispatch only when
    real drift is detected. That avoids both the beep noise and the
    obedience gap, but needs careful handling of HA restarts and
    eventual-consistency on the upstream device. The boolean flag is
    the deliberate interim solution.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._last_mode: dict[str, HVACMode] = {}
        self._last_target: dict[str, float | None] = {}
        # Suppression caches: keyed by (entity_id, mode_value) for
        # capability-check warnings and (entity_id, mode_value) for
        # dispatch errors. We log once per distinct failure tuple and
        # clear the entry on a successful dispatch so that recovery is
        # observable.
        self._warned_unsupported: set[tuple[str, str]] = set()
        self._errored_modes: set[tuple[str, str]] = set()
        # Last-known set of HVAC modes per head, refreshed every time we
        # see the head's state. Used by the capability pre-check.
        self._head_supported_modes: dict[str, frozenset[str]] = {}

    async def apply(
        self,
        zones: Mapping[str, ZoneConfig],
        decision: GroupDecision,
    ) -> None:
        """Apply each zone's dispatched mode/target. One zone's failure
        does not affect the others.
        """
        for zone_id, zone_decision in decision.zones.items():
            zone_cfg = zones[zone_id]
            try:
                await self._apply_one(zone_id, zone_cfg, zone_decision)
            except Exception:
                # Belt-and-braces: _apply_one already catches its own
                # exceptions; this is here so a programming bug in the
                # dispatcher itself can't take down the whole loop.
                _LOGGER.exception(
                    "Unexpected error dispatching zone %s; continuing with "
                    "remaining zones",
                    zone_id,
                )

    async def _apply_one(
        self,
        zone_id: str,
        zone_cfg: ZoneConfig,
        zone_decision,
    ) -> None:
        head_entity = zone_cfg.head_climate_entity
        mode = zone_decision.dispatched_mode
        target = zone_decision.dispatched_target
        assert_every_tick = zone_cfg.always_assert_head_state

        self._refresh_supported_modes(head_entity)

        mode_changed = self._last_mode.get(head_entity) != mode
        if mode_changed or assert_every_tick:
            if not self._head_supports_mode(head_entity, mode):
                # Pre-flight: skip the call entirely; logging is rate
                # limited inside _head_supports_mode.
                return
            ok = await self._call_set_hvac_mode(zone_id, head_entity, mode)
            if not ok:
                # Don't update cache on failure so we'll retry next
                # tick. Don't attempt the temperature write either —
                # an off head doesn't accept setpoints.
                return
            self._last_mode[head_entity] = mode
            _LOGGER.debug(
                "Dispatched hvac_mode=%s to %s (zone %s, %s)",
                mode.value,
                head_entity,
                zone_id,
                "re-asserted" if assert_every_tick and not mode_changed else "changed",
            )

        target_changed = self._last_target.get(head_entity) != target
        if (
            target is not None
            and mode is not HVACMode.OFF
            and (target_changed or assert_every_tick)
        ):
            # HA's climate.set_temperature service interprets
            # `temperature` in the user-display unit and converts
            # internally to the entity's native unit. Convert from
            # our internal Celsius value into HA's user unit so the
            # round-trip preserves the intended setpoint.
            target_in_user_unit = from_celsius(
                target, hass_temperature_unit(self._hass)
            )
            ok = await self._call_set_temperature(
                zone_id, head_entity, target_in_user_unit
            )
            if not ok:
                return
            self._last_target[head_entity] = target
            _LOGGER.debug(
                "Dispatched temperature=%.2f°C (sent as %.2f to %s) for zone %s",
                target,
                target_in_user_unit,
                head_entity,
                zone_id,
            )

    # --- service-call wrappers -------------------------------------------

    async def _call_set_hvac_mode(
        self,
        zone_id: str,
        head_entity: str,
        mode: HVACMode,
    ) -> bool:
        """Returns True on success, False on caught exception."""
        key = (head_entity, mode.value)
        try:
            await self._hass.services.async_call(
                CLIMATE_DOMAIN,
                SERVICE_SET_HVAC_MODE,
                {ATTR_ENTITY_ID: head_entity, "hvac_mode": mode.value},
                blocking=True,
            )
        except Exception as err:
            if key not in self._errored_modes:
                _LOGGER.error(
                    "Dispatch failed for zone %s: set_hvac_mode(%s) on %s "
                    "raised %s: %s. Will retry next tick.",
                    zone_id,
                    mode.value,
                    head_entity,
                    type(err).__name__,
                    err,
                )
                self._errored_modes.add(key)
            return False
        # Recovery: clear the suppression entry so a future failure on
        # the same key gets logged again.
        self._errored_modes.discard(key)
        return True

    async def _call_set_temperature(
        self,
        zone_id: str,
        head_entity: str,
        temperature_in_user_unit: float,
    ) -> bool:
        key = (head_entity, "set_temperature")
        try:
            await self._hass.services.async_call(
                CLIMATE_DOMAIN,
                SERVICE_SET_TEMPERATURE,
                {
                    ATTR_ENTITY_ID: head_entity,
                    ATTR_TEMPERATURE: temperature_in_user_unit,
                },
                blocking=True,
            )
        except Exception as err:
            if key not in self._errored_modes:
                _LOGGER.error(
                    "Dispatch failed for zone %s: set_temperature(%.2f) on "
                    "%s raised %s: %s. Will retry next tick.",
                    zone_id,
                    temperature_in_user_unit,
                    head_entity,
                    type(err).__name__,
                    err,
                )
                self._errored_modes.add(key)
            return False
        self._errored_modes.discard(key)
        return True

    # --- capability check ------------------------------------------------

    def _refresh_supported_modes(self, head_entity: str) -> None:
        """Read the head's ``hvac_modes`` attribute into the cache.

        We only update on a non-empty read so that transient
        unavailability (head briefly missing from the state machine
        during a restart) doesn't spuriously erase the cached
        capability set.
        """
        state = self._hass.states.get(head_entity)
        if state is None:
            return
        modes = state.attributes.get("hvac_modes") or []
        if not modes:
            return
        self._head_supported_modes[head_entity] = frozenset(
            str(m) for m in modes
        )

    def _head_supports_mode(self, head_entity: str, mode: HVACMode) -> bool:
        """True if the head advertises support for ``mode``.

        If we've never seen the head's ``hvac_modes`` attribute, return
        True optimistically — the service call may still succeed, and
        if it doesn't the per-call try/except will catch and log it.
        """
        supported = self._head_supported_modes.get(head_entity)
        if supported is None:
            return True
        if mode.value in supported:
            # Recovery: clear any previous unsupported-mode warning so
            # transient capability changes (head re-onboarded, vendor
            # integration reload) get re-warned on next mismatch.
            self._warned_unsupported.discard((head_entity, mode.value))
            return True
        # Capability mismatch: warn once per (entity, mode) tuple.
        key = (head_entity, mode.value)
        if key not in self._warned_unsupported:
            _LOGGER.warning(
                "Skipping dispatch for %s: head advertises hvac_modes=%s, "
                "which does not include the requested mode %r. "
                "Either pick a different mode in the managed climate "
                "entity, or replace the upstream head with one that "
                "supports it.",
                head_entity,
                sorted(supported),
                mode.value,
            )
            self._warned_unsupported.add(key)
        return False
