"""Recording climate platform.

Configured in YAML, e.g.:

    climate:
      - platform: recording_climate
        name: Fake Head Living
        modes:
          - "off"
          - heat
          - cool
          - dry
          - fan_only
        fan_modes: [auto, low, medium, high]
        swing_modes: [off, vertical]
        initial_temperature: 21
        current_temperature_entity: sensor.living_temp

Each entity exposes:
  * The standard climate state (hvac_mode, target_temperature, fan_mode,
    swing_mode, current_temperature).
  * Diagnostic attributes:
      - last_call_count: total number of service calls received
      - last_set_hvac_mode_call: index of the last set_hvac_mode call
      - last_set_temperature_call: index of the last set_temperature call
      - last_payloads: list of recent (service, kwargs) tuples (max 25)
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.components.climate.const import (
    ATTR_HVAC_MODE,
)
from homeassistant.const import (
    ATTR_TEMPERATURE,
    CONF_NAME,
    EVENT_HOMEASSISTANT_START,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.config_validation import PLATFORM_SCHEMA
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.event import async_track_state_change_event

# Reuse the integration's unit-normalisation helper so this test stub
# behaves correctly when HA is configured in us_customary: HA
# auto-converts temperature-typed sensor states to the user-display
# unit, so a raw float(state.state) would smuggle Fahrenheit values
# into our entity (whose native unit is Celsius) and badly confuse the
# safety layer.
from custom_components.multisplit_zone_controller.units import (
    hass_temperature_unit,
    state_temperature_in_celsius,
)

_LOGGER = logging.getLogger(__name__)

CONF_MODES = "modes"
CONF_FAN_MODES = "fan_modes"
CONF_SWING_MODES = "swing_modes"
CONF_INITIAL_TEMPERATURE = "initial_temperature"
CONF_CURRENT_TEMPERATURE_ENTITY = "current_temperature_entity"

DEFAULT_MODES = ["off", "heat", "cool", "dry", "fan_only"]
DEFAULT_FAN_MODES = ["auto", "low", "medium", "high"]
DEFAULT_SWING_MODES = ["off", "vertical", "horizontal", "both"]

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_NAME): cv.string,
        vol.Optional(CONF_MODES, default=DEFAULT_MODES): vol.All(
            cv.ensure_list, [cv.string]
        ),
        vol.Optional(CONF_FAN_MODES, default=DEFAULT_FAN_MODES): vol.All(
            cv.ensure_list, [cv.string]
        ),
        vol.Optional(CONF_SWING_MODES, default=DEFAULT_SWING_MODES): vol.All(
            cv.ensure_list, [cv.string]
        ),
        vol.Optional(CONF_INITIAL_TEMPERATURE, default=21.0): vol.Coerce(float),
        vol.Optional(CONF_CURRENT_TEMPERATURE_ENTITY): cv.entity_id,
    }
)


async def async_setup_platform(
    hass: HomeAssistant,
    config: dict[str, Any],
    async_add_entities,
    discovery_info=None,
) -> None:
    """Create one recording climate entity per platform block."""
    entity = RecordingClimate(
        name=config[CONF_NAME],
        modes=config[CONF_MODES],
        fan_modes=config[CONF_FAN_MODES],
        swing_modes=config[CONF_SWING_MODES],
        initial_temperature=config[CONF_INITIAL_TEMPERATURE],
        current_temp_entity=config.get(CONF_CURRENT_TEMPERATURE_ENTITY),
    )
    async_add_entities([entity])


class RecordingClimate(ClimateEntity):
    """A climate entity that simply remembers what was sent to it."""

    _attr_should_poll = False
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = 0.5

    def __init__(
        self,
        name: str,
        modes: list[str],
        fan_modes: list[str],
        swing_modes: list[str],
        initial_temperature: float,
        current_temp_entity: str | None,
    ) -> None:
        self._attr_name = name
        slug = name.lower().replace(" ", "_")
        self._attr_unique_id = f"recording_climate_{slug}"
        self._attr_hvac_modes = [HVACMode(m) for m in modes]
        self._attr_fan_modes = list(fan_modes)
        self._attr_swing_modes = list(swing_modes)
        self._attr_supported_features = (
            ClimateEntityFeature.TARGET_TEMPERATURE
            | ClimateEntityFeature.FAN_MODE
            | ClimateEntityFeature.SWING_MODE
            | ClimateEntityFeature.TURN_ON
            | ClimateEntityFeature.TURN_OFF
        )
        self._attr_hvac_mode = HVACMode.OFF
        self._attr_target_temperature = initial_temperature
        self._attr_fan_mode = fan_modes[0] if fan_modes else None
        self._attr_swing_mode = swing_modes[0] if swing_modes else None
        self._attr_current_temperature = initial_temperature
        self._current_temp_entity = current_temp_entity

        # Diagnostic counters
        self._call_count = 0
        self._set_hvac_mode_call_idx = 0
        self._set_temperature_call_idx = 0
        self._set_fan_mode_call_idx = 0
        self._set_swing_mode_call_idx = 0
        self._payloads: list[dict[str, Any]] = []

    async def async_added_to_hass(self) -> None:
        """Hook current temperature to a sensor entity if configured."""
        await super().async_added_to_hass()
        if self._current_temp_entity:

            @callback
            def _on_change(event):
                self._sync_current_temp()

            @callback
            def _on_started(_event):
                self._sync_current_temp()

            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, [self._current_temp_entity], _on_change
                )
            )
            self.async_on_remove(
                self.hass.bus.async_listen_once(
                    EVENT_HOMEASSISTANT_START, _on_started
                )
            )
            self._sync_current_temp()

    def _sync_current_temp(self) -> None:
        if not self._current_temp_entity:
            return
        state = self.hass.states.get(self._current_temp_entity)
        if state is None:
            return
        celsius = state_temperature_in_celsius(
            state, hass_temperature_unit(self.hass)
        )
        if celsius is None:
            return
        self._attr_current_temperature = celsius
        self.async_write_ha_state()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "call_count": self._call_count,
            "set_hvac_mode_call_idx": self._set_hvac_mode_call_idx,
            "set_temperature_call_idx": self._set_temperature_call_idx,
            "set_fan_mode_call_idx": self._set_fan_mode_call_idx,
            "set_swing_mode_call_idx": self._set_swing_mode_call_idx,
            "last_payloads": list(self._payloads[-25:]),
        }

    def _record(self, service: str, payload: dict[str, Any]) -> None:
        self._call_count += 1
        self._payloads.append({"service": service, "payload": payload})
        if len(self._payloads) > 50:
            self._payloads = self._payloads[-50:]

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        self._attr_hvac_mode = hvac_mode
        self._set_hvac_mode_call_idx = self._call_count + 1
        self._record("set_hvac_mode", {"hvac_mode": str(hvac_mode)})
        self.async_write_ha_state()

    async def async_set_temperature(self, **kwargs: Any) -> None:
        if (mode := kwargs.get(ATTR_HVAC_MODE)) is not None:
            self._attr_hvac_mode = HVACMode(mode)
        if (temp := kwargs.get(ATTR_TEMPERATURE)) is not None:
            self._attr_target_temperature = float(temp)
        self._set_temperature_call_idx = self._call_count + 1
        self._record("set_temperature", dict(kwargs))
        self.async_write_ha_state()

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        self._attr_fan_mode = fan_mode
        self._set_fan_mode_call_idx = self._call_count + 1
        self._record("set_fan_mode", {"fan_mode": fan_mode})
        self.async_write_ha_state()

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        self._attr_swing_mode = swing_mode
        self._set_swing_mode_call_idx = self._call_count + 1
        self._record("set_swing_mode", {"swing_mode": swing_mode})
        self.async_write_ha_state()

    async def async_turn_on(self) -> None:
        if HVACMode.HEAT in self._attr_hvac_modes:
            await self.async_set_hvac_mode(HVACMode.HEAT)
        else:
            await self.async_set_hvac_mode(self._attr_hvac_modes[1])

    async def async_turn_off(self) -> None:
        await self.async_set_hvac_mode(HVACMode.OFF)
