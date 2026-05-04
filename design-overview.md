# Home Assistant Multi-Split Zone Controller Implementation Plan

This document defines an implementation-oriented design for a Home Assistant custom integration that manages constrained multi-split HVAC systems through user-friendly managed climate entities, while internally coordinating upstream mini-split heads, independent temperature and humidity sensors, ceiling fans, auxiliary resistive heat, occupancy-aware setback, and future psychrometric comfort optimization.[1][2][3]

## Goals

The integration should let users interact with each zone through a normal climate entity while hiding compressor-group restrictions, auxiliary heating behavior, and arbitration complexity behind the scenes.[1] Administrators should be able to tune compatibility rules, scoring weights, occupancy behavior, setback policy, safety thresholds, and sensor-fusion strategy without exposing that complexity in the normal user interface.[1]

The system should support six major control layers in one coordinator-driven architecture:[1]

- Managed climate entities for each user-facing zone.[1]
- Compatibility-aware arbitration across indoor heads that share a multi-split outdoor unit.[1]
- Sensor fusion across built-in head sensors and selected independent zone sensors.[1]
- Supplemental device orchestration for ceiling fans and resistive heat sources.[2]
- Occupancy-aware setback and pre-conditioning based on expected occupancy signals.[4][5][6]
- A future psychrometric comfort layer that can optimize beyond dry-bulb temperature alone.[7][3][8]

## Control model

### User-facing behavior

Each zone should expose a managed `ClimateEntity` that acts as the primary control surface for dashboards, voice assistants, mobile apps, and automations.[1][9] Users should be able to set target temperature, HVAC mode, preset, and temporary holds exactly as they would with a conventional thermostat, while the coordinator determines whether the requested state can actually be granted at that moment.[1][9]

Auxiliary or emergency heat should be transparent to users and should not appear as a primary mode choice on the managed climate entity.[2] Instead, the integration should activate auxiliary heat automatically when a zone approaches a safety threshold or when configured efficiency or fault conditions justify resistive fallback, and then surface that behavior only through user-friendly status messages such as “Emergency heat active to protect room temperature” or “Supplemental heat assisting during extreme outdoor conditions.”[2]

Independent room sensors should also be transparent to users in normal operation.[1] Users should not need to understand whether the zone is currently following the mini-split head thermistor, an external room sensor, or a blended effective value; they should simply experience more accurate room-level control and clear status messaging when diagnostics are needed.[1]

Ceiling fans should remain mostly transparent from a zone-control perspective.[1] Users may optionally retain manual fan access through their native fan entities, but the coordinator should be able to manage direction and speed as part of the zone comfort strategy without requiring separate automations.[1]

### Internal architecture

The integration should use one Home Assistant config entry for the installation and one coordinator per compressor group or outdoor unit.[1] Each coordinator should own the managed zone entities in its group, consume upstream head entities plus selected independent sensors and supplemental devices, evaluate legal combinations, and dispatch the selected commands on each control cycle.[1]

This separation keeps user intent distinct from hardware actuation.[1] Managed climate entities store desired state, while the coordinator applies compatibility rules, sensor-fusion logic, occupancy policy, minimum runtime protections, safety logic, and device orchestration before writing commands to the actual devices.[1]

## Home Assistant entity strategy

### Core entities

The recommended entity model is shown below.[1][2]

| Entity type | Domain | Visible to normal users | Purpose |
|---|---|---:|---|
| Managed zone thermostat | `climate` | Yes | Main user control surface.[1][9] |
| Upstream mini-split head | `climate` | Usually hidden | Consumed by coordinator as actuator target.[1] |
| Independent temperature sensor | `sensor` | Existing user choice | Preferred occupied-space temperature input.[1] |
| Independent humidity sensor | `sensor` | Existing user choice | Preferred occupied-space humidity input.[1] |
| Ceiling fan | `fan` | Optional | Supplemental comfort actuator with speed and direction control.[1] |
| Baseboard or panel heater | `switch` or `climate` | Usually hidden | Auxiliary resistive heat source controlled by coordinator.[2] |
| Effective zone temperature | `sensor` | Admin only | Fused temperature used by the control loop.[1] |
| Effective zone humidity | `sensor` | Admin only | Fused humidity used by humidity-aware logic.[1] |
| Priority score sensor | `sensor` | Admin only | Diagnostics and tuning.[1] |
| Block reason sensor | `sensor` | Admin only | Human-readable explanation for queueing or denial.[1] |
| EM heat active sensor | `binary_sensor` | Admin only | Indicates transparent supplemental heat activation.[2] |
| Fan comfort offset sensor | `sensor` | Admin only | Tracks fan-contributed apparent comfort adjustment.[1] |
| Effective comfort sensor | `sensor` | Admin only | Future comfort metric using psychrometric or PMV-like model.[7][3] |

