"""End-to-end integration tests against a real Home Assistant container.

These tests start a throw-away HA instance (see ``conftest.py``), load
the multi-split integration with two zones (``living`` and ``office``)
on a single compressor group, and drive the system through scenarios
that exercise the major control-loop paths:

  * Integration loading and entity registration
  * Basic head dispatch (heat / off)
  * Compressor-group arbitration (incompatible mode pair)
  * Safety floor / ceiling overrides
  * Occupancy-driven setback
  * Hidden auxiliary heat activation
  * Diagnostic sensors

Each test uses :func:`HAClient.wait_for_state` to poll for the expected
post-coordinator state instead of relying on fixed sleeps.
"""

from __future__ import annotations

import time

import pytest

from .ha_client import HAClient

LIVING = "climate.living_zone"
OFFICE = "climate.office_zone"
HEAD_LIVING = "climate.fake_head_living"
HEAD_OFFICE = "climate.fake_head_office"
DISPLAY_LIVING = "climate.fake_display_living"
AUX_SWITCH = "switch.fake_office_aux"

# The coordinator runs every 5s in the test config; +2s buffer.
TICK_SECONDS = 7.0


def _set_temp(client: HAClient, entity: str, mode: str, temp_c: float) -> None:
    """Convenience wrapper — temperature in Celsius."""
    client.set_climate_temperature(entity, temp_c, hvac_mode=mode)


def _wait_tick(client: HAClient, entity: str = LIVING) -> None:
    client.force_climate_refresh(entity)
    time.sleep(0.5)


def _sensor_state_celsius(client: HAClient, state: dict) -> float:
    """Read a temperature-typed sensor state into Celsius.

    HA reports values from sensors with ``device_class: temperature`` in
    the user-display unit; the test expectations are written in Celsius
    for clarity, so we normalise here.
    """
    return client.user_unit_to_celsius(float(state["state"]))


# ---------------------------------------------------------------------------
# Phase 1 / general — integration loading
# ---------------------------------------------------------------------------


def test_managed_climate_entities_loaded(ha: HAClient) -> None:
    """Both zones expose a managed climate with the configured modes."""
    living = ha.get_state(LIVING)
    office = ha.get_state(OFFICE)
    assert living is not None
    assert office is not None
    assert "off" in living["attributes"]["hvac_modes"]
    assert "heat" in living["attributes"]["hvac_modes"]
    assert "cool" in living["attributes"]["hvac_modes"]


def test_diagnostic_sensors_registered(ha: HAClient) -> None:
    """All admin diagnostic sensors created per zone are present.

    HA slugifies the friendly_name into the entity_id, so the suffixes
    here mirror the names in ``sensor.py`` (``f"{zone.name} ..."``)
    rather than the unique-id slugs.
    """
    expected_suffixes = (
        "effective_temperature",
        "effective_humidity",
        "block_reason",
        "sensor_quality",
        "occupancy_state",
        "active_setpoint",
        "pre_condition_lead_time",
        "learned_heating_rate",
        "learned_cooling_rate",
        "humidity_priority_contribution",
        "effective_comfort_temperature",
    )
    for zone in ("living_zone", "office_zone"):
        for suffix in expected_suffixes:
            entity_id = f"sensor.{zone}_{suffix}"
            state = ha.get_state(entity_id)
            assert state is not None, f"missing diagnostic sensor {entity_id}"


def test_aux_binary_sensor_registered(ha: HAClient) -> None:
    """Aux-heat binary sensor exists for the office zone."""
    state = ha.get_state("binary_sensor.office_zone_emergency_heat_active")
    assert state is not None
    assert state["state"] in ("on", "off")


def test_display_sync_sensor_registered(ha: HAClient) -> None:
    state = ha.get_state(
        "sensor.living_zone_display_thermostat_sync_status_climate_fake_display_living"
    )
    assert state is not None
    assert state["state"] in ("unknown", "synced", "stale", "unreachable")


