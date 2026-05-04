# Home Assistant Multi-Split Zone Controller

Custom Home Assistant integration that hides multi-split mini-split
restrictions, sensor fusion, occupancy-aware setback, fan coordination,
auxiliary heat, and (eventually) psychrometric comfort behind a single
managed `climate` entity per zone.

The full architectural design is in [`design-overview.md`](design-overview.md);
the staged implementation roadmap is in
[`.cursor/plans/multisplit_zone_controller_roadmap_*.plan.md`](.cursor/plans/).

## Status

| Phase | Title | Status |
|------:|-------|--------|
| 1 | MVP: managed climate, arbitration, basic fusion, safety | complete |
| 2 | Sensor fusion completeness | complete |
| 3 | Occupancy states & setback | complete |
| 4 | Pre-conditioning | complete |
| 5 | Coordinated fan orchestration | complete |
| 6 | Hidden auxiliary heat | complete |
| 7 | Humidity-aware scoring | complete |
| 8 | Full psychrometric comfort | complete |

## Repository layout

```
custom_components/multisplit_zone_controller/
  __init__.py            # YAML setup, defers all HA imports
  manifest.json
  const.py
  models.py              # Frozen dataclasses; pure-Python, no HA imports
  config_schema.py       # voluptuous → typed model parser
  sensor_fusion.py       # head_only, external_preferred (Phase 1)
  safety.py              # Floor/ceiling clamp
  arbitration.py         # Brute-force compatible-subset selection
  occupancy.py           # Confirmed/Expected/Recent/Unoccupied resolver + setback
  preconditioning.py     # Rate estimator, lead-time, EXPECTED timeout
  comfort.py             # Fan apparent-temp credit, fan command, humidity scoring
  fan_proxy.py           # Service calls to upstream fan entities
  aux_heat.py            # Aux-heat policy + transition state machine
  aux_heat_dispatch.py   # Service calls to upstream switch / climate aux device
  psychrometrics.py      # Sat. vapour pressure, humidity ratio, enthalpy, PMV, comfort temp
  coordinator.py         # DataUpdateCoordinator per group; orchestrates everything
  dispatch.py            # Service calls to upstream head climates
  climate.py             # Managed user-facing thermostat
  sensor.py              # Diagnostic sensors (effective T/RH, block reason, occupancy, lead time, ...)
  binary_sensor.py       # em_heat_active per zone

tests/                   # Pure-pytest unit tests (no HA required)
tests/integration/       # Dockerised end-to-end tests (real HA container)
  recording_climate/     # Test-only HA component that records service calls
  ha_config/             # Throw-away HA config exercised by the e2e suite
  docker-compose.test.yml
  ha_client.py           # REST + onboarding helper used by the tests
  conftest.py            # Per-session container fixture, per-test isolation
  test_e2e.py            # Twelve scenario tests covering all phases
config/                  # Manual dev-loop Home Assistant sandbox config
docker-compose.yml       # ghcr.io/home-assistant/home-assistant:stable
```

## Running the test suite

The pure-logic modules have no `homeassistant` dependency and can be
exercised with plain pytest:

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest
```

## Running the dockerised end-to-end suite

`tests/integration/` boots a real Home Assistant container, mounts our
custom integration plus a tiny `recording_climate` test helper that
captures upstream service calls, and drives twelve scenario tests
through the live REST API. They cover integration loading, basic head
dispatch, compressor-group arbitration, safety overrides, occupancy
setback, hidden auxiliary heat (activation and recovery), and
diagnostic sensors.

The tests are gated behind an environment variable so they don't run
on plain `pytest` invocations:

```bash
.venv/bin/pip install -e '.[dev,e2e]'
MSZ_RUN_E2E=1 .venv/bin/python -m pytest tests/integration/ -v
```

Optional toggles:

* `MSZ_KEEP_E2E_CONTAINER=1` — leave the container running after the
  suite finishes (useful when iterating on a single failing test).
* `MSZ_HA_BASE_URL=http://host:port` — point the test client at a
  long-lived HA instance instead of the throw-away container.

The container runs on host port `8124` (the manual dev sandbox uses
`8123`) so both can be up simultaneously.

## Running the Home Assistant sandboxes

Two side-by-side dev sandboxes — pick (or run both at once):

```bash
# YAML-driven sandbox: integration is pre-configured via
# config/configuration.yaml. Best for iterating on coordinator /
# entity behaviour against the dev fixtures.
docker compose -f docker-compose.yml up -d
#  → http://localhost:8123

# UI-driven sandbox: integration starts un-configured so you can
# walk through the config flow and exercise the UI surface area.
# Same fake heads/sensors fixtures, mounted read-only from the
# YAML sandbox's packages/ dir.
docker compose -f docker-compose.ui.yml up -d
#  → http://localhost:8125

docker compose -f docker-compose.yml    logs -f homeassistant
docker compose -f docker-compose.ui.yml logs -f homeassistant
```