### Climate API constraints

Home Assistant deprecated `is_aux_heat`, `turn_aux_heat_on`, and `turn_aux_heat_off` on `ClimateEntity` in Core 2024.4.[2] Because of that, auxiliary or emergency heat should not be implemented as a climate aux-heat flag; it should be modeled as either a separate `SwitchEntity` for simple resistive devices or as a separate thermostat-capable `ClimateEntity` when a smart baseboard or panel heater supports setpoint control.[2]

This fits the desired UX well because auxiliary heat is meant to be hidden from users and treated as an implementation detail unless active.[2] The managed zone thermostat can simply report status attributes or friendly messages when supplemental heating is engaged.[1][2]

## Sensor fusion

### Design requirement

Sensor fusion should be a first-class concern, not an optional afterthought.[1] The integration must accommodate both the sensor built into the mini-split indoor head and any administrator-selected independent temperature and humidity sensors located in the zone.[1]

This matters because the head thermistor often measures return-air conditions near the unit rather than true occupied-space conditions, while independent sensors can better represent the temperature and humidity where occupants actually experience comfort.[10] The coordinator should therefore compute effective zone values from whichever sensor mix is configured rather than assuming any single source is always authoritative.[1]

### Supported sensor sources per zone

Each zone should support zero or more selected sensor entities for each measurement type.[1]

| Source | Measurement | Typical use |
|---|---|---|
| Mini-split head thermistor | Temperature | Native fallback source when no independent room sensor exists.[1] |
| Mini-split head humidity reading, if exposed | Humidity | Native fallback for latent-comfort awareness.[1] |
| Independent room sensor | Temperature | Preferred occupied-space dry-bulb reading.[1] |
| Independent room sensor | Humidity | Preferred occupied-space humidity reading.[1] |
| Combined temp/humidity sensor | Temperature + humidity | Common single-device room reference.[1] |
| Template or helper sensor | Temperature and/or humidity | Supports area averages or custom logic from existing Home Assistant helpers.[1] |

A zone should not be limited to one external sensor.[1] Larger rooms may need multiple selected room sensors, and the integration should support combining them in a configurable way.[1]

### Fused zone values

The coordinator should calculate at least four internal values per zone:[1]

- Effective temperature for control and scoring.[1]
- Effective humidity for latent-comfort and psychrometric staging.[7][3]
- Safety temperature for freeze or overheat protection, optionally using a more conservative strategy.[11][10]
- Sensor-quality or source-status metadata for diagnostics.[1]

The default control path should use effective temperature and effective humidity rather than raw upstream values.[1] This ensures that arbitration, setback, auxiliary heat activation, fan crediting, and future comfort optimization are all based on the best available representation of zone conditions.[1][7][3]

### Fusion strategies

The first implementation should support a small set of clear fusion modes per zone.[1]

| Strategy | Behavior | Best use |
|---|---|---|
| Head only | Uses built-in mini-split sensors only | Basic installs with no independent sensors.[1] |
| External preferred | Uses selected room sensors when available, falls back to head sensors otherwise | Recommended default.[1] |
| Weighted blend | Blends head and independent sensors using admin-defined weights | Useful when neither source is perfect.[1] |
| Average of externals | Averages multiple independent sensors, ignoring head except as fallback | Large zones with several room sensors.[1] |
| Occupancy-weighted external set | Biases toward the sensor nearest current occupancy, then falls back to average | Advanced large-zone behavior.[12][6] |

The default recommended mode should be **external preferred** for both temperature and humidity when suitable sensors are available.[1] A weighted blend should remain available for cases where the head sensor tracks fast equipment response while external sensors better represent occupied-space conditions.[1]

### Suggested internal formulas

A simple starting point for blended temperature is:[1]

```text
effective_temp = (
    temp_head * w_head
  + temp_ext_1 * w_ext_1
  + temp_ext_2 * w_ext_2
  + ...
) / (w_head + w_ext_1 + w_ext_2 + ...)
```

A similar pattern applies to humidity:[1]

```text
effective_humidity = (
    rh_head * w_head
  + rh_ext_1 * w_ext_1
  + rh_ext_2 * w_ext_2
  + ...
) / (w_head + w_ext_1 + w_ext_2 + ...)
```

