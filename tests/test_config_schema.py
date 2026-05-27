"""Tests for the YAML configuration parser."""

from __future__ import annotations

from datetime import timedelta

import pytest
import voluptuous as vol

from custom_components.multisplit_zone_controller.config_schema import (
    entry_to_group_config,
    parse_group_dict,
    parse_groups,
)
from custom_components.multisplit_zone_controller.models import (
    FusionStrategy,
    HVACMode,
    OccupancySignalKind,
)


_MINIMAL = {
    "groups": [
        {
            "group_id": "g1",
            "name": "Outdoor Unit 1",
            "incompatible_mode_pairs": [["heat", "cool"]],
            "zones": [
                {
                    "zone_id": "living",
                    "name": "Living Room",
                    "head_climate": "climate.head_living",
                    "fusion": {
                        "strategy": "external_preferred",
                        "head_temp_sensor": "sensor.head_living_temp",
                        "external_temp_sensors": ["sensor.living_room_temp"],
                    },
                    "safety": {"min_temp": 8.0, "max_temp": 32.0},
                },
                {
                    "zone_id": "bedroom",
                    "name": "Bedroom",
                    "head_climate": "climate.head_bedroom",
                    "fusion": {
                        "strategy": "head_only",
                        "head_temp_sensor": "sensor.head_bedroom_temp",
                    },
                },
            ],
        }
    ]
}


def test_parses_minimal_config() -> None:
    groups = parse_groups(_MINIMAL)
    assert len(groups) == 1
    g = groups[0]
    assert g.group_id == "g1"
    assert g.name == "Outdoor Unit 1"
    assert g.update_interval == timedelta(seconds=30)
    assert g.min_changeover_dwell_minutes == 15.0
    assert frozenset({HVACMode.HEAT, HVACMode.COOL}) in g.incompatible_mode_pairs

    zones_by_id = {z.zone_id: z for z in g.zones}
    living = zones_by_id["living"]
    assert living.fusion.strategy is FusionStrategy.EXTERNAL_PREFERRED
    assert living.fusion.head_temp_sensor == "sensor.head_living_temp"
    assert len(living.fusion.external_temp_sensors) == 1
    assert living.fusion.external_temp_sensors[0].entity_id == "sensor.living_room_temp"
    assert living.safety.min_temp == 8.0
    assert living.safety.max_temp == 32.0
    assert living.demand_deadband == 0.5

    bedroom = zones_by_id["bedroom"]
    assert bedroom.fusion.strategy is FusionStrategy.HEAD_ONLY
    assert bedroom.fusion.external_temp_sensors == ()


def test_parses_zone_demand_deadband() -> None:
    cfg = {
        "groups": [
            {
                "group_id": "g1",
                "name": "G1",
                "zones": [
                    {
                        "zone_id": "z1",
                        "name": "Z1",
                        "head_climate": "climate.head_z1",
                        "demand_deadband": 0.56,
                    }
                ],
            }
        ]
    }

    groups = parse_groups(cfg)

    assert groups[0].zones[0].demand_deadband == 0.56


def test_parses_group_changeover_dwell() -> None:
    cfg = {
        "groups": [
            {
                "group_id": "g1",
                "name": "G1",
                "min_changeover_dwell_minutes": 12.5,
                "zones": [
                    {
                        "zone_id": "z1",
                        "name": "Z1",
                        "head_climate": "climate.head_z1",
                    }
                ],
            }
        ]
    }

    groups = parse_groups(cfg)

    assert groups[0].min_changeover_dwell_minutes == 12.5


def test_rejects_negative_changeover_dwell() -> None:
    cfg = {
        "groups": [
            {
                "group_id": "g1",
                "name": "G1",
                "min_changeover_dwell_minutes": -1.0,
                "zones": [
                    {
                        "zone_id": "z1",
                        "name": "Z1",
                        "head_climate": "climate.head_z1",
                    }
                ],
            }
        ]
    }

    with pytest.raises(vol.Invalid):
        parse_groups(cfg)


