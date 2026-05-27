"""Tests for group-level heat/cool changeover dwell protection."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from custom_components.multisplit_zone_controller.arbitration import arbitrate
from custom_components.multisplit_zone_controller.changeover import apply_changeover_dwell
from custom_components.multisplit_zone_controller.models import (
    EffectiveReadings,
    FusionConfig,
    GroupConfig,
    HVACMode,
    ResolvedIntent,
    SourceQuality,
    ZoneConfig,
)


def _zone(zid: str) -> ZoneConfig:
    return ZoneConfig(
        zone_id=zid,
        name=zid.upper(),
        head_climate_entity=f"climate.head_{zid}",
        fusion=FusionConfig(),
    )


def _group(*zones: ZoneConfig, dwell: float = 15.0) -> GroupConfig:
    return GroupConfig(
        group_id="g",
        name="G",
        zones=tuple(zones),
        incompatible_mode_pairs=frozenset(
            {frozenset({HVACMode.HEAT, HVACMode.COOL})}
        ),
        update_interval=timedelta(seconds=30),
        min_changeover_dwell_minutes=dwell,
    )


def _eff(temp: float) -> EffectiveReadings:
    return EffectiveReadings(
        temperature=temp,
        humidity=None,
        temperature_source="test",
        humidity_source="none",
        quality=SourceQuality.OK,
    )


def _intent(
    zid: str,
    mode: HVACMode,
    target: float,
    *,
    safety: bool = False,
) -> ResolvedIntent:
    return ResolvedIntent(
        zone_id=zid,
        hvac_mode=mode,
        target_temperature=target,
        safety_override=safety,
    )


def _apply(
    group: GroupConfig,
    now: datetime,
    resolved,
    eff,
    previous: HVACMode,
    started_at: datetime,
):
    decision = arbitrate(group, resolved, eff)
    return apply_changeover_dwell(
        group,
        decision,
        resolved,
        eff,
        {},
        {zid: intent.target_temperature for zid, intent in resolved.items()},
        {},
        None,
        previous,
        started_at,
        now,
    )


def test_changeover_dwell_holds_previous_mode_until_window_expires() -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    group = _group(_zone("cool"), _zone("heat"))
    resolved = {
        "cool": _intent("cool", HVACMode.COOL, 24.0),
        "heat": _intent("heat", HVACMode.HEAT, 22.0),
    }
    eff = {
        "cool": _eff(21.5),  # satisfied cooling: zero priority
        "heat": _eff(18.0),  # real heating demand
    }

    decision = _apply(
        group, now, resolved, eff, HVACMode.COOL, now - timedelta(minutes=1)
    )

    assert decision.zones["cool"].dispatched_mode is HVACMode.COOL
    assert decision.zones["heat"].dispatched_mode is HVACMode.OFF
    assert decision.zones["heat"].blocked is True
    assert "Changeover locked" in (decision.zones["heat"].block_reason or "")


def test_changeover_dwell_allows_flip_after_window_expires() -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    group = _group(_zone("cool"), _zone("heat"))
    resolved = {
        "cool": _intent("cool", HVACMode.COOL, 24.0),
        "heat": _intent("heat", HVACMode.HEAT, 22.0),
    }
    eff = {"cool": _eff(21.5), "heat": _eff(18.0)}

    decision = _apply(
        group, now, resolved, eff, HVACMode.COOL, now - timedelta(minutes=16)
    )

    assert decision.zones["heat"].dispatched_mode is HVACMode.HEAT
    assert decision.zones["heat"].blocked is False


def test_changeover_dwell_can_be_disabled() -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    group = _group(_zone("cool"), _zone("heat"), dwell=0.0)
    resolved = {
        "cool": _intent("cool", HVACMode.COOL, 24.0),
        "heat": _intent("heat", HVACMode.HEAT, 22.0),
    }
    eff = {"cool": _eff(21.5), "heat": _eff(18.0)}

    decision = _apply(group, now, resolved, eff, HVACMode.COOL, now)

    assert decision.zones["heat"].dispatched_mode is HVACMode.HEAT


def test_safety_changeover_bypasses_dwell() -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    group = _group(_zone("cool"), _zone("heat"))
    resolved = {
        "cool": _intent("cool", HVACMode.COOL, 24.0),
        "heat": _intent("heat", HVACMode.HEAT, 10.0, safety=True),
    }
    eff = {"cool": _eff(21.5), "heat": _eff(8.0)}

    decision = _apply(
        group, now, resolved, eff, HVACMode.COOL, now - timedelta(minutes=1)
    )

    assert decision.zones["heat"].dispatched_mode is HVACMode.HEAT
    assert decision.zones["heat"].safety_override is True
