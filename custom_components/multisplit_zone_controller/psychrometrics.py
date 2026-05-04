"""Pure-Python psychrometric helpers (Phase 8 / "Level 2" comfort).

Foundational moist-air calculations:

- :func:`saturation_vapor_pressure` (Magnus–Tetens, kPa)
- :func:`vapor_pressure`
- :func:`humidity_ratio` (kg water vapour / kg dry air)
- :func:`enthalpy` (kJ / kg dry air, ASHRAE moist-air enthalpy)
- :func:`dew_point` (°C, Magnus formula)
- :func:`cooling_effect` (°C apparent reduction from air movement)
- :func:`simple_pmv` (very simplified PMV approximation)
- :func:`effective_comfort_temp` (combined dry-bulb + RH + fan into one
  °C-equivalent value used by the optional psychrometric scoring mode)

Full ASHRAE PMV requires iteratively solving for the clothing surface
temperature; the helper here is intentionally a closed-form linear
approximation suitable for qualitative comparison and diagnostic
display, not a regulatory PMV implementation.

All temperatures are in °C unless otherwise noted; relative humidity is
0-100 (percent).
"""

from __future__ import annotations

import math


def saturation_vapor_pressure(t_celsius: float) -> float:
    """Magnus–Tetens saturation vapour pressure of water in air (kPa)."""
    return 0.6108 * math.exp(17.27 * t_celsius / (t_celsius + 237.3))


def vapor_pressure(t_celsius: float, rh_pct: float) -> float:
    """Partial vapour pressure of water in air at given dry-bulb T and RH (kPa)."""
    return saturation_vapor_pressure(t_celsius) * (rh_pct / 100.0)


def humidity_ratio(
    t_celsius: float, rh_pct: float, p_atm_kpa: float = 101.325
) -> float:
    """Mass ratio of water vapour to dry air."""
    e = vapor_pressure(t_celsius, rh_pct)
    return 0.622 * e / (p_atm_kpa - e)


def enthalpy(t_celsius: float, w: float) -> float:
    """Specific enthalpy of moist air, kJ per kg dry air (ASHRAE)."""
    return 1.006 * t_celsius + w * (2501.0 + 1.86 * t_celsius)


def dew_point(t_celsius: float, rh_pct: float) -> float:
    """Dew-point temperature (°C) via the Magnus formula.

    Returns negative infinity when ``rh_pct`` is non-positive.
    """
    if rh_pct <= 0:
        return float("-inf")
    gamma = math.log(rh_pct / 100.0) + (17.27 * t_celsius / (237.3 + t_celsius))
    return 237.3 * gamma / (17.27 - gamma)


def cooling_effect(air_velocity_mps: float) -> float:
    """Approximate apparent-temperature reduction from air movement (°C).

    Smooth, monotonically increasing approximation suitable for
    diagnostic display. Capped at 2 °C to avoid overestimating fan
    impact in very turbulent regimes.
    """
    if air_velocity_mps <= 0:
        return 0.0
    return min(air_velocity_mps * 1.5, 2.0)


def simple_pmv(
    t_celsius: float,
    rh_pct: float,
    met: float = 1.2,
    clo: float = 0.5,
) -> float:
    """Very simplified PMV approximation.

    Designed for qualitative comparison between zones, *not* as a
    drop-in replacement for the full ASHRAE 55 / Fanger PMV. Produces
    values that are zero around 24 °C at 50 % RH for typical office
    metabolic and clothing assumptions, negative when too cool, positive
    when too warm.
    """
    base_t_comfort = 24.0 - 4.0 * (clo - 0.5) - 2.0 * (met - 1.2)
    rh_factor = (rh_pct - 50.0) * 0.01
    return (t_celsius - base_t_comfort) * 0.3 + rh_factor


def effective_comfort_temp(
    t_celsius: float | None,
    rh_pct: float | None = None,
    fan_speed_pct: float | None = None,
    occupied: bool = True,
) -> float | None:
    """Combine dry-bulb T, humidity, and fan use into one comfort-equivalent °C.

    Used when ``GroupConfig.use_psychrometric_scoring`` is True. The
    arbitration scorer computes deviation against the user's setpoint
    using this combined value rather than the raw thermometer reading,
    so a humid but air-stirred room scores closer to comfort than a
    naive dry-bulb deviation would suggest.

    Returns ``None`` if the dry-bulb temperature is missing.
    """
    if t_celsius is None:
        return None
    correction = 0.0
    if rh_pct is not None and t_celsius >= 20.0:
        rh_excess = rh_pct - 50.0
        correction += 0.04 * rh_excess
    if occupied and fan_speed_pct is not None and fan_speed_pct > 0:
        velocity = (fan_speed_pct / 100.0) * 1.5
        correction -= cooling_effect(velocity)
    return t_celsius + correction