def test_rejects_negative_demand_deadband() -> None:
    cfg = {
        "groups": [
            {
                "group_id": "g1",
                "name": "G1",
                "zones": [
                    {
                        "zone_id": "z1",
                        "name": "Z1",
                        "head_climate": "climate.head_z1",
                        "demand_deadband": -0.1,
                    }
                ],
            }
        ]
    }

    with pytest.raises(vol.Invalid):
        parse_groups(cfg)


def test_sensor_string_shorthand_expanded() -> None:
    groups = parse_groups(_MINIMAL)
    sensor = groups[0].zones[0].fusion.external_temp_sensors[0]
    assert sensor.weight == 1.0
    assert sensor.calibration_offset == 0.0


def test_default_incompatible_mode_pairs_are_merged_in() -> None:
    """heat<->fan_only is incompatible by default even when only heat<->cool
    is configured by the user."""
    groups = parse_groups(_MINIMAL)
    pairs = groups[0].incompatible_mode_pairs
    assert frozenset({HVACMode.HEAT, HVACMode.COOL}) in pairs
    assert frozenset({HVACMode.HEAT, HVACMode("fan_only")}) in pairs


def test_default_incompatible_pairs_added_when_user_configures_none() -> None:
    """A group with no ``incompatible_mode_pairs`` still gets the defaults."""
    cfg = {
        "groups": [
            {
                "group_id": "g1",
                "name": "G1",
                "zones": [
                    {
                        "zone_id": "z1",
                        "name": "Z1",
                        "head_climate": "climate.head_z1",
                        "fusion": {
                            "strategy": "head_only",
                            "head_temp_sensor": "sensor.z1_temp",
                        },
                    }
                ],
            }
        ]
    }
    groups = parse_groups(cfg)
    pairs = groups[0].incompatible_mode_pairs
    assert frozenset({HVACMode.HEAT, HVACMode.COOL}) in pairs
    assert frozenset({HVACMode.HEAT, HVACMode("fan_only")}) in pairs


def test_default_incompatible_pairs_merged_when_user_pairs_is_empty_list() -> None:
    """The UI persists ``incompatible_mode_pairs: []`` (an explicit empty
    list, not an omission) when no extra pair is configured. The
    default-merge must still apply in that case — otherwise a UI-built
    entry could end up with NO incompatibility constraints and let
    physically-incompatible mode combinations dispatch concurrently.

    Regression guard for the user-reported case where a UI entry
    arrived with three zones and the integration appeared to permit
    HEAT + COOL + FAN_ONLY simultaneously.
    """
    raw = {
        "group_id": "g1",
        "name": "G1",
        # Exactly what the UI writes when the user doesn't add an
        # extra pair on the compatibility step:
        "incompatible_mode_pairs": [],
        "disable_default_incompatible_mode_pairs": False,
        "zones": [
            {
                "zone_id": "z1",
                "name": "Z1",
                "head_climate": "climate.head_z1",
                "fusion": {
                    "strategy": "head_only",
                    "head_temp_sensor": "sensor.z1_temp",
                },
            }
        ],
    }
    group = parse_group_dict(raw)
    pairs = group.incompatible_mode_pairs
    assert frozenset({HVACMode.HEAT, HVACMode.COOL}) in pairs
    assert frozenset({HVACMode.HEAT, HVACMode("fan_only")}) in pairs