Both sandboxes mount `./custom_components` into
`/config/custom_components` and ship fixture entities in
`config/packages/fixtures.yaml` standing in for real heads, room
sensors, and humidity sensors. Adjust the `input_number.*` helpers from
the UI to drive scenarios while watching the managed
`climate.living_room` / `climate.bedroom` / `climate.office` entities
react. Setup walkthrough for both sandboxes lives in
[`SETUP.md`](SETUP.md).

## Configuration

The integration supports two coexisting setup paths:

* **UI** — Settings → Devices & Services → Add Integration →
  *"Multi-Split Zone Controller"*. One config entry per outdoor
  unit, with form-driven steps for the common knobs and a built-in
  YAML editor for everything else.
* **YAML** — a `multisplit_zone_controller:` block in
  `configuration.yaml` (see `config/configuration.yaml` for a working
  example). Best for multi-group installs and version-controlled
  config.

Both paths can be active at the same time as long as `group_id`s are
unique across them.

**See [`SETUP.md`](SETUP.md) for the full setup guide** (UI walkthrough,
YAML reference, prerequisites, fixture sandboxes, migration, and field
reference for every entity target).

**See [`ADVANCED.md`](ADVANCED.md)** for a deep dive on the advanced
features the form-driven UI doesn't cover yet — multi-sensor fusion,
multi-source occupancy, pre-conditioning, fan/aux-heat tuning,
humidity-aware scoring, and full psychrometric scoring — including
worked examples for each.

Quick YAML snippet:

```yaml
multisplit_zone_controller:
  groups:
    - group_id: outdoor_unit_1
      name: "Outdoor Unit 1"
      # heat<->cool and heat<->fan_only are treated as incompatible by
      # default; list any additional pairs here, or set
      # `disable_default_incompatible_mode_pairs: true` to opt out of
      # the defaults entirely.
      incompatible_mode_pairs:
        - ["heat", "dry"]
      zones:
        - zone_id: living
          name: "Living Room"
          head_climate: climate.upstream_living_head
          fusion:
            strategy: external_preferred
            head_temp_sensor: sensor.upstream_living_head_temp
            external_temp_sensors:
              - sensor.living_room_thermometer
          safety:
            min_temp: 8.0
            max_temp: 32.0
```

## UI roadmap (known gaps)

The form-driven config flow + basic options flow cover the "common
80%" of the YAML schema. Everything else is reachable today via the
**Advanced YAML editor** (the `edit_advanced_yaml` choice during
create, or the `__yaml__` choice in the options flow), which round-
trips the entire entry through the same `voluptuous` schema as
YAML-declared groups. The TODOs below would replace YAML drops with
proper forms.

For *how* to use any of these YAML-only features today (with worked
examples for each), see [`ADVANCED.md`](ADVANCED.md).

Prioritised by user impact (highest first):

### Tier 1 — frequent reasons to drop into YAML

- [ ] **Multi-source fusion forms.** The form caps each zone at one
  external temp sensor and one room humidity sensor. Add a
  repeating sub-step that lets the user add N sensors per zone with
  per-sensor `weight`, `calibration_offset`, and (for the
  `occupancy_weighted` strategy) `occupancy_source`.
- [ ] **Pre-conditioning form** (`preconditioning:` — Phase 4).
  Auto pre-heat / pre-cool when occupancy goes EXPECTED. All
  fields are YAML-only today: `enabled`,
  `default_heating_rate`, `default_cooling_rate`,
  `max_lead_minutes`, `expected_timeout_minutes`, `rate_alpha`.
- [ ] **Humidity scoring form** (`humidity:` — Phase 7). Latent-
  discomfort weighting and dehumidify-bias bonus, all YAML-only:
  `target_humidity`, `tolerance_band`, `weight`,
  `dehumidify_threshold`, `dehumidify_bonus`.
- [ ] **Multi-source occupancy.** Form currently allows exactly
  one occupancy source per zone. Add support for the multi-source
  resolver (with `confirmed_threshold` / `expected_threshold`
  knobs).
- [ ] **Multi-pair compatibility step.** Form currently allows
  exactly one *extra* incompatible mode pair on top of the
  defaults. Let users add N pairs.

### Tier 2 — fill out partially-exposed sections

- [ ] **Fan form** — expose the missing knobs:
  `direction_in_heat`, `direction_in_cool`,
  `max_speed_pct_occupied`, `max_speed_pct_unoccupied`,
  `max_apparent_temp_credit`, `deviation_per_step`.
- [ ] **Aux-heat form** — expose: `aux_target_temperature`,
  `safety_floor_margin`, `deactivation_margin`,
  `fault_on_head_unavailable`, `settle_seconds`.
- [ ] **Setback form** — expose `unoccupied_comfort_weight` (how
  much an unoccupied zone still influences arbitration scoring).