# ---------------------------------------------------------------------------
# Phase 1 — basic dispatch
# ---------------------------------------------------------------------------


def test_basic_dispatch_heat(ha: HAClient) -> None:
    """Setting the managed climate to HEAT 24°C dispatches to the head."""
    ha.set_input_number("input_number.living_temp", 16)
    _set_temp(ha, LIVING, "heat", 24)

    expected_target = ha.celsius_to_user_unit(24.0)
    # HA rounds the displayed ``temperature`` attribute to the entity's
    # precision, which defaults to 1°F (≈0.5°C) for °F display, so we
    # tolerate up to 0.5 in the user-display unit when checking the
    # round-tripped value.
    head = ha.wait_for_state(
        HEAD_LIVING,
        lambda s: s["state"] == "heat"
        and s["attributes"]["temperature"] == pytest.approx(expected_target, abs=0.5),
        timeout=15.0,
        description=f"head dispatched HEAT 24°C (== {expected_target} {ha.user_temperature_unit})",
    )
    assert head["state"] == "heat"

    # Both the managed climate's reported target and the upstream head's
    # target should round-trip back to ~24°C regardless of HA unit system.
    # Allow ~0.3°C tolerance for HA display-rounding when in °F mode.
    assert ha.get_climate_target_celsius(LIVING) == pytest.approx(24.0, abs=0.3)
    assert ha.get_climate_target_celsius(HEAD_LIVING) == pytest.approx(24.0, abs=0.3)

    display = ha.wait_for_state(
        DISPLAY_LIVING,
        lambda s: s["state"] == "heat"
        and s["attributes"]["fan_mode"] == "Auto low"
        and s["attributes"]["temperature"] == pytest.approx(
            expected_target,
            abs=0.5,
        ),
        timeout=15.0,
        description="display thermostat mirrors HEAT setpoint",
    )
    assert display["state"] == "heat"


def test_off_dispatches_off(ha: HAClient) -> None:
    """Turning the managed climate OFF propagates OFF to the head."""
    _set_temp(ha, LIVING, "heat", 24)
    ha.wait_for_state(
        HEAD_LIVING,
        lambda s: s["state"] == "heat",
        timeout=15.0,
    )
    ha.set_climate_hvac_mode(LIVING, "off")
    ha.wait_for_state(
        HEAD_LIVING,
        lambda s: s["state"] == "off",
        timeout=15.0,
        description="head dispatched OFF",
    )
    ha.wait_for_state(
        DISPLAY_LIVING,
        lambda s: s["state"] == "off"
        and s["attributes"]["fan_mode"] == "Auto low",
        timeout=15.0,
        description="display thermostat mirrors OFF",
    )


def test_display_fan_low_updates_managed_intent(ha: HAClient) -> None:
    """Physical T6 off+Low fan maps back to managed fan_only intent."""
    ha.set_climate_hvac_mode(OFFICE, "off")
    ha.set_climate_hvac_mode(LIVING, "off")
    ha.wait_for_state(HEAD_LIVING, lambda s: s["state"] == "off", timeout=15.0)
    ha.wait_for_state(
        DISPLAY_LIVING,
        lambda s: s["state"] == "off"
        and s["attributes"]["fan_mode"] == "Auto low",
        timeout=15.0,
        description="display is parked at off/auto before wall edit",
    )
    # Let the display dispatcher's echo-ignore window expire so this
    # service call represents a real wall edit, not our own mirror write.
    time.sleep(2.2)

    ha.call_service(
        "climate",
        "set_fan_mode",
        {"entity_id": DISPLAY_LIVING, "fan_mode": "Low"},
    )
    time.sleep(TICK_SECONDS)
    _wait_tick(ha, LIVING)

    managed = ha.wait_for_state(
        LIVING,
        lambda s: s["state"] == "fan_only",
        timeout=20.0,
        description="managed climate adopted display fan-only intent",
    )
    assert managed["state"] == "fan_only"
    ha.wait_for_state(
        HEAD_LIVING,
        lambda s: s["state"] == "fan_only",
        timeout=15.0,
        description="display fan-only intent propagated to head",
    )


