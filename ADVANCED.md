# Multi-Split Zone Controller — Advanced Configuration

A reference for the YAML-only knobs that the form-driven UI doesn't
yet expose. These features are fully implemented and tested; the
form just doesn't have screens for them yet (see the *UI roadmap* in
[`README.md`](README.md)). Everything below is reachable today via:

* **`configuration.yaml`** — best for version-controlled multi-group
  installs.
* **The Advanced YAML editor in the UI** — pick `edit_advanced_yaml`
  during create or `__yaml__` in the options flow. The editor opens
  HA's native syntax-highlighted YAML widget over the entire group
  dict; on submit the dict is round-tripped through the same
  `voluptuous` schema as YAML, so validation is identical.

> **Convention:** every snippet below shows just the bit being
> documented, with `…` standing in for surrounding fields. To use a
> snippet, paste it into the appropriate slot inside an existing
> group/zone block.

---

## Multi-sensor fusion

The form lets you pick one head temperature source and one room
temperature sensor per zone. The full schema accepts an arbitrary
number of room sensors, with per-sensor weights, calibration
offsets, and (for the `occupancy_weighted` strategy) an
"occupancy gate" that boosts a sensor's weight while a particular
occupancy source is active.

### Sensor shorthand vs full form

```yaml
zones:
  - zone_id: living
    fusion:
      strategy: weighted_blend
      head_temp_sensor: sensor.upstream_living_head_temp
      head_weight: 0.5            # how much the head's reading matters
      stale_after_seconds: 600    # ignore any source unchanged > 10 min
      external_temp_sensors:
        # String shorthand — weight 1.0, no calibration:
        - sensor.living_room_thermometer
        # Full form:
        - entity_id: sensor.living_room_north_wall
          weight: 2.0             # this sensor counts twice as much
          calibration_offset: -0.3  # subtract 0.3 °C to correct drift
        - entity_id: sensor.living_room_south_wall
          weight: 1.0
          calibration_offset: 0.0
      external_humidity_sensors:
        - sensor.living_room_humidity
        - entity_id: sensor.living_room_humidity_2
          weight: 0.5
          calibration_offset: 1.0
```

### The five strategies

| `strategy` | Behaviour |
|---|---|
| `head_only` | Use the head's built-in sensor exclusively. Externals are ignored. Fallback when no room sensor is reliable. |
| `external_preferred` *(default)* | Average all external sensors. Fall back to the head only if every external is unavailable. **Quality** is reported as `degraded` when the head carries the result alone. |
| `weighted_blend` | Weighted average of *both* the head (with `head_weight`) and the externals. Best when the head sensor is "okay" but room sensors are better — use a small head_weight (e.g. 0.2-0.5) so the head nudges the result without dominating it. |
| `average_of_externals` | Plain mean of the externals. Same as `external_preferred` numerically when every external is available, but never includes the head in the average. |
| `occupancy_weighted` | Weighted average where each sensor's weight is **multiplied by `occupancy_boost`** while its `occupancy_source` (a `binary_sensor.*`, `person.*`, etc.) is active. Lets the sensor "nearest the occupant" dominate the blend. Only meaningful with multiple room sensors that each see different sub-zones. |

### `occupancy_weighted` worked example

```yaml
fusion:
  strategy: occupancy_weighted
  occupancy_boost: 4.0   # active sensor's weight × 4
  external_temp_sensors:
    - entity_id: sensor.living_room_couch
      occupancy_source: binary_sensor.couch_occupied
    - entity_id: sensor.living_room_desk
      occupancy_source: binary_sensor.desk_occupied
    - entity_id: sensor.living_room_ambient
      # No occupancy_source → static weight, never boosted
```

When `binary_sensor.couch_occupied` is on, the couch sensor weighs
4 × 1.0 = 4.0 in the blend; the desk and ambient sensors weigh 1.0
each, for a 4 : 1 : 1 split favouring the couch reading.

### Other fusion knobs

| Field | Default | Purpose |
|---|---|---|
| `head_weight` | `1.0` | Head's weight in `weighted_blend` (and as the head term in `occupancy_weighted`). Set to `0` to exclude the head entirely. |
| `stale_after_seconds` | unset | If set, any sensor whose `last_updated` timestamp is older than this is treated as `unavailable` for that tick. Defends against stuck sensors that never report `unavailable`. |
| `occupancy_boost` | `3.0` | Multiplier applied to a sensor's weight while its `occupancy_source` is active. Only used by `occupancy_weighted`. |

### Further reading

