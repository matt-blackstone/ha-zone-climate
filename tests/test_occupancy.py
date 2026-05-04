"""Tests for occupancy resolution and setback adjustment."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from custom_components.multisplit_zone_controller.models import (
    HVACMode,
    OccupancyConfig,
    OccupancySignalKind,
    OccupancySignalReading,
    OccupancySource,
    OccupancyState,
    SetbackPolicy,
    ZoneIntent,
)
from custom_components.multisplit_zone_controller.occupancy import (
    occupancy_comfort_weight,
    resolve_occupancy,
    setback_adjust,
)


_NOW = datetime(2026, 5, 2, 22, 0, tzinfo=timezone.utc)


def _src(kind: OccupancySignalKind, weight: float = 1.0, eid: str = "") -> OccupancySource:
    return OccupancySource(entity_id=eid or f"{kind.value}_src", kind=kind, weight=weight)


def _reading(source: OccupancySource, active: bool) -> OccupancySignalReading:
    return OccupancySignalReading(source=source, is_active=active)


# --- resolve_occupancy --------------------------------------------------------


def test_no_sources_means_always_confirmed() -> None:
    cfg = OccupancyConfig()
    out = resolve_occupancy(cfg, (), None, _NOW)
    assert out.state is OccupancyState.CONFIRMED
    assert out.confidence == 1.0


def test_active_presence_above_threshold_is_confirmed() -> None:
    src = _src(OccupancySignalKind.PRESENCE, weight=1.0)
    cfg = OccupancyConfig(sources=(src,))
    out = resolve_occupancy(cfg, (_reading(src, True),), None, _NOW)
    assert out.state is OccupancyState.CONFIRMED
    assert src.entity_id in out.active_sources


def test_calendar_only_active_is_expected_not_confirmed() -> None:
    """Calendar/schedule signals never imply *current* presence."""
    cal = _src(OccupancySignalKind.CALENDAR, weight=1.0)
    cfg = OccupancyConfig(sources=(cal,))
    out = resolve_occupancy(cfg, (_reading(cal, True),), None, _NOW)
    assert out.state is OccupancyState.EXPECTED


def test_below_expected_threshold_with_no_history_is_unoccupied() -> None:
    p = _src(OccupancySignalKind.PRESENCE, weight=1.0)
    cal = _src(OccupancySignalKind.CALENDAR, weight=2.0)
    cfg = OccupancyConfig(sources=(p, cal))
    # confidence = 1 / (1+2) = 0.33 < 0.4 expected_threshold
    out = resolve_occupancy(cfg, (_reading(p, True), _reading(cal, False)), None, _NOW)
    assert out.state is OccupancyState.UNOCCUPIED


def test_recent_holds_within_linger_window() -> None:
    p = _src(OccupancySignalKind.PRESENCE, weight=1.0)
    cfg = OccupancyConfig(sources=(p,), linger_minutes=10.0)
    last = _NOW - timedelta(minutes=5)
    out = resolve_occupancy(cfg, (_reading(p, False),), last, _NOW)
    assert out.state is OccupancyState.RECENT


def test_unoccupied_after_linger_window_expires() -> None:
    p = _src(OccupancySignalKind.PRESENCE, weight=1.0)
    cfg = OccupancyConfig(sources=(p,), linger_minutes=10.0)
    last = _NOW - timedelta(minutes=20)
    out = resolve_occupancy(cfg, (_reading(p, False),), last, _NOW)
    assert out.state is OccupancyState.UNOCCUPIED


def test_manual_override_short_circuits_to_confirmed() -> None:
    override = _src(OccupancySignalKind.MANUAL_OVERRIDE, weight=1.0)
    p = _src(OccupancySignalKind.PRESENCE, weight=10.0)
    cfg = OccupancyConfig(sources=(override, p))
    out = resolve_occupancy(
        cfg,
        (_reading(override, True), _reading(p, False)),
        None,
        _NOW,
    )
    assert out.state is OccupancyState.CONFIRMED


def test_multi_source_weighted_confidence() -> None:
    p = _src(OccupancySignalKind.PRESENCE, weight=2.0, eid="binary_sensor.motion")
    cal = _src(OccupancySignalKind.CALENDAR, weight=1.0, eid="calendar.work")
    cfg = OccupancyConfig(sources=(p, cal), confirmed_threshold=0.6)
    # Presence active with weight 2 of total 3 → confidence 0.667 > 0.6
    out = resolve_occupancy(cfg, (_reading(p, True), _reading(cal, False)), None, _NOW)
    assert out.state is OccupancyState.CONFIRMED


# --- setback_adjust -----------------------------------------------------------


def test_setback_passes_through_when_occupied() -> None:
    intent = ZoneIntent("z1", HVACMode.HEAT, 21.0)
    out = setback_adjust(intent, OccupancyState.CONFIRMED, SetbackPolicy())
    assert out is intent


def test_setback_lowers_heat_target_when_unoccupied() -> None:
    intent = ZoneIntent("z1", HVACMode.HEAT, 21.0)
    out = setback_adjust(
        intent, OccupancyState.UNOCCUPIED, SetbackPolicy(setback_offset_heat=4.0)
    )
    assert out.target_temperature == 17.0
    assert out.hvac_mode is HVACMode.HEAT


def test_setback_raises_cool_target_when_unoccupied() -> None:
    intent = ZoneIntent("z1", HVACMode.COOL, 24.0)
    out = setback_adjust(
        intent, OccupancyState.UNOCCUPIED, SetbackPolicy(setback_offset_cool=3.0)
    )
    assert out.target_temperature == 27.0


def test_setback_no_op_for_off_mode() -> None:
    intent = ZoneIntent("z1", HVACMode.OFF, 21.0)
    out = setback_adjust(intent, OccupancyState.UNOCCUPIED, SetbackPolicy())
    assert out is intent


def test_setback_no_op_when_no_target() -> None:
    intent = ZoneIntent("z1", HVACMode.HEAT, None)
    out = setback_adjust(intent, OccupancyState.UNOCCUPIED, SetbackPolicy())
    assert out is intent


# --- comfort weight -----------------------------------------------------------


def test_comfort_weight_is_one_when_not_unoccupied() -> None:
    policy = SetbackPolicy(unoccupied_comfort_weight=0.1)
    for state in (
        OccupancyState.CONFIRMED,
        OccupancyState.EXPECTED,
        OccupancyState.RECENT,
    ):
        assert occupancy_comfort_weight(state, policy) == 1.0


def test_comfort_weight_drops_when_unoccupied() -> None:
    policy = SetbackPolicy(unoccupied_comfort_weight=0.1)
    assert occupancy_comfort_weight(OccupancyState.UNOCCUPIED, policy) == 0.1
