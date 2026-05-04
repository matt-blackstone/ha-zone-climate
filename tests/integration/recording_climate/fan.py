"""Recording fan platform — captures every fan service call for assertions."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.components.fan import (
    DIRECTION_FORWARD,
    DIRECTION_REVERSE,
    FanEntity,
    FanEntityFeature,
)
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.config_validation import PLATFORM_SCHEMA
import homeassistant.helpers.config_validation as cv

_LOGGER = logging.getLogger(__name__)

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_NAME): cv.string,
    }
)


async def async_setup_platform(
    hass: HomeAssistant,
    config: dict[str, Any],
    async_add_entities,
    discovery_info=None,
) -> None:
    async_add_entities([RecordingFan(name=config[CONF_NAME])])


class RecordingFan(FanEntity):
    _attr_should_poll = False
    _attr_supported_features = (
        FanEntityFeature.SET_SPEED
        | FanEntityFeature.DIRECTION
        | FanEntityFeature.TURN_ON
        | FanEntityFeature.TURN_OFF
    )

    def __init__(self, name: str) -> None:
        self._attr_name = name
        slug = name.lower().replace(" ", "_")
        self._attr_unique_id = f"recording_fan_{slug}"
        self._attr_is_on = False
        self._attr_percentage = 0
        self._attr_current_direction = DIRECTION_FORWARD
        self._call_count = 0
        self._payloads: list[dict[str, Any]] = []

    def _record(self, service: str, payload: dict[str, Any]) -> None:
        self._call_count += 1
        self._payloads.append({"service": service, "payload": payload})
        if len(self._payloads) > 50:
            self._payloads = self._payloads[-50:]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "call_count": self._call_count,
            "last_payloads": list(self._payloads[-25:]),
        }

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        self._attr_is_on = True
        if percentage is not None:
            self._attr_percentage = int(percentage)
        self._record(
            "turn_on", {"percentage": percentage, "preset_mode": preset_mode}
        )
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._attr_is_on = False
        self._attr_percentage = 0
        self._record("turn_off", {})
        self.async_write_ha_state()

    async def async_set_percentage(self, percentage: int) -> None:
        self._attr_percentage = int(percentage)
        self._attr_is_on = percentage > 0
        self._record("set_percentage", {"percentage": percentage})
        self.async_write_ha_state()

    async def async_set_direction(self, direction: str) -> None:
        if direction not in (DIRECTION_FORWARD, DIRECTION_REVERSE):
            return
        self._attr_current_direction = direction
        self._record("set_direction", {"direction": direction})
        self.async_write_ha_state()
