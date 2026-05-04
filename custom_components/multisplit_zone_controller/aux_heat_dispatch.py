"""Dispatch auxiliary heat commands to either a switch or a climate device."""

from __future__ import annotations

import logging
from typing import Mapping

from homeassistant.const import ATTR_ENTITY_ID, ATTR_TEMPERATURE
from homeassistant.core import HomeAssistant

from .models import AuxHeatDeviceType, GroupDecision, ZoneConfig
from .units import from_celsius, hass_temperature_unit

_LOGGER = logging.getLogger(__name__)

CLIMATE_DOMAIN = "climate"
SWITCH_DOMAIN = "switch"
SERVICE_TURN_ON = "turn_on"
SERVICE_TURN_OFF = "turn_off"
SERVICE_SET_HVAC_MODE = "set_hvac_mode"
SERVICE_SET_TEMPERATURE = "set_temperature"


class AuxHeatDispatcher:
    """Activate or deactivate the per-zone resistive heater.

    A switch-type device just receives turn_on/turn_off. A climate-type
    device is set to ``heat`` mode and (optionally) a configured
    ``aux_target_temperature`` when activated, and to ``off`` when
    deactivated.

    Like the head dispatcher we re-issue the desired state on every
    coordinator tick so that an out-of-band switch flip / remote press
    is corrected within one update interval.

    Resilience: a single zone's failed aux dispatch does not abort the
    coordinator update for the rest of the group. Errors are caught,
    logged once per (entity, service) tuple to avoid log-spam, and the
    loop continues.
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
            aux_cfg = zone_cfg.aux_heat
            if not aux_cfg.enabled or aux_cfg.device_entity_id is None:
                continue
            entity_id = aux_cfg.device_entity_id
            desired = zone_decision.aux_heat_active
            try:
                await self._dispatch(zone_id, entity_id, aux_cfg, desired)
            except Exception:
                _LOGGER.exception(
                    "Unexpected error dispatching aux heat for zone %s; "
                    "continuing with remaining zones",
                    zone_id,
                )
                continue
            _LOGGER.debug(
                "Aux heat for zone %s -> %s (entity %s)",
                zone_id,
                "ON" if desired else "OFF",
                entity_id,
            )

    async def _dispatch(
        self, zone_id: str, entity_id: str, cfg, desired: bool
    ) -> None:
        if cfg.device_type is AuxHeatDeviceType.SWITCH:
            service = SERVICE_TURN_ON if desired else SERVICE_TURN_OFF
            await self._safe_call(
                zone_id,
                SWITCH_DOMAIN,
                service,
                {ATTR_ENTITY_ID: entity_id},
            )
            return

        if desired:
            ok = await self._safe_call(
                zone_id,
                CLIMATE_DOMAIN,
                SERVICE_SET_HVAC_MODE,
                {ATTR_ENTITY_ID: entity_id, "hvac_mode": "heat"},
            )
            if ok and cfg.aux_target_temperature is not None:
                target_in_user_unit = from_celsius(
                    cfg.aux_target_temperature,
                    hass_temperature_unit(self._hass),
                )
                await self._safe_call(
                    zone_id,
                    CLIMATE_DOMAIN,
                    SERVICE_SET_TEMPERATURE,
                    {
                        ATTR_ENTITY_ID: entity_id,
                        ATTR_TEMPERATURE: target_in_user_unit,
                    },
                )
        else:
            await self._safe_call(
                zone_id,
                CLIMATE_DOMAIN,
                SERVICE_SET_HVAC_MODE,
                {ATTR_ENTITY_ID: entity_id, "hvac_mode": "off"},
            )

    async def _safe_call(
        self,
        zone_id: str,
        domain: str,
        service: str,
        payload: dict,
    ) -> bool:
        """Wrap one ``hass.services.async_call`` with rate-limited
        error logging. Returns True on success, False on caught error.
        """
        entity_id = payload.get(ATTR_ENTITY_ID, "<unknown>")
        key = (entity_id, f"{domain}.{service}")
        try:
            await self._hass.services.async_call(
                domain, service, payload, blocking=True
            )
        except Exception as err:
            if key not in self._errored:
                _LOGGER.error(
                    "Aux dispatch failed for zone %s: %s.%s on %s raised "
                    "%s: %s. Will retry next tick.",
                    zone_id,
                    domain,
                    service,
                    entity_id,
                    type(err).__name__,
                    err,
                )
                self._errored.add(key)
            return False
        self._errored.discard(key)
        return True