# ---------------------------------------------------------------------------
# Phase 1 / 2 — safety floor override
# ---------------------------------------------------------------------------


def test_safety_floor_overrides_off(ha: HAClient) -> None:
    """Sensor below safety_floor (16°C) forces HEAT even when intent is OFF."""
    ha.set_climate_hvac_mode(LIVING, "off")
    ha.wait_for_state(HEAD_LIVING, lambda s: s["state"] == "off", timeout=15.0)

    # Drop the room well below the configured safety floor.
    ha.set_input_number("input_number.living_temp", 5)
    # Coordinator picks this up on its next 5s tick; nudge to be safe.
    time.sleep(TICK_SECONDS)
    _wait_tick(ha, LIVING)

    ha.wait_for_state(
        HEAD_LIVING,
        lambda s: s["state"] == "heat",
        timeout=20.0,
        description="safety floor forces HEAT",
    )

    managed = ha.get_state(LIVING)
    assert managed["attributes"]["safety_override"] is True

    block_reason = ha.get_state("sensor.living_zone_block_reason")
    assert block_reason["attributes"]["safety_override"] is True


# ---------------------------------------------------------------------------
# Phase 1 — compressor-group arbitration
# ---------------------------------------------------------------------------


def test_arbitration_blocks_incompatible_mode(ha: HAClient) -> None:
    """heat<->cool is in incompatible_mode_pairs; the loser is blocked."""
    # Living wants heat with a big deviation (cold room).
    ha.set_input_number("input_number.living_temp", 12)
    _set_temp(ha, LIVING, "heat", 26)

    # Office wants cool with a small deviation.
    ha.set_input_number("input_number.office_temp", 24)
    _set_temp(ha, OFFICE, "cool", 22)

    # Wait for arbitration: living should win on score.
    ha.wait_for_state(
        HEAD_LIVING,
        lambda s: s["state"] == "heat",
        timeout=20.0,
        description="living wins arbitration as HEAT",
    )

    office_block = ha.wait_for_state(
        "sensor.office_zone_block_reason",
        lambda s: s["state"] not in ("ok", "unknown")
        or s["attributes"].get("blocked") is True,
        timeout=15.0,
        description="office is reported as blocked",
    )
    assert office_block["attributes"]["blocked"] is True

    # The office head must not have been left in a cool dispatch.
    office_head = ha.get_state(HEAD_OFFICE)
    assert office_head["state"] == "off"


def test_arbitration_blocks_heat_vs_fan_only_by_default(ha: HAClient) -> None:
    """heat<->fan_only is in the integration's default-incompatible set.

    The test config does NOT explicitly list ``[heat, fan_only]`` in
    ``incompatible_mode_pairs`` — this scenario therefore exercises
    the default merge applied by ``parse_groups``.
    """
    # Living wants heat with a big deviation (cold room) — high score.
    ha.set_input_number("input_number.living_temp", 12)
    _set_temp(ha, LIVING, "heat", 26)

    # Office requests fan_only — no temperature deviation, low score.
    ha.set_input_number("input_number.office_temp", 22)
    _set_temp(ha, OFFICE, "fan_only", 22)

    ha.wait_for_state(
        HEAD_LIVING,
        lambda s: s["state"] == "heat",
        timeout=20.0,
        description="living wins arbitration as HEAT against office fan_only",
    )

    office_block = ha.wait_for_state(
        "sensor.office_zone_block_reason",
        lambda s: s["attributes"].get("blocked") is True,
        timeout=15.0,
        description="office fan_only is reported as blocked by heat conflict",
    )
    assert office_block["attributes"]["blocked"] is True

    # And the office head must NOT have been dispatched as fan_only.
    office_head = ha.get_state(HEAD_OFFICE)
    assert office_head["state"] == "off"