The implementation should also support per-sensor calibration offsets before fusion.[1] That makes it possible to correct known placement bias or cheap-sensor drift without hiding the original sensor readings from Home Assistant users.[1]

### Follow-me as fusion policy

“Follow me” should be treated as a sensor-fusion policy rather than a separate feature.[1] In practical terms, follow-me means the coordinator changes which sensor or sensor blend carries the most weight based on occupancy location, configured preference, or zone mode.[1][12]

That model is simpler and more extensible than implementing a separate control concept.[1] It also aligns naturally with future large-zone handling where more than one independent sensor may be active in the same room.[1][12]

### Why fusion affects the whole system

Sensor fusion is not just a display choice.[1] It should directly affect the following decisions:[1]

- Priority scoring uses effective temperature, not raw head temperature.[1]
- Humidity-aware logic uses effective humidity, not a single arbitrary source.[7][3]
- Setback and safety floors evaluate against fused or conservative safety values.[11][10]
- Pre-conditioning lead-time estimation uses occupied-space measurements where possible.[6]
- Fan comfort credit should be based on actual room conditions, not supply-air-influenced head readings.[1]
- Auxiliary heat escalation should use the best safety-relevant zone reading available.[2][10]

## Occupancy-aware setback and pre-conditioning

### Occupancy states

The coordinator should support four occupancy states per zone.[4][5][6]

| State | Meaning | Control behavior |
|---|---|---|
| Confirmed | Reliable evidence of occupancy now | Full comfort setpoint, full comfort weighting, full fan logic.[12][5] |
| Expected | Occupancy likely soon | Start pre-conditioning toward comfort setpoint.[4][6] |
| Recent | Occupied recently within linger window | Hold comfort briefly before setback.[12] |
| Unoccupied | No evidence now and not expected soon | Setback setpoint, fan off except safety cases, safety floor/ceiling only.[4][6] |

Occupancy should influence comfort weighting, fan credit, and setpoint selection, but it should not remove a zone from safety consideration.[6] Even an unoccupied room may still require freeze protection, pet-safe temperature limits, humidity protection, or condensation control.[11][10]

### Setback model

Each zone should maintain three independent temperature concepts:[1][6]

- Comfort setpoint, used for confirmed or recent occupancy.[6]
- Setback setpoint, used for unoccupied periods until pre-conditioning begins.[6]
- Safety floor and safety ceiling, always enforced regardless of occupancy.[11][10]

Cooling and heating setback offsets should be configurable separately at the zone or group level.[6] The coordinator should also support a linger window and a ramp strategy so that rooms do not immediately jump to setback on brief departures.[12][6]

### Sources of expected occupancy

Expected occupancy should come from confidence-weighted fusion rather than any single source.[12][6] A practical first version can support explicit source categories that are optional per zone or per person:[4][5]

- `person` entities and device trackers for home and away state, plus travel direction when available.[4][5][13]
- Calendar events, including recurring schedules and zone-specific occupancy blocks.[4]
- mmWave, PIR, BLE, bed sensors, door sensors, and media activity for inferred room use.[12][14]
- Historical occupancy priors, such as weekday office hours or overnight bedroom usage.[12][6]
- Alarm or wake-time helpers, which are especially useful for bedrooms.[4]
- Manual “expect occupied soon” or “working from home” overrides exposed through presets or helper toggles.[1]

A useful internal abstraction is a probability or confidence score that resolves to the four occupancy states using configurable thresholds.[12][6] That lets the integration consume signals from Home Assistant-native entities and future prediction helpers without rewriting core control logic.[12][5]

### Pre-conditioning

When a zone is expected to be occupied soon, the coordinator should begin moving that zone from setback toward comfort before arrival.[6] The start time should be estimated from current temperature gap, historical heating or cooling rate for the zone, device capability, and any expected fan contribution, so the room is comfortable by the predicted arrival rather than only after arrival.[6]

This pre-conditioning model should treat occupancy prediction as advisory rather than absolute.[6] If expected occupancy does not become confirmed, the zone can fall back to setback after a timeout or confidence drop, subject to minimum runtime protections.[1]

## Priority and arbitration matrix

### High-level sequence

Each control pass should follow a stable sequence:[1]

