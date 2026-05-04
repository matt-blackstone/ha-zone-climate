"""Managed user-facing climate entities.

Each zone gets exactly one ``ManagedZoneClimate`` instance. The entity
*stores* user intent and delegates everything else (sensor fusion,
arbitration, dispatch) to the group coordinator. Status messaging is
exposed via extra state attributes so dashboards can surface block
reasons and safety overrides without extra controls.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
)
from homeassistant.components.climate import HVACMode as HAHVACMode
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ATTR_BLOCK_REASON,
    ATTR_DISPATCHED_MODE,
    ATTR_EFFECTIVE_HUMIDITY,
    ATTR_EFFECTIVE_TEMPERATURE,
    ATTR_HUMIDITY_SOURCE,
    ATTR_SAFETY_OVERRIDE,
    ATTR_STATUS_MESSAGE,
    ATTR_TEMPERATURE_SOURCE,
    DOMAIN,
)
from .coordinator import GroupCoordinator
from .models import HVACMode, ZoneConfig, ZoneIntent

_LOGGER = logging.getLogger(__name__)

_HA_TO_INTERNAL = {
    HAHVACMode.OFF: HVACMode.OFF,
    HAHVACMode.HEAT: HVACMode.HEAT,
    HAHVACMode.COOL: HVACMode.COOL,
    HAHVACMode.AUTO: HVACMode.AUTO,
    HAHVACMode.FAN_ONLY: HVACMode.FAN_ONLY,
    HAHVACMode.DRY: HVACMode.DRY,
}
_INTERNAL_TO_HA = {v: k for k, v in _HA_TO_INTERNAL.items()}


def _build_zone_entities(
    coordinator: GroupCoordinator,
) -> list["ManagedZoneClimate"]:
    return [ManagedZoneClimate(coordinator, z) for z in coordinator.group.zones]


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """YAML-discovery setup. Emits entities for YAML-declared groups only."""
    if discovery_info is None:
        return
    bucket = hass.data[DOMAIN]
    coordinators: dict[str, GroupCoordinator] = bucket["coordinators"]
    yaml_group_ids: set[str] = bucket.get("yaml_groups", set())
    # If discovery_info passes an explicit list of group_ids, prefer it
    # (so re-entrant discovery loads can target a subset). Otherwise
    # default to every YAML-tracked group.
    target_ids = set(discovery_info.get("yaml_group_ids", yaml_group_ids))
    entities: list[ManagedZoneClimate] = []
    for gid in target_ids:
        coord = coordinators.get(gid)
        if coord is None:
            continue
        entities.extend(_build_zone_entities(coord))
    add_entities(entities)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Any,
    add_entities: AddEntitiesCallback,
) -> None:
    """Config-entry setup. One entry == one group; emit its zone entities."""
    bucket = hass.data[DOMAIN]
    coordinators: dict[str, GroupCoordinator] = bucket["coordinators"]
    group_ids: set[str] = bucket.get("entry_groups", {}).get(entry.entry_id, set())
    entities: list[ManagedZoneClimate] = []
    for gid in group_ids:
        coord = coordinators.get(gid)
        if coord is None:
            continue
        entities.extend(_build_zone_entities(coord))
    add_entities(entities)


class ManagedZoneClimate(CoordinatorEntity[GroupCoordinator], ClimateEntity):
    """User-facing thermostat that hides head and arbitration mechanics."""

    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.TURN_OFF
    )

    def __init__(
        self,
        coordinator: GroupCoordinator,
        zone: ZoneConfig,
    ) -> None:
        super().__init__(coordinator)
        self._zone = zone
        self._attr_unique_id = f"{DOMAIN}_{coordinator.group.group_id}_{zone.zone_id}"
        self._attr_name = zone.name
        self._attr_min_temp = zone.min_temp
        self._attr_max_temp = zone.max_temp
        self._attr_target_temperature_step = zone.target_temp_step
        self._attr_hvac_modes = [
            HAHVACMode.OFF,
            HAHVACMode.HEAT,
            HAHVACMode.COOL,
            HAHVACMode.AUTO,
            HAHVACMode.FAN_ONLY,
            HAHVACMode.DRY,
        ]

    @property
    def _intent(self) -> ZoneIntent:
        return self.coordinator.intents[self._zone.zone_id]

    @property
    def hvac_mode(self) -> HAHVACMode:
        return _INTERNAL_TO_HA[self._intent.hvac_mode]

    @property
    def target_temperature(self) -> float | None:
        return self._intent.target_temperature

    @property
    def current_temperature(self) -> float | None:
        decision = self._zone_decision()
        if decision is None:
            return None
        return decision.effective.temperature

    @property
    def current_humidity(self) -> float | None:
        decision = self._zone_decision()
        if decision is None:
            return None
        return decision.effective.humidity

    @property
    def hvac_action(self) -> HVACAction | None:
        decision = self._zone_decision()
        if decision is None:
            return None
        if decision.dispatched_mode is HVACMode.OFF:
            return HVACAction.OFF if not decision.blocked else HVACAction.IDLE
        if decision.dispatched_mode is HVACMode.HEAT:
            return HVACAction.HEATING
        if decision.dispatched_mode is HVACMode.COOL:
            return HVACAction.COOLING
        if decision.dispatched_mode is HVACMode.FAN_ONLY:
            return HVACAction.FAN
        if decision.dispatched_mode is HVACMode.DRY:
            return HVACAction.DRYING
        return HVACAction.IDLE

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        decision = self._zone_decision()
        if decision is None:
            return {}
        return {
            ATTR_BLOCK_REASON: decision.block_reason,
            ATTR_EFFECTIVE_TEMPERATURE: decision.effective.temperature,
            ATTR_EFFECTIVE_HUMIDITY: decision.effective.humidity,
            ATTR_TEMPERATURE_SOURCE: decision.effective.temperature_source,
            ATTR_HUMIDITY_SOURCE: decision.effective.humidity_source,
            ATTR_SAFETY_OVERRIDE: decision.safety_override,
            ATTR_DISPATCHED_MODE: decision.dispatched_mode.value,
            ATTR_STATUS_MESSAGE: self._status_message(decision),
        }

    def _status_message(self, decision: Any) -> str:
        if decision.aux_heat_active and decision.aux_heat_status:
            return decision.aux_heat_status
        if decision.safety_override:
            return decision.block_reason or "Safety override active"
        if decision.blocked:
            return decision.block_reason or "Blocked by compressor group conflict"
        if decision.dispatched_mode is HVACMode.OFF:
            return "Idle"
        return f"Running ({decision.dispatched_mode.value})"

    async def async_set_hvac_mode(self, hvac_mode: HAHVACMode) -> None:
        internal = _HA_TO_INTERNAL[hvac_mode]
        new_intent = ZoneIntent(
            zone_id=self._zone.zone_id,
            hvac_mode=internal,
            target_temperature=self._intent.target_temperature,
        )
        self.coordinator.set_intent(new_intent)
        await self.coordinator.async_request_refresh()

    async def async_set_temperature(self, **kwargs: Any) -> None:
        target = kwargs.get(ATTR_TEMPERATURE)
        if target is None:
            return
        new_mode = self._intent.hvac_mode
        ha_mode = kwargs.get("hvac_mode")
        if ha_mode is not None:
            new_mode = _HA_TO_INTERNAL[HAHVACMode(ha_mode)]
        new_intent = ZoneIntent(
            zone_id=self._zone.zone_id,
            hvac_mode=new_mode,
            target_temperature=float(target),
        )
        self.coordinator.set_intent(new_intent)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self) -> None:
        await self.async_set_hvac_mode(HAHVACMode.OFF)

    @callback
    def _zone_decision(self):
        data = self.coordinator.data
        if data is None:
            return None
        return data.zones.get(self._zone.zone_id)