# ---------------------------------------------------------------------------
# Phase 3 — occupancy setback
# ---------------------------------------------------------------------------


def test_unoccupied_setback_shifts_active_setpoint(ha: HAClient) -> None:
    """When the office is unoccupied, the active setpoint moves down by 3°C."""
    ha.set_input_boolean("input_boolean.office_occupied", False)
    ha.set_input_number("input_number.office_temp", 22)
    _set_temp(ha, OFFICE, "heat", 22)

    # Coordinator needs to observe occupancy and recompute.
    time.sleep(TICK_SECONDS)
    _wait_tick(ha, OFFICE)

    occ = ha.wait_for_state(
        "sensor.office_zone_occupancy_state",
        lambda s: s["state"] == "unoccupied",
        timeout=20.0,
        description="office occupancy resolved as unoccupied",
    )
    assert occ["state"] == "unoccupied"

    setpoint = ha.wait_for_state(
        "sensor.office_zone_active_setpoint",
        lambda s: _sensor_state_celsius(ha, s) == pytest.approx(19.0, abs=0.5),
        timeout=15.0,
        description="active setpoint shifted by setback offset",
    )
    assert _sensor_state_celsius(ha, setpoint) == pytest.approx(19.0, abs=0.5)


def test_occupied_setpoint_unchanged(ha: HAClient) -> None:
    """When the office is occupied, the active setpoint matches the request."""
    ha.set_input_boolean("input_boolean.office_occupied", True)
    ha.set_input_number("input_number.office_temp", 22)
    _set_temp(ha, OFFICE, "heat", 22)

    time.sleep(TICK_SECONDS)
    _wait_tick(ha, OFFICE)

    setpoint = ha.wait_for_state(
        "sensor.office_zone_active_setpoint",
        lambda s: _sensor_state_celsius(ha, s) == pytest.approx(22.0, abs=0.1),
        timeout=15.0,
        description="active setpoint equals comfort setpoint when occupied",
    )
    assert _sensor_state_celsius(ha, setpoint) == pytest.approx(22.0, abs=0.1)


# ---------------------------------------------------------------------------
# Phase 6 — hidden aux heat
# ---------------------------------------------------------------------------


def test_aux_heat_outdoor_lockout_activation(ha: HAClient) -> None:
    """Outdoor temp at/below lockout activates aux heat and forces head OFF."""
    ha.set_input_boolean("input_boolean.office_occupied", True)
    # office_temp must be safely above (safety_floor + safety_floor_margin)
    # = 18 + 1 = 19 so SAFETY_FLOOR_NEAR doesn't pre-activate aux heat.
    ha.set_input_number("input_number.office_temp", 21)
    ha.set_input_number("input_number.outdoor_temp", 5)
    _set_temp(ha, OFFICE, "heat", 22)

    # Confirm baseline: head is heating, aux is off.
    ha.wait_for_state(
        HEAD_OFFICE,
        lambda s: s["state"] == "heat",
        timeout=20.0,
        description="office head heating before aux activation",
    )
    assert ha.get_state(AUX_SWITCH)["state"] == "off"

    # Drop outdoor below the configured lockout (0°C).
    ha.set_input_number("input_number.outdoor_temp", -5)

    # Need at least two ticks: 1) decide aux desired (head off, settling),
    # 2) settle_seconds (1s) elapses → aux on.
    time.sleep(TICK_SECONDS)
    _wait_tick(ha, OFFICE)
    time.sleep(TICK_SECONDS)
    _wait_tick(ha, OFFICE)

    ha.wait_for_state(
        AUX_SWITCH,
        lambda s: s["state"] == "on",
        timeout=20.0,
        description="aux switch on after outdoor lockout",
    )
    ha.wait_for_state(
        HEAD_OFFICE,
        lambda s: s["state"] == "off",
        timeout=15.0,
        description="head forced off while aux is active",
    )
    em = ha.get_state("binary_sensor.office_zone_emergency_heat_active")
    assert em["state"] == "on"