* [Wikipedia: Sensor fusion](https://en.wikipedia.org/wiki/Sensor_fusion)
  — concepts behind weighted multi-sensor estimation.
* [Wikipedia: Weighted arithmetic mean](https://en.wikipedia.org/wiki/Weighted_arithmetic_mean)
  — the math powering all four non-`head_only` strategies.
* [Home Assistant: Statistics integration](https://www.home-assistant.io/integrations/statistics/)
  — useful for pre-smoothing a noisy room sensor *before* feeding
  it into `external_temp_sensors`.
* [Home Assistant: Min/Max integration](https://www.home-assistant.io/integrations/min_max/)
  — a simpler fallback when you want the average of N sensors but
  not the per-sensor weight/calibration shape this integration
  offers.
* [Home Assistant: Filter sensor](https://www.home-assistant.io/integrations/filter/)
  — low-pass / outlier filtering for sensors that occasionally
  glitch (alternative to `stale_after_seconds`).

---

## Multi-source occupancy

The form lets you pick one occupancy source per zone. The full
resolver supports any number of sources, each with its own weight
and "kind" (presence vs calendar vs schedule vs manual override),
collapsed into a single confidence in `[0.0, 1.0]` and bucketed into
`CONFIRMED` / `EXPECTED` / `RECENT` / `UNOCCUPIED` against two
configurable thresholds.

```yaml
zones:
  - zone_id: living
    occupancy:
      linger_minutes: 20
      confirmed_threshold: 0.7   # confidence ≥ 0.7 → CONFIRMED
      expected_threshold: 0.4    # 0.4 ≤ confidence < 0.7 → EXPECTED
      sources:
        - entity_id: binary_sensor.living_room_motion
          kind: presence
          weight: 1.0
        - entity_id: person.alice
          kind: presence
          weight: 0.8
        - entity_id: calendar.work_from_home
          kind: calendar
          weight: 0.5      # contributes to EXPECTED but not CONFIRMED alone
        - entity_id: schedule.evenings
          kind: schedule
          weight: 0.4
        - entity_id: input_boolean.guest_mode
          kind: manual_override
          weight: 1.0      # explicit overrides win outright
```

### Resolver math (high level)

* Each *active* source contributes its `weight` to the running
  confidence; inactive sources contribute `0`.
* The total is normalised by the sum of *all* configured weights
  (active + inactive) so missing inputs don't artificially inflate
  the score.
* The four kinds are functionally equivalent for the resolver
  itself; they exist so the diagnostic sensors and future
  pre-conditioning logic can distinguish "occupied right now" (PRESENCE)
  from "expected soon" (CALENDAR / SCHEDULE) signals.
* `MANUAL_OVERRIDE` sources, when active, force the state to
  `CONFIRMED` regardless of the others.

### Threshold tuning

| Threshold | Default | What it controls |
|---|---|---|
| `confirmed_threshold` | `0.7` | Confidence at/above this → `CONFIRMED` (no setback, full priority in arbitration). |
| `expected_threshold` | `0.4` | Confidence at/above this but below `confirmed_threshold` → `EXPECTED` (no setback yet, **pre-conditioning may fire** — see below). |

Below `expected_threshold` the resolver checks `linger_minutes`: if
the zone was `CONFIRMED` recently (within the linger window), it
stays `RECENT` (still no setback, but no pre-conditioning either);
otherwise it goes `UNOCCUPIED` and setback engages.

### Setback advanced knob

```yaml
setback:
  setback_offset_heat: 4.0      # degrees below comfort during UNOCCUPIED HEAT
  setback_offset_cool: 4.0      # degrees above comfort during UNOCCUPIED COOL
  unoccupied_comfort_weight: 0.1  # 0..1
```

`unoccupied_comfort_weight` (default `0.2`) controls how much an
unoccupied zone still influences arbitration. With the default, an
`UNOCCUPIED` zone gets 20% of its normal comfort score weight, so
an occupied zone with similar deviation will out-prioritise it for
the compressor. Set to `0.0` to make unoccupied zones invisible
to arbitration; set to `1.0` to disable the de-weighting (unoccupied
zones compete on equal footing).

### Further reading

* [Home Assistant: Bayesian binary sensor](https://www.home-assistant.io/integrations/bayesian/)
  — alternative way to fuse multiple weak presence signals into
  one binary input you can then use as a single occupancy
  source. Pairs well with the form-driven UI's single-source
  picker if you don't want to drop into YAML.
* [Home Assistant: Schedule helper](https://www.home-assistant.io/integrations/schedule/)
  — provides the `schedule.*` entities that fit the `schedule`
  occupancy kind.
* [Home Assistant: Calendar integrations](https://www.home-assistant.io/integrations/calendar/)
  — overview of the `calendar.*` entities that fit the `calendar`
  occupancy kind (Google Calendar, CalDAV, local calendar, etc.).
* [Home Assistant: Person integration](https://www.home-assistant.io/integrations/person/)
  — combines multiple `device_tracker.*` entities into one
  presence signal suitable for the `presence` occupancy kind.
* [ASHRAE Standard 90.1](https://www.ashrae.org/technical-resources/bookstore/standard-90-1)
  — the standard that codifies occupancy-based setback as a
  baseline energy-conservation measure for commercial HVAC; the
  same principles apply at residential scale.

---

## Pre-conditioning (Phase 4)

Auto pre-heat / pre-cool a zone before occupancy returns. Active
when the occupancy resolver returns `EXPECTED`: the integration
estimates how long it'll take to close the gap from the current
temperature to the comfort setpoint, and starts heating/cooling
that many minutes before expected occupancy.

```yaml
zones:
  - zone_id: living
    preconditioning:
      enabled: true                  # default
      default_heating_rate: 0.5      # °C/min, used until rate is learned
      default_cooling_rate: 0.4      # °C/min
      max_lead_minutes: 60.0         # never pre-condition > this far ahead
      expected_timeout_minutes: 60.0 # if EXPECTED stays this long without
                                     # becoming CONFIRMED, fall back to UNOCCUPIED
      rate_alpha: 0.2                # EMA smoothing factor (0..1)
```

### How the rate is learned

While the dispatched mode is `HEAT`, the integration measures the
observed `Δtemperature / Δtime` between consecutive coordinator
ticks and feeds positive deltas into an exponential moving
average (`new = α·observation + (1−α)·old`). Same for `COOL` (with
sign flipped). The learned rate persists for the duration of the
HA process; on restart it resets to the configured defaults until
new observations re-train it.

| Knob | Default | Effect |
|---|---|---|
| `default_heating_rate` | `0.5 °C/min` | Used as the initial guess and as a fallback whenever no observations have been collected. Tune to your real heat-pump's startup rate; under-estimating means late pre-conditioning, over-estimating means starting too late. |
| `default_cooling_rate` | `0.4 °C/min` | Same for cooling. Most heat pumps cool slightly slower than they heat, hence the lower default. |
| `max_lead_minutes` | `60.0` | Hard cap on how far ahead pre-conditioning can run. Protects against pathological learned rates (e.g. `0.001 °C/min` would otherwise propose hours of lead time). |
| `expected_timeout_minutes` | `60.0` | If a zone stays in `EXPECTED` longer than this without ever flipping to `CONFIRMED`, the resolver demotes it to `UNOCCUPIED` so setback re-engages. Defends against calendar events that don't actually result in someone arriving. |
| `rate_alpha` | `0.2` | EMA smoothing. Lower = more conservative learning (slow to adapt to seasonal change but stable); higher = more reactive (adapts quickly but noisier). |
| `enabled` | `true` | Set to `false` to disable pre-conditioning entirely for this zone. The zone will still spend time in `EXPECTED` (visible in diagnostics) but no early dispatch happens. |

### Diagnostic sensors

When pre-conditioning is enabled, three diagnostic sensors per zone
are populated:

* `sensor.<zone>_predicted_lead_time_minutes` — current lead time
  estimate. `0` when at setpoint; `max_lead_minutes` when the
  rate is too low to meet the deadline.
* `sensor.<zone>_learned_heating_rate` — current EMA value, or
  `unknown` until the first observation lands.
* `sensor.<zone>_learned_cooling_rate` — same for cooling.

Worth watching these for the first few days after deploying a new
zone to confirm the learned rates settle to a sensible value.

### Further reading

* [Wikipedia: Exponential moving average](https://en.wikipedia.org/wiki/Moving_average#Exponential_moving_average)
  — the EMA formula the rate estimator uses, including how
  `rate_alpha` controls reactivity vs stability.
* [Wikipedia: Thermal mass](https://en.wikipedia.org/wiki/Thermal_mass)
  — why pre-conditioning works at all (heavyweight buildings
  benefit most; lightweight apartments often see lead times
  collapse to the dispatcher's update interval).
* [U.S. DOE: Programmable thermostats and setback recovery](https://www.energy.gov/energysaver/thermostats)
  — practical background on why "intelligent recovery" out-performs
  fixed schedules for HVAC efficiency.
* [Home Assistant: Generic Thermostat — `away_mode`](https://www.home-assistant.io/integrations/generic_thermostat/)
  — for comparison, the simplest setback model HA ships with;
  this integration's pre-conditioning is the same idea inverted
  (predict re-occupancy and *un*-set-back early).

---

## Fan advanced (Phase 5)

The form exposes `enabled`, `fan_entity_id`, and the lockout
entity. The full config has six more knobs that shape direction,
speed, and the comfort credit fed back into arbitration.

```yaml
zones:
  - zone_id: living
    fan:
      enabled: true
      fan_entity_id: fan.living_room_ceiling
      lockout_entity_id: input_boolean.fan_silent_mode
      direction_in_heat: reverse        # forward | reverse
      direction_in_cool: forward
      max_speed_pct_occupied: 100       # 0..100
      max_speed_pct_unoccupied: 33      # cap when room is empty
      max_apparent_temp_credit: 1.5     # max "free" comfort °C from fan
      deviation_per_step: 2.0           # deviation that maps to full speed
```

### Direction by mode

Real ceiling fans benefit from running **reverse** (drawing air up,
pushing warm air down the walls) in heat mode and **forward**
(blowing down) in cool mode. The integration commands the
direction whenever it changes; if your fan doesn't support
`fan.set_direction` the call is silently dropped at the dispatcher.

### Speed shaping by occupancy

The dispatched speed is a fraction of `max_speed_pct_*` based on the
deviation:

```
speed_pct = round(min(|deviation| / deviation_per_step, 1.0) × max_speed_pct)
```

* When the zone is `CONFIRMED` / `EXPECTED` / `RECENT`, the cap is
  `max_speed_pct_occupied`.
* When `UNOCCUPIED`, the cap is `max_speed_pct_unoccupied` —
  defaults to 33% for quiet, low-power background air mixing.

### Apparent-temperature credit

While the fan is running and the zone is occupied, the integration
subtracts a comfort credit from the absolute deviation that the
arbitration scorer sees:

```
credit = min(|deviation| / deviation_per_step, 1.0) × max_apparent_temp_credit
```

So a `1.5 °C` credit at full deviation makes the room *score* as if
it were 1.5 °C closer to setpoint. This is what allows a
fan-equipped zone to lose arbitration to a more-deserving zone
without ever feeling worse to the occupant. Crank
`max_apparent_temp_credit` up if you have powerful fans / cool
climates; drop it to `0.0` to disable the comfort-credit feedback
without disabling fan dispatch.

### Lockout entity

When the configured `lockout_entity_id` is `on` (a `binary_sensor`,
`input_boolean`, etc.), the fan is forced off **regardless** of
deviation/occupancy. Useful for "do not run the fan while sleeping"
or "guest mode" overrides.

### Further reading

* [ASHRAE Standard 55](https://en.wikipedia.org/wiki/ASHRAE_55)
  — the thermal-comfort standard that quantifies the cooling
  effect of elevated air movement; sections 5.3.3 and 5.4 cover
  the air-velocity comfort credit this integration approximates.
* [ASHRAE Standard 55 — Section 5.3.3 air-speed comfort credit
  (CBE summary)](https://comfort.cbe.berkeley.edu/) — Berkeley
  CBE's interactive thermal-comfort tool implements the full
  cooling-effect calculation; useful for sanity-checking your
  `max_apparent_temp_credit` value against the real
  psychrometric model.
* [Wikipedia: Ceiling fan — Reverse direction (winter mode)](https://en.wikipedia.org/wiki/Ceiling_fan#Direction_of_rotation)
  — the physics behind `direction_in_heat: reverse` (drawing air
  up to push ceiling-stratified warm air down the walls).
* [Home Assistant: Fan entity — `set_direction`](https://developers.home-assistant.io/docs/core/entity/fan/)
  — the upstream service this integration calls; useful for
  checking whether *your* fan integration actually implements
  the direction service before configuring it.

---

## Display thermostats (Phase 9)

A physical display thermostat is a wall-mounted UI mirror for a managed
zone. It is **not** the actuator: the mini-split head configured under
`head_climate` still receives the real HVAC commands, while the wall
thermostat displays intent and accepts quick setpoint/mode/fan edits.

The first validated device profile is the Honeywell T6 Pro Z-Wave via
`zwave_js`, observed in Home Assistant with:

* `hvac_modes: ["off", "heat", "cool"]`
* `fan_modes: ["Auto low", "Low", "Circulation"]`
* `current_temperature`, `current_humidity`, and `temperature` attributes

Configure the T6 for no-load operation, or wire it so it cannot directly
energize HVAC equipment. Disable device-side schedules if Home Assistant
should be the only scheduler.

```yaml
zones:
  - zone_id: bedroom
    head_climate: climate.bedroom_mini_split_head
    display_thermostats:
      - entity_id: climate.bedroom_t6_thermostat
        sync_setpoint: true
        sync_mode: true
        sync_fan_mode: true
        always_assert: false
        contribute_temperature: true
        contribute_humidity: true
        temperature_weight: 0.3
        humidity_weight: 0.3
        auto_fan_mode: "Auto low"
        fan_only_fan_mode: "Low"
        circulate_fan_mode: "Circulation"
```

### Mode mapping

Outbound, managed zone to T6:

| Managed mode | T6 mode | T6 fan |
|---|---|---|
| `off` | `off` | `Auto low` |
| `heat` | `heat` | `Auto low` |
| `cool` | `cool` | `Auto low` |
| `fan_only` | `off` | `Low` |
| `dry` | `cool` | `Auto low` |
| `auto` | `auto` if supported; otherwise sync status becomes `stale` | `Auto low` |

Inbound, T6 to managed zone:

| T6 mode | T6 fan | Managed mode |
|---|---|---|
| `off` | `Auto low` / `auto` | `off` |
| `off` | `Low` / `Circulation` / `on` / `circulate` | `fan_only` |
| `heat` | any | `heat` |
| `cool` | any | `cool` |
| `em_heat` / emergency heat strings | any | `heat` |

Setpoints are converted through Home Assistant's user-display unit, so
Fahrenheit HA installs are safe. Outbound setpoints are clamped to the
display thermostat's advertised `min_temp` / `max_temp`; the managed
zone remains the source of truth.

### Sensor contribution

When `contribute_temperature` or `contribute_humidity` is enabled, the
coordinator treats the thermostat as a low-weight external sensor. The
defaults (`0.3`) are intentionally lower than a fast room sensor because
Z-Wave thermostats can update slowly.

### Diagnostics

Each display gets a diagnostic sensor named from the zone and display
entity. Its state is:

| State | Meaning |
|---|---|
| `synced` | Last mirror pass succeeded. |
| `stale` | The display could not represent part of the requested intent, usually an unsupported mode such as `auto` on a T6 that only exposes `off` / `heat` / `cool`. |
| `unreachable` | The display entity is missing/unavailable or a service call failed. |

Attributes include the desired display HVAC mode, fan mode, target,
sent/clamped target, `last_success`, and `last_error`.

---

## Aux heat advanced (Phase 6)

The form exposes `enabled`, `device_type`, `device_entity_id`,
`outdoor_temp_sensor`, and `outdoor_lockout_temp`. The five
remaining knobs control activation/deactivation hysteresis,
optional aux setpoint, and head-fault behaviour.

```yaml
zones:
  - zone_id: bedroom
    aux_heat:
      enabled: true
      device_entity_id: switch.bedroom_resistive_heater
      device_type: switch                   # or "climate"
      outdoor_temp_sensor: sensor.outdoor_temp
      outdoor_lockout_temp: -5.0            # °C
      aux_target_temperature: 22.0          # only used for device_type: climate
      safety_floor_margin: 1.5              # activate aux when room T is
                                            # within this many °C of safety floor
      deactivation_margin: 0.5              # extra °C above triggers required
                                            # before turning aux off (hysteresis)
      fault_on_head_unavailable: true       # treat head=unavailable as a trigger
      settle_seconds: 60.0                  # head OFF → wait this long → aux ON
```

### Triggers

Any of these activates aux (and they're OR-ed together):

| Trigger | Condition |
|---|---|
| `head_fault` | `fault_on_head_unavailable: true` AND the head's HA state is `unavailable` / `unknown`. |
| `outdoor_lockout` | Outdoor sensor reading ≤ `outdoor_lockout_temp`. |
| `safety_floor_near` | Effective room temperature ≤ `safety.min_temp + safety_floor_margin`. |

A friendly status string is surfaced through the managed climate's
`status_message` attribute (one of three messages, picked by which
trigger is most relevant).

### Hysteresis (the `deactivation_margin`)

Once aux is active, the integration **adds `deactivation_margin`**
to each "is the trigger still firing?" check, so the room must
recover meaningfully before aux drops. With `safety_floor_margin: 1.5`
and `deactivation_margin: 0.5`, an aux activation triggered by the
floor stays on until the room is at least `safety.min_temp + 2.0 °C`.
Same idea on the outdoor side: aux turns off only when outdoor
temp climbs above `outdoor_lockout_temp + deactivation_margin`.

### Transition sequencing

To protect compressors, aux dispatch is staged through a small
state machine in `aux_heat.AuxHeatRuntime`:

1. The integration commands the head to `OFF`.
2. The `settle_seconds` timer starts.
3. After the timer elapses, the aux device is commanded `ON`.

Coming back from aux mirrors the sequence: aux off → settle → head
back on. Keep `settle_seconds` short (15-30s) for switch-type aux
(electric resistance) and longer (60-120s) for climate-type aux to
give the upstream device time to actually go cold/warm.

### `device_type: climate`

When `device_type: climate`, the integration commands the aux entity
with `set_hvac_mode: heat` (and `set_hvac_mode: off` to deactivate)
instead of `turn_on` / `turn_off`. If `aux_target_temperature` is
set, the integration also dispatches `set_temperature` to that
value. Use this when the aux device is itself a thermostat (e.g.
a baseboard heater wrapped in a `generic_thermostat`).

### Diagnostic surface

* `binary_sensor.<zone>_em_heat_active` — `on` whenever aux is
  currently dispatched (after the settle timer; not during the
  intermediate off-period).
* `sensor.<zone>_aux_heat_trigger` — last trigger reason
  (`safety_floor_near` / `outdoor_lockout` / `head_fault` / `none`).
* `sensor.<zone>_block_reason` — populated with the friendly status
  string while aux is active, suitable for surfacing on a
  Lovelace dashboard.

### Further reading

* [Wikipedia: Heat pump — Balance point and supplemental heat](https://en.wikipedia.org/wiki/Heat_pump#Balance_point)
  — background on *why* aux heat exists: the balance-point
  temperature below which a heat pump can't keep up with the
  building load. Helps choose `outdoor_lockout_temp`.
* [Energy Star: Heat pumps — auxiliary heat](https://www.energystar.gov/products/heat_pumps/considerations_buying)
  — practical guidance on when aux heat should and shouldn't
  engage (e.g. "never run aux heat to recover from setback" —
  why pre-conditioning + a sensible `safety_floor_margin`
  matter).
* [Wikipedia: Hysteresis — control systems](https://en.wikipedia.org/wiki/Hysteresis#Control_systems)
  — the two-threshold pattern this integration uses for
  `safety_floor_margin` + `deactivation_margin`.
* [Home Assistant: Generic Hygrostat — hysteresis comparison](https://www.home-assistant.io/integrations/generic_hygrostat/)
  — HA's other built-in hysteresis-based controller, useful as
  a reference for how the same pattern is exposed elsewhere in
  the ecosystem.

---

## Humidity-aware scoring (Phase 7)

The integration's arbitration scorer normally compares zones by
their dry-bulb deviation from setpoint. Phase 7 adds two opt-in
humidity contributions that bias the score so latent-load zones
get more compressor time when they need it.

```yaml
zones:
  - zone_id: bedroom
    humidity:
      target_humidity: 45.0           # %RH
      tolerance_band: 10.0            # ± half-band, so 40-50% is "fine"
      weight: 1.5                     # multiplier on deviation outside band
      dehumidify_threshold: 65.0      # %RH that makes COOL "extra worth it"
      dehumidify_bonus: 5.0           # flat priority bonus when above threshold
```

### Two terms, both opt-in

**1. Latent comfort term (`weight`)** — active in any HVAC mode:

```
excess = max(0, |effective_humidity − target_humidity| − tolerance_band/2)
contribution = excess × weight
```

So with the snippet above, RH at 60% gives `excess = 60 − 45 − 5 = 10`,
contribution = `10 × 1.5 = 15`, which is added to the zone's
priority score (boosting its chance of winning arbitration when
multiple zones are competing).

**2. Dehumidify-bias bonus** — only when the zone's user intent is
`COOL`:

```
if effective_humidity > dehumidify_threshold:
    contribution += dehumidify_bonus
```

Models the "if cool is needed AND humidity is high, run cool
preferentially" preference: a flat priority bump that biases the
arbiter toward COOL zones when latent load is meaningful.

### Defaults

Every field defaults to "off" — `target_humidity = None`, `weight = 0`,
`dehumidify_threshold = None`, `dehumidify_bonus = 0` — so an
unconfigured `humidity:` block changes nothing. Opt in field by
field as needed.

### Required inputs

Either or both terms only fire when the zone has an *effective
humidity* reading. That requires at least one humidity sensor in
the fusion config (`external_humidity_sensors:` — see *Multi-sensor
fusion* above). A humidity term without a humidity sensor logs a
debug-level "no humidity reading" message and contributes 0.

### Diagnostic surface

* `sensor.<zone>_humidity_priority_contribution` — current
  contribution to the priority score, broken into latent +
  dehumidify components in its attributes.
* `sensor.<zone>_effective_humidity` — fused humidity reading.

### Further reading

* [ASHRAE Standard 55 — humidity acceptability](https://en.wikipedia.org/wiki/ASHRAE_55#Humidity_limits)
  — the standard's recommended humidity range (typically
  30 %–60 % RH) and the rationale behind why deviations outside
  it warrant a comfort-priority bias.
* [Wikipedia: Latent heat](https://en.wikipedia.org/wiki/Latent_heat)
  — the energy-physics distinction between sensible and latent
  load that motivates `dehumidify_bonus` (cooling at high RH
  does more work than the dry-bulb deviation alone implies).
* [Wikipedia: Humidex](https://en.wikipedia.org/wiki/Humidex) and
  [Heat index](https://en.wikipedia.org/wiki/Heat_index) — common
  indices that combine dry-bulb and humidity into one perceived
  temperature, conceptually similar to (but simpler than) the
  Phase 8 `effective_comfort_temp` calculation.
* [U.S. EPA: Indoor humidity guidance](https://www.epa.gov/mold/mold-and-health)
  — health/microbial-growth rationale for keeping indoor RH
  bounded, useful for picking `target_humidity` and
  `dehumidify_threshold`.

---

## Full psychrometric scoring (Phase 8)

A *group-level* opt-in that swaps dry-bulb deviation for a
psychrometric *comfort temperature* in the arbitration scorer. The
comfort temperature combines dry-bulb, RH, and active fan speed
into a single °C-equivalent value, so an air-stirred or low-RH
room scores closer to comfort than a naive thermometer reading.

```yaml
groups:
  - group_id: outdoor_unit_1
    use_psychrometric_scoring: true   # default false
    zones:
      …
```

### What it computes

For each zone, every tick:

```
comfort_temp = dry_bulb
             + 0.04 × (rh − 50)            (only when dry_bulb ≥ 20 °C)
             − cooling_effect(fan_velocity)  (only when occupied + fan running)
```

Where `cooling_effect` is the psychrometric chart's standard
cooling-effect approximation for moving air at a given velocity (the
fan velocity is `fan.speed_pct/100 × 1.5 m/s` — 1.5 m/s being a
typical ceiling-fan max).

### When to enable

* You have *reliable* humidity sensors in every zone (otherwise the
  RH term silently no-ops on zones missing it, distorting
  arbitration).
* You're running the fan dispatcher actively (otherwise the only
  term doing work is the RH correction).
* You're seeing arbitration-driven discomfort in summer when the
  air is humid but technically at setpoint.

If any of those is "no", stick with the default dry-bulb scoring.

### Diagnostic surface

* `sensor.<zone>_effective_comfort_temperature` — the psychrometric
  value used by the arbiter. Compare with
  `sensor.<zone>_effective_temperature` (raw fused dry-bulb) to see
  the magnitude of the correction.

### Further reading

* [Wikipedia: Psychrometrics](https://en.wikipedia.org/wiki/Psychrometrics)
  — the underlying field; the [Psychrometric chart](https://en.wikipedia.org/wiki/Psychrometric_chart)
  visually shows how dry-bulb, RH, humidity ratio, and enthalpy
  interrelate. The integration's `psychrometrics.py` module
  exposes `humidity_ratio()`, `enthalpy()`, and other primitives
  callable from your own templates if you want to pull these
  values onto a dashboard.
* [Wikipedia: Thermal comfort — PMV/PPD](https://en.wikipedia.org/wiki/Thermal_comfort#PMV/PPD_method)
  — Fanger's predicted-mean-vote model; the integration ships
  a simplified `pmv_lite()` in `psychrometrics.py` for users who
  want comfort scoring without the full ISO 7730 calculation.
* [CBE Thermal Comfort Tool (Berkeley)](https://comfort.cbe.berkeley.edu/)
  — interactive tool implementing ASHRAE 55, PMV/PPD, SET, and
  the air-velocity cooling effect. Plug your typical room
  conditions in to validate that enabling
  `use_psychrometric_scoring` actually moves the needle for
  *your* climate before flipping it on.
* [ISO 7730:2005 — Ergonomics of the thermal environment](https://www.iso.org/standard/39155.html)
  — formal specification of PMV/PPD and SET if you want the
  authoritative reference (paywalled, but widely summarised).
* [ASHRAE Handbook — Fundamentals, Chapter 1 (Psychrometrics)](https://www.ashrae.org/technical-resources/ashrae-handbook)
  — the canonical engineering reference for every formula in
  `psychrometrics.py`.

---

## Compressor-group compatibility (advanced)

The form lets you add **one** extra incompatible mode pair on top of
the defaults (`heat ↔ cool` and `heat ↔ fan_only`). YAML accepts
any number of pairs.

```yaml
groups:
  - group_id: outdoor_unit_1
    incompatible_mode_pairs:
      - ["heat", "cool"]      # redundant — already in defaults, but explicit is fine
      - ["heat", "dry"]
      - ["cool", "dry"]
      - ["heat", "fan_only"]  # also a default
    disable_default_incompatible_mode_pairs: false  # default; merge with defaults
```

### Disabling the defaults

Some hardware genuinely supports running heat+cool simultaneously
on different indoor heads (true VRF systems with separate
refrigerant circuits per head). For those, opt out of the defaults
and only declare the pairs your hardware actually rejects:

```yaml
groups:
  - group_id: vrf_outdoor
    disable_default_incompatible_mode_pairs: true
    incompatible_mode_pairs: []   # nothing is incompatible
    zones:
      …
```

### Effect

Any pair listed becomes a hard constraint on arbitration: when two
zones request modes from the same pair, the one with the higher
priority score keeps its mode and the other is forced to `OFF`
(visible in `sensor.<zone>_block_reason` and the managed climate's
`status_message`).

### Further reading

* [Wikipedia: Variable refrigerant flow (VRF)](https://en.wikipedia.org/wiki/Variable_refrigerant_flow)
  — the hardware class that *can* legitimately mix heat and
  cool simultaneously across heads, and is therefore the main
  use case for `disable_default_incompatible_mode_pairs: true`.
  Note the distinction between "heat-recovery VRF" (true
  simultaneous heat+cool) and "heat-pump VRF" (one mode at a
  time, same as a typical multi-split) — only the former
  warrants disabling the defaults.
* [Daikin: Heat-pump vs heat-recovery VRF systems](https://www.daikinapplied.com/products/vrv-vrf)
  — manufacturer overview of the two VRF topologies, useful for
  identifying which one your equipment is.
* [Mitsubishi: Multi-split limitations](https://www.mitsubishicomfort.com/products/multi-split)
  — typical residential multi-split documentation; confirms the
  one-mode-at-a-time constraint that makes the default pairs
  the right choice for the vast majority of installations.

---

## Zone polish — `target_temp_step`

The managed climate entity's setpoint UI defaults to 0.5 °C
increments. Override per zone:

```yaml
zones:
  - zone_id: bedroom
    target_temp_step: 0.1   # 0.1 °C buttons — needs a head that supports it
```

Note that the *upstream head* must accept the finer resolution; a
head with 1 °C internal resolution will silently round.

---

## Applying advanced changes

### From `configuration.yaml`

1. Edit the file.
2. Validate: **Developer Tools → YAML → Check Configuration**.
3. Apply: **Developer Tools → YAML → Reload Multi-Split Zone
   Controller** (or restart HA for changes that touch
   integration loading itself, e.g. adding a brand-new group).

### From a UI config entry

1. **Settings → Devices & Services → Multi-Split Zone Controller →
   Configure**.
2. Pick **`__yaml__`** at the top of the options menu.
3. Edit the YAML in HA's syntax-highlighted editor.
4. Submit. Validation runs on the same code path as YAML; on
   failure you stay on the editor with the error inline.

### Per-tick reload-free changes

Most knobs above take effect on the next coordinator tick (no
HA reload needed):

* sensor weights / calibration / `head_weight` / `stale_after_seconds`
* setback offsets, `unoccupied_comfort_weight`, occupancy thresholds
* fan speed caps, comfort credit, direction
* aux heat margins and triggers
* humidity scoring weights and bonuses
* `use_psychrometric_scoring`

A few changes need a reload because they alter entity properties:

* zone `min_temp` / `max_temp` / `target_temp_step` (managed
  climate UI bounds)
* `head_climate` reassignment (rebinds the dispatcher)
* adding/removing zones, fusion sensor entity IDs
* `enabled` flips on `fan` / `aux_heat` (entity property changes)

When in doubt, reload — it's fast and the integration is
stateless across reloads.
