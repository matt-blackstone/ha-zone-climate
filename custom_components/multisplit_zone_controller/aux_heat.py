"""Auxiliary (resistive) heat decision logic and transition state machine.

Auxiliary heat is intentionally hidden from the user: there is no
``aux_heat`` mode on the managed climate entity. Instead, the
coordinator activates a separate switch or climate device when
configured conditions are met, and surfaces a friendly status string
through the managed climate's ``status_message`` attribute.

Activation triggers (any one is sufficient when not currently active):

- the head is unavailable / faulted (``head_fault``),
- outdoor temperature is at or below the configured lockout
  (``outdoor_lockout``),
- the effective room temperature is approaching the configured safety
  floor by less than ``safety_floor_margin`` (``safety_floor_near``).

When already active, hysteresis keeps aux on until *all* triggers have
cleared by ``deactivation_margin`` to avoid flapping.

Activation is staged through :class:`AuxHeatRuntime` so the head is
commanded ``OFF`` first, the configured ``settle_seconds`` elapses, and
only then is the aux device commanded ``ON``. This protects compressor
hardware during the transition.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .models import (
    AuxHeatConfig,
    AuxHeatDecision,
    AuxHeatInputs,
    AuxHeatTrigger,
    HVACMode,
)

_MSG_SAFETY_FLOOR = "Supplemental heat active to protect room temperature."
_MSG_OUTDOOR = "Auxiliary heat assisting due to outdoor conditions."
_MSG_HEAD_FAULT = "Heat pump unavailable, using backup heat temporarily."

_MESSAGES: dict[AuxHeatTrigger, str] = {
    AuxHeatTrigger.SAFETY_FLOOR_NEAR: _MSG_SAFETY_FLOOR,
    AuxHeatTrigger.OUTDOOR_LOCKOUT: _MSG_OUTDOOR,
    AuxHeatTrigger.HEAD_FAULT: _MSG_HEAD_FAULT,
}


def evaluate_aux_heat(cfg: AuxHeatConfig, inputs: AuxHeatInputs) -> AuxHeatDecision:
    """Decide whether aux heat *should* be active right now.

    The transition timing (head off → settle → aux on) is handled by
    :class:`AuxHeatRuntime`; this function only answers the policy
    question. It uses hysteresis when ``inputs.currently_active`` is
    True so a marginal recovery does not immediately drop aux heat.
    """
    if not cfg.enabled or cfg.device_entity_id is None:
        return AuxHeatDecision(False, AuxHeatTrigger.NONE, None)

    if inputs.intent_mode is not HVACMode.HEAT:
        return AuxHeatDecision(False, AuxHeatTrigger.NONE, None)

    if inputs.currently_active:
        floor_ok = (
            inputs.safety_floor is None
            or inputs.effective_temp is None
            or inputs.effective_temp
            >= inputs.safety_floor + cfg.safety_floor_margin + cfg.deactivation_margin
        )
        outdoor_ok = (
            cfg.outdoor_lockout_temp is None
            or inputs.outdoor_temp is None
            or inputs.outdoor_temp
            >= cfg.outdoor_lockout_temp + cfg.deactivation_margin
        )
        head_ok = inputs.head_available or not cfg.fault_on_head_unavailable
        if floor_ok and outdoor_ok and head_ok:
            return AuxHeatDecision(False, AuxHeatTrigger.NONE, None)
        # Stay on; pick the most-relevant trigger to keep showing.
        trigger = _dominant_trigger(cfg, inputs)
        return AuxHeatDecision(True, trigger, _MESSAGES.get(trigger))

    trigger = _first_active_trigger(cfg, inputs)
    if trigger is AuxHeatTrigger.NONE:
        return AuxHeatDecision(False, AuxHeatTrigger.NONE, None)
    return AuxHeatDecision(True, trigger, _MESSAGES[trigger])


def _first_active_trigger(
    cfg: AuxHeatConfig, inputs: AuxHeatInputs
) -> AuxHeatTrigger:
    if cfg.fault_on_head_unavailable and not inputs.head_available:
        return AuxHeatTrigger.HEAD_FAULT
    if (
        cfg.outdoor_lockout_temp is not None
        and inputs.outdoor_temp is not None
        and inputs.outdoor_temp <= cfg.outdoor_lockout_temp
    ):
        return AuxHeatTrigger.OUTDOOR_LOCKOUT
    if (
        inputs.safety_floor is not None
        and inputs.effective_temp is not None
        and inputs.effective_temp <= inputs.safety_floor + cfg.safety_floor_margin
    ):
        return AuxHeatTrigger.SAFETY_FLOOR_NEAR
    return AuxHeatTrigger.NONE


def _dominant_trigger(cfg: AuxHeatConfig, inputs: AuxHeatInputs) -> AuxHeatTrigger:
    """Pick which trigger to surface while aux heat stays latched on.

    During hysteresis it is possible for ``_first_active_trigger`` to
    return ``NONE`` (no trigger is *currently* firing but conditions
    haven't cleared the deactivation margin yet). In that case we
    surface ``SAFETY_FLOOR_NEAR`` as a sensible default so the operator
    still sees a friendly message.
    """
    trigger = _first_active_trigger(cfg, inputs)
    if trigger is AuxHeatTrigger.NONE:
        return AuxHeatTrigger.SAFETY_FLOOR_NEAR
    return trigger


@dataclass
class AuxHeatStepOutcome:
    """Per-tick outcome the coordinator acts upon."""

    aux_on: bool                 # whether to dispatch aux device on/off
    force_head_off: bool         # whether to override the head dispatch with OFF
    in_settling: bool            # whether we are mid-transition to aux-on
    decision: AuxHeatDecision    # forwarded for diagnostics


@dataclass
class AuxHeatRuntime:
    """Per-zone state machine managing the head→settle→aux transition."""

    config: AuxHeatConfig
    actual_active: bool = False
    pending_since: datetime | None = None

    def step(
        self, decision: AuxHeatDecision, now: datetime
    ) -> AuxHeatStepOutcome:
        if not decision.desired_active:
            if self.actual_active:
                self.actual_active = False
            self.pending_since = None
            return AuxHeatStepOutcome(
                aux_on=False,
                force_head_off=False,
                in_settling=False,
                decision=decision,
            )

        # decision.desired_active is True
        if self.actual_active:
            return AuxHeatStepOutcome(
                aux_on=True,
                force_head_off=True,
                in_settling=False,
                decision=decision,
            )

        # Transitioning OFF -> ON: enforce settle delay.
        if self.pending_since is None:
            self.pending_since = now
            return AuxHeatStepOutcome(
                aux_on=False,
                force_head_off=True,
                in_settling=True,
                decision=decision,
            )

        if now - self.pending_since >= timedelta(seconds=self.config.settle_seconds):
            self.actual_active = True
            self.pending_since = None
            return AuxHeatStepOutcome(
                aux_on=True,
                force_head_off=True,
                in_settling=False,
                decision=decision,
            )

        return AuxHeatStepOutcome(
            aux_on=False,
            force_head_off=True,
            in_settling=True,
            decision=decision,
        )
