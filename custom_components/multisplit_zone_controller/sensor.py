"""Admin-only diagnostic sensors for each zone.

Three sensors per zone:

- ``<zone>_effective_temperature``: fused temperature actually used by
  the control loop.
- ``<zone>_effective_humidity``: fused humidity (None when no humidity
  source is configured).
- ``<zone>_block_reason``: human-readable reason the zone was blocked or
  forced into a safety state, or ``"ok"`` when nothing is wrong.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory, PERCENTAGE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import GroupCoordinator
from .models import ZoneConfig

_LOGGER = logging.getLogger(__name__)


def _build_zone_entities(coordinator: GroupCoordinator) -> list[SensorEntity]:
    out: list[SensorEntity] = []
    for zone_cfg in coordinator.group.zones:
        out.append(EffectiveTemperatureSensor(coordinator, zone_cfg))
        out.append(EffectiveHumiditySensor(coordinator, zone_cfg))
        out.append(BlockReasonSensor(coordinator, zone_cfg))
        out.append(SensorQualitySensor(coordinator, zone_cfg))
        out.append(OccupancyStateSensor(coordinator, zone_cfg))
        out.append(ActiveSetpointSensor(coordinator, zone_cfg))
        out.append(LeadTimeSensor(coordinator, zone_cfg))
        out.append(LearnedHeatingRateSensor(coordinator, zone_cfg))
        out.append(LearnedCoolingRateSensor(coordinator, zone_cfg))
        out.append(HumidityPrioritySensor(coordinator, zone_cfg))
        out.append(EffectiveComfortSensor(coordinator, zone_cfg))
    return out


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
    entities: list[SensorEntity] = []
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
    entities: list[SensorEntity] = []
    for gid in group_ids:
        coord = coordinators.get(gid)
        if coord is None:
            continue
        entities.extend(_build_zone_entities(coord))
    add_entities(entities)


class _ZoneSensorBase(CoordinatorEntity[GroupCoordinator], SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: GroupCoordinator,
        zone: ZoneConfig,
        suffix: str,
    ) -> None:
        super().__init__(coordinator)
        self._zone = zone
        self._attr_unique_id = (
            f"{DOMAIN}_{coordinator.group.group_id}_{zone.zone_id}_{suffix}"
        )

    def _decision(self):
        data = self.coordinator.data
        if data is None:
            return None
        return data.zones.get(self._zone.zone_id)


class EffectiveTemperatureSensor(_ZoneSensorBase):
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def __init__(self, coordinator: GroupCoordinator, zone: ZoneConfig) -> None:
        super().__init__(coordinator, zone, "effective_temperature")
        self._attr_name = f"{zone.name} effective temperature"

    @property
    def native_value(self) -> float | None:
        decision = self._decision()
        if decision is None:
            return None
        return decision.effective.temperature

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        decision = self._decision()
        if decision is None:
            return {}
        return {
            "source": decision.effective.temperature_source,
            "quality": decision.effective.quality.value,
            "safety_floor_temp": decision.effective.safety_floor_temp,
            "safety_ceiling_temp": decision.effective.safety_ceiling_temp,
            "contributing_sources": list(decision.effective.contributing_sources),
        }


class EffectiveHumiditySensor(_ZoneSensorBase):
    _attr_device_class = SensorDeviceClass.HUMIDITY
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(self, coordinator: GroupCoordinator, zone: ZoneConfig) -> None:
        super().__init__(coordinator, zone, "effective_humidity")
        self._attr_name = f"{zone.name} effective humidity"

    @property
    def native_value(self) -> float | None:
        decision = self._decision()
        if decision is None:
            return None
        return decision.effective.humidity

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        decision = self._decision()
        if decision is None:
            return {}
        return {
            "source": decision.effective.humidity_source,
            "quality": decision.effective.quality.value,
        }


class SensorQualitySensor(_ZoneSensorBase):
    """Coarse-grained quality flag for the fused readings.

    State is one of ``ok``, ``degraded``, or ``unavailable``. Attributes
    expose which sensors actually contributed to the fused result so the
    operator can see, for example, that the head sensor carried the load
    when an external sensor went offline.
    """

    def __init__(self, coordinator: GroupCoordinator, zone: ZoneConfig) -> None:
        super().__init__(coordinator, zone, "sensor_quality")
        self._attr_name = f"{zone.name} sensor quality"

    @property
    def native_value(self) -> str:
        decision = self._decision()
        if decision is None:
            return "unknown"
        return decision.effective.quality.value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        decision = self._decision()
        if decision is None:
            return {}
        return {
            "temperature_source": decision.effective.temperature_source,
            "humidity_source": decision.effective.humidity_source,
            "contributing_sources": list(decision.effective.contributing_sources),
        }


class BlockReasonSensor(_ZoneSensorBase):
    def __init__(self, coordinator: GroupCoordinator, zone: ZoneConfig) -> None:
        super().__init__(coordinator, zone, "block_reason")
        self._attr_name = f"{zone.name} block reason"

    @property
    def native_value(self) -> str:
        decision = self._decision()
        if decision is None:
            return "unknown"
        if decision.block_reason:
            return decision.block_reason
        if decision.safety_override:
            return "safety_override"
        return "ok"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        decision = self._decision()
        if decision is None:
            return {}
        return {
            "blocked": decision.blocked,
            "safety_override": decision.safety_override,
            "dispatched_mode": decision.dispatched_mode.value,
            "priority_score": decision.priority_score,
        }


class OccupancyStateSensor(_ZoneSensorBase):
    def __init__(self, coordinator: GroupCoordinator, zone: ZoneConfig) -> None:
        super().__init__(coordinator, zone, "occupancy_state")
        self._attr_name = f"{zone.name} occupancy state"

    @property
    def native_value(self) -> str:
        decision = self._decision()
        if decision is None:
            return "unknown"
        return decision.occupancy_state.value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        decision = self._decision()
        if decision is None:
            return {}
        return {
            "confidence": decision.occupancy_confidence,
        }


class LeadTimeSensor(_ZoneSensorBase):
    """Estimated minutes to reach the comfort setpoint at the learned rate."""

    _attr_native_unit_of_measurement = "min"

    def __init__(self, coordinator: GroupCoordinator, zone: ZoneConfig) -> None:
        super().__init__(coordinator, zone, "lead_time_minutes")
        self._attr_name = f"{zone.name} pre-condition lead time"

    @property
    def native_value(self) -> float | None:
        decision = self._decision()
        if decision is None:
            return None
        return decision.predicted_lead_time_minutes


class LearnedHeatingRateSensor(_ZoneSensorBase):
    """Observed heating rate for this zone in degrees per minute."""

    _attr_native_unit_of_measurement = "°C/min"

    def __init__(self, coordinator: GroupCoordinator, zone: ZoneConfig) -> None:
        super().__init__(coordinator, zone, "learned_heating_rate")
        self._attr_name = f"{zone.name} learned heating rate"

    @property
    def native_value(self) -> float | None:
        decision = self._decision()
        if decision is None:
            return None
        return decision.learned_heating_rate


class LearnedCoolingRateSensor(_ZoneSensorBase):
    """Observed cooling rate for this zone in degrees per minute."""

    _attr_native_unit_of_measurement = "°C/min"

    def __init__(self, coordinator: GroupCoordinator, zone: ZoneConfig) -> None:
        super().__init__(coordinator, zone, "learned_cooling_rate")
        self._attr_name = f"{zone.name} learned cooling rate"

    @property
    def native_value(self) -> float | None:
        decision = self._decision()
        if decision is None:
            return None
        return decision.learned_cooling_rate


class EffectiveComfortSensor(_ZoneSensorBase):
    """Combined °C-equivalent comfort temperature (Phase 8 / Psych Level 2).

    Populated when the group has ``use_psychrometric_scoring: true``.
    Returns ``None`` otherwise — the sensor still exists for symmetry
    but reports as unknown until the feature flag is enabled.
    """

    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_device_class = SensorDeviceClass.TEMPERATURE

    def __init__(self, coordinator: GroupCoordinator, zone: ZoneConfig) -> None:
        super().__init__(coordinator, zone, "effective_comfort")
        self._attr_name = f"{zone.name} effective comfort temperature"

    @property
    def native_value(self) -> float | None:
        decision = self._decision()
        if decision is None:
            return None
        return decision.effective_comfort_temperature


class HumidityPrioritySensor(_ZoneSensorBase):
    """Humidity-driven contribution to the priority score."""

    def __init__(self, coordinator: GroupCoordinator, zone: ZoneConfig) -> None:
        super().__init__(coordinator, zone, "humidity_priority")
        self._attr_name = f"{zone.name} humidity priority contribution"

    @property
    def native_value(self) -> float | None:
        decision = self._decision()
        if decision is None:
            return None
        return decision.humidity_priority_contribution


class ActiveSetpointSensor(_ZoneSensorBase):
    """The setpoint actually in force after setback adjustment.

    Differs from the comfort setpoint (what the user requested) when the
    zone is unoccupied and setback has shifted the target.
    """

    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_device_class = SensorDeviceClass.TEMPERATURE

    def __init__(self, coordinator: GroupCoordinator, zone: ZoneConfig) -> None:
        super().__init__(coordinator, zone, "active_setpoint")
        self._attr_name = f"{zone.name} active setpoint"

    @property
    def native_value(self) -> float | None:
        decision = self._decision()
        if decision is None:
            return None
        return decision.active_setpoint

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        decision = self._decision()
        if decision is None:
            return {}
        return {
            "comfort_setpoint": decision.comfort_setpoint,
            "occupancy_state": decision.occupancy_state.value,
        }
