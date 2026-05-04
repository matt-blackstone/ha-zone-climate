"""Typed domain models for the Multi-Split Zone Controller.

These models are deliberately free of any Home Assistant imports so the
decision engine (sensor fusion, safety, arbitration) can be exercised in
isolation with plain pytest.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from enum import Enum
from typing import Mapping


class HVACMode(str, Enum):
    """HVAC modes recognised by the coordinator.

    Mirrors the subset of Home Assistant climate modes that are relevant
    for multi-split arbitration.
    """

    OFF = "off"
    HEAT = "heat"
    COOL = "cool"
    AUTO = "auto"
    FAN_ONLY = "fan_only"
    DRY = "dry"


class FusionStrategy(str, Enum):
    """Sensor-fusion strategies. Phase 1 implements only the first two."""

    HEAD_ONLY = "head_only"
    EXTERNAL_PREFERRED = "external_preferred"
    WEIGHTED_BLEND = "weighted_blend"
    AVERAGE_OF_EXTERNALS = "average_of_externals"
    OCCUPANCY_WEIGHTED = "occupancy_weighted"


class SourceQuality(str, Enum):
    """Coarse-grained quality flag attached to a fused reading."""

    OK = "ok"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class OccupancyState(str, Enum):
    """Four-state occupancy model from the design doc.

    - ``CONFIRMED``: reliable evidence of occupancy now.
    - ``EXPECTED``: occupancy likely soon (used by Phase 4 pre-conditioning).
    - ``RECENT``: occupied recently within ``linger_minutes``.
    - ``UNOCCUPIED``: no evidence and not expected.
    """

    CONFIRMED = "confirmed"
    EXPECTED = "expected"
    RECENT = "recent"
    UNOCCUPIED = "unoccupied"


class OccupancySignalKind(str, Enum):
    """Categories of occupancy evidence the resolver knows how to weight."""

    PRESENCE = "presence"          # active when zone is occupied right now
    CALENDAR = "calendar"          # active when a calendar event suggests occupancy
    SCHEDULE = "schedule"          # active when within a scheduled occupied window
    MANUAL_OVERRIDE = "manual_override"  # explicit user override


@dataclass(frozen=True)
class OccupancySource:
    """A single configured occupancy evidence input for a zone."""

    entity_id: str
    kind: OccupancySignalKind
    weight: float = 1.0


@dataclass(frozen=True)
class OccupancyConfig:
    """Per-zone occupancy resolver configuration.

    A zone with ``sources == ()`` is treated as always-CONFIRMED so
    upstream callers and existing Phase-1/2 behaviour are unaffected.
    """

    sources: tuple[OccupancySource, ...] = ()
    linger_minutes: float = 15.0
    confirmed_threshold: float = 0.7
    expected_threshold: float = 0.4


@dataclass(frozen=True)
class SetbackPolicy:
    """How far the active setpoint moves away from comfort when unoccupied."""

    setback_offset_heat: float = 3.0
    setback_offset_cool: float = 3.0
    unoccupied_comfort_weight: float = 0.2


@dataclass(frozen=True)
class PreConditioningConfig:
    """Per-zone pre-conditioning behaviour.

    Used when the occupancy resolver returns ``EXPECTED``: the rate
    estimator predicts how long it will take to close the gap from the
    current temperature to the comfort setpoint. If ``EXPECTED`` persists
    for longer than ``expected_timeout_minutes`` without becoming
    ``CONFIRMED``, the coordinator falls the zone back to ``UNOCCUPIED``
    so setback can re-engage.
    """

    enabled: bool = True
    default_heating_rate: float = 0.5
    default_cooling_rate: float = 0.4
    max_lead_minutes: float = 60.0
    expected_timeout_minutes: float = 60.0
    rate_alpha: float = 0.2


@dataclass(frozen=True)
class OccupancySignalReading:
    """A resolved (boolean) reading of one configured occupancy source."""

    source: OccupancySource
    is_active: bool


@dataclass(frozen=True)
class OccupancyResolution:
    """Output of the occupancy resolver for one zone, one tick."""

    state: OccupancyState
    confidence: float
    active_sources: tuple[str, ...]


@dataclass(frozen=True)
class SensorRef:
    """Reference to an upstream sensor entity with optional weighting/calibration.

    ``occupancy_source`` is only used by Phase 5's ``occupancy_weighted``
    fusion strategy: when the named occupancy source is active, the
    coordinator multiplies this sensor's effective weight by
    ``FusionConfig.occupancy_boost`` so the sensor "nearest" the occupant
    dominates the blend.
    """

    entity_id: str
    weight: float = 1.0
    calibration_offset: float = 0.0
    occupancy_source: str | None = None


class FanDirection(str, Enum):
    """Ceiling-fan rotation direction."""

    FORWARD = "forward"
    REVERSE = "reverse"


@dataclass(frozen=True)
class FanCommand:
    """A resolved per-tick instruction for a zone's ceiling fan."""

    on: bool
    speed_pct: int
    direction: FanDirection