1. Read managed zone intent, upstream device state, supplemental device state, independent sensor state, outdoor conditions, and diagnostic timers.[1]
2. Resolve fused temperature and humidity per zone.[1]
3. Resolve occupancy state and select active setpoint for each zone.[4][6]
4. Check safety limits and auxiliary heat eligibility before normal comfort arbitration.[2][10]
5. Compute legal mode combinations for each compressor group.[1]
6. Score candidate zones or candidate combinations and choose the best valid outcome.[1]
7. Dispatch mini-split, fan, and auxiliary heat commands in the correct order.[1][2]
8. Update exposed status and diagnostic entities.[1]

### Priority inputs

The priority matrix can be changed later, but the implementation should already support the following inputs so roadmap decisions do not require major refactoring.[1]

| Input | Purpose |
|---|---|
| Effective temperature deviation | Core comfort gap from active setpoint using fused zone temperature.[1] |
| Effective humidity deviation | Accounts for latent discomfort and future psychrometric behavior using fused humidity.[7][10][3] |
| Occupancy weight | Gives active rooms higher comfort priority than empty rooms.[6] |
| Expected occupancy weight | Allows pre-conditioning before arrival.[6] |
| Safety urgency | Overrides comfort when floors or ceilings are near breach.[11][10] |
| Minimum runtime penalty | Prevents short-cycling and excessive winner swapping.[1] |
| Fan comfort offset | Credits air movement where it materially improves comfort.[1] |
| Auxiliary heat state | Allows transparent fallback without user mode changes.[2] |
| Compressor compatibility cost | Penalizes transitions that require broad group changes.[1] |
| Lockout and settle timers | Protect hardware during transitions.[1][2] |

### Occupancy in priority

Occupancy should be part of the priority decision matrix, but only for comfort components.[6] Safety-related components should remain active even when the room is unoccupied, which keeps freeze protection and high-temperature protection independent of presence.[11][10]

This means the scoring model should separate comfort priority from safety priority.[10][6] A comfort score can be occupancy-weighted, while a safety score can bypass that weighting and escalate directly when a configured threshold is near violation.[11][10]

## Ceiling fan orchestration

Ceiling fans should be modeled as comfort actuators that can reduce perceived temperature during cooling and improve air mixing during heating.[1] In cooling mode, air movement may allow the coordinator to defer compressor operation when the room is only modestly above target and occupancy is confirmed.[7][8]

In heating mode, reverse-low fan operation can reduce stratification and distribute heat without creating wind chill.[10] During auxiliary resistive heating, reverse-low circulation can also help distribute baseboard output more evenly through the room.[10]

The fan control model should therefore include:[1]

- Enable or disable coordinated fan control per zone.[1]
- Direction policy, such as forward in cooling and reverse-low in heating.[10]
- Speed policy based on occupancy and deviation from setpoint.[1]
- A configurable apparent-temperature credit used only when the room is occupied.[1]
- Lockouts that prevent the fan from fighting comfort when occupants dislike air movement.[1]

Fan control should remain subordinate to safety behavior.[10] For example, an unoccupied room should not earn comfort credit from a fan, but a fan may still run under safety logic if it materially improves heat distribution or protects equipment.[10]

## Transparent auxiliary heat behavior

### When to activate

Auxiliary heat should be a hidden implementation detail that activates automatically under configurable conditions.[2] These conditions can include:[2][10]

- Zone approaching a safety minimum or maximum.[11][10]
- Outdoor temperature below a heat-pump lockout threshold.[10]
- Efficiency threshold breach, such as estimated COP dropping below a configured floor.[10]
- Mini-split head fault, prolonged defrost, or compressor unavailability.[10]
- Explicit emergency or recovery logic chosen by the administrator.[1]

### How to activate

The coordinator should treat auxiliary heat as a separate actuation path.[2] A safe sequence is to command the mini-split head off if necessary, respect any settle or lockout timer, activate the auxiliary heater, optionally adjust fan behavior, and then mark the managed climate status as assisted or emergency heat active.[2]

This preserves the user-facing fiction of a single smart thermostat while still respecting Home Assistant’s current entity model and protecting hardware.[1][2]

### User messaging

Because auxiliary heat is intentionally hidden, the integration should expose clear status strings instead of extra controls.[2] Examples include:[1]

- “Supplemental heat active to protect room temperature.”[1]
- “Auxiliary heat assisting due to outdoor conditions.”[1]
- “Heat pump unavailable, using backup heat temporarily.”[1]

## Future psychrometric comfort optimization

### Why psychrometrics matters