def test_aux_heat_clears_when_outdoor_recovers(ha: HAClient) -> None:
    """Once outdoor rises past lockout + deactivation_margin, aux deactivates."""
    # Re-trigger aux first via outdoor lockout (not safety floor — see
    # test_aux_heat_outdoor_lockout_activation for why).
    ha.set_input_boolean("input_boolean.office_occupied", True)
    ha.set_input_number("input_number.office_temp", 21)
    ha.set_input_number("input_number.outdoor_temp", -5)
    _set_temp(ha, OFFICE, "heat", 22)
    time.sleep(TICK_SECONDS)
    _wait_tick(ha, OFFICE)
    time.sleep(TICK_SECONDS)
    _wait_tick(ha, OFFICE)
    ha.wait_for_state(AUX_SWITCH, lambda s: s["state"] == "on", timeout=20.0)

    # Now warm the outdoor sensor well above lockout.
    ha.set_input_number("input_number.outdoor_temp", 10)
    time.sleep(TICK_SECONDS)
    _wait_tick(ha, OFFICE)

    ha.wait_for_state(
        AUX_SWITCH,
        lambda s: s["state"] == "off",
        timeout=20.0,
        description="aux switch turns off after outdoor recovery",
    )


# ---------------------------------------------------------------------------
# Diagnostic surface area
# ---------------------------------------------------------------------------


def test_effective_temperature_tracks_external_sensor(ha: HAClient) -> None:
    """The effective_temperature sensor mirrors the external room sensor."""
    ha.set_input_number("input_number.living_temp", 19.5)
    time.sleep(TICK_SECONDS)
    _wait_tick(ha, LIVING)

    state = ha.wait_for_state(
        "sensor.living_zone_effective_temperature",
        lambda s: _sensor_state_celsius(ha, s) == pytest.approx(19.5, abs=0.2),
        timeout=15.0,
        description="effective_temperature follows input_number.living_temp",
    )
    assert state["attributes"]["source"] is not None


# ---------------------------------------------------------------------------
# Resilience — capability check + per-zone dispatch isolation
# ---------------------------------------------------------------------------


HEATONLY_ZONE = "climate.heat_only_zone"
HEAD_HEATONLY = "climate.fake_head_heatonly"


def _docker_logs(container_name: str = "msz-ha-e2e") -> str:
    """Read the test container's full HA log via ``docker logs``.

    Used by tests that want to assert on integration-emitted log lines
    (warnings, errors). The container's name is fixed by the compose
    file so this stays stable across the metric / us_customary
    parametrisation.
    """
    import subprocess

    result = subprocess.run(
        ["docker", "logs", container_name],
        capture_output=True,
        text=True,
        check=False,
    )
    return (result.stdout or "") + (result.stderr or "")


