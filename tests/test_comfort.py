"""Tests for fan comfort credit and fan command resolution."""

from __future__ import annotations

import pytest

from custom_components.multisplit_zone_controller.comfort import (
    apparent_temp_credit,
    fan_command_for,
)
from custom_components.multisplit_zone_controller.models import (
    FanConfig,
    FanDirection,
    HVACMode,
    OccupancyState,
)


def _fan(**kwargs) -> FanConfig:
    base = dict(
        fan_entity_id="fan.living",
        enabled=True,
        max_apparent_temp_credit=2.0,
        deviation_per_step=2.0,
        max_speed_pct_occupied=100,
        max_speed_pct_unoccupied=33,
    )
    base.update(kwargs)
    return FanConfig(**base)


# --- apparent_temp_credit ----------------------------------------------------


def test_credit_zero_when_fan_disabled() -> None:
    cfg = _fan(enabled=False)
    assert apparent_temp_credit(3.0, cfg, OccupancyState.CONFIRMED, False) == 0.0


def test_credit_zero_when_no_entity() -> None:
    cfg = _fan(fan_entity_id=None)
    assert apparent_temp_credit(3.0, cfg, OccupancyState.CONFIRMED, False) == 0.0


def test_credit_zero_when_locked_out() -> None:
    cfg = _fan()
    assert apparent_temp_credit(3.0, cfg, OccupancyState.CONFIRMED, True) == 0.0


def test_credit_zero_when_unoccupied() -> None:
    cfg = _fan()
    assert apparent_temp_credit(3.0, cfg, OccupancyState.UNOCCUPIED, False) == 0.0


def test_credit_grows_with_deviation() -> None:
    cfg = _fan(max_apparent_temp_credit=2.0, deviation_per_step=2.0)
    assert apparent_temp_credit(0.0, cfg, OccupancyState.CONFIRMED, False) == 0.0
    assert apparent_temp_credit(1.0, cfg, OccupancyState.CONFIRMED, False) == pytest.approx(1.0)
    assert apparent_temp_credit(2.0, cfg, OccupancyState.CONFIRMED, False) == pytest.approx(2.0)


def test_credit_capped_at_max() -> None:
    cfg = _fan(max_apparent_temp_credit=2.0, deviation_per_step=2.0)
    assert apparent_temp_credit(10.0, cfg, OccupancyState.CONFIRMED, False) == 2.0


def test_credit_negative_deviation_uses_magnitude() -> None:
    cfg = _fan()
    assert apparent_temp_credit(-3.0, cfg, OccupancyState.CONFIRMED, False) == 2.0


def test_credit_applies_for_recent_and_expected_too() -> None:
    cfg = _fan()
    for state in (OccupancyState.RECENT, OccupancyState.EXPECTED):
        assert apparent_temp_credit(2.0, cfg, state, False) == pytest.approx(2.0)


# --- fan_command_for ---------------------------------------------------------


def test_fan_off_when_disabled_or_no_entity() -> None:
    for cfg in (_fan(enabled=False), _fan(fan_entity_id=None)):
        cmd = fan_command_for(
            HVACMode.HEAT, 3.0, cfg, OccupancyState.CONFIRMED, False
        )
        assert cmd.on is False
        assert cmd.speed_pct == 0


def test_fan_off_when_locked_out() -> None:
    cmd = fan_command_for(
        HVACMode.COOL, 5.0, _fan(), OccupancyState.CONFIRMED, True
    )
    assert cmd.on is False


def test_fan_off_when_hvac_mode_off() -> None:
    cmd = fan_command_for(
        HVACMode.OFF, 5.0, _fan(), OccupancyState.CONFIRMED, False
    )
    assert cmd.on is False


def test_fan_uses_reverse_in_heat() -> None:
    cmd = fan_command_for(
        HVACMode.HEAT, 2.0, _fan(), OccupancyState.CONFIRMED, False
    )
    assert cmd.direction is FanDirection.REVERSE


def test_fan_uses_forward_in_cool() -> None:
    cmd = fan_command_for(
        HVACMode.COOL, 2.0, _fan(), OccupancyState.CONFIRMED, False
    )
    assert cmd.direction is FanDirection.FORWARD


def test_fan_speed_scales_with_deviation_when_occupied() -> None:
    cfg = _fan(max_speed_pct_occupied=100, deviation_per_step=4.0)
    cmd = fan_command_for(HVACMode.COOL, 2.0, cfg, OccupancyState.CONFIRMED, False)
    assert cmd.speed_pct == 50  # 2/4 * 100


def test_fan_speed_capped_unoccupied() -> None:
    cfg = _fan(max_speed_pct_occupied=100, max_speed_pct_unoccupied=33, deviation_per_step=2.0)
    cmd = fan_command_for(HVACMode.HEAT, 5.0, cfg, OccupancyState.UNOCCUPIED, False)
    assert cmd.speed_pct == 33


def test_fan_full_speed_when_deviation_per_step_zero() -> None:
    cfg = _fan(deviation_per_step=0.0, max_speed_pct_occupied=100)
    cmd = fan_command_for(HVACMode.HEAT, 0.5, cfg, OccupancyState.CONFIRMED, False)
    assert cmd.speed_pct == 100
