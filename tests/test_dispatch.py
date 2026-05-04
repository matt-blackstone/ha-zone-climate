"""Unit tests for the head dispatcher's resilience and capability checks.

These tests build a minimal duck-typed ``HomeAssistant`` substitute
that exposes just the bits the dispatcher touches:

* ``hass.services.async_call(domain, service, payload, blocking)``
  — async, returns nothing on success, may raise.
* ``hass.states.get(entity_id)`` — returns ``None`` or an object with
  ``attributes`` (dict).

A real HA install is *not* required (and not present in the dev
venv); ``tests/conftest.py`` registers minimal ``homeassistant.const``
and ``homeassistant.core`` stubs at session start.
"""

from __future__ import annotations

from typing import Any

import pytest

from custom_components.multisplit_zone_controller.dispatch import (
    HeadDispatcher,
)
from custom_components.multisplit_zone_controller.models import (
    EffectiveReadings,
    FusionConfig,
    GroupDecision,
    HVACMode,
    SourceQuality,
    ZoneConfig,
    ZoneDecision,
)


# ---------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------


class _FakeState:
    def __init__(self, attributes: dict[str, Any]) -> None:
        self.attributes = attributes


class _FakeStates:
    def __init__(self) -> None:
        self._by_entity: dict[str, _FakeState] = {}

    def set(self, entity_id: str, **attributes: Any) -> None:
        self._by_entity[entity_id] = _FakeState(attributes)

    def get(self, entity_id: str) -> _FakeState | None:
        return self._by_entity.get(entity_id)


