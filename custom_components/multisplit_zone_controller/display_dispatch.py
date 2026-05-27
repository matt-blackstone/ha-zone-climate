"""Dispatch managed zone intent to physical display thermostats."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping

from homeassistant.const import ATTR_ENTITY_ID, ATTR_TEMPERATURE
from homeassistant.core import HomeAssistant

from .display_echo import DisplayEchoGuard
from .display_mapping import managed_mode_to_display
from .models import DisplayThermostatConfig, HVACMode, ZoneConfig, ZoneIntent
from .units import from_celsius, hass_temperature_unit

_LOGGER = logging.getLogger(__name__)

CLIMATE_DOMAIN = "climate"
SERVICE_SET_HVAC_MODE = "set_hvac_mode"
SERVICE_SET_FAN_MODE = "set_fan_mode"
SERVICE_SET_TEMPERATURE = "set_temperature"

SYNC_UNKNOWN = "unknown"
SYNC_SYNCED = "synced"
SYNC_STALE = "stale"
SYNC_UNREACHABLE = "unreachable"


@dataclass(frozen=True)
class DisplaySyncStatus:
    """Diagnostic status for one display thermostat mirror."""

    state: str = SYNC_UNKNOWN
    last_error: str | None = None
    last_success: str | None = None
    clamped: bool = False
    desired_hvac_mode: str | None = None
    desired_fan_mode: str | None = None
    desired_temperature: float | None = None
    sent_temperature: float | None = None


class DisplayDispatcher:
    """Mirror managed-zone intent to configured physical thermostats.

    This dispatcher intentionally uses the user's stored intent rather than
    the arbitration result. A display thermostat is a UI mirror; it should
    show what the user asked that zone to do even if the compressor group
    temporarily blocks the real head from honoring the request.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        echo_guard: DisplayEchoGuard,
    ) -> None:
        self._hass = hass
        self._echo_guard = echo_guard
        self._last_mode: dict[str, str | None] = {}
        self._last_fan_mode: dict[str, str | None] = {}
        self._last_target: dict[str, float | None] = {}
        self._statuses: dict[tuple[str, str], DisplaySyncStatus] = {}
        self._warned_unsupported: set[tuple[str, str, str]] = set()
        self._errored: set[tuple[str, str]] = set()

    async def apply(
        self,
        zones: Mapping[str, ZoneConfig],
        intents: Mapping[str, ZoneIntent],
    ) -> None:
        for zone_id, zone_cfg in zones.items():
            intent = intents[zone_id]
            for display in zone_cfg.display_thermostats:
                try:
                    await self._apply_one(zone_cfg, intent, display)
                except Exception:
                    _LOGGER.exception(
                        "Unexpected error dispatching display thermostat %s "
                        "for zone %s; continuing",
                        display.entity_id,
                        zone_id,
                    )
                    self._set_status(
                        zone_id,
                        display,
                        SYNC_UNREACHABLE,
                        "unexpected dispatch error",
                    )

    def status(self, zone_id: str, entity_id: str) -> DisplaySyncStatus:
        return self._statuses.get((zone_id, entity_id), DisplaySyncStatus())

    async def _apply_one(
        self,
        zone_cfg: ZoneConfig,
        intent: ZoneIntent,
        display: DisplayThermostatConfig,
    ) -> None:
        entity_id = display.entity_id
        state = self._hass.states.get(entity_id)
        if state is None or state.state in ("unavailable", "unknown", None, ""):
            self._set_status(
                zone_cfg.zone_id,
                display,
                SYNC_UNREACHABLE,
                "display entity unavailable",
            )
            return

        attrs = state.attributes or {}
        supported_modes = tuple(str(m) for m in attrs.get("hvac_modes") or ())
        supported_fan_modes = tuple(str(m) for m in attrs.get("fan_modes") or ())
        command = managed_mode_to_display(
            intent.hvac_mode,
            display,
            supported_fan_modes,
        )
        assert_every_tick = display.always_assert
        stale_reasons: list[str] = []

        if display.sync_mode and command.hvac_mode is not None:
            if not self._supports_value(
                supported_modes,
                command.hvac_mode,
                entity_id,
                "hvac_mode",
            ):
                stale_reasons.append(f"unsupported hvac_mode {command.hvac_mode}")
            elif (
                self._last_mode.get(entity_id) != command.hvac_mode
                or assert_every_tick
            ):
                ok = await self._safe_call(
                    zone_cfg.zone_id,
                    entity_id,
                    SERVICE_SET_HVAC_MODE,
                    {ATTR_ENTITY_ID: entity_id, "hvac_mode": command.hvac_mode},
                )
                if not ok:
                    self._set_status(
                        zone_cfg.zone_id,
                        display,
                        SYNC_UNREACHABLE,
                        f"set_hvac_mode({command.hvac_mode}) failed",
                    )
                    return
                self._last_mode[entity_id] = command.hvac_mode
                self._echo_guard.mark_write(entity_id)

        if display.sync_fan_mode:
            if command.fan_mode is None:
                stale_reasons.append("no supported fan mode mapping")
            elif (
                self._last_fan_mode.get(entity_id) != command.fan_mode
                or assert_every_tick
            ):
                ok = await self._safe_call(
                    zone_cfg.zone_id,
                    entity_id,
                    SERVICE_SET_FAN_MODE,
                    {ATTR_ENTITY_ID: entity_id, "fan_mode": command.fan_mode},
                )
                if not ok:
                    self._set_status(
                        zone_cfg.zone_id,
                        display,
                        SYNC_UNREACHABLE,
                        f"set_fan_mode({command.fan_mode}) failed",
                    )
                    return
                self._last_fan_mode[entity_id] = command.fan_mode
                self._echo_guard.mark_write(entity_id)

        sent_temperature: float | None = None
        clamped = False
        if self._should_sync_setpoint(display, intent.hvac_mode):
            target = intent.target_temperature
            if target is not None:
                target_in_user_unit = from_celsius(
                    target,
                    hass_temperature_unit(self._hass),
                )
                sent_temperature, clamped = self._clamp_target(
                    target_in_user_unit,
                    attrs,
                )
                if (
                    self._last_target.get(entity_id) != sent_temperature
                    or assert_every_tick
                ):
                    ok = await self._safe_call(
                        zone_cfg.zone_id,
                        entity_id,
                        SERVICE_SET_TEMPERATURE,
                        {
                            ATTR_ENTITY_ID: entity_id,
                            ATTR_TEMPERATURE: sent_temperature,
                        },
                    )
                    if not ok:
                        self._set_status(
                            zone_cfg.zone_id,
                            display,
                            SYNC_UNREACHABLE,
                            f"set_temperature({sent_temperature}) failed",
                        )
                        return
                    self._last_target[entity_id] = sent_temperature
                    self._echo_guard.mark_write(entity_id)

        status_state = SYNC_STALE if stale_reasons else SYNC_SYNCED
        self._statuses[(zone_cfg.zone_id, entity_id)] = DisplaySyncStatus(
            state=status_state,
            last_error="; ".join(stale_reasons) if stale_reasons else None,
            last_success=_now_iso() if not stale_reasons else None,
            clamped=clamped,
            desired_hvac_mode=command.hvac_mode,
            desired_fan_mode=command.fan_mode,
            desired_temperature=intent.target_temperature,
            sent_temperature=sent_temperature,
        )

    def _set_status(
        self,
        zone_id: str,
        display: DisplayThermostatConfig,
        state: str,
        error: str | None,
    ) -> None:
        self._statuses[(zone_id, display.entity_id)] = DisplaySyncStatus(
            state=state,
            last_error=error,
        )

    @staticmethod
    def _should_sync_setpoint(
        display: DisplayThermostatConfig,
        mode: HVACMode,
    ) -> bool:
        return (
            display.sync_setpoint
            and mode is not HVACMode.OFF
            and mode is not HVACMode.FAN_ONLY
        )

    def _supports_value(
        self,
        supported: tuple[str, ...],
        desired: str,
        entity_id: str,
        attr_name: str,
    ) -> bool:
        if not supported:
            return True
        if _normalise(desired) in {_normalise(v) for v in supported}:
            self._warned_unsupported.discard((entity_id, attr_name, desired))
            return True
        key = (entity_id, attr_name, desired)
        if key not in self._warned_unsupported:
            _LOGGER.warning(
                "Skipping display sync for %s: %s=%s is not in supported "
                "values %s",
                entity_id,
                attr_name,
                desired,
                sorted(supported),
            )
            self._warned_unsupported.add(key)
        return False

    @staticmethod
    def _clamp_target(
        target_in_user_unit: float | None,
        attrs: Mapping[str, object],
    ) -> tuple[float | None, bool]:
        if target_in_user_unit is None:
            return None, False
        out = float(target_in_user_unit)
        clamped = False
        min_temp = _float_or_none(attrs.get("min_temp"))
        max_temp = _float_or_none(attrs.get("max_temp"))
        if min_temp is not None and out < min_temp:
            out = min_temp
            clamped = True
        if max_temp is not None and out > max_temp:
            out = max_temp
            clamped = True
        return out, clamped

    async def _safe_call(
        self,
        zone_id: str,
        entity_id: str,
        service: str,
        payload: dict,
    ) -> bool:
        key = (entity_id, service)
        try:
            await self._hass.services.async_call(
                CLIMATE_DOMAIN,
                service,
                payload,
                blocking=True,
            )
        except Exception as err:
            if key not in self._errored:
                _LOGGER.error(
                    "Display dispatch failed for zone %s: climate.%s on %s "
                    "raised %s: %s. Will retry next tick.",
                    zone_id,
                    service,
                    entity_id,
                    type(err).__name__,
                    err,
                )
                self._errored.add(key)
            return False
        self._errored.discard(key)
        return True


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _float_or_none(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalise(value: object) -> str:
    return str(value).strip().lower().replace("_", " ")
