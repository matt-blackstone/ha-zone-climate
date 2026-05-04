"""Tests for the auxiliary-heat evaluator and transition state machine."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from custom_components.multisplit_zone_controller.aux_heat import (
    AuxHeatRuntime,
    evaluate_aux_heat,
)
from custom_components.multisplit_zone_controller.models import (
    AuxHeatConfig,
    AuxHeatInputs,
    AuxHeatTrigger,
    HVACMode,
)


_NOW = datetime(2026, 5, 2, 22, 0, tzinfo=timezone.utc)


def _cfg(**kwargs) -> AuxHeatConfig:
    base = dict(
        enabled=True,
        device_entity_id="switch.aux_heater",
        safety_floor_margin=1.0,
        deactivation_margin=1.0,
        outdoor_lockout_temp=-5.0,
        fault_on_head_unavailable=True,
        settle_seconds=60.0,
    )
    base.update(kwargs)
    return AuxHeatConfig(**base)


def _inputs(**kwargs) -> AuxHeatInputs:
    base = dict(
        intent_mode=HVACMode.HEAT,
        effective_temp=18.0,
        safety_floor=10.0,
        outdoor_temp=5.0,
        head_available=True,
        currently_active=False,
    )
    base.update(kwargs)
    return AuxHeatInputs(**base)


# --- evaluator ---------------------------------------------------------------


def test_disabled_means_no_aux() -> None:
    out = evaluate_aux_heat(_cfg(enabled=False), _inputs())
    assert out.desired_active is False
    assert out.trigger is AuxHeatTrigger.NONE


def test_no_device_means_no_aux() -> None:
    out = evaluate_aux_heat(_cfg(device_entity_id=None), _inputs())
    assert out.desired_active is False


def test_non_heat_mode_means_no_aux() -> None:
    out = evaluate_aux_heat(_cfg(), _inputs(intent_mode=HVACMode.COOL))
    assert out.desired_active is False


def test_head_fault_triggers_aux() -> None:
    out = evaluate_aux_heat(_cfg(), _inputs(head_available=False))
    assert out.desired_active is True
    assert out.trigger is AuxHeatTrigger.HEAD_FAULT
    assert "Heat pump unavailable" in (out.status_message or "")


def test_outdoor_lockout_triggers_aux() -> None:
    out = evaluate_aux_heat(_cfg(outdoor_lockout_temp=-5.0), _inputs(outdoor_temp=-6.0))
    assert out.desired_active is True
    assert out.trigger is AuxHeatTrigger.OUTDOOR_LOCKOUT


def test_safety_floor_near_triggers_aux() -> None:
    # safety floor 10, margin 1 -> activate when temp <= 11
    out = evaluate_aux_heat(_cfg(), _inputs(effective_temp=10.5))
    assert out.desired_active is True
    assert out.trigger is AuxHeatTrigger.SAFETY_FLOOR_NEAR


def test_no_trigger_when_well_above_floor_and_warm_outside() -> None:
    out = evaluate_aux_heat(_cfg(outdoor_lockout_temp=-5.0), _inputs(effective_temp=20.0, outdoor_temp=10.0))
    assert out.desired_active is False


# --- hysteresis --------------------------------------------------------------


def test_hysteresis_keeps_aux_on_until_clearance_margin_met() -> None:
    # Currently active. floor=10, margin=1, deactivation=1 -> need temp >= 12 to clear.
    out = evaluate_aux_heat(_cfg(), _inputs(effective_temp=11.5, currently_active=True))
    assert out.desired_active is True
    assert out.trigger is AuxHeatTrigger.SAFETY_FLOOR_NEAR


def test_hysteresis_clears_when_all_conditions_pass_margin() -> None:
    # temp 12.5 >= 12 (floor 10 + margin 1 + deactivation 1)
    out = evaluate_aux_heat(_cfg(outdoor_lockout_temp=-5.0), _inputs(
        effective_temp=12.5, outdoor_temp=-3.0, head_available=True, currently_active=True
    ))
    assert out.desired_active is False


# --- AuxHeatRuntime ---------------------------------------------------------


def test_runtime_off_when_decision_inactive() -> None:
    rt = AuxHeatRuntime(_cfg())
    decision = evaluate_aux_heat(_cfg(), _inputs(effective_temp=20.0))
    out = rt.step(decision, _NOW)
    assert out.aux_on is False
    assert out.force_head_off is False
    assert rt.actual_active is False


def test_runtime_first_tick_starts_settle_and_forces_head_off() -> None:
    cfg = _cfg(settle_seconds=60)
    rt = AuxHeatRuntime(cfg)
    decision = evaluate_aux_heat(cfg, _inputs(effective_temp=10.5))
    out = rt.step(decision, _NOW)
    assert out.aux_on is False
    assert out.force_head_off is True
    assert out.in_settling is True
    assert rt.actual_active is False
    assert rt.pending_since == _NOW


def test_runtime_activates_aux_after_settle_elapsed() -> None:
    cfg = _cfg(settle_seconds=60)
    rt = AuxHeatRuntime(cfg)
    decision = evaluate_aux_heat(cfg, _inputs(effective_temp=10.5))
    rt.step(decision, _NOW)
    later = _NOW + timedelta(seconds=65)
    out = rt.step(decision, later)
    assert out.aux_on is True
    assert out.force_head_off is True
    assert out.in_settling is False
    assert rt.actual_active is True


def test_runtime_keeps_aux_on_while_active() -> None:
    cfg = _cfg(settle_seconds=60)
    rt = AuxHeatRuntime(cfg)
    decision = evaluate_aux_heat(cfg, _inputs(effective_temp=10.5))
    rt.step(decision, _NOW)
    rt.step(decision, _NOW + timedelta(seconds=120))
    out = rt.step(decision, _NOW + timedelta(seconds=180))
    assert out.aux_on is True
    assert out.force_head_off is True


def test_runtime_deactivates_immediately_when_decision_clears() -> None:
    cfg = _cfg(settle_seconds=10)
    rt = AuxHeatRuntime(cfg)
    rt.step(evaluate_aux_heat(cfg, _inputs(effective_temp=10.5)), _NOW)
    rt.step(evaluate_aux_heat(cfg, _inputs(effective_temp=10.5)), _NOW + timedelta(seconds=20))
    assert rt.actual_active is True

    cool_decision = evaluate_aux_heat(
        cfg, _inputs(effective_temp=20.0, currently_active=True)
    )
    out = rt.step(cool_decision, _NOW + timedelta(seconds=30))
    assert out.aux_on is False
    assert out.force_head_off is False
    assert rt.actual_active is False
