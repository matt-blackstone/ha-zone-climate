"""Dispatch FanCommands to upstream Home Assistant fan entities.

Mirrors :mod:`dispatch` for the fan domain. Like the head dispatcher
the fan dispatcher re-issues the desired settings on every coordinator
tick so that an out-of-band remote / wall switch press is corrected
within one update interval.
"""

from __future__ import annotations

import logging
from typing import Mapping

from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant

from .models import FanCommand, GroupDecision, ZoneConfig

_LOGGER = logging.getLogger(__name__)

FAN_DOMAIN = "fan"
SERVICE_TURN_ON = "turn_on"
SERVICE_TURN_OFF = "turn_off"
SERVICE_SET_PERCENTAGE = "set_percentage"
SERVICE_SET_DIRECTION = "set_direction"


class FanDispatcher:
    """Apply per-zone fan commands every coordinator tick.

    Resilience: a single zone's failed fan dispatch does not abort the
    coordinator update for the rest of the group. Errors are caught,
    logged once per (entity, error-key) tuple to avoid log-spam, and
    the loop continues.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._errored: set[tuple[str, str]] = set()

    async def apply(
        self,
        zones: Mapping[str, ZoneConfig],
        decision: GroupDecision,
    ) -> None:
        for zone_id, zone_decision in decision.zones.items():
            zone_cfg = zones[zone_id]
            cmd: FanCommand | None = zone_decision.fan_command
            if cmd is None or not zone_cfg.fan.enabled:
                continue
            entity_id = zone_cfg.fan.fan_entity_id
            if entity_id is None:
                continue
            try:
                await self._apply_one(zone_id, entity_id, cmd)
            except Exception:
                _LOGGER.exception(
                    "Unexpected error dispatching fan for zone %s; "
                    "continuing with remaining zones",
                    zone_id,
                )

    async def _apply_one(
        self, zone_id: str, entity_id: str, cmd: FanCommand
    ) -> None:
        if not cmd.on:
            ok = await self._safe_call(
                zone_id,
                entity_id,
                SERVICE_TURN_OFF,
                {ATTR_ENTITY_ID: entity_id},
            )
            if ok:
                _LOGGER.debug("Fan %s -> turn_off", entity_id)
            return

        # Always assert the desired speed (which implicitly turns the
        # fan on if it was off) and direction every tick.
        speed_ok = await self._safe_call(
            zone_id,
            entity_id,
            SERVICE_SET_PERCENTAGE,
            {ATTR_ENTITY_ID: entity_id, "percentage": cmd.speed_pct},
        )
        dir_ok = await self._safe_call(
            zone_id,
            entity_id,
            SERVICE_SET_DIRECTION,
            {ATTR_ENTITY_ID: entity_id, "direction": cmd.direction.value},
        )
        if speed_ok and dir_ok:
            _LOGGER.debug(
                "Fan %s -> on speed=%d direction=%s",
                entity_id,
                cmd.speed_pct,
                cmd.direction.value,
            )

    async def _safe_call(
        self,
        zone_id: str,
        entity_id: str,
        service: str,
        payload: dict,
    ) -> bool:
        """Wrap one ``hass.services.async_call`` with rate-limited
        error logging. Returns True on success, False on caught error.
        """
        key = (entity_id, service)
        try:
            await self._hass.services.async_call(
                FAN_DOMAIN, service, payload, blocking=True
            )
        except Exception as err:
            if key not in self._errored:
                _LOGGER.error(
                    "Fan dispatch failed for zone %s: fan.%s on %s "
                    "raised %s: %s. Will retry next tick.",
                    zone_id,
                    service,
                    entity_id,
                    type(err).__name__,
                    err,
                )
                self._errored.add(key)
            return False
        self._errored.discard(key)
        return True
