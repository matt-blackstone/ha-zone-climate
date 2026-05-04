"""Tests for runtime-invariant checks on the coordinator's output."""

from __future__ import annotations

from custom_components.multisplit_zone_controller.invariants import (
    incompatible_mode_violations,
)
from custom_components.multisplit_zone_controller.models import (
    EffectiveReadings,
    GroupDecision,
    HVACMode,
    SourceQuality,
    ZoneDecision,
)


def _eff() -> EffectiveReadings:
    return EffectiveReadings(
        temperature=20.0,
        humidity=None,
        temperature_source="head",
        humidity_source="none",
        quality=SourceQuality.OK,
    )


def _zone_decision(
    zone_id: str,
    mode: HVACMode,
    *,
    target: float | None = 21.0,
    blocked: bool = False,
) -> ZoneDecision:
    return ZoneDecision(
        zone_id=zone_id,
        dispatched_mode=mode,
        dispatched_target=target if mode is not HVACMode.OFF else None,
        blocked=blocked,
        block_reason=None,
        safety_override=False,
        priority_score=0.0,
        effective=_eff(),
    )


def _decision(*zds: ZoneDecision) -> GroupDecision:
    return GroupDecision(group_id="g", zones={z.zone_id: z for z in zds})


_HEAT_COOL = frozenset({HVACMode.HEAT, HVACMode.COOL})
_HEAT_FAN = frozenset({HVACMode.HEAT, HVACMode.FAN_ONLY})


def test_no_violations_when_all_off() -> None:
    decision = _decision(
        _zone_decision("z1", HVACMode.OFF),
        _zone_decision("z2", HVACMode.OFF),
    )
    assert incompatible_mode_violations(decision, [_HEAT_COOL]) == []


def test_no_violations_when_modes_compatible() -> None:
    decision = _decision(
        _zone_decision("z1", HVACMode.HEAT),
        _zone_decision("z2", HVACMode.HEAT),
    )
    assert incompatible_mode_violations(decision, [_HEAT_COOL]) == []


def test_off_paired_with_anything_is_compatible() -> None:
    """OFF is universally compatible (head not running)."""
    decision = _decision(
        _zone_decision("z1", HVACMode.OFF),
        _zone_decision("z2", HVACMode.HEAT),
        _zone_decision("z3", HVACMode.COOL, blocked=True),
    )
    # z3 is shown as COOL but blocked=True from arbitration's view.
    # That's actually still flagged here because the *dispatched mode*
    # is what the dispatcher will write to the head; if arbitration
    # said COOL it should have set OFF (which it does in normal flow).
    # This test guards the contract: blocked=True alone doesn't make a
    # mode "safe" — only OFF does.
    assert incompatible_mode_violations(decision, [_HEAT_COOL]) == [
        ("z2", "z3", HVACMode.HEAT, HVACMode.COOL),
    ]


def test_heat_cool_pair_flagged() -> None:
    decision = _decision(
        _zone_decision("z_heat", HVACMode.HEAT),
        _zone_decision("z_cool", HVACMode.COOL),
    )
    violations = incompatible_mode_violations(decision, [_HEAT_COOL])
    assert violations == [("z_cool", "z_heat", HVACMode.COOL, HVACMode.HEAT)]


def test_three_way_violation_lists_every_offending_pair() -> None:
    """User's reported scenario: HEAT + COOL + FAN_ONLY all live at
    once → both forbidden pairs (heat-cool and heat-fan_only) get
    individually flagged. The compatible pair (cool-fan_only) is not
    in the incompatible set so it is not flagged.
    """
    decision = _decision(
        _zone_decision("z_heat", HVACMode.HEAT),
        _zone_decision("z_cool", HVACMode.COOL),
        _zone_decision("z_fan", HVACMode.FAN_ONLY),
    )
    violations = incompatible_mode_violations(
        decision, [_HEAT_COOL, _HEAT_FAN]
    )
    pairs = {(a, b) for a, b, _, _ in violations}
    assert ("z_cool", "z_heat") in pairs
    assert ("z_fan", "z_heat") in pairs
    # cool ↔ fan_only is NOT incompatible — must not be in the list.
    assert ("z_cool", "z_fan") not in pairs
    assert len(violations) == 2


def test_no_pairs_configured_means_no_violations() -> None:
    decision = _decision(
        _zone_decision("z1", HVACMode.HEAT),
        _zone_decision("z2", HVACMode.COOL),
    )
    assert incompatible_mode_violations(decision, []) == []


def test_violation_results_are_sorted_for_stable_suppression_keys() -> None:
    """The coordinator's warn-once cache keys off the result tuple, so
    repeat calls with the same logical violation must always produce
    the same (sorted) tuple regardless of dict-iteration order.
    """
    decision_a = _decision(
        _zone_decision("z_heat", HVACMode.HEAT),
        _zone_decision("z_cool", HVACMode.COOL),
    )
    decision_b = _decision(
        _zone_decision("z_cool", HVACMode.COOL),
        _zone_decision("z_heat", HVACMode.HEAT),
    )
    a = incompatible_mode_violations(decision_a, [_HEAT_COOL])
    b = incompatible_mode_violations(decision_b, [_HEAT_COOL])
    assert a == b