class AuxHeatDeviceType(str, Enum):
    """Which kind of HA entity drives the auxiliary heater."""

    SWITCH = "switch"
    CLIMATE = "climate"


class AuxHeatTrigger(str, Enum):
    """Why aux heat was activated, surfaced for diagnostics + status messages."""

    NONE = "none"
    SAFETY_FLOOR_NEAR = "safety_floor_near"
    OUTDOOR_LOCKOUT = "outdoor_lockout"
    HEAD_FAULT = "head_fault"


@dataclass(frozen=True)
class AuxHeatConfig:
    """Per-zone auxiliary (resistive) heat configuration.

    A zone with ``enabled=False`` (the default) behaves as in earlier
    phases: no aux dispatch, no transition-sequencing on the head, and
    the ``em_heat_active`` binary sensor stays off.
    """

    enabled: bool = False
    device_entity_id: str | None = None
    device_type: AuxHeatDeviceType = AuxHeatDeviceType.SWITCH
    aux_target_temperature: float | None = None
    safety_floor_margin: float = 1.0
    deactivation_margin: float = 1.0
    outdoor_lockout_temp: float | None = None
    outdoor_temp_sensor: str | None = None
    fault_on_head_unavailable: bool = True
    settle_seconds: float = 60.0


@dataclass(frozen=True)
class AuxHeatDecision:
    """Output of the pure aux-heat evaluator (without transition timing)."""

    desired_active: bool
    trigger: AuxHeatTrigger
    status_message: str | None


@dataclass(frozen=True)
class AuxHeatInputs:
    """Input bundle for the pure aux-heat evaluator."""

    intent_mode: HVACMode
    effective_temp: float | None
    safety_floor: float | None
    outdoor_temp: float | None
    head_available: bool
    currently_active: bool


@dataclass(frozen=True)
class HumidityPolicy:
    """Per-zone humidity-aware scoring (Phase 7, "psychrometric Level 1").

    All values default to disabled so an unconfigured zone is scored
    purely on dry-bulb deviation, matching Phases 1-6. Opt-in by
    setting ``target_humidity`` plus a non-zero ``weight`` (general
    latent-discomfort term) and/or a ``dehumidify_threshold`` plus
    non-zero ``dehumidify_bonus`` (bias toward COOL when humidity is
    high enough that dehumidification would matter).
    """

    target_humidity: float | None = None
    tolerance_band: float = 10.0
    weight: float = 0.0
    dehumidify_threshold: float | None = None
    dehumidify_bonus: float = 0.0


