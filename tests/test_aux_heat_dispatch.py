"""Unit tests for the auxiliary-heat dispatcher's resilience."""

from __future__ import annotations

import logging
from typing import Any

import pytest

from custom_components.multisplit_zone_controller.aux_heat_dispatch import (
    AuxHeatDispatcher,
)
from custom_components.multisplit_zone_controller.models import (
    AuxHeatConfig,
    AuxHeatDeviceType,
    AuxHeatTrigger,
    EffectiveReadings,
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
        self.config = type(
            "Cfg",
            (),
            {
                "units": type(
                    "Units",
                    (),
                    {"temperature_unit": "\u00b0C"},
                )()
            },
        )()


def _eff() -> EffectiveReadings:
    return EffectiveReadings(
        temperature=20.0,
        humidity=None,
        temperature_source="head",
        humidity_source="none",
        quality=SourceQuality.OK,
    )


def _zone(zid: str, switch_entity: str) -> ZoneConfig:
    return ZoneConfig(
        zone_id=zid,
        name=zid,
        head_climate_entity=f"climate.head_{zid}",
        fusion=FusionConfig(),
        aux_heat=AuxHeatConfig(
            enabled=True,
            device_entity_id=switch_entity,
            device_type=AuxHeatDeviceType.SWITCH,
        ),
    )


def _zd(zid: str, *, aux_on: bool) -> ZoneDecision:
    return ZoneDecision(
        zone_id=zid,
        dispatched_mode=HVACMode.HEAT,
        dispatched_target=21.0,
        blocked=False,
        block_reason=None,
        safety_override=False,
        priority_score=0.0,
        effective=_eff(),
        aux_heat_active=aux_on,
        aux_heat_trigger=(
            AuxHeatTrigger.SAFETY_FLOOR_NEAR if aux_on else AuxHeatTrigger.NONE
        ),
    )


def _decision(*zds: ZoneDecision) -> GroupDecision:
    return GroupDecision(group_id="g", zones={z.zone_id: z for z in zds})


@pytest.mark.asyncio
async def test_aux_dispatch_failure_does_not_abort_remaining_zones() -> None:
    hass = _FakeHass()
    dispatcher = AuxHeatDispatcher(hass)
    decision = _decision(
        _zd("a", aux_on=True),
        _zd("b", aux_on=True),
        _zd("c", aux_on=True),
    )
    zones = {
        "a": _zone("a", "switch.aux_a"),
        "b": _zone("b", "switch.aux_b"),
        "c": _zone("c", "switch.aux_c"),
    }
    hass.services.raise_on(
        "switch", "turn_on", "switch.aux_b", RuntimeError("nope")
    )

    await dispatcher.apply(zones, decision)

    entities_called = {c[2]["entity_id"] for c in hass.services.calls}
    assert {"switch.aux_a", "switch.aux_b", "switch.aux_c"} <= entities_called


@pytest.mark.asyncio
async def test_aux_repeated_failure_logs_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    hass = _FakeHass()
    dispatcher = AuxHeatDispatcher(hass)
    decision = _decision(_zd("z", aux_on=True))
    zones = {"z": _zone("z", "switch.aux_z")}
    for _ in range(5):
        hass.services.raise_on(
            "switch", "turn_on", "switch.aux_z", RuntimeError("boom")
        )

    with caplog.at_level(
        logging.ERROR,
        logger="custom_components.multisplit_zone_controller.aux_heat_dispatch",
    ):
        for _ in range(5):
            await dispatcher.apply(zones, decision)

    err_recs = [
        r for r in caplog.records if "Aux dispatch failed" in r.getMessage()
    ]
    assert len(err_recs) == 1
