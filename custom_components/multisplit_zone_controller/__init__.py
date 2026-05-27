"""Multi-Split Zone Controller integration entry point.

The integration supports **two configuration sources** simultaneously:

* **YAML** — historical path; ``configuration.yaml`` may declare any
  number of compressor groups under a top-level
  ``multisplit_zone_controller:`` key. Validated via
  :data:`CONFIG_SCHEMA`.
* **Config entries (UI)** — a single config entry corresponds to a
  single compressor group (one outdoor unit). Multiple entries can
  coexist with each other and with YAML-declared groups.

To make those sources interchangeable, the integration keeps a
flat-by-group ``coordinators`` registry in ``hass.data[DOMAIN]`` plus
two indexes that record which source contributed each group:

* ``yaml_groups`` — group ids registered via YAML
* ``entry_groups`` — ``{entry_id: {group_id, ...}}`` for UI entries

Platform modules (``climate``, ``sensor``, ``binary_sensor``) expose
both ``async_setup_platform`` (called via YAML discovery) and
``async_setup_entry`` (called by config-entry forwarding). Each only
emits entities for the groups that belong to its source, but both
read the coordinators from the same shared dict so arbitration and
diagnostics work identically.

Heavy ``homeassistant`` and ``voluptuous`` imports are deferred into
the setup functions so the pure-logic submodules
(``models``, ``sensor_fusion``, ``arbitration``, ``safety``,
``config_schema``) can be imported and unit-tested without HA
installed.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ("climate", "sensor", "binary_sensor")


import voluptuous as vol

from .config_schema import INTEGRATION_SCHEMA

CONFIG_SCHEMA = vol.Schema({DOMAIN: INTEGRATION_SCHEMA}, extra=vol.ALLOW_EXTRA)


def _ensure_data(hass: "HomeAssistant") -> dict:
    """Return the integration's shared ``hass.data`` slot, creating it.

    Centralises the schema for the slot so YAML setup and config-entry
    setup can't drift apart.
    """
    bucket = hass.data.setdefault(DOMAIN, {})
    bucket.setdefault("coordinators", {})
    bucket.setdefault("yaml_groups", set())
    bucket.setdefault("entry_groups", {})
    return bucket


async def async_setup(hass: "HomeAssistant", config) -> bool:
    """Set up the integration from YAML.

    Always returns True so a config-entry-only deployment doesn't
    fail just because the YAML key is absent.
    """
    import voluptuous as vol
    from homeassistant.helpers import discovery

    from .config_schema import parse_groups
    from .coordinator import GroupCoordinator

    bucket = _ensure_data(hass)

    raw = config.get(DOMAIN)
    if raw is None:
        return True

    try:
        groups = parse_groups(raw)
    except vol.Invalid as err:
        _LOGGER.error("Invalid multisplit_zone_controller configuration: %s", err)
        return False

    yaml_group_ids: set[str] = set()
    for group in groups:
        if group.group_id in bucket["coordinators"]:
            _LOGGER.warning(
                "Skipping YAML group %s: a group with that id is already "
                "registered (likely from a UI config entry).",
                group.group_id,
            )
            continue
        coordinator = GroupCoordinator(hass, group)
        # YAML-only setup has no config entry, so we use async_refresh()
        # rather than async_config_entry_first_refresh().
        await coordinator.async_refresh()
        bucket["coordinators"][group.group_id] = coordinator
        yaml_group_ids.add(group.group_id)

    bucket["yaml_groups"].update(yaml_group_ids)

    if not yaml_group_ids:
        return True

    discovery_payload = {"yaml_group_ids": sorted(yaml_group_ids)}
    for platform in PLATFORMS:
        hass.async_create_task(
            discovery.async_load_platform(
                hass, platform, DOMAIN, discovery_payload, config
            )
        )

    _LOGGER.info(
        "multisplit_zone_controller loaded %d YAML group(s): %s",
        len(yaml_group_ids),
        ", ".join(sorted(yaml_group_ids)),
    )
    return True


async def async_setup_entry(
    hass: "HomeAssistant", entry: "ConfigEntry"
) -> bool:
    """Set up a UI-configured compressor group from a config entry.

    Each entry corresponds to exactly one :class:`GroupConfig`, built
    from the entry's ``data`` (and any options-flow overrides applied
    on top via :func:`entry_to_group_config`). Platforms are forwarded
    via :func:`async_forward_entry_setups`; the platform modules look
    up their entities from ``hass.data[DOMAIN]['entry_groups'][entry.entry_id]``.
    """
    from .config_schema import entry_to_group_config
    from .coordinator import GroupCoordinator

    bucket = _ensure_data(hass)
    try:
        group = entry_to_group_config(entry)
    except Exception as err:  # noqa: BLE001 - surface as setup failure
        _LOGGER.error(
            "Could not build group from config entry %s: %s",
            entry.entry_id,
            err,
        )
        return False

    if group.group_id in bucket["coordinators"]:
        _LOGGER.error(
            "Refusing to set up entry %s: a group with id %s is already "
            "registered. Pick a different group_id in the integration's "
            "options (or remove the duplicate YAML/UI entry).",
            entry.entry_id,
            group.group_id,
        )
        return False

    coordinator = GroupCoordinator(hass, group, config_entry=entry)
    await coordinator.async_config_entry_first_refresh()

    bucket["coordinators"][group.group_id] = coordinator
    bucket["entry_groups"][entry.entry_id] = {group.group_id}

    # Reload the entry whenever options change so UI tweaks take effect
    # without requiring a full HA restart.
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _LOGGER.info(
        "multisplit_zone_controller loaded UI group %s from entry %s",
        group.group_id,
        entry.entry_id,
    )
    return True


async def async_unload_entry(
    hass: "HomeAssistant", entry: "ConfigEntry"
) -> bool:
    """Unload a config entry, dropping its coordinator and entities."""
    bucket = _ensure_data(hass)

    unload_ok = await hass.config_entries.async_unload_platforms(
        entry, PLATFORMS
    )
    if not unload_ok:
        return False

    group_ids = bucket["entry_groups"].pop(entry.entry_id, set())
    for gid in group_ids:
        coordinator = bucket["coordinators"].pop(gid, None)
        if coordinator is not None:
            coordinator.detach_display_listeners()
    return True


async def _async_reload_entry(
    hass: "HomeAssistant", entry: "ConfigEntry"
) -> None:
    """Reload an entry — invoked when the options flow saves changes."""
    await hass.config_entries.async_reload(entry.entry_id)