def test_ui_shape_three_zones_arbitrate_to_compatible_subset() -> None:
    """End-to-end regression: feed the UI's persisted dict shape (with
    three zones and no user-configured incompatible pairs) through
    ``parse_group_dict`` and run arbitration. Assert the resulting
    dispatched mode set is *globally compatible* — i.e. no
    HEAT+COOL or HEAT+FAN_ONLY combination is ever live at once.

    This is the test that would have caught a "default-merge silently
    skipped" bug in the UI persistence path before it manifested as
    multiple heads stuck in incompatible modes in the field.
    """
    from custom_components.multisplit_zone_controller.arbitration import (
        arbitrate,
    )
    from custom_components.multisplit_zone_controller.models import (
        EffectiveReadings,
        ResolvedIntent,
        SourceQuality,
    )

    def _zone_dict(zid: str, head: str) -> dict:
        return {
            "zone_id": zid,
            "name": zid,
            "head_climate": head,
            "fusion": {
                "strategy": "head_only",
                "head_temp_sensor": f"sensor.{zid}_temp",
            },
        }

    raw = {
        "group_id": "unit_1",
        "name": "Outdoor Unit 1",
        "incompatible_mode_pairs": [],
        "disable_default_incompatible_mode_pairs": False,
        "zones": [
            _zone_dict("living", "climate.head_living"),
            _zone_dict("bedroom", "climate.head_bedroom"),
            _zone_dict("office", "climate.head_office"),
        ],
    }
    group = parse_group_dict(raw)

    intents = {
        "living": ResolvedIntent(
            zone_id="living",
            hvac_mode=HVACMode.HEAT,
            target_temperature=22.0,
            safety_override=False,
        ),
        "bedroom": ResolvedIntent(
            zone_id="bedroom",
            hvac_mode=HVACMode.COOL,
            target_temperature=22.0,
            safety_override=False,
        ),
        "office": ResolvedIntent(
            zone_id="office",
            hvac_mode=HVACMode("fan_only"),
            target_temperature=22.0,
            safety_override=False,
        ),
    }
    eff = {
        zid: EffectiveReadings(
            temperature=20.0,
            humidity=None,
            temperature_source="head",
            humidity_source="none",
            quality=SourceQuality.OK,
        )
        for zid in intents
    }
    decision = arbitrate(group, intents, eff)

    live_modes = {
        d.dispatched_mode
        for d in decision.zones.values()
        if d.dispatched_mode is not HVACMode.OFF
    }
    fan_only = HVACMode("fan_only")
    assert not {HVACMode.HEAT, HVACMode.COOL}.issubset(live_modes)
    assert not {HVACMode.HEAT, fan_only}.issubset(live_modes)


def test_disable_default_incompatible_pairs_opt_out() -> None:
    """``disable_default_incompatible_mode_pairs: true`` strips the defaults."""
    cfg = {
        "groups": [
            {
                "group_id": "g1",
                "name": "G1",
                "disable_default_incompatible_mode_pairs": True,
                "incompatible_mode_pairs": [["heat", "cool"]],
                "zones": [
                    {
                        "zone_id": "z1",
                        "name": "Z1",
                        "head_climate": "climate.head_z1",
                        "fusion": {
                            "strategy": "head_only",
                            "head_temp_sensor": "sensor.z1_temp",
                        },
                    }
                ],
            }
        ]
    }
    groups = parse_groups(cfg)
    pairs = groups[0].incompatible_mode_pairs
    # Only the user's explicitly-listed pair remains; defaults are gone.
    assert pairs == frozenset({frozenset({HVACMode.HEAT, HVACMode.COOL})})
    assert frozenset({HVACMode.HEAT, HVACMode("fan_only")}) not in pairs


def test_user_configured_pairs_extend_defaults() -> None:
    """User-configured pairs are added on top of the defaults (not replaced)."""
    cfg = {
        "groups": [
            {
                "group_id": "g1",
                "name": "G1",
                "incompatible_mode_pairs": [["heat", "dry"]],
                "zones": [
                    {
                        "zone_id": "z1",
                        "name": "Z1",
                        "head_climate": "climate.head_z1",
                        "fusion": {
                            "strategy": "head_only",
                            "head_temp_sensor": "sensor.z1_temp",
                        },
                    }
                ],
            }
        ]
    }
    groups = parse_groups(cfg)
    pairs = groups[0].incompatible_mode_pairs
    assert frozenset({HVACMode.HEAT, HVACMode("dry")}) in pairs
    assert frozenset({HVACMode.HEAT, HVACMode.COOL}) in pairs
    assert frozenset({HVACMode.HEAT, HVACMode("fan_only")}) in pairs


