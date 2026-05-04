"""UI configuration flow for the Multi-Split Zone Controller.

The flow collects exactly one *compressor group* per HA config entry,
matching the integration's "one outdoor unit per entry" architecture
(see ``__init__.py``). Multiple entries can coexist with each other
and with YAML-declared groups — they share the same coordinator
registry and arbitration semantics.

## Flow shape

The flow accumulates a YAML-compatible group dict in
``self._group_data`` and round-trips it through
:func:`config_schema.parse_group_dict` for validation, so UI-built
groups go through *exactly* the same validators (incl. the
default-incompatible-mode-pair merge) as YAML-built ones. This keeps
the two configuration sources behaviourally identical.

::

  user
    ↓
  group       (group_id, name, update_interval)
    ↓
  compat      (incompatible mode pairs, defaults toggle, head-state opt-in)
    ↓
  zone_menu   ── "add zone" ──→ zone_basic → zone_fusion → zone_safety
    ↑                                                          │
    │                                                          ↓
    │                                          zone_advanced_menu
    │                                                          │
    │                            ┌──── occupancy / setback / fan / aux ────┐
    │                            ↓                                          │
    └────────────────────────────┴──────────────────────────────────────────┘
      "finish" → create entry

## Scope

Phase B exposes the *common* surface area (per the user's choice):

* group basics + compatibility + per-zone basics, fusion, safety,
  occupancy, setback, fan, aux_heat
* pre-conditioning, humidity scoring, psychrometric scoring stay
  YAML-only for now and can be added later via an "advanced YAML
  paste" step or further forms

Validation uses the same voluptuous schema as YAML by serialising the
collected dict through :func:`parse_group_dict` *before* writing the
config entry. A validation error rolls back to the offending step.
"""

from __future__ import annotations

import copy
import logging
from typing import Any

import voluptuous as vol
import yaml
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from .config_schema import parse_group_dict
from .const import (
    CONF_ALWAYS_ASSERT_HEAD_STATE,
    CONF_AUX_DEVICE_ENTITY_ID,
    CONF_AUX_DEVICE_TYPE,
    CONF_AUX_ENABLED,
    CONF_AUX_HEAT,
    CONF_AUX_OUTDOOR_LOCKOUT_TEMP,
    CONF_AUX_OUTDOOR_TEMP_SENSOR,
    CONF_DEFAULT_TARGET,
    CONF_DISABLE_DEFAULT_INCOMPATIBLE_MODE_PAIRS,
    CONF_EXTERNAL_HUMIDITY_SENSORS,
    CONF_EXTERNAL_TEMP_SENSORS,
    CONF_FAN,
    CONF_FAN_ENABLED,
    CONF_FAN_ENTITY_ID,
    CONF_FAN_LOCKOUT_ENTITY,
    CONF_FUSION,
    CONF_GROUP_ID,
    CONF_HEAD_CLIMATE,
    CONF_HEAD_TEMP_SENSOR,
    CONF_INCOMPATIBLE_MODE_PAIRS,
    CONF_MAX_TEMP,
    CONF_MIN_TEMP,
    CONF_NAME,
    CONF_OCCUPANCY,
    CONF_OCCUPANCY_KIND,
    CONF_OCCUPANCY_LINGER_MINUTES,
    CONF_OCCUPANCY_SOURCES,
    CONF_SAFETY,
    CONF_SETBACK,
    CONF_SETBACK_OFFSET_COOL,
    CONF_SETBACK_OFFSET_HEAT,
    CONF_STRATEGY,
    CONF_UPDATE_INTERVAL,
    CONF_ZONE_ID,
    CONF_ZONES,
    DEFAULT_UPDATE_INTERVAL_SECONDS,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Form-helper utilities
# ---------------------------------------------------------------------------

# All HVAC modes the user can pick from when listing incompatible pairs.
# Mirrors the modes the managed climate entity advertises.
_HVAC_MODE_CHOICES = ("off", "heat", "cool", "auto", "fan_only", "dry")

_FUSION_STRATEGY_CHOICES = (
    "head_only",
    "external_preferred",
    "weighted_blend",
    "average_of_externals",
    "occupancy_weighted",
)

_OCCUPANCY_KIND_CHOICES = (
    "presence",
    "calendar",
    "schedule",
    "manual_override",
)


def _entity_selector(domains: list[str]) -> selector.EntitySelector:
    return selector.EntitySelector(
        selector.EntitySelectorConfig(domain=domains)
    )


def _select_one(options: tuple[str, ...]) -> selector.SelectSelector:
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=list(options),
            mode=selector.SelectSelectorMode.DROPDOWN,
        )
    )


def _select_many(options: tuple[str, ...]) -> selector.SelectSelector:
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=list(options),
            multiple=True,
            mode=selector.SelectSelectorMode.LIST,
        )
    )


