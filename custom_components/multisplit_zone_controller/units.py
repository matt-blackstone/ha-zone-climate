"""Temperature unit normalisation helpers.

The whole integration works in Celsius internally. Home Assistant
deployments may, however, be configured in either the metric or the
``us_customary`` unit system, and the climate service handlers will
convert the ``temperature`` argument from the user's display unit
(``hass.config.units.temperature_unit``) to the entity's native unit
before invoking the entity. That means the integration must:

* convert outgoing setpoints from internal Celsius to HA's user unit
  before issuing ``climate.set_temperature`` (or the aux equivalent);
* convert incoming sensor / climate readings from whatever unit the
  source entity reports them in into Celsius before feeding them into
  the pure-logic decision engine.

These helpers do nothing at all when HA is already on Celsius, so the
common path stays branch-light.
"""

from __future__ import annotations

import logging
from typing import Any

_LOGGER = logging.getLogger(__name__)

# Recognised unit strings. HA accepts a variety of spellings, so we keep
# this loose. Anything we don't recognise is treated as Celsius and a
# debug log is emitted so test suites can spot configuration mistakes.
_CELSIUS_TOKENS = frozenset({"°c", "c", "celsius"})
_FAHRENHEIT_TOKENS = frozenset({"°f", "f", "fahrenheit"})


def _normalise_unit(unit: Any) -> str | None:
    if unit is None:
        return None
    text = str(unit).strip().lower()
    if not text:
        return None
    return text


def is_celsius(unit: Any) -> bool:
    """Return True for any plausible Celsius unit token."""
    text = _normalise_unit(unit)
    return text in _CELSIUS_TOKENS


def is_fahrenheit(unit: Any) -> bool:
    text = _normalise_unit(unit)
    return text in _FAHRENHEIT_TOKENS


def f_to_c(value: float) -> float:
    return (float(value) - 32.0) * 5.0 / 9.0


def c_to_f(value: float) -> float:
    return float(value) * 9.0 / 5.0 + 32.0


def to_celsius(value: float | None, source_unit: Any) -> float | None:
    """Normalise a value from ``source_unit`` into Celsius.

    Returns ``None`` when ``value`` is ``None``. Unknown units are
    treated as Celsius (with a debug log) so a misconfigured sensor
    doesn't blow up the control loop.
    """
    if value is None:
        return None
    if is_fahrenheit(source_unit):
        return f_to_c(value)
    if not is_celsius(source_unit) and source_unit not in (None, "", "?"):
        _LOGGER.debug(
            "Unknown temperature unit %r; treating value %s as Celsius",
            source_unit,
            value,
        )
    return float(value)


def from_celsius(value: float | None, target_unit: Any) -> float | None:
    """Convert a Celsius value into ``target_unit``.

    Mirrors :func:`to_celsius`: ``None`` passes through unchanged and
    unrecognised units fall back to "no conversion".
    """
    if value is None:
        return None
    if is_fahrenheit(target_unit):
        return c_to_f(value)
    if not is_celsius(target_unit) and target_unit not in (None, "", "?"):
        _LOGGER.debug(
            "Unknown target temperature unit %r; emitting %s°C unchanged",
            target_unit,
            value,
        )
    return float(value)


def hass_temperature_unit(hass: Any) -> Any:
    """Return HA's configured user-display temperature unit, or None.

    Tries a couple of attribute paths because HA's internal layout
    differs slightly between versions; falls back to ``None`` (treated
    as Celsius by ``from_celsius``) when the attribute can't be found.
    """
    try:
        return hass.config.units.temperature_unit
    except AttributeError:
        return None


def state_temperature_in_celsius(
    state: Any, default_unit: Any = None
) -> float | None:
    """Read a temperature value off any HA state object as Celsius.

    Climate-domain entities expose their temperature as the
    ``current_temperature`` attribute. Importantly, HA's
    :class:`ClimateEntity` base class converts that value (via
    ``show_temp``) into the **user-display unit** before serialising it
    into ``state.attributes`` — and it does *not* publish a
    ``temperature_unit`` attribute alongside it. Sensor-style entities
    by contrast publish their value in ``state.state`` together with
    a ``unit_of_measurement`` attribute (which HA may also have
    converted, depending on device class).

    To handle both cases we look at any explicit unit hint on the
    state first, and only fall back to ``default_unit`` (typically
    ``hass.config.units.temperature_unit`` — i.e. HA's user-display
    unit) when none is present. Only as a last resort do we treat the
    value as Celsius unchanged.
    """
    if state is None:
        return None

    attr_temp = state.attributes.get("current_temperature")
    if attr_temp is not None:
        try:
            value = float(attr_temp)
        except (TypeError, ValueError):
            value = None
        if value is not None:
            # ClimateEntity does not include temperature_unit in its
            # state attributes; the value is already in HA's user-display
            # unit. ``unit_of_measurement`` is also not used by climate
            # entities, so the fallback here is the right call.
            unit = (
                state.attributes.get("temperature_unit")
                or state.attributes.get("unit_of_measurement")
                or default_unit
            )
            return to_celsius(value, unit)

    if state.state in ("unknown", "unavailable", None, ""):
        return None

    try:
        value = float(state.state)
    except (TypeError, ValueError):
        return None

    # For sensor-style entities HA exposes the value already converted
    # into the unit advertised in ``unit_of_measurement``, so we trust
    # that hint when present; otherwise we fall back to HA's display
    # unit so we don't silently mis-interpret a numeric value.
    unit = (
        state.attributes.get("unit_of_measurement")
        or state.attributes.get("temperature_unit")
        or default_unit
    )
    return to_celsius(value, unit)
