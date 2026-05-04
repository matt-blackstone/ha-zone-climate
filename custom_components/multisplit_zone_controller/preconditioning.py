"""Per-zone pre-conditioning helpers.

Two responsibilities:

- :class:`RateEstimator` learns how fast a given zone heats and cools by
  observing temperature samples while the dispatched mode is HEAT or
  COOL. It uses an exponential moving average so it adapts to seasonal
  drift without forgetting recent behaviour. Until enough samples have
  been observed, it returns the configured defaults from
  ``PreConditioningConfig``.

- :func:`lead_time_minutes` computes how many minutes are required to
  close the temperature gap from a current reading to a target, given
  the current heating/cooling rate. The result is bounded by
  ``max_lead_minutes`` so a cold-start zone does not propose
  pre-conditioning hours in advance.

- :func:`expected_timed_out` returns True when ``EXPECTED`` has been
  active continuously past ``expected_timeout_minutes`` without becoming
  ``CONFIRMED``, signalling the coordinator to treat the zone as
  ``UNOCCUPIED`` so setback can re-engage.

All datetimes are timezone-aware UTC.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .models import HVACMode, PreConditioningConfig


@dataclass
class RateEstimator:
    """Exponential moving average heating/cooling rate estimator (°C/min)."""

    config: PreConditioningConfig
    _heating_rate: float | None = field(default=None, init=False)
    _cooling_rate: float | None = field(default=None, init=False)
    _last_ts: datetime | None = field(default=None, init=False)
    _last_temp: float | None = field(default=None, init=False)

    def observe(self, ts: datetime, temp: float | None, mode: HVACMode) -> None:
        if temp is None:
            return
        if self._last_ts is None or self._last_temp is None:
            self._last_ts = ts
            self._last_temp = temp
            return
        dt_min = (ts - self._last_ts).total_seconds() / 60.0
        if dt_min <= 0:
            return
        d_temp = temp - self._last_temp
        rate = d_temp / dt_min
        if mode is HVACMode.HEAT and rate > 0:
            self._heating_rate = self._ema(self._heating_rate, rate)
        elif mode is HVACMode.COOL and rate < 0:
            self._cooling_rate = self._ema(self._cooling_rate, -rate)
        self._last_ts = ts
        self._last_temp = temp

    @property
    def learned_heating_rate(self) -> float | None:
        return self._heating_rate

    @property
    def learned_cooling_rate(self) -> float | None:
        return self._cooling_rate

    @property
    def heating_rate(self) -> float:
        return (
            self._heating_rate
            if self._heating_rate is not None
            else self.config.default_heating_rate
        )

    @property
    def cooling_rate(self) -> float:
        return (
            self._cooling_rate
            if self._cooling_rate is not None
            else self.config.default_cooling_rate
        )

    def _ema(self, current: float | None, new: float) -> float:
        if current is None:
            return new
        a = self.config.rate_alpha
        return a * new + (1.0 - a) * current


def lead_time_minutes(
    current_temp: float | None,
    target_temp: float | None,
    heating_rate: float,
    cooling_rate: float,
    max_lead_minutes: float,
) -> float | None:
    """Estimate minutes to reach ``target_temp`` from ``current_temp``.

    Returns ``None`` when either temperature is missing. If the gap goes
    in a direction without a configured rate (e.g. needing to heat but
    ``heating_rate <= 0``) the function returns ``max_lead_minutes`` to
    signal "cannot meet the deadline; pre-condition immediately".
    """
    if current_temp is None or target_temp is None:
        return None
    gap = target_temp - current_temp
    if abs(gap) < 1e-3:
        return 0.0
    rate = heating_rate if gap > 0 else cooling_rate
    if rate <= 0:
        return max_lead_minutes
    return min(abs(gap) / rate, max_lead_minutes)


def expected_timed_out(
    expected_since: datetime | None,
    now: datetime,
    config: PreConditioningConfig,
) -> bool:
    """Return True when the EXPECTED timeout has elapsed."""
    if expected_since is None:
        return False
    return now - expected_since > timedelta(minutes=config.expected_timeout_minutes)
