# Multi-Split Zone Controller — Setup Guide

This integration supports **two coexisting configuration paths**:

| Path | Best for | Notes |
|---|---|---|
| **UI** (Settings → Devices & Services → Add Integration) | First-time setup, casual users, single-group installs | One config entry per outdoor unit (compressor group). Common knobs available as forms; advanced knobs reachable via a built-in YAML editor. |
| **YAML** (`configuration.yaml`) | Multi-group installs, version-controlled config, reaching every knob the schema supports | One `multisplit_zone_controller:` block can declare any number of groups. Edit-and-reload workflow. |

Both paths can be active at the same time **as long as their `group_id`s
don't collide.** A YAML group and a UI entry sharing the same `group_id`
will be rejected (the second one to load loses).

## Prerequisites — what to set up first

> **Have you installed the integration yet?** This guide assumes
> the `multisplit_zone_controller` integration is already present
> in your `<config>/custom_components/` directory and Home Assistant
> has been restarted. If not, start with [`INSTALL.md`](INSTALL.md)
> and come back here.

Before configuring the integration, your Home Assistant should already
have working entities for everything it will read or command:

1. **One upstream `climate` entity per indoor head.** This is the entity
   the integration *commands*. It must accept `climate.set_hvac_mode`
   and `climate.set_temperature` service calls. Examples: a Smart IR
   `climate.living_head`, a `generic_thermostat`, a vendor integration's
   built-in head entity. (The dev sandbox ships
   `climate.fake_head_living/bedroom/office` for testing.)
2. **Optional room temperature sensors** (`sensor.*` with
   `device_class: temperature`) — needed for `external_preferred` or
   `weighted` fusion. The head's own temperature is usually pulled from
   its `climate` entity attribute, but a separate `sensor.*` reading the
   head's temperature is also fine.
3. **Optional humidity sensors**, occupancy sources
   (`binary_sensor.occupancy`, `device_tracker.*`, `person.*`,
   `input_boolean.*`), ceiling fan entities (`fan.*`), aux-heat devices
   (`switch.*` or a separate `climate.*`), and an outdoor temperature
   sensor (for aux-heat outdoor lockout).

If you're just exploring, the **dev sandboxes below** provide all of
these as fakes you can poke from the UI.

---

## Path A — UI configuration

### 1. Spin up the UI sandbox (optional — for testing only)

```bash
docker compose -f docker-compose.ui.yml up -d
# UI on http://localhost:8125
docker compose -f docker-compose.ui.yml logs -f homeassistant
```

This sandbox is a clean HA install with **no `multisplit_zone_controller:`
block**, so the integration starts empty and waits to be added from the
UI. The fake heads / sensors from `config/packages/fixtures.yaml` are
preloaded so you have entities to point the flow at.

> First run through the standard HA onboarding wizard (create
> owner account, pick a unit system, skip the analytics question).

### 2. Add the integration

1. Settings → Devices & Services → **Add Integration**
2. Search for **"Multi-Split Zone Controller"**
3. The flow will walk through these steps:

| Step | What you're configuring |
|---|---|
| **Group** | `group_id` (permanent — short slug like `outdoor_unit_1`), friendly name, update interval |
| **Compatibility** | Add up to one extra incompatible mode pair (heat↔cool and heat↔fan_only are blocked by default). Tick *Disable defaults* only if your hardware genuinely supports those combinations. |
| **Zone menu** | Loop: pick `add_zone` to add another head, `edit_advanced_yaml` to drop into the YAML editor, or `finish` to review and create. |
| → **Zone basics** | `zone_id`, friendly name, the upstream head's `climate` entity, default target temp, safety floor/ceiling, optional "always re-issue commands" toggle (for IR-controlled heads that may drift). |
| → **Sensor fusion** | Strategy + the head temp source + any external room sensors / humidity sensor. |
| → **Safety** | Floor & ceiling enforced regardless of user intent (independent of the basic-step defaults). |
| → **Advanced (optional)** | Per-zone occupancy source, setback offsets, ceiling fan, auxiliary heat. Skippable. |
| **Confirm** | Read-only YAML preview of the entire group. From here you can `create`, `edit_yaml` (full editor), or `back_to_menu` for further per-zone tweaks. |

### 3. Edit later (Options flow)

Settings → Devices & Services → *Multi-Split Zone Controller* → **Configure**

The options flow exposes:

* **Group settings** — update interval.
* **Per-zone settings** — safety floor / ceiling, setback offsets,
  occupancy linger, fan/aux enable toggles.
* **`__yaml__`** — opens HA's built-in YAML editor over the **entire
  entry**. Use this to reach knobs the basic forms don't expose
  (pre-conditioning, humidity scoring, multi-sensor fusion with
  weights, occupancy expected-windows, psychrometric scoring, etc.).
  The init step also shows a read-only YAML preview of the current
  configuration.

> **Structural changes** (add/remove a zone, change a zone's
> `head_climate` or fusion strategy entity) are only possible through
> the YAML editor or by deleting and re-creating the entry.

### 4. Reach knobs the form doesn't expose