@dataclass(frozen=True)
class FanConfig:
    """Per-zone coordinated ceiling-fan configuration.

    A zone with ``enabled=False`` (the default) behaves as in Phases 1-4:
    no fan dispatch, no apparent-temperature credit, scoring is purely
    deviation-driven.
    """

    fan_entity_id: str | None = None
    enabled: bool = False
    direction_in_heat: FanDirection = FanDirection.REVERSE
    direction_in_cool: FanDirection = FanDirection.FORWARD
    lockout_entity_id: str | None = None
    max_speed_pct_occupied: int = 100
    max_speed_pct_unoccupied: int = 33
    max_apparent_temp_credit: float = 1.5
    deviation_per_step: float = 2.0


@dataclass(frozen=True)
class SafetyLimits:
    """Hard floor/ceiling enforced regardless of user intent."""

    min_temp: float | None = None
    max_temp: float | None = None


@dataclass(frozen=True)
class FusionConfig:
    """Per-zone configuration of how raw sensors collapse into fused readings."""

    strategy: FusionStrategy = FusionStrategy.EXTERNAL_PREFERRED
    head_temp_sensor: str | None = None
    head_humidity_sensor: str | None = None
    external_temp_sensors: tuple[SensorRef, ...] = ()
    external_humidity_sensors: tuple[SensorRef, ...] = ()
    head_weight: float = 1.0
    stale_after_seconds: float | None = None
    occupancy_boost: float = 3.0


@dataclass(frozen=True)
class ZoneConfig:
    """Static zone definition loaded from YAML.

    ``always_assert_head_state`` controls whether the head dispatcher
    re-issues ``set_hvac_mode`` and ``set_temperature`` on every
    coordinator tick (True) or de-duplicates against the last value it
    sent (False, default).

    Re-asserting every tick guarantees that an out-of-band IR remote /
    wall-control press is corrected within one update interval, but
    many real mini-split heads beep on every IR command and the audible
    spam every few seconds is unacceptable in a bedroom. Leave this
    False (the default) when the heads beep, and rely on the user to
    surrender control to the integration.

    TODO: a future iteration should detect actual upstream-state drift
    by listening for ``state_changed`` events on the head climate
    entity (with a context-id check to ignore the integration's own
    writes), or by comparing the upstream state against the last
    dispatched value, and only re-issue when drift is observed. The
    boolean here is the explicit-opt-in stop-gap.
    """

    zone_id: str
    name: str
    head_climate_entity: str
    fusion: FusionConfig
    safety: SafetyLimits = field(default_factory=SafetyLimits)
    occupancy: OccupancyConfig = field(default_factory=OccupancyConfig)
    setback: SetbackPolicy = field(default_factory=SetbackPolicy)
    pre_conditioning: PreConditioningConfig = field(default_factory=PreConditioningConfig)
    fan: FanConfig = field(default_factory=FanConfig)
    aux_heat: AuxHeatConfig = field(default_factory=AuxHeatConfig)
    humidity: HumidityPolicy = field(default_factory=HumidityPolicy)
    min_temp: float = 16.0
    max_temp: float = 30.0
    target_temp_step: float = 0.5
    default_target_temperature: float = 21.0
    always_assert_head_state: bool = False


#: Mode pairs that the integration treats as physically incompatible on
#: a shared outdoor unit unless the user explicitly opts out via
#: ``disable_default_incompatible_mode_pairs: true`` on the group.
#:
#: * ``HEAT`` <-> ``COOL``: the obvious one — a single compressor can't
#:   reverse-cycle for two indoor heads at once.
#: * ``HEAT`` <-> ``FAN_ONLY``: while ``fan_only`` doesn't engage the
#:   compressor on most multi-splits, on many real-world units the
#:   outdoor unit refuses heat operation when *any* indoor head is in
#:   ``fan_only`` (the firmware holds the cycle in a safe state).
#:   Treating them as incompatible by default avoids head-scratching
#:   "why won't it heat?" reports; users with hardware that genuinely
#:   supports the combo can opt out at the group level.
DEFAULT_INCOMPATIBLE_MODE_PAIRS: frozenset[frozenset[HVACMode]] = frozenset(
    {
        frozenset({HVACMode.HEAT, HVACMode.COOL}),
        frozenset({HVACMode.HEAT, HVACMode.FAN_ONLY}),
    }
)


