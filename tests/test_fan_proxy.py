"""Unit tests for the fan dispatcher's resilience.

Same approach as ``tests/test_dispatch.py``: a duck-typed ``hass``
fake captures all service calls and lets the test arm per-call
exceptions to verify error handling.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from custom_components.multisplit_zone_controller.fan_proxy import (
    FanDispatcher,
)
from custom_components.multisplit_zone_controller.models import (
    EffectiveReadings,
    FanCommand,
    FanConfig,
    FanDirection,
    FusionConfig,
    GroupDecision,
    HVACMode,
    SourceQuality,
    ZoneConfig,
    ZoneDecision,
)


class _FakeServices:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self._failures: dict[tuple[str, str, str], list[Exception]] = {}

    def raise_on(
        self, domain: str, service: str, entity_id: str, exc: Exception
    ) -> None:
        self._failures.setdefault(
            (domain, service, entity_id), []
        ).append(exc)

    async def async_call(
        self,
        domain: str,
        service: str,
        payload: dict[str, Any],
        blocking: bool = False,
    ) -> None:
        self.calls.append((domain, service, dict(payload)))
        queued = self._failures.get(
            (domain, service, payload.get("entity_id", ""))
        )
        if queued:
            raise queued.pop(0)


class _FakeHass:
    def __init__(self) -> None:
        self.services = _FakeServices()


def _eff() -> EffectiveReadings:
    return EffectiveReadings(
        temperature=20.0,
        humidity=None,
        temperature_source="head",
        humidity_source="none",
        quality=SourceQuality.OK,
    )


def _zone(zid: str, fan_entity: str) -> ZoneConfig:
    return ZoneConfig(
        zone_id=zid,
        name=zid,
        head_climate_entity=f"climate.head_{zid}",
        fusion=FusionConfig(),
        fan=FanConfig(enabled=True, fan_entity_id=fan_entity),
    )


def _zd(zid: str, *, fan: FanCommand | None) -> ZoneDecision:
    return ZoneDecision(
        zone_id=zid,
        dispatched_mode=HVACMode.HEAT,
        dispatched_target=21.0,
        blocked=False,
        block_reason=None,
        safety_override=False,
        priority_score=0.0,
        effective=_eff(),
        fan_command=fan,
    )


def _decision(*zds: ZoneDecision) -> GroupDecision:
    return GroupDecision(group_id="g", zones={z.zone_id: z for z in zds})


@pytest.mark.asyncio
async def test_fan_dispatch_failure_does_not_abort_remaining_zones() -> None:
    hass = _FakeHass()
    dispatcher = FanDispatcher(hass)
    cmd = FanCommand(on=True, speed_pct=66, direction=FanDirection.FORWARD)
    decision = _decision(
        _zd("a", fan=cmd),
        _zd("b", fan=cmd),
        _zd("c", fan=cmd),
    )
    zones = {
        "a": _zone("a", "fan.a"),
        "b": _zone("b", "fan.b"),
        "c": _zone("c", "fan.c"),
    }
    hass.services.raise_on(
        "fan", "set_percentage", "fan.b", RuntimeError("nope")
    )

    await dispatcher.apply(zones, decision)

    entities_set = {
        c[2]["entity_id"]
        for c in hass.services.calls
        if c[1] == "set_percentage"
    }
    assert entities_set == {"fan.a", "fan.b", "fan.c"}
    # All three zones got their direction call too — fan.b's direction
    # call still ran even though set_percentage failed (independent
    # safe_call). The important thing is that the loop reached fan.c.
    direction_entities = {
        c[2]["entity_id"]
        for c in hass.services.calls
        if c[1] == "set_direction"
    }
    assert "fan.c" in direction_entities


@pytest.mark.asyncio
async def test_fan_repeated_failure_logs_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    hass = _FakeHass()
    dispatcher = FanDispatcher(hass)
    cmd = FanCommand(on=True, speed_pct=50, direction=FanDirection.FORWARD)
    decision = _decision(_zd("z", fan=cmd))
    zones = {"z": _zone("z", "fan.z")}
    for _ in range(4):
        hass.services.raise_on(
            "fan", "set_percentage", "fan.z", RuntimeError("boom")
        )

    with caplog.at_level(
        logging.ERROR,
        logger="custom_components.multisplit_zone_controller.fan_proxy",
    ):
        for _ in range(4):
            await dispatcher.apply(zones, decision)

    err_recs = [
        r for r in caplog.records if "Fan dispatch failed" in r.getMessage()
    ]
    assert len(err_recs) == 1