The form-driven create flow intentionally covers the "common" knob set.
If you need any of:

* `preconditioning:` (lead-time auto pre-heat / pre-cool)
* `humidity:` (setpoint, dehumidification preference, psych scoring)
* `fusion.weights` / `fusion.calibrations` (weighted multi-sensor)
* `occupancy.expected_windows` / `occupancy.kind: device_tracker_with_zone`
* `psychrometric:` (PMV, SET, enthalpy, cooling-effect scoring)
* Multiple incompatible mode pairs

…use the **`edit_advanced_yaml`** choice during create, or the
**`__yaml__`** choice in options. The dict structure is identical to
one entry in the YAML schema's `groups:` list (see Path B below).

For *how* each advanced section actually works (knobs, defaults,
worked examples, and tuning guidance), see
[`ADVANCED.md`](ADVANCED.md).

---

## Path B — YAML configuration

### 1. Spin up the YAML sandbox (optional — for testing only)

```bash
docker compose up -d
# UI on http://localhost:8123
docker compose logs -f homeassistant
```

This sandbox preloads `config/configuration.yaml` (which **does** contain
a `multisplit_zone_controller:` block) plus the same fixtures.

### 2. Add the block to `configuration.yaml`

```yaml
multisplit_zone_controller:
  groups:
    - group_id: outdoor_unit_1
      name: "Outdoor Unit 1"
      update_interval: 15

      # heat<->cool and heat<->fan_only are treated as incompatible by
      # default. Add extra pairs here, or set
      # `disable_default_incompatible_mode_pairs: true` to opt out.
      incompatible_mode_pairs:
        - ["heat", "dry"]

      zones:
        - zone_id: living
          name: "Living Room"
          head_climate: climate.upstream_living_head
          default_target_temperature: 21.0

          fusion:
            strategy: external_preferred       # head_only | external_preferred | weighted
            head_temp_sensor: sensor.upstream_living_head_temp
            external_temp_sensors:
              - sensor.living_room_thermometer
            external_humidity_sensors:
              - sensor.living_room_humidity

          safety:
            min_temp: 8.0
            max_temp: 32.0

          # Optional advanced sections — see design-overview.md for
          # the full list of supported keys.
          # occupancy: { ... }
          # setback:   { ... }
          # fan:       { ... }
          # aux_heat:  { ... }
          # preconditioning: { ... }
          # humidity:  { ... }
          # psychrometric: { ... }
```

### 3. Apply changes

`Developer Tools → YAML → Reload Multi-Split Zone Controller` (or
restart HA). The integration validates the entire block; on failure HA
will log the schema error and skip setup until you fix it.

---

## Coexistence rules

* **Multiple YAML groups** in one block are fine — each gets its own
  coordinator and entities.
* **Multiple UI entries** are fine — each is one outdoor unit /
  compressor group.
* **Mixing** YAML and UI entries is fine **as long as `group_id`s are
  unique across the union of both sources.** The integration registers
  every active `group_id` in `hass.data` and the config flow refuses
  duplicates with the `duplicate_group_id` error.
* If you accidentally configure the same `group_id` from both YAML and
  the UI, whichever loaded first wins; the other surfaces an error and
  is skipped.

## Migrating between paths

* **YAML → UI:** delete the group block from `configuration.yaml`,
  reload, then re-create the entry through the UI. (No automatic
  migration — `group_id`s would collide otherwise.)
* **UI → YAML:** open Options → `__yaml__`, copy the YAML out of the
  editor, paste it under `multisplit_zone_controller.groups:` in
  `configuration.yaml`, then delete the UI entry.

## Common entity targets — what each field expects

| Field | Domain | What it must do |
|---|---|---|
| `head_climate` | `climate.*` | Accept `set_hvac_mode` (off/heat/cool/auto/fan_only/dry as relevant) and `set_temperature` |
| `fusion.head_temp_sensor` | `sensor.*` or `climate.*` | Provide the head's local thermistor reading. If a `climate.*` is given, its `current_temperature` attribute is used. |
| `fusion.external_temp_sensors` | `sensor.*` (`device_class: temperature`) | Independent room thermometers |
| `fusion.external_humidity_sensors` | `sensor.*` (`device_class: humidity`) | Independent room hygrometers |
| `display_thermostats[].entity_id` | `climate.*` | Optional wall display thermostat. Must accept `set_hvac_mode`, `set_temperature`, and ideally `set_fan_mode`; Honeywell T6 Pro Z-Wave is the reference device. |
| `occupancy.source_entity_id` | `binary_sensor.*`, `device_tracker.*`, `person.*`, `input_boolean.*` | Truthy = occupied |
| `fan.fan_entity_id` | `fan.*` | Standard `fan.turn_on/off` (and ideally `set_percentage`) |
| `aux_heat.device_entity_id` | `switch.*` or `climate.*` | A resistive heater the integration can turn on transparently |
| `aux_heat.outdoor_temp_sensor` | `sensor.*` (`device_class: temperature`) | Used for the aux-heat outdoor lockout |

The full reference for every supported key lives in
[`design-overview.md`](design-overview.md).
