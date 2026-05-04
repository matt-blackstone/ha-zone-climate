"""Pytest fixtures for the dockerised end-to-end integration suite.

The whole suite is parametrised over Home Assistant's two unit systems
(``metric`` and ``us_customary``), so every scenario test runs twice —
once against a HA container configured in Celsius and once in
Fahrenheit. The integration's internal representation is Celsius; the
units helper layer is what reconciles the two.

A session-level "log scanner" finalizer reads the container logs after
all tests finish and fails the suite if Home Assistant reported any
deprecation, legacy-platform, or non-thread-safe warnings during the
run. This catches problems before they become breaking changes in a
future HA release.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from .ha_client import HAClient

HERE = Path(__file__).resolve().parent
COMPOSE_FILE = HERE / "docker-compose.test.yml"
HA_BASE_URL = os.environ.get("MSZ_HA_BASE_URL", "http://127.0.0.1:8124")
COMPOSE_PROJECT = "msz_e2e"

CONFIG_TEMPLATE = HERE / "ha_config" / "configuration.yaml.template"
CONFIG_TARGET = HERE / "ha_config" / "configuration.yaml"

STATE_DIRS_TO_PURGE = (
    HERE / "ha_config" / ".storage",
    HERE / "ha_config" / ".cloud",
    HERE / "ha_config" / "deps",
    HERE / "ha_config" / "tts",
    HERE / "ha_config" / "blueprints",
)
STATE_FILES_TO_PURGE = (
    HERE / "ha_config" / "home-assistant_v2.db",
    HERE / "ha_config" / "home-assistant_v2.db-shm",
    HERE / "ha_config" / "home-assistant_v2.db-wal",
    HERE / "ha_config" / "home-assistant.log",
    HERE / "ha_config" / "home-assistant.log.fault",
    HERE / "ha_config" / "secrets.yaml",
    HERE / "ha_config" / ".HA_VERSION",
)


@dataclass(frozen=True)
class UnitSystemConfig:
    """One row of the unit-system parametrisation matrix."""

    name: str            # short id used in test names
    unit_system: str     # value for homeassistant.unit_system
    temperature_unit: str  # value for homeassistant.temperature_unit
    expected_user_unit: str  # what tests should expect HA to display

    def __str__(self) -> str:
        return self.name


UNIT_SYSTEMS: tuple[UnitSystemConfig, ...] = (
    UnitSystemConfig(
        name="metric",
        unit_system="metric",
        temperature_unit="C",
        expected_user_unit="°C",
    ),
    UnitSystemConfig(
        name="us_customary",
        unit_system="us_customary",
        temperature_unit="F",
        expected_user_unit="°F",
    ),
)


# Patterns that indicate Home Assistant flagged something we should fix
# before the next HA release. Each tuple is (label, regex). Matched in
# log lines emitted at WARNING or ERROR level.
_DEPRECATION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "legacy-template",
        re.compile(r"legacy.+platform: template", re.IGNORECASE),
    ),
    (
        "removed-in-version",
        re.compile(r"is being removed in (?:Home Assistant )?\d{4}\.\d+", re.IGNORECASE),
    ),
    (
        "stops-working-in-version",
        re.compile(r"stops working in (?:Home Assistant )?(?:version )?\d{4}\.\d+", re.IGNORECASE),
    ),
    (
        "non-thread-safe-call",
        re.compile(
            r"Detected that custom integration .+ calls (?:async_)?\w+ from a thread other than the event loop",
            re.IGNORECASE,
        ),
    ),
    (
        "deprecated-please-migrate",
        re.compile(r"deprecated.+please (?:migrate|use)", re.IGNORECASE),
    ),
    (
        "blocking-call-in-event-loop",
        re.compile(r"Detected blocking call to .+ in the event loop", re.IGNORECASE),
    ),
)


# Lines we expect to see and want to ignore even when they superficially
# look like deprecation warnings. Add narrowly-scoped exceptions only.
_DEPRECATION_IGNORES: tuple[re.Pattern[str], ...] = (
    # HA logs every custom integration with this generic warning at
    # startup; not a deprecation.
    re.compile(r"has not been tested by Home Assistant", re.IGNORECASE),
)


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        subprocess.run(
            ["docker", "info"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


def _purge_state() -> None:
    """Reset HA persistent state so onboarding starts clean each run."""
    for path in STATE_DIRS_TO_PURGE:
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
    for path in STATE_FILES_TO_PURGE:
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def _render_config(unit: UnitSystemConfig) -> None:
    """Substitute the unit-system placeholders into configuration.yaml."""
    template = CONFIG_TEMPLATE.read_text()
    rendered = (
        template
        .replace("__UNIT_SYSTEM__", unit.unit_system)
        .replace("__TEMPERATURE_UNIT__", unit.temperature_unit)
    )
    CONFIG_TARGET.write_text(rendered)


def _compose(*args: str, project: str = COMPOSE_PROJECT) -> subprocess.CompletedProcess:
    cmd = [
        "docker",
        "compose",
        "-p",
        project,
        "-f",
        str(COMPOSE_FILE),
        *args,
    ]
    return subprocess.run(cmd, check=False, capture_output=True, text=True)


def _scan_logs_for_deprecations(logs: str) -> list[tuple[str, str]]:
    """Return all (pattern_label, line) matches found in the log output."""
    findings: list[tuple[str, str]] = []
    for line in logs.splitlines():
        if any(ignore.search(line) for ignore in _DEPRECATION_IGNORES):
            continue
        for label, pattern in _DEPRECATION_PATTERNS:
            if pattern.search(line):
                findings.append((label, line.strip()))
                break
    return findings


@pytest.fixture(scope="session", params=UNIT_SYSTEMS, ids=lambda u: u.name)
def ha_container(request) -> str:
    """Bring up an isolated HA container per unit-system parametrisation.

    Yields the base URL the test client should hit. After the suite
    finishes, scans container logs for deprecation / legacy-platform /
    thread-safety warnings and fails the session if any are found.
    """
    if not os.environ.get("MSZ_RUN_E2E"):
        pytest.skip(
            "set MSZ_RUN_E2E=1 to run dockerised end-to-end integration tests"
        )
    if not _docker_available():
        pytest.skip("docker not available; skipping dockerised integration tests")

    unit: UnitSystemConfig = request.param
    project = f"{COMPOSE_PROJECT}_{unit.name}"

    # Clean slate
    _compose("down", "-v", "--remove-orphans", project=project)
    _purge_state()
    _render_config(unit)

    up = _compose("up", "-d", "--force-recreate", project=project)
    if up.returncode != 0:
        pytest.fail(
            "docker compose up failed:\n"
            f"stdout:\n{up.stdout}\nstderr:\n{up.stderr}"
        )

    failures: list[str] = []
    try:
        yield HA_BASE_URL
    finally:
        # Capture logs BEFORE teardown so they're not lost.
        logs_proc = _compose("logs", "--no-color", project=project)
        logs = (logs_proc.stdout or "") + (logs_proc.stderr or "")

        if not os.environ.get("MSZ_KEEP_E2E_CONTAINER"):
            _compose("down", "-v", "--remove-orphans", project=project)

        findings = _scan_logs_for_deprecations(logs)
        if findings:
            grouped: dict[str, list[str]] = {}
            for label, line in findings:
                grouped.setdefault(label, []).append(line)
            summary_lines = [
                f"  [{label}] ({len(lines)} match"
                + ("es" if len(lines) > 1 else "")
                + f"): {lines[0]}"
                for label, lines in grouped.items()
            ]
            failures.append(
                f"Home Assistant emitted deprecation / legacy / thread-safety "
                f"warnings under unit_system={unit.name!r}:\n"
                + "\n".join(summary_lines)
            )

    if failures:
        pytest.fail("\n\n".join(failures))


@pytest.fixture(scope="session")
def ha(ha_container) -> HAClient:
    """Authenticated HAClient for the current unit-system container."""
    client = HAClient.bootstrap(ha_container)
    # Wait until our integration has produced its managed climate entities.
    client.wait_for_state(
        "climate.living_zone",
        lambda s: s["state"] in ("off", "heat", "cool", "auto", "fan_only", "dry"),
        timeout=90.0,
        description="multisplit integration loaded climate.living_zone",
    )
    client.wait_for_state(
        "climate.office_zone",
        lambda s: s["state"] in ("off", "heat", "cool", "auto", "fan_only", "dry"),
        timeout=30.0,
        description="multisplit integration loaded climate.office_zone",
    )
    return client


@pytest.fixture(autouse=True)
def _reset_zone_intent_between_tests(request, ha_container):
    """Ensure each test starts from a known intent baseline.

    Only runs for tests inside the dockerised module; pure-logic tests
    elsewhere are unaffected because they don't request ``ha_container``.
    """
    yield
    if "ha" not in request.fixturenames:
        return
    client: HAClient | None = request.getfixturevalue("ha")
    if client is None:
        return
    for entity in (
        "climate.living_zone",
        "climate.office_zone",
        "climate.heat_only_zone",
    ):
        try:
            client.set_climate_hvac_mode(entity, "off")
        except Exception:
            pass
    try:
        client.set_input_number("input_number.living_temp", 22)
        client.set_input_number("input_number.office_temp", 22)
        client.set_input_number("input_number.outdoor_temp", 5)
        client.set_input_boolean("input_boolean.office_occupied", True)
    except Exception:
        pass
    time.sleep(2.0)
