"""Pure-logic test fixtures.

The pure decision modules (``models``, ``sensor_fusion``,
``arbitration``, ``safety``, ``config_schema``) remain testable
without ``homeassistant`` installed.

For the dispatcher modules (``dispatch``, ``fan_proxy``,
``aux_heat_dispatch``) we want unit-test coverage without dragging in
the full HA package (~hundreds of MB). Those modules only touch a
handful of HA symbols (a couple of constants and the
``HomeAssistant`` class as a type hint). We register lightweight
``sys.modules`` stubs below so a plain ``import homeassistant.const``
or ``import homeassistant.core`` succeeds.

The stubs are skipped if ``homeassistant`` is genuinely installed
(e.g. when running inside the Dockerised HA container for the e2e
suite), so they only activate in the lean dev/CI venv.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _install_ha_stubs() -> None:
    """Register stub ``homeassistant`` submodules in ``sys.modules``.

    Only runs when the real package is absent. Provides just enough
    surface area for the dispatcher modules to import successfully and
    for tests to feed them mock service registries / state objects.
    """
    if importlib.util.find_spec("homeassistant") is not None:
        return  # Real HA is available; let the dispatchers use it.

    ha = types.ModuleType("homeassistant")
    ha.__path__ = []  # mark as a package
    sys.modules.setdefault("homeassistant", ha)

    const = types.ModuleType("homeassistant.const")
    const.ATTR_ENTITY_ID = "entity_id"
    const.ATTR_TEMPERATURE = "temperature"
    sys.modules.setdefault("homeassistant.const", const)

    # ``HomeAssistant`` is only used as a type hint by the dispatchers
    # (with ``from __future__ import annotations`` it isn't evaluated
    # at runtime), but ``from homeassistant.core import HomeAssistant``
    # must still resolve. A bare class is enough.
    core = types.ModuleType("homeassistant.core")

    class HomeAssistant:  # noqa: D401 — stub
        """Stub HomeAssistant class for type-hint resolution only."""

    core.HomeAssistant = HomeAssistant
    sys.modules.setdefault("homeassistant.core", core)


_install_ha_stubs()