def test_always_assert_head_state_defaults_false() -> None:
    """Default is the quiet path (de-dup) so heads don't beep on every tick."""
    groups = parse_groups(_MINIMAL)
    for z in groups[0].zones:
        assert z.always_assert_head_state is False


def test_always_assert_head_state_can_be_enabled() -> None:
    """Opt-in re-issue every tick when configured."""
    cfg = {
        "groups": [
            {
                "group_id": "g1",
                "name": "G1",
                "incompatible_mode_pairs": [["heat", "cool"]],
                "zones": [
                    {
                        "zone_id": "z1",
                        "name": "Z1",
                        "head_climate": "climate.head_z1",
                        "always_assert_head_state": True,
                        "fusion": {
                            "strategy": "head_only",
                            "head_temp_sensor": "sensor.z1_temp",
                        },
                    }
                ],
            }
        ]
    }
    groups = parse_groups(cfg)
    assert groups[0].zones[0].always_assert_head_state is True


def test_display_thermostat_defaults_parse() -> None:
    cfg = {
        "groups": [
            {
                "group_id": "g1",
                "name": "G1",
                "zones": [
                    {
                        "zone_id": "z1",
                        "name": "Z1",
                        "head_climate": "climate.head_z1",
                        "display_thermostats": [
                            {"entity_id": "climate.honeywell_t6_z1"}
                        ],
                    }
                ],
            }
        ]
    }
    groups = parse_groups(cfg)
    display = groups[0].zones[0].display_thermostats[0]
    assert display.entity_id == "climate.honeywell_t6_z1"
    assert display.sync_setpoint is True
    assert display.sync_mode is True
    assert display.sync_fan_mode is True
    assert display.always_assert is False
    assert display.contribute_temperature is True
    assert display.contribute_humidity is True
    assert display.temperature_weight == 0.3
    assert display.humidity_weight == 0.3
    assert display.auto_fan_mode == "Auto low"
    assert display.fan_only_fan_mode == "Low"
    assert display.circulate_fan_mode == "Circulation"


def test_display_thermostat_options_parse() -> None:
    cfg = {
        "groups": [
            {
                "group_id": "g1",
                "name": "G1",
                "zones": [
                    {
                        "zone_id": "z1",
                        "name": "Z1",
                        "head_climate": "climate.head_z1",
                        "display_thermostats": [
                            {
                                "entity_id": "climate.display",
                                "sync_setpoint": False,
                                "sync_mode": False,
                                "sync_fan_mode": False,
                                "always_assert": True,
                                "contribute_temperature": False,
                                "contribute_humidity": False,
                                "temperature_weight": 0.5,
                                "humidity_weight": 0.4,
                                "auto_fan_mode": "auto",
                                "fan_only_fan_mode": "on",
                                "circulate_fan_mode": "circulate",
                            }
                        ],
                    }
                ],
            }
        ]
    }
    display = parse_groups(cfg)[0].zones[0].display_thermostats[0]
    assert display.sync_setpoint is False
    assert display.sync_mode is False
    assert display.sync_fan_mode is False
    assert display.always_assert is True
    assert display.contribute_temperature is False
    assert display.contribute_humidity is False
    assert display.temperature_weight == 0.5
    assert display.humidity_weight == 0.4
    assert display.auto_fan_mode == "auto"
    assert display.fan_only_fan_mode == "on"
    assert display.circulate_fan_mode == "circulate"


def test_sensor_full_dict_form_preserves_weight_and_offset() -> None:
    cfg = {
        "groups": [
            {
                "group_id": "g1",
                "name": "G1",
                "zones": [
                    {
                        "zone_id": "z1",
                        "name": "Z1",
                        "head_climate": "climate.head",
                        "fusion": {
                            "external_temp_sensors": [
                                {
                                    "entity_id": "sensor.a",
                                    "weight": 2.0,
                                    "calibration_offset": -0.5,
                                }
                            ]
                        },
                    }
                ],
            }
        ]
    }
    groups = parse_groups(cfg)
    sensor = groups[0].zones[0].fusion.external_temp_sensors[0]
    assert sensor.weight == 2.0
    assert sensor.calibration_offset == -0.5