Dry-bulb temperature alone does not capture comfort, especially when humidity and air movement vary across rooms.[7][8] ASHRAE 55 comfort guidance considers operative temperature and humidity boundaries, and modern comfort tools also expose PMV, PPD, SET, and cooling-effect calculations that are useful for richer control models.[7][3][8]

This is directly relevant to a zone controller that already observes humidity sensors, coordinates fans, and supports sensor fusion across multiple temperature and humidity sources.[7][3] Once temperature-only control is stable, the system can evolve toward a comfort metric that rewards combinations such as slightly warmer air with meaningful air movement, or earlier dehumidification when latent discomfort is becoming dominant.[7][10][3]

### Recommended future model

The initial implementation should not attempt full psychrometric optimization in the primary control loop, but it should collect the data needed for it.[7][3] A good roadmap-friendly approach is to stage the feature in three levels:[7][3]

| Level | Behavior | Suggested timing |
|---|---|---|
| Level 0 | Temperature-only control with humidity diagnostics and fused sensors | Initial release |
| Level 1 | Humidity-aware scoring and dehumidification preference | Early enhancement |
| Level 2 | Comfort metric using PMV, SET, cooling effect, or similar psychrometric inputs | Advanced feature |

A future comfort sensor can combine dry-bulb temperature, relative humidity, occupancy, and fan state into an “effective comfort” value that influences scoring without replacing the underlying safety and compatibility logic.[7][3][8]

## Suggested Home Assistant project structure

A practical custom integration layout could look like this:[1]

```text
custom_components/
  multisplit_zone_controller/
    __init__.py
    manifest.json
    const.py
    config_flow.py
    options_flow.py
    coordinator.py
    models.py
    arbitration.py
    occupancy.py
    sensor_fusion.py
    comfort.py
    psychrometrics.py
    sensors.py
    climate.py
    fan_proxy.py
    aux_heat.py
    binary_sensor.py
    diagnostics.py
    services.yaml
    translations/
      en.json
    tests/
      conftest.py
      test_config_flow.py
      test_climate_entities.py
      test_sensor_fusion.py
      test_arbitration.py
      test_occupancy.py
      test_aux_heat.py
      test_fan_coordination.py
      test_psychrometrics.py
```

Recommended module responsibilities:[1]

- `coordinator.py`: refresh loop, state gathering, dispatch orchestration.[1]
- `arbitration.py`: legal combinations, scoring, winner selection, transition ordering.[1]
- `occupancy.py`: four-state occupancy resolution and expected-occupancy fusion.[12][6]
- `sensor_fusion.py`: effective temperature and humidity calculation, weighting, source preference, and follow-me source selection.[1]
- `comfort.py`: effective temperature helpers, fan comfort offset, and comfort-score helpers.[7][3]
- `psychrometrics.py`: future humidity-ratio, enthalpy, PMV/SET helpers and staging hooks.[7][3][8]
- `aux_heat.py`: fallback eligibility, settle-delay handling, and hidden-status transitions.[2]
- `climate.py`: managed user-facing thermostats.[1][9]
- `fan_proxy.py`: supplemental fan wrappers or coordination helpers.[1]
- `sensors.py` and `binary_sensor.py`: diagnostics, block reason, comfort, occupancy, fallback state, and fused-zone-value sensors.[1]

## Claude Code implementation guidance

The implementation request to Claude Code should emphasize a staged, test-first build rather than a full feature dump in one pass.[1] A strong prompt should ask for domain models first, then sensor fusion and arbitration, then entity scaffolding, then occupancy and auxiliary heat logic, and only after that the future psychrometric hooks.[7][3]

Suggested phases:[1]

1. Build entity models, zone configuration schema, and coordinator skeleton.[1]
2. Implement sensor-fusion models and pure functions for effective temperature and humidity.[1]
3. Implement managed `ClimateEntity` wrappers and upstream head mapping.[1][9]
4. Implement compressor-group compatibility arbitration with tests.[1]
5. Add occupancy states, setback policy, and expected-occupancy fusion.[12][4][6]
6. Add coordinated fan behavior and apparent-temperature crediting.[1][7]
7. Add hidden auxiliary heat logic using separate entities and status messages.[2]
8. Add future psychrometric helpers and diagnostic sensors behind feature flags.[7][3]

A useful prompt constraint is to require typed Python models, pure decision functions for fusion, scoring, and arbitration, and high test isolation so the control engine can be validated without needing a live Home Assistant core in every test.[1]

## Home Assistant in Docker test environment

### Recommended setup

