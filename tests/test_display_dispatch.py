"""Unit tests for physical display thermostat outbound dispatch."""

from __future__ import annotations

from typing import Any

import pytest

from custom_components.multisplit_zone_controller.display_dispatch import (
    SYNC_STALE,
    SYNC_SYNCED,
    DisplayDispatcher,
)
from custom_components.multisplit_zone_controller.display_echo import DisplayEchoGuard
from custom_components.multisplit_zone_controller.models import (
    DisplayThermostatConfig,
    FusionConfig,
    HVACMode,
    ZoneConfig,
    ZoneIntent,
)


class _FakeState:
    def __init__(self, state: str = "off", attributes: dict[str, Any] | None = None):
        self.state = state
        self.attributes = attributes or {}


class _FakeStates:
    def __init__(self) -> None:
        self._by_entity: dict[str, _FakeState] = {}

    def set(self, entity_id: str, state: str = "off", **attributes: Any) -> None:
        self._by_entity[entity_id] = _FakeState(state, attributes)

    def get(self, entity_id: str) -> _FakeState | None:
        return self._by_entity.get(entity_id)


class _FakeServices:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def async_call(
        self,
        domain: str,
        service: str,
        payload: dict[str, Any],
        blocking: bool = False,
    ) -> None:
        self.calls.append((domain, service, dict(payload)))


class _FakeHass:
    def __init__(self) -> None:
        self.services = _FakeServices()
        self.states = _FakeStates()
        self.config = type(
            "Cfg",
            (),
            {"units": type("Units", (), {"temperature_unit": "°F"})()},
        )()


def _zone(display: DisplayThermostatConfig | None = None) -> ZoneConfig:
    return ZoneConfig(
        zone_id="z",
        name="Z",
        head_climate_entity="climate.head",
        fusion=FusionConfig(),
        display_thermostats=(display or DisplayThermostatConfig("climate.t6"),),
    )


def _seed_t6(hass: _FakeHass, *, modes: list[str] | None = None) -> None:
    hass.states.set(
        "climate.t6",
        "off",
        hvac_modes=modes or ["off", "heat", "cool"],
        fan_modes=["Auto low", "Low", "Circulation"],
        min_temp=32.0,
        max_temp=122.0,
    )


@pytest.mark.asyncio
async def test_heat_intent_dispatches_t6_heat_auto_low_and_setpoint() -> None:
    hass = _FakeHass()
    _seed_t6(hass)
    dispatcher = DisplayDispatcher(hass, DisplayEchoGuard())
    zone = _zone()

    await dispatcher.apply(
        {"z": zone},
        {"z": ZoneIntent("z", HVACMode.HEAT, 20.0)},
    )

    assert hass.services.calls == [
        (
            "climate",
            "set_hvac_mode",
            {"entity_id": "climate.t6", "hvac_mode": "heat"},
        ),
        (
            "climate",
            "set_fan_mode",
            {"entity_id": "climate.t6", "fan_mode": "Auto low"},
        ),
        (
            "climate",
            "set_temperature",
            {"entity_id": "climate.t6", "temperature": pytest.approx(68.0)},
        ),
    ]
    status = dispatcher.status("z", "climate.t6")
    assert status.state == SYNC_SYNCED
    assert status.sent_temperature == pytest.approx(68.0)


@pytest.mark.asyncio
async def test_fan_only_intent_dispatches_t6_off_low_without_setpoint() -> None:
    hass = _FakeHass()
    _seed_t6(hass)
    dispatcher = DisplayDispatcher(hass, DisplayEchoGuard())
    zone = _zone()

    await dispatcher.apply(
        {"z": zone},
        {"z": ZoneIntent("z", HVACMode.FAN_ONLY, 22.0)},
    )

    assert hass.services.calls == [
        (
            "climate",
            "set_hvac_mode",
            {"entity_id": "climate.t6", "hvac_mode": "off"},
        ),
        (
            "climate",
            "set_fan_mode",
            {"entity_id": "climate.t6", "fan_mode": "Low"},
        ),
    ]


@pytest.mark.asyncio
async def test_setpoint_is_clamped_to_display_range() -> None:
    hass = _FakeHass()
    _seed_t6(hass)
    dispatcher = DisplayDispatcher(hass, DisplayEchoGuard())
    zone = _zone()

    await dispatcher.apply(
        {"z": zone},
        {"z": ZoneIntent("z", HVACMode.HEAT, -10.0)},
    )

    set_temp = [c for c in hass.services.calls if c[1] == "set_temperature"][0]
    assert set_temp[2]["temperature"] == 32.0
    status = dispatcher.status("z", "climate.t6")
    assert status.clamped is True
    assert status.sent_temperature == 32.0


@pytest.mark.asyncio
async def test_unsupported_display_mode_reports_stale() -> None:
    hass = _FakeHass()
    _seed_t6(hass, modes=["off", "heat", "cool"])
    dispatcher = DisplayDispatcher(hass, DisplayEchoGuard())
    zone = _zone()

    await dispatcher.apply(
        {"z": zone},
        {"z": ZoneIntent("z", HVACMode.AUTO, 22.0)},
    )

    set_mode_calls = [c for c in hass.services.calls if c[1] == "set_hvac_mode"]
    assert set_mode_calls == []
    status = dispatcher.status("z", "climate.t6")
    assert status.state == SYNC_STALE
    assert "unsupported hvac_mode auto" in (status.last_error or "")