class _FakeServices:
    """Records every service call and lets tests script per-call failures.

    ``raise_on(domain, service, entity_id, exc)`` queues an exception
    to be raised the *next* time that exact call is made (then it's
    consumed). Subsequent identical calls succeed unless re-armed.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self._failures: dict[tuple[str, str, str], list[Exception]] = {}

    def raise_on(
        self,
        domain: str,
        service: str,
        entity_id: str,
        exc: Exception,
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
        entity_id = payload.get("entity_id", "")
        queued = self._failures.get((domain, service, entity_id))
        if queued:
            raise queued.pop(0)


class _FakeHass:
    def __init__(self) -> None:
        self.services = _FakeServices()
        self.states = _FakeStates()
        # Required by units.hass_temperature_unit (read off
        # hass.config.units.temperature_unit). Pin Celsius for these
        # tests so target conversion is identity.
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


# ---------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------


def _zone(
    zid: str,
    head: str,
    *,
    always_assert: bool = False,
) -> ZoneConfig:
    return ZoneConfig(
        zone_id=zid,
        name=zid.upper(),
        head_climate_entity=head,
        fusion=FusionConfig(),
        always_assert_head_state=always_assert,
    )


def _eff() -> EffectiveReadings:
    return EffectiveReadings(
        temperature=20.0,
        humidity=None,
        temperature_source="head",
        humidity_source="none",
        quality=SourceQuality.OK,
    )


def _zd(
    zid: str,
    mode: HVACMode,
    target: float | None = 21.0,
) -> ZoneDecision:
    return ZoneDecision(
        zone_id=zid,
        dispatched_mode=mode,
        dispatched_target=target if mode is not HVACMode.OFF else None,
        blocked=False,
        block_reason=None,
        safety_override=False,
        priority_score=0.0,
        effective=_eff(),
    )


def _decision(*zds: ZoneDecision) -> GroupDecision:
    return GroupDecision(group_id="g", zones={z.zone_id: z for z in zds})


# ---------------------------------------------------------------------
# Resilience
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_one_zone_failure_does_not_abort_remaining_zones() -> None:
    """Reproduces the user-reported regression: zone B raises, zones A
    and C must still get their dispatch.
    """
    hass = _FakeHass()
    # Heads with no advertised hvac_modes → capability check is
    # neutral; the failure has to come from the service call.
    dispatcher = HeadDispatcher(hass)

    decision = _decision(
        _zd("z_a", HVACMode.HEAT, target=22.0),
        _zd("z_b", HVACMode.COOL, target=20.0),
        _zd("z_c", HVACMode.HEAT, target=23.0),
    )
    zones = {
        "z_a": _zone("z_a", "climate.head_a"),
        "z_b": _zone("z_b", "climate.head_b"),
        "z_c": _zone("z_c", "climate.head_c"),
    }

    # Make z_b's set_hvac_mode call blow up exactly once.
    hass.services.raise_on(
        "climate",
        "set_hvac_mode",
        "climate.head_b",
        ValueError("HVAC mode cool is not valid"),
    )

    await dispatcher.apply(zones, decision)

    services_called_for = {
        (call[1], call[2].get("entity_id")) for call in hass.services.calls
    }
    # z_a was reached: hvac_mode + temperature
    assert ("set_hvac_mode", "climate.head_a") in services_called_for
    assert ("set_temperature", "climate.head_a") in services_called_for
    # z_b's set_hvac_mode was attempted (and failed)
    assert ("set_hvac_mode", "climate.head_b") in services_called_for
    # z_b's set_temperature was NOT attempted because mode-set failed
    assert ("set_temperature", "climate.head_b") not in services_called_for
    # z_c was reached *after* z_b crashed
    assert ("set_hvac_mode", "climate.head_c") in services_called_for
    assert ("set_temperature", "climate.head_c") in services_called_for


@pytest.mark.asyncio
async def test_failed_dispatch_is_retried_next_tick() -> None:
    """Until a dispatch succeeds, the integration must keep retrying
    every tick rather than caching the failed mode as the last-sent
    value (which would silently mask the failure).
    """
    hass = _FakeHass()
    dispatcher = HeadDispatcher(hass)
    zones = {"z": _zone("z", "climate.h")}
    decision = _decision(_zd("z", HVACMode.HEAT, target=22.0))

    # First two ticks fail, third succeeds.
    for _ in range(2):
        hass.services.raise_on(
            "climate",
            "set_hvac_mode",
            "climate.h",
            RuntimeError("boom"),
        )

    await dispatcher.apply(zones, decision)
    await dispatcher.apply(zones, decision)
    await dispatcher.apply(zones, decision)

    set_mode_calls = [
        c for c in hass.services.calls if c[1] == "set_hvac_mode"
    ]
    # Three attempts total — the dispatcher kept trying.
    assert len(set_mode_calls) == 3
    # Last one succeeded so set_temperature ran exactly once.
    set_temp_calls = [
        c for c in hass.services.calls if c[1] == "set_temperature"
    ]
    assert len(set_temp_calls) == 1


@pytest.mark.asyncio
async def test_repeated_failure_logs_only_once_per_mode(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Avoid log-spam: identical failures on the same (entity, mode)
    are coalesced into a single ERROR until the situation changes.
    """
    import logging

    hass = _FakeHass()
    dispatcher = HeadDispatcher(hass)
    zones = {"z": _zone("z", "climate.h")}
    decision = _decision(_zd("z", HVACMode.HEAT, target=22.0))

    for _ in range(5):
        hass.services.raise_on(
            "climate",
            "set_hvac_mode",
            "climate.h",
            RuntimeError("boom"),
        )

    with caplog.at_level(
        logging.ERROR,
        logger="custom_components.multisplit_zone_controller.dispatch",
    ):
        for _ in range(5):
            await dispatcher.apply(zones, decision)

    error_records = [
        r for r in caplog.records if "Dispatch failed" in r.getMessage()
    ]
    assert len(error_records) == 1


