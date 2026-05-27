"""Tests for compressor-group arbitration and scoring."""

from __future__ import annotations

from datetime import timedelta

from custom_components.multisplit_zone_controller.arbitration import (
    SAFETY_PRIORITY_BONUS,
    arbitrate,
    score_zone,
)
from custom_components.multisplit_zone_controller.models import (
    EffectiveReadings,
    FusionConfig,
    GroupConfig,
    HumidityPolicy,
    HVACMode,
    OccupancyResolution,
    OccupancyState,
    ResolvedIntent,
    SetbackPolicy,
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


def _group(*zones: ZoneConfig, incompatible=()) -> GroupConfig:
    return GroupConfig(
        group_id="g",
        name="G",
        zones=tuple(zones),
        incompatible_mode_pairs=frozenset(frozenset(p) for p in incompatible),
        update_interval=timedelta(seconds=30),
    )


def _eff(temp: float | None) -> EffectiveReadings:
    return EffectiveReadings(
        temperature=temp,
        humidity=None,
        temperature_source="head",
        humidity_source="none",
        quality=SourceQuality.OK if temp is not None else SourceQuality.UNAVAILABLE,
    )


def _intent(
    zid: str,
    mode: HVACMode,
    target: float | None = 21.0,
    safety: bool = False,
) -> ResolvedIntent:
    return ResolvedIntent(
        zone_id=zid,
        hvac_mode=mode,
        target_temperature=target,
        safety_override=safety,
    )


# --- score_zone ---------------------------------------------------------------


def test_score_off_zone_is_zero() -> None:
    z = _zone("z1")
    assert score_zone(z, _intent("z1", HVACMode.OFF), _eff(20.0)) == 0.0


def test_score_uses_directional_heat_cool_demand_with_deadband() -> None:
    z = _zone("z1")
    heat_score = score_zone(
        z, _intent("z1", HVACMode.HEAT, target=22.0), _eff(18.0)
    )
    cool_score = score_zone(
        z, _intent("z1", HVACMode.COOL, target=22.0), _eff(25.0)
    )
    satisfied_heat = score_zone(
        z, _intent("z1", HVACMode.HEAT, target=22.0), _eff(25.0)
    )
    satisfied_cool = score_zone(
        z, _intent("z1", HVACMode.COOL, target=22.0), _eff(19.0)
    )

    assert heat_score == 3.5
    assert cool_score == 2.5
    assert satisfied_heat == 0.0
    assert satisfied_cool == 0.0


def test_score_honors_custom_deadband() -> None:
    z = ZoneConfig(
        zone_id="z1",
        name="Z1",
        head_climate_entity="climate.h1",
        fusion=FusionConfig(),
        demand_deadband=1.0,
    )

    score = score_zone(z, _intent("z1", HVACMode.HEAT, target=22.0), _eff(18.0))

    assert score == 3.0


def test_safety_override_dominates_score() -> None:
    z = _zone("z1")
    score = score_zone(
        z,
        _intent("z1", HVACMode.HEAT, target=22.0, safety=True),
        _eff(18.0),
    )
    assert score >= SAFETY_PRIORITY_BONUS


# --- arbitrate ----------------------------------------------------------------


def test_all_off_dispatches_off() -> None:
    g = _group(_zone("z1"), _zone("z2"))
    intents = {z.zone_id: _intent(z.zone_id, HVACMode.OFF) for z in g.zones}
    eff = {z.zone_id: _eff(20.0) for z in g.zones}
    decision = arbitrate(g, intents, eff)
    for d in decision.zones.values():
        assert d.dispatched_mode is HVACMode.OFF
        assert d.blocked is False
        assert d.block_reason is None


def test_compatible_modes_all_dispatched() -> None:
    g = _group(
        _zone("z1"),
        _zone("z2"),
        incompatible=[(HVACMode.HEAT, HVACMode.COOL)],
    )
    intents = {
        "z1": _intent("z1", HVACMode.HEAT, target=22.0),
        "z2": _intent("z2", HVACMode.HEAT, target=21.0),
    }
    eff = {"z1": _eff(18.0), "z2": _eff(19.0)}
    decision = arbitrate(g, intents, eff)
    assert decision.zones["z1"].dispatched_mode is HVACMode.HEAT
    assert decision.zones["z2"].dispatched_mode is HVACMode.HEAT
    assert all(not d.blocked for d in decision.zones.values())


def test_incompatible_modes_winner_takes_higher_score() -> None:
    g = _group(
        _zone("z1"),
        _zone("z2"),
        incompatible=[(HVACMode.HEAT, HVACMode.COOL)],
    )
    # z1 wants heat with deviation of 5, z2 wants cool with deviation of 1.
    intents = {
        "z1": _intent("z1", HVACMode.HEAT, target=22.0),
        "z2": _intent("z2", HVACMode.COOL, target=21.0),
    }
    eff = {"z1": _eff(17.0), "z2": _eff(22.0)}
    decision = arbitrate(g, intents, eff)
    assert decision.zones["z1"].dispatched_mode is HVACMode.HEAT
    assert decision.zones["z1"].blocked is False
    assert decision.zones["z2"].dispatched_mode is HVACMode.OFF
    assert decision.zones["z2"].blocked is True
    assert "Z1" in (decision.zones["z2"].block_reason or "")


def test_satisfied_cool_zones_do_not_outvote_real_heat_demand() -> None:
    """Regression for satisfied cooling zones summing into false demand."""
    g = _group(
        _zone("cool1"),
        _zone("cool2"),
        _zone("heat"),
        incompatible=[(HVACMode.HEAT, HVACMode.COOL)],
    )
    intents = {
        "cool1": _intent("cool1", HVACMode.COOL, target=24.0),
        "cool2": _intent("cool2", HVACMode.COOL, target=24.0),
        "heat": _intent("heat", HVACMode.HEAT, target=22.0),
    }
    eff = {
        "cool1": _eff(21.5),
        "cool2": _eff(21.5),
        "heat": _eff(18.0),
    }

    decision = arbitrate(g, intents, eff)

    assert decision.zones["cool1"].priority_score == 0.0
    assert decision.zones["cool2"].priority_score == 0.0
    assert decision.zones["heat"].priority_score == 3.5
    assert decision.zones["heat"].dispatched_mode is HVACMode.HEAT
    assert decision.zones["cool1"].blocked is True
    assert decision.zones["cool2"].blocked is True


def test_zero_demand_active_zone_stays_dispatched_without_conflict() -> None:
    """A satisfied request should not be marked blocked by the empty subset."""
    g = _group(_zone("z1"))
    intents = {"z1": _intent("z1", HVACMode.COOL, target=22.0)}
    eff = {"z1": _eff(22.25)}

    decision = arbitrate(g, intents, eff)

    assert decision.zones["z1"].priority_score == 0.0
    assert decision.zones["z1"].dispatched_mode is HVACMode.COOL
    assert decision.zones["z1"].blocked is False


def test_allowed_thermal_mode_excludes_opposite_changeover_mode() -> None:
    g = _group(
        _zone("cool"),
        _zone("heat"),
        incompatible=[(HVACMode.HEAT, HVACMode.COOL)],
    )
    intents = {
        "cool": _intent("cool", HVACMode.COOL, target=24.0),
        "heat": _intent("heat", HVACMode.HEAT, target=22.0),
    }
    eff = {"cool": _eff(21.5), "heat": _eff(18.0)}

    decision = arbitrate(g, intents, eff, allowed_thermal_mode=HVACMode.COOL)

    assert decision.zones["cool"].dispatched_mode is HVACMode.COOL
    assert decision.zones["heat"].dispatched_mode is HVACMode.OFF


def test_heat_and_fan_only_are_treated_as_incompatible() -> None:
    """The integration's default-incompatible set blocks heat<->fan_only.

    This test asserts the same arbitration code-path used for
    heat<->cool also covers heat<->fan_only when that pair is in the
    group's ``incompatible_mode_pairs``. The default merge happens at
    ``parse_groups`` time (see ``test_config_schema``); here we just
    confirm the runtime honors any explicit pair the same way.
    """
    g = _group(
        _zone("z1"),
        _zone("z2"),
        incompatible=[(HVACMode.HEAT, HVACMode.FAN_ONLY)],
    )
    # z1 wants heat with a large deviation; z2 wants fan_only.
    intents = {
        "z1": _intent("z1", HVACMode.HEAT, target=22.0),
        "z2": _intent("z2", HVACMode.FAN_ONLY, target=21.0),
    }
    eff = {"z1": _eff(15.0), "z2": _eff(21.0)}
    decision = arbitrate(g, intents, eff)
    assert decision.zones["z1"].dispatched_mode is HVACMode.HEAT
    assert decision.zones["z1"].blocked is False
    assert decision.zones["z2"].dispatched_mode is HVACMode.OFF
    assert decision.zones["z2"].blocked is True


def test_safety_override_always_wins_arbitration() -> None:
    g = _group(
        _zone("z1"),
        _zone("z2"),
        incompatible=[(HVACMode.HEAT, HVACMode.COOL)],
    )
    # z1 has a huge comfort deviation but no safety; z2 hit a safety floor.
    intents = {
        "z1": _intent("z1", HVACMode.COOL, target=21.0),
        "z2": _intent("z2", HVACMode.HEAT, target=10.0, safety=True),
    }
    eff = {"z1": _eff(35.0), "z2": _eff(7.0)}
    decision = arbitrate(g, intents, eff)
    assert decision.zones["z2"].dispatched_mode is HVACMode.HEAT
    assert decision.zones["z2"].safety_override is True
    assert decision.zones["z1"].dispatched_mode is HVACMode.OFF
    assert decision.zones["z1"].blocked is True


def test_no_incompatibility_means_anything_goes() -> None:
    g = _group(_zone("z1"), _zone("z2"))
    intents = {
        "z1": _intent("z1", HVACMode.HEAT, target=22.0),
        "z2": _intent("z2", HVACMode.COOL, target=21.0),
    }
    eff = {"z1": _eff(20.0), "z2": _eff(23.0)}
    decision = arbitrate(g, intents, eff)
    assert decision.zones["z1"].dispatched_mode is HVACMode.HEAT
    assert decision.zones["z2"].dispatched_mode is HVACMode.COOL


def test_three_way_arbitration_picks_best_subset() -> None:
    g = _group(
        _zone("z1"),
        _zone("z2"),
        _zone("z3"),
        incompatible=[(HVACMode.HEAT, HVACMode.COOL)],
    )
    # Two heat zones with combined demand 5 should beat one cool zone of 4.5.
    intents = {
        "z1": _intent("z1", HVACMode.HEAT, target=22.0),
        "z2": _intent("z2", HVACMode.HEAT, target=22.0),
        "z3": _intent("z3", HVACMode.COOL, target=21.0),
    }
    eff = {"z1": _eff(19.0), "z2": _eff(19.0), "z3": _eff(26.0)}
    decision = arbitrate(g, intents, eff)
    assert decision.zones["z1"].blocked is False
    assert decision.zones["z2"].blocked is False
    assert decision.zones["z3"].blocked is True


def test_occupancy_weights_comfort_score() -> None:
    """An unoccupied zone with similar deviation should lose to an occupied one."""
    z1 = ZoneConfig(
        zone_id="z1",
        name="Z1",
        head_climate_entity="climate.h1",
        fusion=FusionConfig(),
        setback=SetbackPolicy(unoccupied_comfort_weight=0.2),
    )
    z2 = ZoneConfig(
        zone_id="z2",
        name="Z2",
        head_climate_entity="climate.h2",
        fusion=FusionConfig(),
        setback=SetbackPolicy(unoccupied_comfort_weight=0.2),
    )
    g = GroupConfig(
        group_id="g",
        name="G",
        zones=(z1, z2),
        incompatible_mode_pairs=frozenset({frozenset({HVACMode.HEAT, HVACMode.COOL})}),
        update_interval=timedelta(seconds=30),
    )
    intents = {
        "z1": _intent("z1", HVACMode.HEAT, target=22.0),
        "z2": _intent("z2", HVACMode.COOL, target=22.0),
    }
    eff = {"z1": _eff(19.0), "z2": _eff(25.0)}  # both deviation 3
    occupancy = {
        "z1": OccupancyResolution(OccupancyState.CONFIRMED, 1.0, ()),
        "z2": OccupancyResolution(OccupancyState.UNOCCUPIED, 0.0, ()),
    }
    decision = arbitrate(g, intents, eff, occupancy)
    assert decision.zones["z1"].dispatched_mode is HVACMode.HEAT
    assert decision.zones["z2"].dispatched_mode is HVACMode.OFF
    assert decision.zones["z2"].blocked is True
    assert decision.zones["z1"].occupancy_state is OccupancyState.CONFIRMED
    assert decision.zones["z2"].occupancy_state is OccupancyState.UNOCCUPIED


def test_fan_offset_reduces_comfort_score() -> None:
    """A zone with a fan credit should lose to one without when deviations match."""
    g = _group(
        _zone("z1"),
        _zone("z2"),
        incompatible=[(HVACMode.HEAT, HVACMode.COOL)],
    )
    intents = {
        "z1": _intent("z1", HVACMode.HEAT, target=22.0),
        "z2": _intent("z2", HVACMode.COOL, target=22.0),
    }
    eff = {"z1": _eff(20.0), "z2": _eff(24.0)}  # both deviation 2
    occupancy = {
        "z1": OccupancyResolution(OccupancyState.CONFIRMED, 1.0, ()),
        "z2": OccupancyResolution(OccupancyState.CONFIRMED, 1.0, ()),
    }
    # z2's fan offers 1.5°C of credit, dropping its demand to 0 vs z1's 1.5.
    fan_offsets = {"z1": 0.0, "z2": 1.5}
    decision = arbitrate(g, intents, eff, occupancy, None, fan_offsets)
    assert decision.zones["z1"].dispatched_mode is HVACMode.HEAT
    assert decision.zones["z2"].blocked is True
    assert decision.zones["z2"].fan_comfort_offset == 1.5


def test_safety_still_wins_even_when_unoccupied() -> None:
    """Safety override on an unoccupied zone must still beat occupied comfort."""
    g = _group(
        _zone("z1"),
        _zone("z2"),
        incompatible=[(HVACMode.HEAT, HVACMode.COOL)],
    )
    intents = {
        "z1": _intent("z1", HVACMode.COOL, target=21.0),
        "z2": _intent("z2", HVACMode.HEAT, target=10.0, safety=True),
    }
    eff = {"z1": _eff(35.0), "z2": _eff(7.0)}
    occupancy = {
        "z1": OccupancyResolution(OccupancyState.CONFIRMED, 1.0, ()),
        "z2": OccupancyResolution(OccupancyState.UNOCCUPIED, 0.0, ()),
    }
    decision = arbitrate(g, intents, eff, occupancy)
    assert decision.zones["z2"].dispatched_mode is HVACMode.HEAT
    assert decision.zones["z2"].safety_override is True


def test_comfort_temp_override_changes_score() -> None:
    """When psychrometric scoring is on, deviation uses the comfort temp override."""
    g = _group(
        _zone("z1"),
        _zone("z2"),
        incompatible=[(HVACMode.HEAT, HVACMode.COOL)],
    )
    intents = {
        "z1": _intent("z1", HVACMode.COOL, target=22.0),
        "z2": _intent("z2", HVACMode.HEAT, target=22.0),
    }
    # Raw dry-bulb temperatures suggest equal deviation (z1 hot by 2, z2 cold by 2).
    eff = {"z1": _eff(24.0), "z2": _eff(20.0)}
    # But with humidity correction, z1 feels +2°C hotter than its dry bulb.
    comfort_temps = {"z1": 26.0, "z2": 20.0}
    decision = arbitrate(g, intents, eff, None, None, None, comfort_temps)
    assert decision.zones["z1"].dispatched_mode is HVACMode.COOL
    assert decision.zones["z2"].blocked is True
    assert decision.zones["z1"].effective_comfort_temperature == 26.0


def test_humidity_dehumidify_bonus_promotes_cool_zone() -> None:
    """A humid cool zone with a dehumidify bonus should beat a heat zone of equal deviation."""
    z_cool = ZoneConfig(
        zone_id="cool",
        name="COOL",
        head_climate_entity="climate.h1",
        fusion=FusionConfig(),
        humidity=HumidityPolicy(dehumidify_threshold=60.0, dehumidify_bonus=5.0),
    )
    z_heat = ZoneConfig(
        zone_id="heat",
        name="HEAT",
        head_climate_entity="climate.h2",
        fusion=FusionConfig(),
    )
    g = GroupConfig(
        group_id="g",
        name="G",
        zones=(z_cool, z_heat),
        incompatible_mode_pairs=frozenset({frozenset({HVACMode.HEAT, HVACMode.COOL})}),
        update_interval=timedelta(seconds=30),
    )
    intents = {
        "cool": _intent("cool", HVACMode.COOL, target=22.0),
        "heat": _intent("heat", HVACMode.HEAT, target=22.0),
    }
    eff = {
        "cool": EffectiveReadings(
            temperature=24.0,
            humidity=70.0,
            temperature_source="head",
            humidity_source="external:sensor.h",
            quality=SourceQuality.OK,
        ),
        "heat": _eff(20.0),
    }
    decision = arbitrate(g, intents, eff)
    assert decision.zones["cool"].dispatched_mode is HVACMode.COOL
    assert decision.zones["heat"].blocked is True
    assert decision.zones["cool"].humidity_priority_contribution == 5.0


def test_three_zones_heat_cool_fan_only_never_all_dispatched() -> None:
    """User-reported scenario: three zones each in HEAT/COOL/FAN_ONLY.

    With the integration's default-incompatible pairs ({HEAT, COOL}
    and {HEAT, FAN_ONLY}), arbitration must NEVER permit all three
    modes to run together. The compatible subsets are:

    * ``{HEAT}`` alone (blocks COOL and FAN_ONLY),
    * ``{COOL, FAN_ONLY}`` together (blocks HEAT),
    * subsets thereof.

    This test fixes the HEAT zone's deviation high enough that the
    arbiter picks ``{HEAT}`` as the winner, then asserts the other
    two are explicitly blocked.
    """
    g = _group(
        _zone("z_heat"),
        _zone("z_cool"),
        _zone("z_fan"),
        incompatible=[
            (HVACMode.HEAT, HVACMode.COOL),
            (HVACMode.HEAT, HVACMode.FAN_ONLY),
        ],
    )
    intents = {
        "z_heat": _intent("z_heat", HVACMode.HEAT, target=22.0),
        "z_cool": _intent("z_cool", HVACMode.COOL, target=22.0),
        "z_fan": _intent("z_fan", HVACMode.FAN_ONLY, target=22.0),
    }
    # HEAT zone: 7°C below target → score 6.5. COOL zone is 1°C above
    # target → score 0.5; FAN_ONLY has no temperature demand. {HEAT} wins.
    eff = {
        "z_heat": _eff(15.0),
        "z_cool": _eff(23.0),
        "z_fan": _eff(23.0),
    }
    decision = arbitrate(g, intents, eff)

    assert decision.zones["z_heat"].dispatched_mode is HVACMode.HEAT
    assert decision.zones["z_heat"].blocked is False
    assert decision.zones["z_cool"].dispatched_mode is HVACMode.OFF
    assert decision.zones["z_cool"].blocked is True
    assert decision.zones["z_fan"].dispatched_mode is HVACMode.OFF
    assert decision.zones["z_fan"].blocked is True


def test_three_zones_heat_cool_fan_only_picks_compatible_pair_when_better() -> None:
    """Same three-zone setup; when COOL+FAN_ONLY combined beat HEAT alone.

    Because COOL and FAN_ONLY are *not* in any incompatible pair, the
    arbiter is allowed to run them together. With a heavily one-sided
    deviation profile (COOL/FAN_ONLY zones hot, HEAT zone barely off
    target), the optimum subset is ``{COOL, FAN_ONLY}`` and HEAT
    must be blocked.

    This complements the prior test: between them they show the
    arbiter neither defaults to "HEAT always wins" nor lets all three
    run.
    """
    g = _group(
        _zone("z_heat"),
        _zone("z_cool"),
        _zone("z_fan"),
        incompatible=[
            (HVACMode.HEAT, HVACMode.COOL),
            (HVACMode.HEAT, HVACMode.FAN_ONLY),
        ],
    )
    intents = {
        "z_heat": _intent("z_heat", HVACMode.HEAT, target=22.0),
        "z_cool": _intent("z_cool", HVACMode.COOL, target=22.0),
        "z_fan": _intent("z_fan", HVACMode.FAN_ONLY, target=22.0),
    }
    # HEAT zone: 0.5°C below target → inside deadband. COOL is 5°C above
    # target → score 4.5; FAN_ONLY has no temperature demand but can join
    # the compatible COOL subset. {COOL, FAN_ONLY} wins.
    eff = {
        "z_heat": _eff(21.5),
        "z_cool": _eff(27.0),
        "z_fan": _eff(27.0),
    }
    decision = arbitrate(g, intents, eff)

    assert decision.zones["z_cool"].dispatched_mode is HVACMode.COOL
    assert decision.zones["z_cool"].blocked is False
    assert decision.zones["z_fan"].dispatched_mode is HVACMode.FAN_ONLY
    assert decision.zones["z_fan"].blocked is False
    assert decision.zones["z_heat"].dispatched_mode is HVACMode.OFF
    assert decision.zones["z_heat"].blocked is True


def test_three_zone_arbitration_never_yields_an_incompatible_mode_set() -> None:
    """Property-style: across many score profiles, the dispatched mode
    set never contains an incompatible pair.

    This is the broader invariant the user wanted to lock down:
    regardless of which combination of zones happens to score highest,
    the arbiter MUST emit a globally compatible set. If any future
    refactor breaks the compatibility check or introduces a code-path
    that bypasses it (e.g., safety override mishandled, partial
    fallback, etc.), this test will catch it.
    """
    g = _group(
        _zone("z_heat"),
        _zone("z_cool"),
        _zone("z_fan"),
        incompatible=[
            (HVACMode.HEAT, HVACMode.COOL),
            (HVACMode.HEAT, HVACMode.FAN_ONLY),
        ],
    )
    intents = {
        "z_heat": _intent("z_heat", HVACMode.HEAT, target=22.0),
        "z_cool": _intent("z_cool", HVACMode.COOL, target=22.0),
        "z_fan": _intent("z_fan", HVACMode.FAN_ONLY, target=22.0),
    }
    # Sweep a small grid of effective temperatures — every
    # combination represents a different relative-priority outcome
    # for the three zones.
    forbidden_subsets = [
        frozenset({HVACMode.HEAT, HVACMode.COOL}),
        frozenset({HVACMode.HEAT, HVACMode.FAN_ONLY}),
        frozenset({HVACMode.HEAT, HVACMode.COOL, HVACMode.FAN_ONLY}),
    ]
    for heat_eff in (15.0, 18.0, 21.5, 22.0, 22.5, 25.0, 28.0):
        for cool_eff in (15.0, 22.0, 25.0, 28.0):
            for fan_eff in (15.0, 22.0, 25.0, 28.0):
                eff = {
                    "z_heat": _eff(heat_eff),
                    "z_cool": _eff(cool_eff),
                    "z_fan": _eff(fan_eff),
                }
                decision = arbitrate(g, intents, eff)
                # Collect the modes actually dispatched (ignoring OFF,
                # which is always compatible with anything).
                live_modes = {
                    d.dispatched_mode
                    for d in decision.zones.values()
                    if d.dispatched_mode is not HVACMode.OFF
                }
                for forbidden in forbidden_subsets:
                    assert not forbidden.issubset(live_modes), (
                        f"Arbitration produced an incompatible live mode "
                        f"set {live_modes} (forbidden: {forbidden}) for "
                        f"effs heat={heat_eff} cool={cool_eff} "
                        f"fan={fan_eff}"
                    )


def test_blocked_zone_has_human_readable_reason() -> None:
    g = _group(
        _zone("z1"),
        _zone("z2"),
        incompatible=[(HVACMode.HEAT, HVACMode.COOL)],
    )
    intents = {
        "z1": _intent("z1", HVACMode.HEAT, target=22.0),
        "z2": _intent("z2", HVACMode.COOL, target=21.0),
    }
    eff = {"z1": _eff(15.0), "z2": _eff(22.0)}
    decision = arbitrate(g, intents, eff)
    blocked = decision.zones["z2"]
    assert blocked.blocked is True
    assert blocked.block_reason
    assert "cool" in blocked.block_reason.lower()
    assert "z1" in blocked.block_reason.lower() or "Z1" in blocked.block_reason