Testing should use both unit tests and an integration sandbox running Home Assistant in Docker.[1] The Docker environment is useful for entity registration, config flows, service calls, and state restoration behavior, while pure Python unit tests should cover most fusion, arbitration, and occupancy logic.[1]

A practical local workflow is:[1]

- Run Home Assistant Core in Docker with the custom integration mounted into `custom_components`.[1]
- Keep example helper entities in YAML or package files for upstream climates, independent temperature sensors, humidity sensors, fans, and backup heaters.[1]
- Use deterministic fixture entities so scenario replay is reproducible.[1]
- Run the Home Assistant test suite plus targeted `pytest` files for pure logic modules.[1]

### Example Docker compose skeleton

```yaml
services:
  homeassistant:
    image: ghcr.io/home-assistant/home-assistant:stable
    container_name: ha-dev
    restart: unless-stopped
    network_mode: host
    volumes:
      - ./config:/config
      - ./custom_components:/config/custom_components
    environment:
      - TZ=America/Chicago
```

This is enough for most local integration testing as long as the custom component is mounted into the container configuration directory.[1] Scenario helpers can then be defined in the same config volume and reloaded between test runs.[1]

### Scenario matrix for testing

The test plan should cover both algorithmic logic and Home Assistant behavior.[1][2][6]

| Scenario | Expected result |
|---|---|
| No external sensors configured | Zone falls back to head sensor and still functions normally.[1] |
| External temp sensor configured | Effective temperature uses selected room sensor or configured fusion mode.[1] |
| External humidity sensor configured | Effective humidity uses selected room sensor or configured fusion mode.[1] |
| Multiple room sensors configured | Fusion strategy resolves deterministic effective values.[1] |
| One zone requests heat, others off | Heat allowed for that head only if group rules permit.[1] |
| One zone requests heat, another requests cool | Coordinator resolves conflict and exposes block reason without invalid dispatch.[1] |
| Occupied room and empty room both deviate equally | Occupied room gets comfort priority.[6] |
| Room becomes unoccupied briefly | Linger logic prevents immediate setback.[12][6] |
| Zone expected soon | Pre-conditioning begins before predicted arrival.[6] |
| Safety floor approached while unoccupied | Safety override prevents excessive setback.[11][10] |
| Fan enabled in occupied cooling zone | Effective comfort improves and compressor need may reduce.[7][8] |
| Auxiliary heater activates | Separate heater entity engages; managed climate reports friendly status only.[2] |
| Psychrometric feature flag off | Control loop remains temperature-first with humidity diagnostics only.[7][3] |

### Suggested test levels

- Unit tests for pure sensor-fusion functions and fallback behavior.[1]
- Unit tests for pure arbitration and scoring functions.[1]
- Unit tests for occupancy state resolution and expected-occupancy fusion.[12][6]
- Unit tests for setback and safety-limit transitions.[11][6]
- Unit tests for fan coordination and comfort offset calculations.[7]
- Unit tests for auxiliary heat eligibility and transition ordering.[2]
- Integration tests for config flow, entity creation, service handling, and state updates in Home Assistant.[1][9]
- Manual Docker scenarios for whole-system validation with real Lovelace cards and status messaging.[1]

## Acceptance criteria

The first implementation should be considered successful when the following conditions are met:[1][2][6]

- Users can control each zone through a single managed climate entity.[1][9]
- Compressor-group incompatibilities are never dispatched to upstream devices.[1]
- Independent temperature and humidity sensors can be selected per zone and used in the control loop.[1]
- The coordinator computes effective temperature and humidity values rather than assuming the head sensor is always authoritative.[1]
- Auxiliary resistive heat can activate automatically without appearing as a user mode control.[2]
- Fan coordination can contribute to comfort strategy without separate automations.[1]
- Occupancy-aware setback and pre-conditioning work with configurable confidence sources.[12][4][6]
- Safety floors and ceilings override comfort logic when necessary.[11][10]
- Diagnostic entities make fusion behavior, arbitration decisions, and block reasons observable to administrators.[1]
- Psychrometric optimization remains optional and staged so it does not destabilize the initial release.[7][3]

## Recommended roadmap framing

The roadmap can be decided later without changing the architecture if the integration is implemented with separable decision layers.[1] The important point now is to ensure that sensor fusion, occupancy, setback, fan coordination, auxiliary heat, and psychrometric hooks are represented in the data model and test plan from the beginning, even if some are feature-flagged or introduced in later milestones.[7][3][6]