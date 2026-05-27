"""Listen for physical display thermostat changes and update zone intent."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from .display_echo import DisplayEchoGuard
from .display_mapping import display_state_to_managed_mode
from .models import DisplayThermostatConfig, GroupConfig, ZoneIntent
from .units import hass_temperature_unit, to_celsius

_LOGGER = logging.getLogger(__name__)

_TARGET_ATTRS = ("temperature", "target_temp_low", "target_temp_high")


class DisplayListener:
    """Translates physical thermostat edits into managed-zone intent."""

    def __init__(
        self,
        hass: Any,
        group: GroupConfig,
        echo_guard: DisplayEchoGuard,
        get_intent: Callable[[str], ZoneIntent],
        set_intent: Callable[[ZoneIntent], None],
    ) -> None:
        self._hass = hass
        self._group = group
        self._echo_guard = echo_guard
        self._get_intent = get_intent
        self._set_intent = set_intent
        self._unsubscribers: list[Callable[[], None]] = []

    def attach(self) -> None:
        """Register HA state listeners for all configured display thermostats."""
        if self._unsubscribers:
            return
        displays = [
            (zone.zone_id, display)
            for zone in self._group.zones
            for display in zone.display_thermostats
        ]
        if not displays:
            return
        from homeassistant.helpers.event import async_track_state_change_event

        for zone_id, display in displays:
            unsub = async_track_state_change_event(
                self._hass,
                [display.entity_id],
                self._make_handler(zone_id, display),
            )
            self._unsubscribers.append(unsub)

    def detach(self) -> None:
        """Remove registered state listeners."""
        for unsub in self._unsubscribers:
            unsub()
        self._unsubscribers.clear()

    def _make_handler(self, zone_id: str, display: DisplayThermostatConfig):
        def _handle(event) -> None:
            old_state = event.data.get("old_state")
            new_state = event.data.get("new_state")
            if old_state is None or new_state is None:
                return
            if not display_user_intent_changed(old_state, new_state, display):
                return
            if self._echo_guard.should_ignore(display.entity_id):
                return

            current = self._get_intent(zone_id)
            mode = current.hvac_mode
            target = current.target_temperature

            if display.sync_mode or display.sync_fan_mode:
                mapped = display_state_to_managed_mode(
                    new_state.state,
                    (new_state.attributes or {}).get("fan_mode"),
                    display,
                )
                if mapped is not None:
                    mode = mapped

            if display.sync_setpoint:
                new_target = display_target_temperature_celsius(
                    new_state,
                    hass_temperature_unit(self._hass),
                )
                if new_target is not None:
                    target = new_target

            new_intent = ZoneIntent(
                zone_id=zone_id,
                hvac_mode=mode,
                target_temperature=target,
            )
            if new_intent == current:
                return
            self._set_intent(new_intent)
            _LOGGER.debug(
                "Display thermostat %s updated zone %s intent: mode=%s "
                "target=%s",
                display.entity_id,
                zone_id,
                new_intent.hvac_mode.value,
                new_intent.target_temperature,
            )

        return _handle


def display_user_intent_changed(
    old_state: Any,
    new_state: Any,
    display: DisplayThermostatConfig,
) -> bool:
    """True when a state_changed event touched user-intent fields."""
    if old_state.state != new_state.state and display.sync_mode:
        return True

    old_attrs = old_state.attributes or {}
    new_attrs = new_state.attributes or {}
    if display.sync_fan_mode and old_attrs.get("fan_mode") != new_attrs.get("fan_mode"):
        return True
    if display.sync_setpoint:
        for attr in _TARGET_ATTRS:
            if old_attrs.get(attr) != new_attrs.get(attr):
                return True
    return False


def display_target_temperature_celsius(
    state: Any,
    default_unit: Any,
) -> float | None:
    """Read a climate target temperature attribute as Celsius."""
    attrs = state.attributes or {}
    for attr in _TARGET_ATTRS:
        raw = attrs.get(attr)
        if raw is None:
            continue
        try:
            return to_celsius(float(raw), default_unit)
        except (TypeError, ValueError):
            continue
    return None