def _yaml_object_selector() -> selector.ObjectSelector:
    """A native YAML editor in HA's UI.

    HA's frontend renders an ``ObjectSelector`` field as a syntax-
    highlighted YAML textarea. The value comes back as a plain Python
    dict / list — no string round-trip needed.
    """
    return selector.ObjectSelector(selector.ObjectSelectorConfig())


def _dump_yaml(data: dict[str, Any]) -> str:
    """Render a config dict as YAML for read-only preview text.

    ``sort_keys=False`` preserves insertion order so previews look
    similar to the YAML the user might paste back into
    ``configuration.yaml``. Block style is used (rather than the
    flow ``{...}`` style) for readability.
    """
    return yaml.safe_dump(data, sort_keys=False, default_flow_style=False)


def _slugify_id(text: str) -> str:
    """Cheap slugifier for default ids derived from a friendly name.

    Real config flows usually let the user override; this is a hint
    placeholder to make the form less intimidating.
    """
    out = []
    last_underscore = False
    for ch in text.lower().strip():
        if ch.isalnum():
            out.append(ch)
            last_underscore = False
        elif not last_underscore and out:
            out.append("_")
            last_underscore = True
    return "".join(out).strip("_")


# ---------------------------------------------------------------------------
# Config flow
# ---------------------------------------------------------------------------


class MultiSplitZoneConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Linear, stateful flow that builds one compressor group per entry."""

    VERSION = 1

    def __init__(self) -> None:
        # Group-level dict, accumulated step by step. Same shape as a
        # single entry under YAML's ``groups:`` list so we can validate
        # and reload it through the existing schema.
        self._group_data: dict[str, Any] = {}
        # Completed zones in the current flow run.
        self._zones: list[dict[str, Any]] = []
        # Partially-built zone being collected by the zone_* steps.
        self._current_zone: dict[str, Any] = {}

    def _group_id_already_running(self, group_id: str) -> bool:
        """True if a group with this id is already loaded by any source.

        Looks at the integration's shared coordinator registry (see
        ``__init__.py``), which catches both YAML-declared and other
        config-entry-declared groups in one check.
        """
        bucket = self.hass.data.get(DOMAIN, {})
        coordinators = bucket.get("coordinators", {}) if bucket else {}
        return group_id in coordinators

    # ---- step 1: user (alias for "group basics") ----

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        return await self.async_step_group(user_input)

    # ---- step 2: group basics ----

    async def async_step_group(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Collect group_id, friendly name, and tick interval.

        ``group_id`` doubles as the flow's ``unique_id`` so HA refuses
        a second entry that would collide with an existing UI- or
        YAML-declared group.
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            group_id = user_input[CONF_GROUP_ID].strip()
            if not group_id.isidentifier():
                errors[CONF_GROUP_ID] = "invalid_id"
            elif self._group_id_already_running(group_id):
                # ``unique_id`` only catches UI-vs-UI collisions. The
                # runtime coordinator registry catches the UI-vs-YAML
                # case (a group with this id is already loaded from
                # configuration.yaml) so the user is warned *now*
                # rather than after walking the entire flow.
                errors[CONF_GROUP_ID] = "duplicate_group_id"
            else:
                await self.async_set_unique_id(group_id)
                self._abort_if_unique_id_configured()
                self._group_data[CONF_GROUP_ID] = group_id
                self._group_data[CONF_NAME] = user_input[CONF_NAME].strip()
                self._group_data[CONF_UPDATE_INTERVAL] = user_input[
                    CONF_UPDATE_INTERVAL
                ]
                return await self.async_step_compat()

        schema = vol.Schema(
            {
                vol.Required(CONF_GROUP_ID): str,
                vol.Required(CONF_NAME): str,
                vol.Required(
                    CONF_UPDATE_INTERVAL,
                    default=DEFAULT_UPDATE_INTERVAL_SECONDS,
                ): vol.All(int, vol.Range(min=5, max=300)),
            }
        )
        return self.async_show_form(
            step_id="group", data_schema=schema, errors=errors
        )

    # ---- step 3: compatibility ----

    async def async_step_compat(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Configure incompatible mode pairs at the group level.

        Defaults to the integration baseline (``heat<->cool`` and
        ``heat<->fan_only``). Users can add additional pairs via a
        ``mode_a / mode_b`` selector pair, or opt out of the
        baseline entirely via ``disable_default_incompatible_mode_pairs``.
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            extra_pairs: list[list[str]] = []
            mode_a = user_input.get("extra_pair_mode_a") or ""
            mode_b = user_input.get("extra_pair_mode_b") or ""
            if mode_a and mode_b:
                if mode_a == mode_b:
                    errors["extra_pair_mode_b"] = "modes_must_differ"
                else:
                    extra_pairs.append([mode_a, mode_b])
            elif mode_a or mode_b:
                # Asymmetrically filled — a clearer error than
                # silently dropping the input.
                errors["extra_pair_mode_b"] = "both_modes_required"

            if not errors:
                self._group_data[CONF_INCOMPATIBLE_MODE_PAIRS] = extra_pairs
                self._group_data[
                    CONF_DISABLE_DEFAULT_INCOMPATIBLE_MODE_PAIRS
                ] = bool(
                    user_input.get(
                        CONF_DISABLE_DEFAULT_INCOMPATIBLE_MODE_PAIRS, False
                    )
                )
                return await self.async_step_zone_menu()

        schema = vol.Schema(
            {
                vol.Optional("extra_pair_mode_a", default=""): _select_one(
                    ("",) + _HVAC_MODE_CHOICES
                ),
                vol.Optional("extra_pair_mode_b", default=""): _select_one(
                    ("",) + _HVAC_MODE_CHOICES
                ),
                vol.Optional(
                    CONF_DISABLE_DEFAULT_INCOMPATIBLE_MODE_PAIRS,
                    default=False,
                ): bool,
            }
        )
        return self.async_show_form(
            step_id="compat", data_schema=schema, errors=errors
        )

    # ---- step 4: zone menu ----

    async def async_step_zone_menu(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Add another zone, edit the raw YAML, or move to the confirm step.

        The form-driven flow only covers the "common" knob set; the
        ``edit_advanced_yaml`` choice opens a free-form YAML editor
        that reaches the YAML-only options (pre-conditioning, humidity
        scoring, multi-sensor fusion, etc.).
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            choice = user_input["next"]
            if choice == "add_zone":
                self._current_zone = {}
                return await self.async_step_zone_basic()
            if choice == "edit_advanced_yaml":
                return await self.async_step_advanced_yaml()
            if choice == "finish":
                if not self._zones:
                    errors["next"] = "need_at_least_one_zone"
                else:
                    return await self.async_step_confirm()

        choices = ("add_zone", "edit_advanced_yaml", "finish")
        schema = vol.Schema(
            {
                vol.Required("next", default="add_zone"): _select_one(choices),
            }
        )
        zones_added_descr = (
            f"{len(self._zones)} zone(s) added: "
            + ", ".join(z[CONF_ZONE_ID] for z in self._zones)
            if self._zones
            else "No zones added yet."
        )
        return self.async_show_form(
            step_id="zone_menu",
            data_schema=schema,
            errors=errors,
            description_placeholders={"zones_added": zones_added_descr},
        )

    # ---- per-zone steps ----

    async def async_step_zone_basic(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Per-zone basics: id, name, head climate entity, target/limits."""
        errors: dict[str, str] = {}

        if user_input is not None:
            zone_id = user_input[CONF_ZONE_ID].strip()
            if not zone_id.isidentifier():
                errors[CONF_ZONE_ID] = "invalid_id"
            elif any(z[CONF_ZONE_ID] == zone_id for z in self._zones):
                errors[CONF_ZONE_ID] = "duplicate_zone_id"
            elif (
                user_input[CONF_MIN_TEMP] >= user_input[CONF_MAX_TEMP]
            ):
                errors[CONF_MIN_TEMP] = "min_must_be_below_max"
            else:
                self._current_zone[CONF_ZONE_ID] = zone_id
                self._current_zone[CONF_NAME] = user_input[CONF_NAME].strip()
                self._current_zone[CONF_HEAD_CLIMATE] = user_input[
                    CONF_HEAD_CLIMATE
                ]
                self._current_zone[CONF_DEFAULT_TARGET] = user_input[
                    CONF_DEFAULT_TARGET
                ]
                self._current_zone[CONF_MIN_TEMP] = user_input[CONF_MIN_TEMP]
                self._current_zone[CONF_MAX_TEMP] = user_input[CONF_MAX_TEMP]
                self._current_zone[CONF_ALWAYS_ASSERT_HEAD_STATE] = bool(
                    user_input.get(CONF_ALWAYS_ASSERT_HEAD_STATE, False)
                )
                return await self.async_step_zone_fusion()

        schema = vol.Schema(
            {
                vol.Required(CONF_ZONE_ID): str,
                vol.Required(CONF_NAME): str,
                vol.Required(CONF_HEAD_CLIMATE): _entity_selector(["climate"]),
                vol.Required(CONF_DEFAULT_TARGET, default=21.0): vol.All(
                    vol.Coerce(float), vol.Range(min=5.0, max=40.0)
                ),
                vol.Required(CONF_MIN_TEMP, default=16.0): vol.Coerce(float),
                vol.Required(CONF_MAX_TEMP, default=30.0): vol.Coerce(float),
                vol.Optional(
                    CONF_ALWAYS_ASSERT_HEAD_STATE, default=False
                ): bool,
            }
        )
        return self.async_show_form(
            step_id="zone_basic", data_schema=schema, errors=errors
        )

    async def async_step_zone_fusion(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Pick the fusion strategy and the head/external sensors.

        The UI exposes a single optional external sensor; users who
        need multiple sensors per zone (with weighting) can edit the
        entry's YAML or use the options flow once it grows that
        ability.
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            fusion: dict[str, Any] = {
                CONF_STRATEGY: user_input[CONF_STRATEGY],
            }
            head_sensor = user_input.get(CONF_HEAD_TEMP_SENSOR) or None
            external_sensor = user_input.get("external_temp_sensor") or None
            # The UI used to expose ``head_humidity_sensor`` here, but
            # very few real mini-split heads ship a usable humidity
            # reading; nearly everyone wires up a separate room
            # hygrometer. Persist the picked entity into
            # ``external_humidity_sensors`` so it goes through the
            # same fusion path as the room temperature sensor.
            external_humidity = (
                user_input.get("external_humidity_sensor") or None
            )
            if head_sensor:
                fusion[CONF_HEAD_TEMP_SENSOR] = head_sensor
            if external_humidity:
                fusion[CONF_EXTERNAL_HUMIDITY_SENSORS] = [external_humidity]
            if external_sensor:
                fusion[CONF_EXTERNAL_TEMP_SENSORS] = [external_sensor]

            strategy = user_input[CONF_STRATEGY]
            if (
                strategy
                in ("external_preferred", "weighted_blend", "average_of_externals")
                and not external_sensor
            ):
                errors["external_temp_sensor"] = (
                    "external_sensor_required_for_strategy"
                )
            elif strategy == "head_only" and not head_sensor:
                errors[CONF_HEAD_TEMP_SENSOR] = "head_sensor_required_for_strategy"
            else:
                self._current_zone[CONF_FUSION] = fusion
                return await self.async_step_zone_safety()

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_STRATEGY, default="external_preferred"
                ): _select_one(_FUSION_STRATEGY_CHOICES),
                vol.Optional(CONF_HEAD_TEMP_SENSOR): _entity_selector(
                    ["climate", "sensor"]
                ),
                vol.Optional("external_temp_sensor"): _entity_selector(
                    ["sensor"]
                ),
                vol.Optional("external_humidity_sensor"): _entity_selector(
                    ["sensor"]
                ),
            }
        )
        return self.async_show_form(
            step_id="zone_fusion", data_schema=schema, errors=errors
        )

    async def async_step_zone_safety(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Hard floor / ceiling enforced regardless of user intent."""
        errors: dict[str, str] = {}

        if user_input is not None:
            min_t = user_input.get(CONF_MIN_TEMP)
            max_t = user_input.get(CONF_MAX_TEMP)
            if (
                min_t is not None
                and max_t is not None
                and min_t >= max_t
            ):
                errors[CONF_MIN_TEMP] = "min_must_be_below_max"
            else:
                safety: dict[str, Any] = {}
                if min_t is not None:
                    safety[CONF_MIN_TEMP] = min_t
                if max_t is not None:
                    safety[CONF_MAX_TEMP] = max_t
                if safety:
                    self._current_zone[CONF_SAFETY] = safety
                return await self.async_step_zone_advanced_menu()

        schema = vol.Schema(
            {
                vol.Optional(CONF_MIN_TEMP): vol.Coerce(float),
                vol.Optional(CONF_MAX_TEMP): vol.Coerce(float),
            }
        )
        return self.async_show_form(
            step_id="zone_safety", data_schema=schema, errors=errors
        )

    async def async_step_zone_advanced_menu(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Pick which optional zone features to configure (or finish zone).

        Each menu pick routes to a focused sub-step; selecting "done"
        commits the current zone into ``self._zones`` and returns to
        the group-level zone menu.
        """
        if user_input is not None:
            choice = user_input["section"]
            if choice == "done":
                # Finalise this zone and return to the group menu.
                self._zones.append(self._current_zone)
                self._current_zone = {}
                return await self.async_step_zone_menu()
            if choice == "occupancy":
                return await self.async_step_zone_occupancy()
            if choice == "setback":
                return await self.async_step_zone_setback()
            if choice == "fan":
                return await self.async_step_zone_fan()
            if choice == "aux_heat":
                return await self.async_step_zone_aux()

        configured = sorted(
            k for k in (CONF_OCCUPANCY, CONF_SETBACK, CONF_FAN, CONF_AUX_HEAT)
            if k in self._current_zone
        )
        descr = (
            "Already configured: " + ", ".join(configured)
            if configured
            else "No optional sections configured for this zone yet."
        )
        schema = vol.Schema(
            {
                vol.Required("section", default="done"): _select_one(
                    (
                        "done",
                        "occupancy",
                        "setback",
                        "fan",
                        "aux_heat",
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="zone_advanced_menu",
            data_schema=schema,
            description_placeholders={"configured": descr},
        )

    async def async_step_zone_occupancy(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """A single occupancy source (kind + entity), plus linger time.

        Multi-source configurations stay YAML-only for now to keep the
        form small; users with complex occupancy can either add the
        zone via YAML or extend it later via the options flow.
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            entity_id = user_input.get("source_entity_id") or None
            kind = user_input.get(CONF_OCCUPANCY_KIND, "presence")
            sources: list[dict[str, Any]] = []
            if entity_id:
                sources.append({"entity_id": entity_id, "kind": kind})
            occupancy: dict[str, Any] = {
                CONF_OCCUPANCY_LINGER_MINUTES: user_input[
                    CONF_OCCUPANCY_LINGER_MINUTES
                ],
                CONF_OCCUPANCY_SOURCES: sources,
            }
            self._current_zone[CONF_OCCUPANCY] = occupancy
            return await self.async_step_zone_advanced_menu()

        schema = vol.Schema(
            {
                vol.Optional("source_entity_id"): _entity_selector(
                    ["binary_sensor", "person", "input_boolean", "calendar"]
                ),
                vol.Optional(
                    CONF_OCCUPANCY_KIND, default="presence"
                ): _select_one(_OCCUPANCY_KIND_CHOICES),
                vol.Required(
                    CONF_OCCUPANCY_LINGER_MINUTES, default=15.0
                ): vol.All(vol.Coerce(float), vol.Range(min=0, max=240)),
            }
        )
        return self.async_show_form(
            step_id="zone_occupancy", data_schema=schema, errors=errors
        )

    async def async_step_zone_setback(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Setback offsets applied while occupancy state is UNOCCUPIED."""
        if user_input is not None:
            self._current_zone[CONF_SETBACK] = {
                CONF_SETBACK_OFFSET_HEAT: user_input[CONF_SETBACK_OFFSET_HEAT],
                CONF_SETBACK_OFFSET_COOL: user_input[CONF_SETBACK_OFFSET_COOL],
            }
            return await self.async_step_zone_advanced_menu()

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SETBACK_OFFSET_HEAT, default=3.0
                ): vol.All(vol.Coerce(float), vol.Range(min=0, max=15)),
                vol.Required(
                    CONF_SETBACK_OFFSET_COOL, default=3.0
                ): vol.All(vol.Coerce(float), vol.Range(min=0, max=15)),
            }
        )
        return self.async_show_form(step_id="zone_setback", data_schema=schema)

    async def async_step_zone_fan(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Optional ceiling-fan integration for this zone."""
        if user_input is not None:
            fan: dict[str, Any] = {
                CONF_FAN_ENABLED: bool(user_input.get(CONF_FAN_ENABLED, False)),
            }
            entity = user_input.get(CONF_FAN_ENTITY_ID) or None
            lockout = user_input.get(CONF_FAN_LOCKOUT_ENTITY) or None
            if entity:
                fan[CONF_FAN_ENTITY_ID] = entity
            if lockout:
                fan[CONF_FAN_LOCKOUT_ENTITY] = lockout
            self._current_zone[CONF_FAN] = fan
            return await self.async_step_zone_advanced_menu()

        schema = vol.Schema(
            {
                vol.Required(CONF_FAN_ENABLED, default=False): bool,
                vol.Optional(CONF_FAN_ENTITY_ID): _entity_selector(["fan"]),
                vol.Optional(CONF_FAN_LOCKOUT_ENTITY): _entity_selector(
                    ["binary_sensor", "input_boolean"]
                ),
            }
        )
        return self.async_show_form(step_id="zone_fan", data_schema=schema)

    async def async_step_zone_aux(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Optional auxiliary (resistive) heat for this zone."""
        if user_input is not None:
            aux: dict[str, Any] = {
                CONF_AUX_ENABLED: bool(user_input.get(CONF_AUX_ENABLED, True)),
                CONF_AUX_DEVICE_TYPE: user_input[CONF_AUX_DEVICE_TYPE],
            }
            device = user_input.get(CONF_AUX_DEVICE_ENTITY_ID) or None
            outdoor_sensor = user_input.get(CONF_AUX_OUTDOOR_TEMP_SENSOR) or None
            outdoor_lockout = user_input.get(CONF_AUX_OUTDOOR_LOCKOUT_TEMP)
            if device:
                aux[CONF_AUX_DEVICE_ENTITY_ID] = device
            if outdoor_sensor:
                aux[CONF_AUX_OUTDOOR_TEMP_SENSOR] = outdoor_sensor
            if outdoor_lockout is not None:
                aux[CONF_AUX_OUTDOOR_LOCKOUT_TEMP] = outdoor_lockout
            self._current_zone[CONF_AUX_HEAT] = aux
            return await self.async_step_zone_advanced_menu()

        schema = vol.Schema(
            {
                vol.Required(CONF_AUX_ENABLED, default=True): bool,
                vol.Required(
                    CONF_AUX_DEVICE_TYPE, default="switch"
                ): _select_one(("switch", "climate")),
                vol.Optional(CONF_AUX_DEVICE_ENTITY_ID): _entity_selector(
                    ["switch", "climate"]
                ),
                vol.Optional(CONF_AUX_OUTDOOR_TEMP_SENSOR): _entity_selector(
                    ["sensor", "input_number"]
                ),
                vol.Optional(CONF_AUX_OUTDOOR_LOCKOUT_TEMP): vol.Coerce(float),
            }
        )
        return self.async_show_form(step_id="zone_aux", data_schema=schema)

    # ---- advanced YAML (create flow) ----

    async def async_step_advanced_yaml(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Edit the entire group dict as raw YAML.

        The current accumulated state (group fields + zones) is
        rendered into HA's YAML editor. On submit we replace
        ``self._group_data`` and ``self._zones`` wholesale with the
        edited dict, validate via ``parse_group_dict``, and return to
        the zone menu (or surface the error here).
        """
        errors: dict[str, str] = {}
        # Snapshot of the current state, used as the editor's initial
        # value. We deep-copy so the user editing the form doesn't
        # mutate our in-flight state until they actually submit.
        current = copy.deepcopy(self._group_data)
        current[CONF_ZONES] = copy.deepcopy(self._zones)

        if user_input is not None:
            new_dict = user_input.get("group_yaml") or {}
            # Defensive: HA's ObjectSelector returns dict/list/scalar;
            # anything else (e.g. None) is treated as "no change".
            if not isinstance(new_dict, dict):
                errors["group_yaml"] = "yaml_must_be_a_mapping"
            else:
                try:
                    parse_group_dict(new_dict)
                except vol.Invalid as err:
                    _LOGGER.error("Advanced-YAML validation failed: %s", err)
                    errors["group_yaml"] = "invalid_group"
                    # Show the error message to the user in addition to
                    # the generic key.
                    self.context["last_yaml_error"] = str(err)
                else:
                    # Adopt the new dict wholesale. Group-level fields
                    # live at the top level; zones live in CONF_ZONES.
                    new_zones = list(new_dict.pop(CONF_ZONES, []))
                    self._group_data = new_dict
                    self._zones = new_zones
                    return await self.async_step_zone_menu()

        schema = vol.Schema(
            {
                vol.Required(
                    "group_yaml", default=current
                ): _yaml_object_selector(),
            }
        )
        return self.async_show_form(
            step_id="advanced_yaml",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "last_error": self.context.get("last_yaml_error", ""),
            },
        )

    # ---- confirm (preview + create) ----

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Read-only YAML preview followed by create / back-to-edit.

        Renders the accumulated group dict as YAML so the user can
        eyeball what's about to be persisted. Picking ``create``
        round-trips through validation one last time and creates the
        entry; picking ``edit_yaml`` jumps to the advanced editor;
        picking ``back_to_menu`` returns to the zone menu for further
        per-zone tweaks.
        """
        if user_input is not None:
            choice = user_input["next"]
            if choice == "create":
                return await self._async_create_entry_from_state()
            if choice == "edit_yaml":
                return await self.async_step_advanced_yaml()
            if choice == "back_to_menu":
                return await self.async_step_zone_menu()

        preview = copy.deepcopy(self._group_data)
        preview[CONF_ZONES] = list(self._zones)
        yaml_text = _dump_yaml(preview)

        schema = vol.Schema(
            {
                vol.Required("next", default="create"): _select_one(
                    ("create", "edit_yaml", "back_to_menu")
                ),
            }
        )
        return self.async_show_form(
            step_id="confirm",
            data_schema=schema,
            description_placeholders={"yaml": yaml_text},
        )

    # ---- finalisation ----

    async def _async_create_entry_from_state(self) -> FlowResult:
        """Validate the accumulated dict and create the config entry.

        Validation goes through the *same* parser the YAML path uses,
        so any rejected configuration produces the same error a YAML
        deployment would have hit. On rejection we surface the
        message in the zone menu rather than leaving the user stuck
        in an aborted flow.
        """
        self._group_data[CONF_ZONES] = self._zones
        try:
            group = parse_group_dict(self._group_data)
        except vol.Invalid as err:
            _LOGGER.error("Config-flow validation failed: %s", err)
            errors = {"base": "invalid_group"}
            schema = vol.Schema(
                {
                    vol.Required("next", default="finish"): _select_one(
                        ("add_zone", "finish")
                    ),
                }
            )
            return self.async_show_form(
                step_id="zone_menu",
                data_schema=schema,
                errors=errors,
                description_placeholders={
                    "zones_added": str(err),
                },
            )
        return self.async_create_entry(
            title=group.name, data=self._group_data
        )

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> "MultiSplitZoneOptionsFlow":
        """Surface the options flow to HA's UI."""
        return MultiSplitZoneOptionsFlow(config_entry)


# ---------------------------------------------------------------------------
# Options flow
# ---------------------------------------------------------------------------


class MultiSplitZoneOptionsFlow(config_entries.OptionsFlow):
    """Edit a subset of frequently-tuned settings on an existing entry.

    Scope (per Phase C in the roadmap):

    * **Group level** — ``update_interval``
    * **Per-zone** — ``safety`` floor/ceiling, ``setback`` offsets,
      ``occupancy.linger_minutes``, ``fan.enabled``, ``aux_heat.enabled``

    Anything structural (add/remove a zone, change a fusion strategy,
    swap the head_climate entity) still requires a reconfigure (recreate
    the entry). That keeps this flow small and protects users from
    accidentally invalidating the entry's identity.

    The flow writes its result into ``entry.options``. ``entry.data``
    is left untouched. ``entry_to_group_config`` shallow-merges
    options over data, which means whole-key replacements are simple
    and per-zone edits compose a fresh ``zones`` list before
    submitting.
    """

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._entry = config_entry
        # Working copy of the merged dict that we mutate as the user
        # walks the options forms. We snapshot data + options at start
        # so cancellation / step-back doesn't corrupt the persisted entry.
        self._working: dict[str, Any] = {
            **config_entry.data,
            **(config_entry.options or {}),
        }

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Top-level menu: edit group settings, a zone, or the raw YAML.

        The ``__yaml__`` choice opens a free-form YAML editor over the
        full merged ``data | options`` dict, so power users can reach
        knobs the basic-tier per-zone form doesn't expose.
        """
        zones: list[dict[str, Any]] = list(self._working.get(CONF_ZONES, []))
        if user_input is not None:
            choice = user_input["target"]
            if choice == "__group__":
                return await self.async_step_group_settings()
            if choice == "__yaml__":
                return await self.async_step_advanced_yaml()
            # Zone target choices look like "zone:<zone_id>".
            if choice.startswith("zone:"):
                self._editing_zone_id = choice.removeprefix("zone:")
                return await self.async_step_zone_settings()

        zone_choices = [f"zone:{z[CONF_ZONE_ID]}" for z in zones]
        choices = ("__group__", "__yaml__", *zone_choices)
        # Read-only preview so users can audit what's currently
        # persisted before choosing where to dive in.
        yaml_preview = _dump_yaml(self._working)
        schema = vol.Schema(
            {
                vol.Required("target", default="__group__"): _select_one(
                    choices
                ),
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            description_placeholders={
                "zone_count": str(len(zones)),
                "yaml": yaml_preview,
            },
        )

    async def async_step_advanced_yaml(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Edit the entry as raw YAML (advanced).

        The whole working dict is rendered into HA's YAML editor, the
        user submits a replacement, and we save it via the same
        ``_save`` codepath as the basic forms — meaning validation
        also goes through ``parse_group_dict``.
        """
        errors: dict[str, str] = {}
        if user_input is not None:
            new_dict = user_input.get("group_yaml") or {}
            if not isinstance(new_dict, dict):
                errors["group_yaml"] = "yaml_must_be_a_mapping"
            else:
                try:
                    parse_group_dict(new_dict)
                except vol.Invalid as err:
                    _LOGGER.error(
                        "Advanced-YAML options validation failed: %s", err
                    )
                    errors["group_yaml"] = "invalid_group"
                else:
                    self._working = new_dict
                    return await self._save()

        schema = vol.Schema(
            {
                vol.Required(
                    "group_yaml", default=copy.deepcopy(self._working)
                ): _yaml_object_selector(),
            }
        )
        return self.async_show_form(
            step_id="advanced_yaml",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_group_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Group-level tunables that don't change identity or topology."""
        if user_input is not None:
            self._working[CONF_UPDATE_INTERVAL] = user_input[
                CONF_UPDATE_INTERVAL
            ]
            return await self._save()

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_UPDATE_INTERVAL,
                    default=self._working.get(
                        CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL_SECONDS
                    ),
                ): vol.All(int, vol.Range(min=5, max=300)),
            }
        )
        return self.async_show_form(
            step_id="group_settings", data_schema=schema
        )

    async def async_step_zone_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Per-zone tunables. ``self._editing_zone_id`` selects the target."""
        zones: list[dict[str, Any]] = list(self._working.get(CONF_ZONES, []))
        idx, zone = self._find_zone(zones, self._editing_zone_id)

        errors: dict[str, str] = {}
        if user_input is not None:
            safety_min = user_input.get(CONF_MIN_TEMP)
            safety_max = user_input.get(CONF_MAX_TEMP)
            if (
                safety_min is not None
                and safety_max is not None
                and safety_min >= safety_max
            ):
                errors[CONF_MIN_TEMP] = "min_must_be_below_max"
            else:
                # Build a fresh copy of the zone dict so HA stores
                # the new options as a clean payload (no shared refs
                # with the entry's existing dict).
                new_zone = dict(zone)
                # Safety
                safety = dict(new_zone.get(CONF_SAFETY, {}))
                if safety_min is not None:
                    safety[CONF_MIN_TEMP] = safety_min
                else:
                    safety.pop(CONF_MIN_TEMP, None)
                if safety_max is not None:
                    safety[CONF_MAX_TEMP] = safety_max
                else:
                    safety.pop(CONF_MAX_TEMP, None)
                if safety:
                    new_zone[CONF_SAFETY] = safety
                else:
                    new_zone.pop(CONF_SAFETY, None)
                # Setback
                new_zone[CONF_SETBACK] = {
                    CONF_SETBACK_OFFSET_HEAT: user_input[
                        CONF_SETBACK_OFFSET_HEAT
                    ],
                    CONF_SETBACK_OFFSET_COOL: user_input[
                        CONF_SETBACK_OFFSET_COOL
                    ],
                }
                # Occupancy linger (preserve existing sources untouched)
                occupancy = dict(new_zone.get(CONF_OCCUPANCY, {}))
                occupancy[CONF_OCCUPANCY_LINGER_MINUTES] = user_input[
                    CONF_OCCUPANCY_LINGER_MINUTES
                ]
                new_zone[CONF_OCCUPANCY] = occupancy
                # Fan / aux enable toggles only — keep entity ids etc.
                # We use distinct form field names ("fan_enabled" /
                # "aux_enabled") because CONF_FAN_ENABLED and
                # CONF_AUX_ENABLED are both the literal string
                # "enabled" (their nested YAML namespaces keep them
                # apart there) and would otherwise collide in this
                # flat form.
                fan = dict(new_zone.get(CONF_FAN, {}))
                fan[CONF_FAN_ENABLED] = bool(user_input.get("fan_enabled", False))
                new_zone[CONF_FAN] = fan
                aux = dict(new_zone.get(CONF_AUX_HEAT, {}))
                aux[CONF_AUX_ENABLED] = bool(user_input.get("aux_enabled", False))
                new_zone[CONF_AUX_HEAT] = aux

                zones[idx] = new_zone
                self._working[CONF_ZONES] = zones
                return await self._save()

        existing_safety = zone.get(CONF_SAFETY, {}) or {}
        existing_setback = zone.get(CONF_SETBACK, {}) or {}
        existing_occupancy = zone.get(CONF_OCCUPANCY, {}) or {}
        existing_fan = zone.get(CONF_FAN, {}) or {}
        existing_aux = zone.get(CONF_AUX_HEAT, {}) or {}

        schema_dict: dict[Any, Any] = {}
        if CONF_MIN_TEMP in existing_safety:
            schema_dict[
                vol.Optional(CONF_MIN_TEMP, default=existing_safety[CONF_MIN_TEMP])
            ] = vol.Coerce(float)
        else:
            schema_dict[vol.Optional(CONF_MIN_TEMP)] = vol.Coerce(float)
        if CONF_MAX_TEMP in existing_safety:
            schema_dict[
                vol.Optional(CONF_MAX_TEMP, default=existing_safety[CONF_MAX_TEMP])
            ] = vol.Coerce(float)
        else:
            schema_dict[vol.Optional(CONF_MAX_TEMP)] = vol.Coerce(float)
        schema_dict[
            vol.Required(
                CONF_SETBACK_OFFSET_HEAT,
                default=existing_setback.get(CONF_SETBACK_OFFSET_HEAT, 3.0),
            )
        ] = vol.All(vol.Coerce(float), vol.Range(min=0, max=15))
        schema_dict[
            vol.Required(
                CONF_SETBACK_OFFSET_COOL,
                default=existing_setback.get(CONF_SETBACK_OFFSET_COOL, 3.0),
            )
        ] = vol.All(vol.Coerce(float), vol.Range(min=0, max=15))
        schema_dict[
            vol.Required(
                CONF_OCCUPANCY_LINGER_MINUTES,
                default=existing_occupancy.get(
                    CONF_OCCUPANCY_LINGER_MINUTES, 15.0
                ),
            )
        ] = vol.All(vol.Coerce(float), vol.Range(min=0, max=240))
        schema_dict[
            vol.Required(
                "fan_enabled",
                default=bool(existing_fan.get(CONF_FAN_ENABLED, False)),
            )
        ] = bool
        schema_dict[
            vol.Required(
                "aux_enabled",
                default=bool(existing_aux.get(CONF_AUX_ENABLED, False)),
            )
        ] = bool

        return self.async_show_form(
            step_id="zone_settings",
            data_schema=vol.Schema(schema_dict),
            errors=errors,
            description_placeholders={
                "zone_id": self._editing_zone_id,
                "zone_name": zone.get(CONF_NAME, self._editing_zone_id),
            },
        )

    # ---- helpers ----

    @staticmethod
    def _find_zone(
        zones: list[dict[str, Any]], zone_id: str
    ) -> tuple[int, dict[str, Any]]:
        for i, z in enumerate(zones):
            if z[CONF_ZONE_ID] == zone_id:
                return i, z
        raise KeyError(zone_id)

    async def _save(self) -> FlowResult:
        """Persist the working dict by validating and writing options.

        We validate by round-tripping through ``parse_group_dict`` so a
        bad combination (e.g. invalid mode pair, contradictory safety
        limits) is caught here rather than at the next coordinator
        tick.
        """
        try:
            parse_group_dict(self._working)
        except vol.Invalid as err:
            _LOGGER.error("Options-flow validation failed: %s", err)
            return self.async_abort(
                reason="invalid_options",
                description_placeholders={"detail": str(err)},
            )
        return self.async_create_entry(title="", data=self._working)