def test_unsupported_strategy_rejected() -> None:
    cfg = {
        "groups": [
            {
                "group_id": "g1",
                "name": "G1",
                "zones": [
                    {
                        "zone_id": "z1",
                        "name": "Z1",
                        "head_climate": "climate.head",
                        "fusion": {"strategy": "psychrometric_pmv"},
                    }
                ],
            }
        ]
    }
    with pytest.raises(vol.Invalid):
        parse_groups(cfg)


def test_occupancy_and_setback_block_parsed() -> None:
    cfg = {
        "groups": [
            {
                "group_id": "g1",
                "name": "G1",
                "zones": [
                    {
                        "zone_id": "z1",
                        "name": "Z1",
                        "head_climate": "climate.head",
                        "occupancy": {
                            "sources": [
                                {
                                    "entity_id": "binary_sensor.motion",
                                    "kind": "presence",
                                    "weight": 2.0,
                                },
                                {
                                    "entity_id": "calendar.work",
                                    "kind": "calendar",
                                },
                            ],
                            "linger_minutes": 20,
                            "confirmed_threshold": 0.65,
                            "expected_threshold": 0.35,
                        },
                        "setback": {
                            "offset_heat": 4.0,
                            "offset_cool": 2.5,
                            "unoccupied_comfort_weight": 0.1,
                        },
                    }
                ],
            }
        ]
    }
    groups = parse_groups(cfg)
    zone = groups[0].zones[0]
    assert len(zone.occupancy.sources) == 2
    assert zone.occupancy.sources[0].kind is OccupancySignalKind.PRESENCE
    assert zone.occupancy.sources[0].weight == 2.0
    assert zone.occupancy.linger_minutes == 20.0
    assert zone.occupancy.confirmed_threshold == 0.65
    assert zone.setback.setback_offset_heat == 4.0
    assert zone.setback.setback_offset_cool == 2.5
    assert zone.setback.unoccupied_comfort_weight == 0.1


def test_invalid_occupancy_kind_rejected() -> None:
    cfg = {
        "groups": [
            {
                "group_id": "g1",
                "name": "G1",
                "zones": [
                    {
                        "zone_id": "z1",
                        "name": "Z1",
                        "head_climate": "climate.head",
                        "occupancy": {
                            "sources": [
                                {"entity_id": "x", "kind": "telepathy"}
                            ]
                        },
                    }
                ],
            }
        ]
    }
    with pytest.raises(vol.Invalid):
        parse_groups(cfg)


def test_weighted_blend_now_supported_with_head_weight() -> None:
    cfg = {
        "groups": [
            {
                "group_id": "g1",
                "name": "G1",
                "zones": [
                    {
                        "zone_id": "z1",
                        "name": "Z1",
                        "head_climate": "climate.head",
                        "fusion": {
                            "strategy": "weighted_blend",
                            "head_weight": 2.5,
                            "stale_after_seconds": 600,
                        },
                    }
                ],
            }
        ]
    }
    groups = parse_groups(cfg)
    fusion = groups[0].zones[0].fusion
    assert fusion.head_weight == 2.5
    assert fusion.stale_after_seconds == 600.0


def test_duplicate_group_ids_rejected() -> None:
    cfg = {
        "groups": [
            {
                "group_id": "dup",
                "name": "A",
                "zones": [
                    {"zone_id": "z1", "name": "Z1", "head_climate": "climate.h1"}
                ],
            },
            {
                "group_id": "dup",
                "name": "B",
                "zones": [
                    {"zone_id": "z2", "name": "Z2", "head_climate": "climate.h2"}
                ],
            },
        ]
    }
    with pytest.raises(vol.Invalid):
        parse_groups(cfg)