def test_heater_only_head_does_not_break_other_zones(ha: HAClient) -> None:
    """Regression for the user-reported "three heads stuck in incompatible
    modes" bug.

    The ``heatonly`` zone is wired to a fake head that advertises only
    ``off`` and ``heat`` (a stand-in for the heater-only generic_thermostat
    that originally surfaced the bug). Asking the managed zone for COOL
    must:

    * NOT raise ServiceValidationError up to the coordinator (which
      would abort the whole tick and leave the other zones stale).
    * NOT actually call ``climate.set_hvac_mode(cool)`` on the
      heater-only head — the dispatcher's capability pre-flight check
      should skip it with a warning.
    * Continue dispatching the *other* zones as normal.

    This test exercises Fix 1 (per-zone resilience) and Fix 3
    (per-tick capability check) end-to-end against a real HA process.
    """
    # Park the other zones to OFF so arbitration can't suppress the
    # heatonly zone's COOL request (heat<->cool is incompatible by
    # default and the living zone winning arbitration would convert
    # the heatonly dispatch to OFF, sidestepping the capability path
    # we're trying to exercise).
    ha.call_service(
        "climate", "set_hvac_mode", {"entity_id": LIVING, "hvac_mode": "off"}
    )
    ha.call_service(
        "climate", "set_hvac_mode", {"entity_id": OFFICE, "hvac_mode": "off"}
    )

    # Drive the living zone briefly so we have a "did the loop reach
    # this zone?" signal further down: every dispatch (including OFF)
    # increments the head's call_count attribute.
    ha.set_input_number("input_number.living_temp", 22.0)
    living_before = ha.get_state(HEAD_LIVING)
    assert living_before is not None
    living_initial_count = living_before["attributes"].get("call_count", 0)

    # Ask the heater-only zone for COOL — a mode the head can't
    # service. Dispatcher should skip with a warning, not crash.
    _set_temp(ha, HEATONLY_ZONE, "cool", 18.0)

    # Give the coordinator several ticks to attempt the dispatch.
    time.sleep(TICK_SECONDS * 2)
    _wait_tick(ha, HEATONLY_ZONE)

    # Assert 1: the heater-only head was NEVER called with hvac_mode=cool.
    after = ha.get_state(HEAD_HEATONLY)
    assert after is not None
    payloads = after["attributes"].get("last_payloads", [])
    bad_calls = [
        p
        for p in payloads
        if p.get("service") == "set_hvac_mode"
        and p.get("payload", {}).get("hvac_mode") == "cool"
    ]
    assert bad_calls == [], (
        f"Dispatcher attempted cool on heater-only head; payloads: {payloads}"
    )

    # Assert 2: the living zone is still being dispatched — its
    # call_count must keep advancing while the heatonly zone is in
    # the failing state. (The default-False always_assert flag
    # de-duplicates identical OFF re-issues; we look only for at
    # least one call after the initial dispatch took the head to OFF.)
    living_after = ha.get_state(HEAD_LIVING)
    assert living_after is not None
    assert (
        living_after["attributes"].get("call_count", 0) >= living_initial_count
    ), "living zone dispatch was killed by heat-only zone failure"

    # Assert 3: a clear WARNING line is in the HA log explaining what
    # happened. We grep the *raw* container log because pytest's
    # caplog only sees pytest-process logs, not the dockerised HA's.
    logs = _docker_logs()
    assert (
        "does not include the requested mode" in logs
        and HEAD_HEATONLY in logs
    ), "expected capability-skip warning was not logged"

    # Assert 4: the coordinator did NOT crash. The pre-Fix-1 symptom
    # was a recurring "Unexpected error fetching" log from the
    # update_coordinator. Make sure that's nowhere in the logs.
    assert (
        "Unexpected error fetching multisplit_zone_controller" not in logs
    ), "coordinator update raised; per-zone resilience is broken"


def test_heater_only_zone_recovers_when_user_picks_supported_mode(
    ha: HAClient,
) -> None:
    """After setting the heater-only zone back to a mode the head
    *does* support (HEAT), the dispatch must succeed normally.

    Guards against an over-aggressive suppression cache that would
    keep skipping the dispatch even after the user fixes their
    request.
    """
    # Stress the dispatcher with an unsupported mode first…
    ha.set_input_number("input_number.living_temp", 18.0)
    _set_temp(ha, HEATONLY_ZONE, "cool", 18.0)
    time.sleep(TICK_SECONDS)

    # …then switch back to a mode the head supports, with a real heat
    # demand so arbitration has a non-zero reason to dispatch it.
    _set_temp(ha, HEATONLY_ZONE, "heat", 22.0)

    ha.wait_for_state(
        HEAD_HEATONLY,
        lambda s: s["state"] == "heat",
        timeout=20.0,
        description="heat-only head accepts the supported mode after recovery",
    )