# ---------------------------------------------------------------------
# Capability pre-flight check
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatcher_skips_unsupported_mode_without_calling_service(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """If the head advertises a limited ``hvac_modes`` set, the
    dispatcher must NOT attempt the service call (which would raise a
    ServiceValidationError) — it should log a warning and skip.
    """
    import logging

    hass = _FakeHass()
    # Heater-only head, mirroring the user's generic_thermostat fake.
    hass.states.set("climate.heater_only", hvac_modes=["off", "heat"])
    dispatcher = HeadDispatcher(hass)
    zones = {"z": _zone("z", "climate.heater_only")}
    decision = _decision(_zd("z", HVACMode.COOL, target=21.0))

    with caplog.at_level(
        logging.WARNING,
        logger="custom_components.multisplit_zone_controller.dispatch",
    ):
        await dispatcher.apply(zones, decision)

    assert hass.services.calls == []  # Service was never invoked.
    warnings = [
        r for r in caplog.records
        if "does not include the requested mode" in r.getMessage()
    ]
    assert len(warnings) == 1


@pytest.mark.asyncio
async def test_capability_warning_only_logged_once_per_mode(
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging

    hass = _FakeHass()
    hass.states.set("climate.h", hvac_modes=["off", "heat"])
    dispatcher = HeadDispatcher(hass)
    zones = {"z": _zone("z", "climate.h")}
    decision = _decision(_zd("z", HVACMode.COOL, target=21.0))

    with caplog.at_level(
        logging.WARNING,
        logger="custom_components.multisplit_zone_controller.dispatch",
    ):
        for _ in range(5):
            await dispatcher.apply(zones, decision)

    warnings = [
        r for r in caplog.records
        if "does not include the requested mode" in r.getMessage()
    ]
    assert len(warnings) == 1


@pytest.mark.asyncio
async def test_capability_check_optimistic_when_head_state_unknown() -> None:
    """If the head isn't in the state machine yet (e.g. integration
    is still loading), the dispatcher must NOT skip the call — it
    optimistically attempts it and falls back to the per-call
    try/except. This ensures we don't silently no-op while the user
    waits for the head to register.
    """
    hass = _FakeHass()
    # Note: no states set.
    dispatcher = HeadDispatcher(hass)
    zones = {"z": _zone("z", "climate.unknown")}
    decision = _decision(_zd("z", HVACMode.COOL, target=21.0))

    await dispatcher.apply(zones, decision)
    # Both calls were attempted.
    services_called = {c[1] for c in hass.services.calls}
    assert services_called == {"set_hvac_mode", "set_temperature"}


@pytest.mark.asyncio
async def test_supported_mode_is_dispatched_normally() -> None:
    """Sanity check: when the head supports the mode, the call goes
    through and last-sent caching takes effect (no re-issue on a
    subsequent identical tick).
    """
    hass = _FakeHass()
    hass.states.set(
        "climate.h", hvac_modes=["off", "heat", "cool", "fan_only", "auto"]
    )
    dispatcher = HeadDispatcher(hass)
    zones = {"z": _zone("z", "climate.h")}
    decision = _decision(_zd("z", HVACMode.HEAT, target=22.0))

    await dispatcher.apply(zones, decision)
    first_call_count = len(hass.services.calls)
    assert first_call_count == 2  # set_hvac_mode + set_temperature

    # Same decision next tick → no re-issue (always_assert is False).
    await dispatcher.apply(zones, decision)
    assert len(hass.services.calls) == first_call_count


@pytest.mark.asyncio
async def test_always_assert_re_issues_every_tick() -> None:
    hass = _FakeHass()
    hass.states.set(
        "climate.h", hvac_modes=["off", "heat", "cool", "fan_only", "auto"]
    )
    dispatcher = HeadDispatcher(hass)
    zones = {"z": _zone("z", "climate.h", always_assert=True)}
    decision = _decision(_zd("z", HVACMode.HEAT, target=22.0))

    await dispatcher.apply(zones, decision)
    await dispatcher.apply(zones, decision)
    set_mode_calls = [c for c in hass.services.calls if c[1] == "set_hvac_mode"]
    assert len(set_mode_calls) == 2