def test_duplicate_zone_ids_in_group_rejected() -> None:
    cfg = {
        "groups": [
            {
                "group_id": "g1",
                "name": "G1",
                "zones": [
                    {"zone_id": "same", "name": "A", "head_climate": "climate.a"},
                    {"zone_id": "same", "name": "B", "head_climate": "climate.b"},
                ],
            }
        ]
    }
    with pytest.raises(vol.Invalid):
        parse_groups(cfg)


def test_mode_pair_must_have_two_distinct_modes() -> None:
    cfg = {
        "groups": [
            {
                "group_id": "g1",
                "name": "G1",
                "incompatible_mode_pairs": [["heat", "heat"]],
                "zones": [
                    {"zone_id": "z1", "name": "Z1", "head_climate": "climate.x"}
                ],
            }
        ]
    }
    with pytest.raises(vol.Invalid):
        parse_groups(cfg)


# ---------------------------------------------------------------------------
# parse_group_dict / entry_to_group_config (config-flow path)
# ---------------------------------------------------------------------------

_MINIMAL_GROUP_DICT = {
    "group_id": "ui_group",
    "name": "UI Group",
    "zones": [
        {
            "zone_id": "den",
            "name": "Den",
            "head_climate": "climate.den_head",
            "fusion": {
                "strategy": "head_only",
                "head_temp_sensor": "sensor.den_head_temp",
            },
        }
    ],
}


def test_parse_group_dict_returns_a_single_groupconfig() -> None:
    """The config-flow path bypasses the wrapping ``groups:`` list."""
    group = parse_group_dict(_MINIMAL_GROUP_DICT)
    assert group.group_id == "ui_group"
    assert group.name == "UI Group"
    assert len(group.zones) == 1
    assert group.zones[0].zone_id == "den"
    # Default-incompatible pairs are merged in just like for the YAML
    # path \u2014 this is the whole point of routing both sources
    # through the same parser.
    assert frozenset({HVACMode.HEAT, HVACMode.COOL}) in group.incompatible_mode_pairs


def test_parse_group_dict_validates_like_yaml() -> None:
    """Invalid input is rejected by the same voluptuous machinery."""
    bad = {
        "group_id": "g",
        "name": "G",
        "incompatible_mode_pairs": [["heat", "heat"]],
        "zones": [
            {"zone_id": "z", "name": "Z", "head_climate": "climate.z"}
        ],
    }
    with pytest.raises(vol.Invalid):
        parse_group_dict(bad)


class _FakeEntry:
    """Stand-in for ``ConfigEntry`` for unit tests (no HA dependency)."""

    def __init__(
        self,
        data: dict[str, object],
        options: dict[str, object] | None = None,
    ) -> None:
        self.data = data
        self.options = options or {}


def test_entry_to_group_config_uses_data_only_when_no_options() -> None:
    entry = _FakeEntry(data=_MINIMAL_GROUP_DICT)
    group = entry_to_group_config(entry)
    assert group.group_id == "ui_group"
    assert group.zones[0].zone_id == "den"


def test_entry_options_override_entry_data() -> None:
    """Options-flow values take precedence over initial-setup values."""
    base = dict(_MINIMAL_GROUP_DICT)
    options = {"name": "Renamed via options"}
    entry = _FakeEntry(data=base, options=options)
    group = entry_to_group_config(entry)
    assert group.name == "Renamed via options"
    # Untouched fields still come from data.
    assert group.group_id == "ui_group"


def test_entry_options_can_replace_entire_zones_list() -> None:
    """Options-flow may submit a fully restructured zones list."""
    base = dict(_MINIMAL_GROUP_DICT)
    options = {
        "zones": [
            {
                "zone_id": "kitchen",
                "name": "Kitchen",
                "head_climate": "climate.kitchen_head",
                "fusion": {
                    "strategy": "head_only",
                    "head_temp_sensor": "sensor.kitchen_head_temp",
                },
            }
        ]
    }
    entry = _FakeEntry(data=base, options=options)
    group = entry_to_group_config(entry)
    assert [z.zone_id for z in group.zones] == ["kitchen"]
