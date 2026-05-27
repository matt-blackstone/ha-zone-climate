# Home Assistant Multi-Split Zone Controller

[![Validate](https://github.com/matt-blackstone/ha-zone-climate/actions/workflows/validate.yml/badge.svg)](https://github.com/matt-blackstone/ha-zone-climate/actions/workflows/validate.yml)
[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)

Custom Home Assistant integration that hides multi-split mini-split
restrictions, sensor fusion, occupancy-aware setback, fan coordination,
auxiliary heat, psychrometric comfort, and optional physical display
thermostats behind a single managed `climate` entity per zone.

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
| 9 | Physical display thermostat support | complete |

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
  display_mapping.py     # Physical thermostat mode/fan-mode translation
  display_dispatch.py    # Service calls to display thermostat climate entities
  display_listener.py    # Wall-thermostat edits → managed zone intent
  display_echo.py        # Echo-ignore window for bidirectional sync
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

hacs.json                # HACS custom-integration metadata (display name,
                         # min HA version, README rendering)
.github/workflows/
  validate.yml           # HACS + hassfest validation on PR / push / nightly

scripts/
  validate.sh            # Run the same HACS + hassfest checks locally
                         # via Docker, before pushing
```

### What HACS ships vs what stays in the repo

The repo intentionally segregates **shipping content** from
**dev infrastructure**:

| Path | Shipped to user installs by HACS? |
|---|---|
| `custom_components/multisplit_zone_controller/` | **Yes** — copied verbatim into `<user_config>/custom_components/multisplit_zone_controller/`. This is the only directory HACS install touches. |
| `tests/`, `scripts/`, `config/`, `config-ui/`, `docker-compose*.yml`, `pyproject.toml` | No — repo-only dev scaffolding. |
| `*.md` (README, INSTALL, SETUP, ADVANCED, design-overview) | No — rendered on the GitHub repo page (and HACS shows the README inline via `render_readme: true`), but never copied to user installs. |
| `.github/`, `hacs.json`, `.gitignore` | No — repo-tooling metadata, never relevant to a user's HA. |

So **anything you add to `custom_components/multisplit_zone_controller/`
ships to every user**; anything outside that directory stays in the
repo. Be deliberate about which side of that line a new file goes
on. If in doubt: dev fixtures, sandbox configs, test helpers,
documentation, and CI configuration all stay outside; only runtime
integration code, the manifest, and the translations go inside.

Hassfest scans the *entire* repo for `manifest.json` files, so the
test-only `tests/integration/recording_climate/manifest.json` is also
validated by CI even though HACS will never ship it. Keep its
manifest valid (key order, required fields) for that reason — it
doesn't reach end users, but it does need to pass hassfest.

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
captures upstream service calls, and drives scenario tests through
the live REST API. They cover integration loading, basic head
dispatch, display thermostat mirroring, compressor-group arbitration,
safety overrides, occupancy setback, hidden auxiliary heat
(activation and recovery), and diagnostic sensors.

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

## Installation

See [`INSTALL.md`](INSTALL.md) for the full installation guide,
covering all four Home Assistant install types (HAOS, Supervised,
Container, Core) and three install methods (HACS as a custom
repository, manual release zip, git clone). Quick version: add
`https://github.com/matt-blackstone/ha-zone-climate` as a custom
repository in HACS, download, restart HA. Then continue at
*Configuration* below.

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

## Feature roadmap

User-visible features that are designed and queued for implementation
but not yet built. Each entry captures the agreed shape so a future
implementation pass (or contributor) doesn't have to re-litigate the
design decisions.

### Physical display thermostat support (Phase 9, complete in v0.2.0)

Lets a wall-mounted physical thermostat (e.g. **Honeywell T6 Pro
Z-Wave**) act as a **bidirectional UI mirror** of a zone's managed
climate entity — without it being the actuator. Lower-friction
than reaching for a phone for every setpoint nudge, especially in
multi-occupant households.

The physical thermostat is configured (or wired) **not** to control
a load. The mini-split head still does the work; the wall device
just displays state and accepts physical adjustments.

**Three I/O surfaces**, no changes to arbitration / safety logic:

| Surface | Direction | Module | Pattern |
|---|---|---|---|
| Display dispatcher | Managed → T6 | `display_dispatch.py` | Mirrors `dispatch.py` (rate-limited, capability-checked, optional `always_assert`). Calls `climate.set_hvac_mode`, `climate.set_fan_mode`, `climate.set_temperature` on each configured display thermostat. |
| Display listener | T6 → managed | `display_listener.py` | `async_track_state_change_event` per configured display, but the handler **filters inside** to only act when user-intent attributes change (`hvac_mode`, `fan_mode`, `temperature` / `target_temp_low` / `target_temp_high`). Updates in-memory user intent on the coordinator; the **next natural coordinator tick** propagates it to the head. Echo-ignore window (~2 s) prevents our own writes from feeding back as user input. |
| Sensor contribution | T6 → fusion | `coordinator.py` | Auto-pulls `current_temperature` / `current_humidity` from the climate entity's attributes. Low default weight (~0.3) reflects Z-Wave's slow update cadence. |

**Mode and fan-mode mapping** (the trickiest part — the T6 exposes
*two* user-facing knobs, and `fan_only` / `dry` semantically live in
the fan column, not the mode column):

*Managed → T6:*

| Managed mode | T6 mode | T6 fan |
|---|---|---|
| `off` | `off` | `Auto low` |
| `heat` | `heat` | `Auto low` |
| `cool` | `cool` | `Auto low` |
| `fan_only` | `off` | `Low` |
| `dry` | `cool` | `Auto low` |
| `auto` | `auto` if supported; otherwise sync status becomes `stale` | `Auto low` |

*T6 → Managed:*

| T6 mode | T6 fan | Managed mode |
|---|---|---|
| `off` | `Auto low` / `auto` | `off` |
| `off` | `Low` / `Circulation` / `on` / `circulate` | `fan_only` |
| `heat` / `cool` / `auto` | any | same |
| `em_heat` | any | `heat` (integration's own aux logic still decides whether aux fires) |

**Other agreed decisions:**

- **Auto-mode setpoint:** managed stays single-setpoint. Honeywell
  T6 Pro Z-Wave units observed through `zwave_js` expose only
  `off` / `heat` / `cool`, so a managed `auto` request is reported
  as display-sync `stale` rather than forcing an invented dual
  setpoint shape onto the managed entity.
- **Sensor weighting:** default `temperature_weight: 0.3`,
  `humidity_weight: 0.3` (vs `1.0` for fast WiFi/Zigbee sensors)
  because Z-Wave thermostats typically update every 30-60 s.
  Users can still also list separate `sensor.*` entities under
  `external_temp_sensors:` for different per-source weighting.
- **Setpoint range clamping:** when the managed setpoint falls
  outside the T6's `min_temp` / `max_temp` (typically 50-99 °F),
  clamp on the T6 (it shows the clamped value) and surface the
  truth via the managed entity's `status_message` so the actual
  state is visible in HA's own UI.
- **Conflict resolution:** last writer wins, with a ~2 s
  echo-ignore window. The physical action naturally arrives
  later than a mobile-UI tap, so this favours the person at
  the wall without needing a special rule.
- **Listener strategy:** event-driven for user-intent
  attributes (mode, fan_mode, setpoint); fusion data
  (`current_temperature`, `current_humidity`) stays
  poll-driven through the existing coordinator tick. The
  handler is a single `async_track_state_change_event`
  subscription per display that filters internally by
  comparing `old_state.attributes` to `new_state.attributes`
  on the user-intent subset — cheap (~10 lines), self-
  documenting, no extra wakeups from fusion-data updates
  feeding through the listener.
- **Refresh timing:** the listener does **not** trigger an
  immediate coordinator refresh. It updates in-memory user
  intent and lets the next natural coordinator tick (≤ 5 s)
  carry it through to the head. Trade-off: setpoint nudges
  on the T6 take up to 5 s to translate into mini-split
  action, but we avoid spamming `coordinator.async_request_refresh()`
  on every wall-thermostat poke. If field experience shows
  5 s feels sluggish, promoting to `async_request_refresh()`
  (or a debounced version of it) is a one-line change with
  no public API impact.
- **Multi-display zones:** `display_thermostats:` is a list, so
  one zone can mirror to N wall devices (e.g. master bedroom
  + ensuite both display the bedroom-zone state).
- **Scope:** T6-Pro-Z-Wave is the validated reference. The
  dispatcher / listener are written against the generic HA
  `climate` interface so Ecobee, Sinopé, etc. can be added as a
  "verify their mode/fan-mode value strings" exercise rather
  than a re-architecture.

**Config shape:**

```yaml
zones:
  - zone_id: living
    head_climate: climate.upstream_mini_split_living
    display_thermostats:
      - entity_id: climate.honeywell_t6_pro_living
        sync_setpoint: true            # default
        sync_mode: true                # default
        sync_fan_mode: true            # default
        always_assert: false           # default; see always_assert_head_state
        contribute_temperature: true   # default
        contribute_humidity: true      # default
        temperature_weight: 0.3        # low because Z-Wave updates slowly
        humidity_weight: 0.3
        auto_fan_mode: "Auto low"      # Honeywell T6 / zwave_js default
        fan_only_fan_mode: "Low"
        circulate_fan_mode: "Circulation"
```

**Documentation impact:**

- `ADVANCED.md → Display thermostats` has the worked example,
  mode-mapping table, T6 wiring caveats (configure for no-load
  operation; disable T6 schedules if you'd rather schedule from HA).
- `INSTALL.md` calls out that this is opt-in and does not change
  any existing behaviour.
- A diagnostic sensor per display reports `synced` / `stale` /
  `unreachable`, with attributes showing desired mode, fan mode,
  target, clamping, and last error.

## Releasing

The integration is distributed via HACS as a custom repository. Cutting
a new release is two files + two git commands.

### Pre-flight

* CI is green on `main` (the `Validate` workflow runs HACS validation
  + hassfest on every push). To run the same checks **locally before
  pushing**, use `scripts/validate.sh` — it runs both validators in
  the exact Docker images CI uses:

  ```bash
  scripts/validate.sh                # hassfest + HACS
  scripts/validate.sh hassfest       # hassfest only (no token needed)
  scripts/validate.sh hacs           # HACS only (needs GITHUB_TOKEN
                                     # or gh auth login)
  ```

  Hassfest catches the structural issues (manifest key order, missing
  CONFIG_SCHEMA, translation key drift); HACS catches store-specific
  issues (hacs.json shape, version field presence). The HACS validator
  needs a GitHub token because it queries the GitHub API for repo
  metadata and brand registration; hassfest needs no credentials.
* `custom_components/multisplit_zone_controller/manifest.json` →
  bump `"version"` to the new semver.
* `hacs.json` → no version bump needed (HACS reads the version from
  the manifest), but **do** bump the `"homeassistant"` floor here
  if the release uses a newly-required HA API. Keep the two values
  consistent with what `INSTALL.md` documents.
* Update `INSTALL.md`'s *Prerequisites* table if the HA-version
  floor changed.

### Tag and release

```bash
# Replace v0.X.Y with the version you bumped manifest.json to.
git tag v0.X.Y
git push origin v0.X.Y

gh release create v0.X.Y \
  --title "v0.X.Y" \
  --notes-file CHANGELOG-v0.X.Y.md   # or --generate-notes
```

HACS picks up new releases within a few minutes of the tag landing
on GitHub. Users on existing installs will see *Update available* in
their HACS UI on the next refresh cycle.

### Three sources of truth, kept in sync

| File | What it advertises | Used by |
|---|---|---|
| `manifest.json` `"version"` | The integration version Home Assistant reports in *Settings → Devices & Services → ⋮ → System info* and in `hass diagnostics`. | Home Assistant Core, HACS update detection. |
| `hacs.json` `"homeassistant"` | Minimum HA version HACS will allow the integration to be installed on. | HACS catalog filter. |
| `INSTALL.md` *Prerequisites* table | Human-readable install requirements. | End users reading the docs. |

A version bump should normally only touch `manifest.json`. The HA
floor only moves when you start using a new HA API; when it does,
update all three in the same commit.

### What if there's never been a release yet?

HACS *can* install custom integrations off the default branch (`main`)
when no tagged releases exist — it falls back to the latest commit's
manifest version. Tagged releases are still preferred because users
get update notifications. The display-thermostat feature is intended
for `v0.2.0`.
