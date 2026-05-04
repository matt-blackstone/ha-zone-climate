"""Binary sensors for the Multi-Split Zone Controller.

Phase 6 introduces ``em_heat_active`` per zone — true while the
auxiliary resistive heater is engaged. Hidden from the managed climate
entity by design, but surfaced here for dashboards and automations.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import GroupCoordinator
from .models import ZoneConfig

_LOGGER = logging.getLogger(__name__)


def _build_zone_entities(
    coordinator: GroupCoordinator,
) -> list[BinarySensorEntity]:
    return [
        EmHeatActiveBinarySensor(coordinator, z) for z in coordinator.group.zones
    ]


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    if discovery_info is None:
        return
    bucket = hass.data[DOMAIN]
    coordinators: dict[str, GroupCoordinator] = bucket["coordinators"]
    target_ids = set(
        discovery_info.get("yaml_group_ids", bucket.get("yaml_groups", set()))
    )
    entities: list[BinarySensorEntity] = []
    for gid in target_ids:
        coord = coordinators.get(gid)
        if coord is None:
            continue
        entities.extend(_build_zone_entities(coord))
    add_entities(entities)


async def async_setup_entry(
    hass: HomeAssistant,
    entry,
    add_entities: AddEntitiesCallback,
) -> None:
    bucket = hass.data[DOMAIN]
    coordinators: dict[str, GroupCoordinator] = bucket["coordinators"]
    group_ids = bucket.get("entry_groups", {}).get(entry.entry_id, set())
    entities: list[BinarySensorEntity] = []
    for gid in group_ids:
        coord = coordinators.get(gid)
        if coord is None:
            continue
        entities.extend(_build_zone_entities(coord))
    add_entities(entities)


class EmHeatActiveBinarySensor(
    CoordinatorEntity[GroupCoordinator], BinarySensorEntity
):
    _attr_device_class = BinarySensorDeviceClass.HEAT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: GroupCoordinator, zone: ZoneConfig) -> None:
        super().__init__(coordinator)
        self._zone = zone
        self._attr_unique_id = (
            f"{DOMAIN}_{coordinator.group.group_id}_{zone.zone_id}_em_heat_active"
        )
        self._attr_name = f"{zone.name} emergency heat active"

    @property
    def is_on(self) -> bool:
        decision = self._decision()
        return decision is not None and decision.aux_heat_active

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        decision = self._decision()
        if decision is None:
            return {}
        return {
            "trigger": decision.aux_heat_trigger.value,
            "status_message": decision.aux_heat_status,
        }

    def _decision(self):
        data = self.coordinator.data
        if data is None:
            return None
        return data.zones.get(self._zone.zone_id)