- [ ] **Zone basics** — expose `target_temp_step` (managed-entity
  setpoint resolution; defaults to 0.5 °C).
- [ ] **Fusion advanced** — expose `head_weight`,
  `stale_after_seconds`, `occupancy_boost`.
- [ ] **Group toggle** — expose `use_psychrometric_scoring`
  (Phase 8 PMV / SET / enthalpy comfort temperature).

### Tier 3 — options-flow editing parity

The basic options flow currently only edits a thin slice
(update_interval, safety floor/ceiling, setback offsets, occupancy
linger, fan/aux enable toggles). Extend it to cover everything
mutated above, plus:

- [ ] Group `name`, `incompatible_mode_pairs`,
  `disable_default_incompatible_mode_pairs`.
- [ ] Per-zone `name`, `default_target_temperature`, managed
  `min_temp`/`max_temp` (distinct from safety),
  `always_assert_head_state`, `target_temp_step`.
- [ ] Per-zone fusion strategy + sensor reassignment.
- [ ] Per-zone occupancy `source_entity_id` / `kind` (today the
  flow can change linger but can't retarget the source).
- [ ] Per-zone fan / aux entity reassignment (today the flow can
  flip enable but can't retarget).

### Tier 4 — structural changes (no UI path at all today)

These require the YAML editor *or* deleting and re-creating the
entry. They're hard to do well via forms because of HA's
"data is immutable, options layered on top" model.

- [ ] **Add a zone** to an existing group post-create.
- [ ] **Remove a zone** from an existing group post-create.
- [ ] **Rename** a `zone_id` (or a `group_id`) without losing
  history. Probably not worth doing — both are intentionally
  permanent identifiers.

### Cross-cutting

- [ ] **Auto-grow `recording_climate` pickers** so the dev sandboxes
  list a curated "test fakes" group at the top of the entity-picker
  drop-downs. Currently the user has to know the entity IDs.
- [ ] **Live YAML preview update**. The confirm step renders a
  YAML preview, but it's static between submits. A live re-render
  on each form change would make the advanced YAML path feel less
  like a separate mode and more like an editable rendering of the
  form.
- [ ] **Translations** — only `en.json` exists. Add at least one
  other locale to validate the translation key surface.

## Hardware-safety roadmap

Gaps that protect the *equipment* rather than the user experience.
Tracked here (rather than in the UI roadmap) because they're
behavioural defaults that should ship enabled, not configuration
exposed in a form.

- [ ] **Changeover-valve thrash protection.** Arbitration is
  stateless across ticks today and will happily flip the group's
  dominant mode (HEAT ↔ COOL) every update interval if per-tick
  scores oscillate around a compatibility boundary. Real heat-pump
  reversing valves are mechanical and rated for a finite number
  of cycles per hour; rapid changeover also stresses the
  compressor (oil migration, liquid slugging risk).

  Design sketch:

  * New `GroupConfig` field `min_changeover_dwell_minutes`. **Default
    needs research before shipping** — older single-stage compressor
    guidance of 5-15 min may be overly conservative for modern
    inverter-driven residential mini-splits. Survey real
    manufacturer spec sheets (Mitsubishi M-Series, Daikin Aurora,
    Fujitsu Halcyon, LG LMU, Senville, Pioneer, MrCool DIY) for
    documented minimum *changeover* intervals (distinct from
    minimum on-time / off-time cycle floors), cross-reference
    ASHRAE / AHRI guidance, and check what value the HA
    `generic_thermostat.min_cycle_duration` community has settled on
    for similar concerns. Picking too high penalises legitimate
    comfort transitions (cool morning → warm afternoon); too low
    defeats the protection. Best guess pre-research: 2-5 min for
    modern inverter multi-splits; 10-15 min for legacy single-stage
    hardware. Probably exposed as a per-group setting so users with
    older equipment can dial it up.
  * Coordinator tracks the group's current dominant mode and the
    timestamp of the last changeover.
  * When `arbitrate()` proposes a winning subset whose dominant
    mode differs from the previous tick's, either (a) re-run
    arbitration with the previous dominant mode pinned until the
    dwell window expires, or (b) only allow the flip when the new
    winning score exceeds the previous-mode-pinned score by a
    configurable hysteresis margin.
  * Surface throttled zones via `block_reason: changeover_throttled`
    and a new `sensor.<group>_changeover_locked` diagnostic so
    users can see when the throttle is engaging.
  * Safety-driven changeovers (floor/ceiling engaged) bypass the
    throttle — protecting the room beats protecting the equipment
    when both are at stake. Aux heat is already the heat-side
    escape hatch; the cool side may need an equivalent
    "emergency cool override" for high-temperature lockout.

  Inline TODO marker lives at the top of `arbitrate()` in
  `custom_components/multisplit_zone_controller/arbitration.py`.
