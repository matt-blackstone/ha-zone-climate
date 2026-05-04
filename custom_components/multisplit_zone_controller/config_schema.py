"""Voluptuous schema and parser for the YAML-based configuration.

The integration is configured entirely from ``configuration.yaml`` for
Phase 1. A future phase may add a config_flow UI; the parser here will
remain the single source of truth for translating raw dicts into
``GroupConfig`` instances.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import voluptuous as vol

from .const import (
    CONF_ALWAYS_ASSERT_HEAD_STATE,
    CONF_AUX_DEACTIVATION_MARGIN,
    CONF_AUX_DEVICE_ENTITY_ID,
    CONF_AUX_DEVICE_TYPE,
    CONF_AUX_ENABLED,
    CONF_AUX_FAULT_ON_HEAD_UNAVAILABLE,
    CONF_AUX_HEAT,
    CONF_AUX_OUTDOOR_LOCKOUT_TEMP,
    CONF_AUX_OUTDOOR_TEMP_SENSOR,
    CONF_AUX_SAFETY_FLOOR_MARGIN,
    CONF_AUX_SETTLE_SECONDS,
    CONF_AUX_TARGET_TEMPERATURE,
    CONF_CALIBRATION_OFFSET,
    CONF_DEFAULT_TARGET,
    CONF_DISABLE_DEFAULT_INCOMPATIBLE_MODE_PAIRS,
    CONF_ENTITY_ID,
    CONF_EXTERNAL_HUMIDITY_SENSORS,
    CONF_EXTERNAL_TEMP_SENSORS,
    CONF_FAN,
    CONF_HUMIDITY,
    CONF_HUMIDITY_DEHUMIDIFY_BONUS,
    CONF_HUMIDITY_DEHUMIDIFY_THRESHOLD,
    CONF_HUMIDITY_TARGET,
    CONF_HUMIDITY_TOLERANCE_BAND,
    CONF_HUMIDITY_WEIGHT,
    CONF_FAN_DEVIATION_PER_STEP,
    CONF_FAN_DIRECTION_COOL,
    CONF_FAN_DIRECTION_HEAT,
    CONF_FAN_ENABLED,
    CONF_FAN_ENTITY_ID,
    CONF_FAN_LOCKOUT_ENTITY,
    CONF_FAN_MAX_CREDIT,
    CONF_FAN_MAX_SPEED_OCC,
    CONF_FAN_MAX_SPEED_UNOCC,
    CONF_FUSION,
    CONF_GROUPS,
    CONF_GROUP_ID,
    CONF_HEAD_CLIMATE,
    CONF_HEAD_HUMIDITY_SENSOR,
    CONF_HEAD_TEMP_SENSOR,
    CONF_HEAD_WEIGHT,
    CONF_INCOMPATIBLE_MODE_PAIRS,
    CONF_MAX_TEMP,
    CONF_MIN_TEMP,
    CONF_NAME,
    CONF_OCCUPANCY,
    CONF_OCCUPANCY_BOOST,
    CONF_OCCUPANCY_CONFIRMED_THRESHOLD,
    CONF_OCCUPANCY_EXPECTED_THRESHOLD,
    CONF_OCCUPANCY_KIND,
    CONF_OCCUPANCY_LINGER_MINUTES,
    CONF_OCCUPANCY_SOURCE,
    CONF_OCCUPANCY_SOURCES,
    CONF_PRECOND_DEFAULT_COOLING_RATE,
    CONF_PRECOND_DEFAULT_HEATING_RATE,
    CONF_PRECOND_ENABLED,
    CONF_PRECOND_EXPECTED_TIMEOUT_MINUTES,
    CONF_PRECOND_MAX_LEAD_MINUTES,
    CONF_PRECOND_RATE_ALPHA,
    CONF_PRECONDITIONING,
    CONF_SAFETY,
    CONF_SETBACK,
    CONF_SETBACK_OFFSET_COOL,
    CONF_SETBACK_OFFSET_HEAT,
    CONF_SETBACK_UNOCC_WEIGHT,
    CONF_STALE_AFTER_SECONDS,
    CONF_STRATEGY,
    CONF_TARGET_TEMP_STEP,
    CONF_UPDATE_INTERVAL,
    CONF_USE_PSYCHROMETRIC_SCORING,
    CONF_WEIGHT,
    CONF_ZONES,
    CONF_ZONE_ID,
    DEFAULT_UPDATE_INTERVAL_SECONDS,
)
from .models import (
    DEFAULT_INCOMPATIBLE_MODE_PAIRS,
    AuxHeatConfig,
    AuxHeatDeviceType,
    FanConfig,
    FanDirection,
    HumidityPolicy,
    FusionConfig,
    FusionStrategy,
    GroupConfig,
    HVACMode,
    OccupancyConfig,
    OccupancySignalKind,
    OccupancySource,
    PreConditioningConfig,
    SafetyLimits,
    SensorRef,
    SetbackPolicy,
    ZoneConfig,
)

SUPPORTED_FUSION_STRATEGIES = {
    FusionStrategy.HEAD_ONLY.value,
    FusionStrategy.EXTERNAL_PREFERRED.value,
    FusionStrategy.WEIGHTED_BLEND.value,
    FusionStrategy.AVERAGE_OF_EXTERNALS.value,
    FusionStrategy.OCCUPANCY_WEIGHTED.value,
}

SENSOR_REF_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ENTITY_ID): str,
        vol.Optional(CONF_WEIGHT, default=1.0): vol.Coerce(float),
        vol.Optional(CONF_CALIBRATION_OFFSET, default=0.0): vol.Coerce(float),
        vol.Optional(CONF_OCCUPANCY_SOURCE, default=None): vol.Any(None, str),
    }
)


def _sensor_ref_or_string(value: Any) -> dict[str, Any]:
    """Allow ``"sensor.foo"`` shorthand or a full ``{entity_id, weight, ...}`` dict."""
    if isinstance(value, str):
        return {
            CONF_ENTITY_ID: value,
            CONF_WEIGHT: 1.0,
            CONF_CALIBRATION_OFFSET: 0.0,
            CONF_OCCUPANCY_SOURCE: None,
        }
    return SENSOR_REF_SCHEMA(value)


FUSION_SCHEMA = vol.Schema(
    {
        vol.Optional(
            CONF_STRATEGY, default=FusionStrategy.EXTERNAL_PREFERRED.value
        ): vol.In(SUPPORTED_FUSION_STRATEGIES),
        vol.Optional(CONF_HEAD_TEMP_SENSOR): str,
        vol.Optional(CONF_HEAD_HUMIDITY_SENSOR): str,
        vol.Optional(CONF_EXTERNAL_TEMP_SENSORS, default=list): [_sensor_ref_or_string],
        vol.Optional(CONF_EXTERNAL_HUMIDITY_SENSORS, default=list): [
            _sensor_ref_or_string
        ],
        vol.Optional(CONF_HEAD_WEIGHT, default=1.0): vol.All(
            vol.Coerce(float), vol.Range(min=0.0)
        ),
        vol.Optional(CONF_STALE_AFTER_SECONDS): vol.All(
            vol.Coerce(float), vol.Range(min=0.0)
        ),
        vol.Optional(CONF_OCCUPANCY_BOOST, default=3.0): vol.All(
            vol.Coerce(float), vol.Range(min=1.0)
        ),
    }
)

SAFETY_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_MIN_TEMP): vol.Coerce(float),
        vol.Optional(CONF_MAX_TEMP): vol.Coerce(float),
    }
)

OCCUPANCY_SOURCE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ENTITY_ID): str,
        vol.Required(CONF_OCCUPANCY_KIND): vol.In(
            [k.value for k in OccupancySignalKind]
        ),
        vol.Optional(CONF_WEIGHT, default=1.0): vol.All(
            vol.Coerce(float), vol.Range(min=0.0)
        ),
    }
)

OCCUPANCY_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_OCCUPANCY_SOURCES, default=list): [OCCUPANCY_SOURCE_SCHEMA],
        vol.Optional(CONF_OCCUPANCY_LINGER_MINUTES, default=15.0): vol.All(
            vol.Coerce(float), vol.Range(min=0.0)
        ),
        vol.Optional(CONF_OCCUPANCY_CONFIRMED_THRESHOLD, default=0.7): vol.All(
            vol.Coerce(float), vol.Range(min=0.0, max=1.0)
        ),
        vol.Optional(CONF_OCCUPANCY_EXPECTED_THRESHOLD, default=0.4): vol.All(
            vol.Coerce(float), vol.Range(min=0.0, max=1.0)
        ),
    }
)

SETBACK_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_SETBACK_OFFSET_HEAT, default=3.0): vol.All(
            vol.Coerce(float), vol.Range(min=0.0)
        ),
        vol.Optional(CONF_SETBACK_OFFSET_COOL, default=3.0): vol.All(
            vol.Coerce(float), vol.Range(min=0.0)
        ),
        vol.Optional(CONF_SETBACK_UNOCC_WEIGHT, default=0.2): vol.All(
            vol.Coerce(float), vol.Range(min=0.0, max=1.0)
        ),
    }
)

FAN_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_FAN_ENTITY_ID, default=None): vol.Any(None, str),
        vol.Optional(CONF_FAN_ENABLED, default=False): bool,
        vol.Optional(CONF_FAN_DIRECTION_HEAT, default="reverse"): vol.In(
            ["forward", "reverse"]
        ),
        vol.Optional(CONF_FAN_DIRECTION_COOL, default="forward"): vol.In(
            ["forward", "reverse"]
        ),
        vol.Optional(CONF_FAN_LOCKOUT_ENTITY, default=None): vol.Any(None, str),
        vol.Optional(CONF_FAN_MAX_SPEED_OCC, default=100): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=100)
        ),
        vol.Optional(CONF_FAN_MAX_SPEED_UNOCC, default=33): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=100)
        ),
        vol.Optional(CONF_FAN_MAX_CREDIT, default=1.5): vol.All(
            vol.Coerce(float), vol.Range(min=0.0)
        ),
        vol.Optional(CONF_FAN_DEVIATION_PER_STEP, default=2.0): vol.All(
            vol.Coerce(float), vol.Range(min=0.0)
        ),
    }
)


HUMIDITY_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_HUMIDITY_TARGET, default=None): vol.Any(
            None, vol.All(vol.Coerce(float), vol.Range(min=0.0, max=100.0))
        ),
        vol.Optional(CONF_HUMIDITY_TOLERANCE_BAND, default=10.0): vol.All(
            vol.Coerce(float), vol.Range(min=0.0, max=100.0)
        ),
        vol.Optional(CONF_HUMIDITY_WEIGHT, default=0.0): vol.All(
            vol.Coerce(float), vol.Range(min=0.0)
        ),
        vol.Optional(CONF_HUMIDITY_DEHUMIDIFY_THRESHOLD, default=None): vol.Any(
            None, vol.All(vol.Coerce(float), vol.Range(min=0.0, max=100.0))
        ),
        vol.Optional(CONF_HUMIDITY_DEHUMIDIFY_BONUS, default=0.0): vol.All(
            vol.Coerce(float), vol.Range(min=0.0)
        ),
    }
)


AUX_HEAT_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_AUX_ENABLED, default=False): bool,
        vol.Optional(CONF_AUX_DEVICE_ENTITY_ID, default=None): vol.Any(None, str),
        vol.Optional(CONF_AUX_DEVICE_TYPE, default="switch"): vol.In(
            ["switch", "climate"]
        ),
        vol.Optional(CONF_AUX_TARGET_TEMPERATURE, default=None): vol.Any(
            None, vol.Coerce(float)
        ),
        vol.Optional(CONF_AUX_SAFETY_FLOOR_MARGIN, default=1.0): vol.All(
            vol.Coerce(float), vol.Range(min=0.0)
        ),
        vol.Optional(CONF_AUX_DEACTIVATION_MARGIN, default=1.0): vol.All(
            vol.Coerce(float), vol.Range(min=0.0)
        ),
        vol.Optional(CONF_AUX_OUTDOOR_LOCKOUT_TEMP, default=None): vol.Any(
            None, vol.Coerce(float)
        ),
        vol.Optional(CONF_AUX_OUTDOOR_TEMP_SENSOR, default=None): vol.Any(None, str),
        vol.Optional(CONF_AUX_FAULT_ON_HEAD_UNAVAILABLE, default=True): bool,
        vol.Optional(CONF_AUX_SETTLE_SECONDS, default=60.0): vol.All(
            vol.Coerce(float), vol.Range(min=0.0)
        ),
    }
)


PRECONDITIONING_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_PRECOND_ENABLED, default=True): bool,
        vol.Optional(CONF_PRECOND_DEFAULT_HEATING_RATE, default=0.5): vol.All(
            vol.Coerce(float), vol.Range(min=0.0)
        ),
        vol.Optional(CONF_PRECOND_DEFAULT_COOLING_RATE, default=0.4): vol.All(
            vol.Coerce(float), vol.Range(min=0.0)
        ),
        vol.Optional(CONF_PRECOND_MAX_LEAD_MINUTES, default=60.0): vol.All(
            vol.Coerce(float), vol.Range(min=0.0)
        ),
        vol.Optional(CONF_PRECOND_EXPECTED_TIMEOUT_MINUTES, default=60.0): vol.All(
            vol.Coerce(float), vol.Range(min=0.0)
        ),
        vol.Optional(CONF_PRECOND_RATE_ALPHA, default=0.2): vol.All(
            vol.Coerce(float), vol.Range(min=0.0, max=1.0)
        ),
    }
)

ZONE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ZONE_ID): str,
        vol.Required(CONF_NAME): str,
        vol.Required(CONF_HEAD_CLIMATE): str,
        vol.Optional(CONF_FUSION, default=dict): FUSION_SCHEMA,
        vol.Optional(CONF_SAFETY, default=dict): SAFETY_SCHEMA,
        vol.Optional(CONF_OCCUPANCY, default=dict): OCCUPANCY_SCHEMA,
        vol.Optional(CONF_SETBACK, default=dict): SETBACK_SCHEMA,
        vol.Optional(CONF_PRECONDITIONING, default=dict): PRECONDITIONING_SCHEMA,
        vol.Optional(CONF_FAN, default=dict): FAN_SCHEMA,
        vol.Optional(CONF_AUX_HEAT, default=dict): AUX_HEAT_SCHEMA,
        vol.Optional(CONF_HUMIDITY, default=dict): HUMIDITY_SCHEMA,
        vol.Optional(CONF_MIN_TEMP, default=16.0): vol.Coerce(float),
        vol.Optional(CONF_MAX_TEMP, default=30.0): vol.Coerce(float),
        vol.Optional(CONF_TARGET_TEMP_STEP, default=0.5): vol.Coerce(float),
        vol.Optional(CONF_DEFAULT_TARGET, default=21.0): vol.Coerce(float),
        vol.Optional(CONF_ALWAYS_ASSERT_HEAD_STATE, default=False): bool,
    }
)


def _mode_pair(value: Any) -> frozenset[HVACMode]:
    # Accept any non-string sequence (list, tuple, HA's NodeListClass, etc.)
    if isinstance(value, (str, bytes)) or not hasattr(value, "__iter__"):
        raise vol.Invalid(
            "incompatible_mode_pairs entries must be 2-element sequences"
        )
    items = list(value)
    if len(items) != 2:
        raise vol.Invalid(
            "incompatible_mode_pairs entries must be 2-element sequences"
        )
    try:
        modes = {HVACMode(v) for v in items}
    except ValueError as exc:
        raise vol.Invalid(str(exc)) from exc
    if len(modes) != 2:
        raise vol.Invalid("incompatible_mode_pairs entries must be distinct modes")
    return frozenset(modes)


GROUP_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_GROUP_ID): str,
        vol.Required(CONF_NAME): str,
        vol.Optional(
            CONF_UPDATE_INTERVAL, default=DEFAULT_UPDATE_INTERVAL_SECONDS
        ): vol.All(vol.Coerce(int), vol.Range(min=5)),
        vol.Optional(CONF_INCOMPATIBLE_MODE_PAIRS, default=list): [_mode_pair],
        vol.Optional(
            CONF_DISABLE_DEFAULT_INCOMPATIBLE_MODE_PAIRS, default=False
        ): bool,
        vol.Optional(CONF_USE_PSYCHROMETRIC_SCORING, default=False): bool,
        vol.Required(CONF_ZONES): vol.All([ZONE_SCHEMA], vol.Length(min=1)),
    }
)

INTEGRATION_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_GROUPS): vol.All([GROUP_SCHEMA], vol.Length(min=1)),
    }
)


def parse_groups(raw: dict[str, Any]) -> tuple[GroupConfig, ...]:
    """Validate and convert the raw integration config into GroupConfig tuples."""
    validated = INTEGRATION_SCHEMA(raw)
    groups: list[GroupConfig] = []
    seen_group_ids: set[str] = set()
    for raw_group in validated[CONF_GROUPS]:
        group = _build_group(raw_group)
        if group.group_id in seen_group_ids:
            raise vol.Invalid(f"Duplicate group_id: {group.group_id}")
        seen_group_ids.add(group.group_id)
        groups.append(group)
    return tuple(groups)


def parse_group_dict(raw_group: dict[str, Any]) -> GroupConfig:
    """Validate a single group dict and return its :class:`GroupConfig`.

    Used by the config flow / config-entry setup path. The input
    ``raw_group`` follows the *same* shape as one entry of the YAML
    ``groups:`` list, which lets the UI persist its data in a format
    that is round-trip compatible with YAML and means the same
    validators (incl. default-incompatible-pair merging, mode
    coercion, etc.) apply uniformly to both sources.
    """
    validated = GROUP_SCHEMA(raw_group)
    return _build_group(validated)


def entry_to_group_config(entry: Any) -> GroupConfig:
    """Build a :class:`GroupConfig` from a Home Assistant config entry.

    Resolution order: ``entry.options`` (set via the options flow) is
    layered on top of ``entry.data`` (set during initial setup). This
    mirrors the conventional HA pattern where ``data`` is immutable
    after creation and ``options`` carries user-tunable overrides.
    """
    raw_group: dict[str, Any] = {**entry.data, **(entry.options or {})}
    return parse_group_dict(raw_group)


def _build_group(raw: dict[str, Any]) -> GroupConfig:
    zones: list[ZoneConfig] = []
    seen_zone_ids: set[str] = set()
    for raw_zone in raw[CONF_ZONES]:
        zone = _build_zone(raw_zone)
        if zone.zone_id in seen_zone_ids:
            raise vol.Invalid(
                f"Duplicate zone_id {zone.zone_id!r} in group {raw[CONF_GROUP_ID]!r}"
            )
        seen_zone_ids.add(zone.zone_id)
        zones.append(zone)

    user_pairs = frozenset(raw[CONF_INCOMPATIBLE_MODE_PAIRS])
    # Merge the integration's baseline incompatible-mode pairs with
    # whatever the user configured, unless they've explicitly opted out
    # of the defaults at the group level.
    if raw[CONF_DISABLE_DEFAULT_INCOMPATIBLE_MODE_PAIRS]:
        merged_pairs = user_pairs
    else:
        merged_pairs = user_pairs | DEFAULT_INCOMPATIBLE_MODE_PAIRS

    return GroupConfig(
        group_id=raw[CONF_GROUP_ID],
        name=raw[CONF_NAME],
        zones=tuple(zones),
        incompatible_mode_pairs=merged_pairs,
        update_interval=timedelta(seconds=raw[CONF_UPDATE_INTERVAL]),
        use_psychrometric_scoring=raw[CONF_USE_PSYCHROMETRIC_SCORING],
    )


def _build_zone(raw: dict[str, Any]) -> ZoneConfig:
    raw_fusion = raw[CONF_FUSION]
    raw_safety = raw[CONF_SAFETY]
    raw_occ = raw[CONF_OCCUPANCY]
    raw_setback = raw[CONF_SETBACK]
    raw_precond = raw[CONF_PRECONDITIONING]
    raw_fan = raw[CONF_FAN]
    raw_aux = raw[CONF_AUX_HEAT]
    raw_humidity = raw[CONF_HUMIDITY]

    fusion = FusionConfig(
        strategy=FusionStrategy(raw_fusion[CONF_STRATEGY]),
        head_temp_sensor=raw_fusion.get(CONF_HEAD_TEMP_SENSOR),
        head_humidity_sensor=raw_fusion.get(CONF_HEAD_HUMIDITY_SENSOR),
        external_temp_sensors=tuple(
            SensorRef(
                entity_id=s[CONF_ENTITY_ID],
                weight=s[CONF_WEIGHT],
                calibration_offset=s[CONF_CALIBRATION_OFFSET],
                occupancy_source=s.get(CONF_OCCUPANCY_SOURCE),
            )
            for s in raw_fusion[CONF_EXTERNAL_TEMP_SENSORS]
        ),
        external_humidity_sensors=tuple(
            SensorRef(
                entity_id=s[CONF_ENTITY_ID],
                weight=s[CONF_WEIGHT],
                calibration_offset=s[CONF_CALIBRATION_OFFSET],
                occupancy_source=s.get(CONF_OCCUPANCY_SOURCE),
            )
            for s in raw_fusion[CONF_EXTERNAL_HUMIDITY_SENSORS]
        ),
        head_weight=raw_fusion[CONF_HEAD_WEIGHT],
        stale_after_seconds=raw_fusion.get(CONF_STALE_AFTER_SECONDS),
        occupancy_boost=raw_fusion[CONF_OCCUPANCY_BOOST],
    )

    safety = SafetyLimits(
        min_temp=raw_safety.get(CONF_MIN_TEMP),
        max_temp=raw_safety.get(CONF_MAX_TEMP),
    )

    occupancy = OccupancyConfig(
        sources=tuple(
            OccupancySource(
                entity_id=s[CONF_ENTITY_ID],
                kind=OccupancySignalKind(s[CONF_OCCUPANCY_KIND]),
                weight=s[CONF_WEIGHT],
            )
            for s in raw_occ[CONF_OCCUPANCY_SOURCES]
        ),
        linger_minutes=raw_occ[CONF_OCCUPANCY_LINGER_MINUTES],
        confirmed_threshold=raw_occ[CONF_OCCUPANCY_CONFIRMED_THRESHOLD],
        expected_threshold=raw_occ[CONF_OCCUPANCY_EXPECTED_THRESHOLD],
    )

    setback = SetbackPolicy(
        setback_offset_heat=raw_setback[CONF_SETBACK_OFFSET_HEAT],
        setback_offset_cool=raw_setback[CONF_SETBACK_OFFSET_COOL],
        unoccupied_comfort_weight=raw_setback[CONF_SETBACK_UNOCC_WEIGHT],
    )

    precond = PreConditioningConfig(
        enabled=raw_precond[CONF_PRECOND_ENABLED],
        default_heating_rate=raw_precond[CONF_PRECOND_DEFAULT_HEATING_RATE],
        default_cooling_rate=raw_precond[CONF_PRECOND_DEFAULT_COOLING_RATE],
        max_lead_minutes=raw_precond[CONF_PRECOND_MAX_LEAD_MINUTES],
        expected_timeout_minutes=raw_precond[CONF_PRECOND_EXPECTED_TIMEOUT_MINUTES],
        rate_alpha=raw_precond[CONF_PRECOND_RATE_ALPHA],
    )

    fan = FanConfig(
        fan_entity_id=raw_fan.get(CONF_FAN_ENTITY_ID),
        enabled=raw_fan[CONF_FAN_ENABLED],
        direction_in_heat=FanDirection(raw_fan[CONF_FAN_DIRECTION_HEAT]),
        direction_in_cool=FanDirection(raw_fan[CONF_FAN_DIRECTION_COOL]),
        lockout_entity_id=raw_fan.get(CONF_FAN_LOCKOUT_ENTITY),
        max_speed_pct_occupied=raw_fan[CONF_FAN_MAX_SPEED_OCC],
        max_speed_pct_unoccupied=raw_fan[CONF_FAN_MAX_SPEED_UNOCC],
        max_apparent_temp_credit=raw_fan[CONF_FAN_MAX_CREDIT],
        deviation_per_step=raw_fan[CONF_FAN_DEVIATION_PER_STEP],
    )

    humidity = HumidityPolicy(
        target_humidity=raw_humidity.get(CONF_HUMIDITY_TARGET),
        tolerance_band=raw_humidity[CONF_HUMIDITY_TOLERANCE_BAND],
        weight=raw_humidity[CONF_HUMIDITY_WEIGHT],
        dehumidify_threshold=raw_humidity.get(CONF_HUMIDITY_DEHUMIDIFY_THRESHOLD),
        dehumidify_bonus=raw_humidity[CONF_HUMIDITY_DEHUMIDIFY_BONUS],
    )

    aux = AuxHeatConfig(
        enabled=raw_aux[CONF_AUX_ENABLED],
        device_entity_id=raw_aux.get(CONF_AUX_DEVICE_ENTITY_ID),
        device_type=AuxHeatDeviceType(raw_aux[CONF_AUX_DEVICE_TYPE]),
        aux_target_temperature=raw_aux.get(CONF_AUX_TARGET_TEMPERATURE),
        safety_floor_margin=raw_aux[CONF_AUX_SAFETY_FLOOR_MARGIN],
        deactivation_margin=raw_aux[CONF_AUX_DEACTIVATION_MARGIN],
        outdoor_lockout_temp=raw_aux.get(CONF_AUX_OUTDOOR_LOCKOUT_TEMP),
        outdoor_temp_sensor=raw_aux.get(CONF_AUX_OUTDOOR_TEMP_SENSOR),
        fault_on_head_unavailable=raw_aux[CONF_AUX_FAULT_ON_HEAD_UNAVAILABLE],
        settle_seconds=raw_aux[CONF_AUX_SETTLE_SECONDS],
    )

    return ZoneConfig(
        zone_id=raw[CONF_ZONE_ID],
        name=raw[CONF_NAME],
        head_climate_entity=raw[CONF_HEAD_CLIMATE],
        fusion=fusion,
        safety=safety,
        occupancy=occupancy,
        setback=setback,
        pre_conditioning=precond,
        fan=fan,
        aux_heat=aux,
        humidity=humidity,
        min_temp=raw[CONF_MIN_TEMP],
        max_temp=raw[CONF_MAX_TEMP],
        target_temp_step=raw[CONF_TARGET_TEMP_STEP],
        default_target_temperature=raw[CONF_DEFAULT_TARGET],
        always_assert_head_state=raw[CONF_ALWAYS_ASSERT_HEAD_STATE],
    )