@dataclass(frozen=True)
class GroupConfig:
    """A compressor / outdoor-unit group containing one or more zones."""

    group_id: str
    name: str
    zones: tuple[ZoneConfig, ...]
    incompatible_mode_pairs: frozenset[frozenset[HVACMode]] = field(
        default_factory=frozenset
    )
    update_interval: timedelta = field(default_factory=lambda: timedelta(seconds=30))
    use_psychrometric_scoring: bool = False

    def zone(self, zone_id: str) -> ZoneConfig:
        for z in self.zones:
            if z.zone_id == zone_id:
                return z
        raise KeyError(zone_id)


@dataclass(frozen=True)
class SensorReading:
    """A raw sensor reading after stripping unavailable/unknown values."""

    entity_id: str
    value: float | None
    weight: float = 1.0
    calibration_offset: float = 0.0


@dataclass(frozen=True)
class EffectiveReadings:
    """Fused per-zone readings that the control loop should use.

    ``temperature`` / ``humidity`` are the values used for normal control
    and scoring. ``safety_floor_temp`` / ``safety_ceiling_temp`` are the
    *conservative* values used by the safety clamp: floor uses the
    coldest valid sensor (so a single cold reading triggers protection)
    and ceiling uses the hottest valid sensor.

    ``contributing_sources`` lists the entity_ids that participated in
    the fused reading; useful for the ``sensor_quality`` diagnostic.
    """

    temperature: float | None
    humidity: float | None
    temperature_source: str
    humidity_source: str
    quality: SourceQuality
    safety_floor_temp: float | None = None
    safety_ceiling_temp: float | None = None
    contributing_sources: tuple[str, ...] = ()

    @classmethod
    def unavailable(cls) -> EffectiveReadings:
        return cls(
            temperature=None,
            humidity=None,
            temperature_source="none",
            humidity_source="none",
            quality=SourceQuality.UNAVAILABLE,
        )


@dataclass(frozen=True)
class ZoneIntent:
    """A user's expressed intent for a zone (from the managed climate entity)."""

    zone_id: str
    hvac_mode: HVACMode
    target_temperature: float | None = None


@dataclass(frozen=True)
class ResolvedIntent:
    """Zone intent after the safety clamp has been applied.

    `safety_override` is True when the safety layer has overridden the
    user-requested mode and/or setpoint.
    """

    zone_id: str
    hvac_mode: HVACMode
    target_temperature: float | None
    safety_override: bool
    safety_reason: str | None = None


@dataclass(frozen=True)
class ZoneDecision:
    """Final per-zone outcome after arbitration."""

    zone_id: str
    dispatched_mode: HVACMode
    dispatched_target: float | None
    blocked: bool
    block_reason: str | None
    safety_override: bool
    priority_score: float
    effective: EffectiveReadings
    occupancy_state: OccupancyState = OccupancyState.CONFIRMED
    occupancy_confidence: float = 1.0
    comfort_setpoint: float | None = None
    active_setpoint: float | None = None
    predicted_lead_time_minutes: float | None = None
    learned_heating_rate: float | None = None
    learned_cooling_rate: float | None = None
    fan_command: FanCommand | None = None
    fan_comfort_offset: float = 0.0
    aux_heat_active: bool = False
    aux_heat_trigger: AuxHeatTrigger = AuxHeatTrigger.NONE
    aux_heat_status: str | None = None
    humidity_priority_contribution: float = 0.0
    effective_comfort_temperature: float | None = None


@dataclass(frozen=True)
class GroupDecision:
    """Aggregated arbitration outcome for a compressor group."""

    group_id: str
    zones: Mapping[str, ZoneDecision]
