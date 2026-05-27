# Multi-Split Zone Controller — Installation

How to add this integration to your Home Assistant instance. After
install, see [`SETUP.md`](SETUP.md) for the configuration walkthrough
and [`ADVANCED.md`](ADVANCED.md) for the full feature reference.

> **What gets installed:** a single `custom_components/multisplit_zone_controller/`
> directory inside your Home Assistant config folder. No external
> Python packages, no system services, no network requirements — the
> integration is `local_push` and only talks to your existing HA
> entities.

> **Display thermostats:** v0.2.0 adds optional physical display
> thermostat mirroring, validated first with Honeywell T6 Pro Z-Wave
> devices. It is opt-in per zone through `display_thermostats:` and
> does not change existing zones unless configured. Configuration
> temperature values remain Celsius; the integration converts to/from
> Home Assistant's configured display unit at the entity boundary.

---

## Prerequisites

| Requirement | Minimum | Recommended |
|---|---|---|
| Home Assistant | **2024.1** (uses `EntitySelector`, `ObjectSelector`, modern climate APIs) | Latest `stable` (the test suite runs against `ghcr.io/home-assistant/home-assistant:stable`) |
| Python | Provided by HA — no separate install | n/a |
| Network | None (no cloud, no external API) | n/a |

### Compatible installation types

All four mainstream HA install types are supported. The only thing
that varies is *where* the `custom_components/` directory lives:

| HA install type | Config path | How to get a shell / file access |
|---|---|---|
| **Home Assistant OS** (HAOS) | `/config` | *Samba share*, *SSH & Web Terminal*, or *File editor* add-on. |
| **Home Assistant Supervised** | `/usr/share/hassio/homeassistant` (host) → `/config` (inside HA) | SSH to host, then edit `/usr/share/hassio/homeassistant/custom_components/`. |
| **Home Assistant Container** | Whichever host directory you mounted to `/config` in your `docker run` / `docker-compose.yml` | Edit on the host directly. |
| **Home Assistant Core** (manual venv install) | `~/.homeassistant/` (or whatever `--config` points at) | Edit directly with your normal shell. |

If your config directory does not yet have a `custom_components/`
folder, create one before installing.

---

## Installation method 1 — HACS (recommended)

HACS (the *Home Assistant Community Store*) is the easiest way to
install and keep the integration up to date. **It must be added as a
"custom repository" because this integration is not in the default
HACS index.**

> **Compatibility:** the repo ships a `hacs.json` at its root
> declaring the display name, the minimum-supported HA version, and
> README rendering. Both HACS validation and Home Assistant Core's
> `hassfest` validator run on every push (see
> `.github/workflows/validate.yml`), so the install bundle is
> guaranteed to satisfy HACS's structural requirements at the time
> of release.

### Step 1 — Install HACS itself

If you don't already have HACS, follow the official installer:
[hacs.xyz/docs/setup/download](https://hacs.xyz/docs/setup/download)

After install, you'll see a **HACS** entry in the HA sidebar.

### Step 2 — Add this repository as a custom repository

1. Open **HACS** in the sidebar.
2. Click the **⋮** (three-dot menu) in the top-right.
3. Select **Custom repositories**.
4. **Repository:** paste `https://github.com/matt-blackstone/ha-zone-climate`
5. **Type:** `Integration`
6. Click **Add**.

The repository will appear in your HACS list within a few seconds.

### Step 3 — Download the integration

1. Inside HACS, search for **"Multi-Split Zone Controller"**.
2. Click the entry, then **Download** in the bottom-right.
3. Confirm the version (latest is fine).
4. HACS will copy the integration into
   `config/custom_components/multisplit_zone_controller/`.

### Step 4 — Restart Home Assistant

**Settings → System → top-right ⋮ → Restart Home Assistant**.

Wait for HA to come back up. Continue at *Verify the installation*
below.

---

## Installation method 2 — Manual install (release zip)

Use this when you don't run HACS, or when you want to pin to a
specific tagged release without going through HACS's UI.

### Step 1 — Download the release

1. Open the project's [Releases page](https://github.com/matt-blackstone/ha-zone-climate/releases).
2. Download the source zip for the release you want (or the
   `Latest` tag if you want the newest tagged build).

### Step 2 — Extract and copy

Extract the zip somewhere temporary, then copy *only* the
`custom_components/multisplit_zone_controller/` directory into
your HA config:

```
<your HA config>/
└── custom_components/
    └── multisplit_zone_controller/
        ├── __init__.py
        ├── manifest.json
        ├── const.py
        ├── ... (all the .py files)
        └── translations/
            └── en.json
```

> **Important:** copy the *contents* of `custom_components/`, not the
> `custom_components/` folder itself. The end state is one folder
> named `multisplit_zone_controller` *inside* your existing
> `custom_components/`.

For each install type, that translates to:

```bash
# Container / Supervised / Core:
cp -r ./custom_components/multisplit_zone_controller \
   /path/to/ha/config/custom_components/

# HAOS (via SSH add-on):
scp -r ./custom_components/multisplit_zone_controller \
   root@homeassistant.local:/config/custom_components/
```

### Step 3 — Restart Home Assistant

**Settings → System → top-right ⋮ → Restart Home Assistant**.

---

## Installation method 3 — Git clone (contributors / track `main`)

Best when you want to follow `main` between releases or contribute
back upstream.

```bash
cd /path/to/ha/config/custom_components/
git clone https://github.com/matt-blackstone/ha-zone-climate.git tmp-msz
cp -r tmp-msz/custom_components/multisplit_zone_controller .
rm -rf tmp-msz
```

Or, if you want a live working copy you can `git pull` to update:

```bash
# Clone the repo somewhere you control:
git clone https://github.com/matt-blackstone/ha-zone-climate.git ~/code/ha-msz

# Symlink the integration directory into HA:
ln -s ~/code/ha-msz/custom_components/multisplit_zone_controller \
   /path/to/ha/config/custom_components/multisplit_zone_controller
```

> **HAOS users:** symlinks across container mounts can behave
> unpredictably. Stick with the copy-based approach above unless you
> know your add-on's mount semantics.

Restart HA.

---

## Verify the installation

After the restart completes, run through this checklist:

### 1. No errors in the Home Assistant log

**Settings → System → Logs**. Filter for `multisplit_zone_controller`
or `Multi-Split`. You should see **no `ERROR` entries**. Expected
benign INFO lines look like:

```
INFO (MainThread) [custom_components.multisplit_zone_controller]
  Setting up multisplit_zone_controller
INFO (MainThread) [homeassistant.setup]
  Setup of domain multisplit_zone_controller took N.NN seconds
```

If you see `ERROR Unable to load…manifest.json`, the directory was
copied to the wrong location — the manifest must live at
`<config>/custom_components/multisplit_zone_controller/manifest.json`.

### 2. Integration appears in the "Add Integration" dropdown

**Settings → Devices & Services → + Add Integration**. Type
`multi-split` in the search box; **Multi-Split Zone Controller**
should show up. (The first time it appears, HA may take a few
seconds to index it.)

> If it doesn't appear, your browser may have cached the old list:
> hard-refresh (Ctrl/Cmd-Shift-R) and try again, or open in a
> private window.

### 3. (Optional) Pure-Python check before restart

You can syntax-check the integration without restarting HA by
running, on any machine with Python 3.11+:

```bash
python -c "import ast, pathlib; \
  [ast.parse(p.read_text()) for p in \
   pathlib.Path('custom_components/multisplit_zone_controller').rglob('*.py')]"
```

A silent exit means every `.py` file parses cleanly.

---

## Next steps

You're installed but not yet configured. Pick your path:

* **UI configuration (recommended for first install):** follow
  [`SETUP.md` → Path A](SETUP.md). One config entry per outdoor
  unit, form-driven, with an embedded YAML editor for advanced
  knobs.
* **YAML configuration:** follow
  [`SETUP.md` → Path B](SETUP.md). Best for multi-group installs
  and version-controlled configuration.
* **Advanced features** (multi-sensor fusion, pre-conditioning,
  fan/aux tuning, humidity-aware scoring, full psychrometric
  comfort): see [`ADVANCED.md`](ADVANCED.md) — every YAML-only
  knob with a worked example and tuning guidance.

---

## Updating

### HACS

1. Open **HACS** in the sidebar.
2. If an update is available, the integration entry shows an
   "Update" button. Click it.
3. **Restart Home Assistant** after the download completes.

You can subscribe to update notifications via HACS's normal
notification settings.

### Manual / git

```bash
# Manual: redownload the new release zip and re-copy.

# Git working copy:
cd ~/code/ha-msz && git pull
# (No further copy needed if you symlinked.)
```

Then restart HA.

### Read the changelog before updating

Tagged releases include release notes describing breaking changes,
schema additions, and new diagnostic sensors. Check the
[Releases page](https://github.com/matt-blackstone/ha-zone-climate/releases)
before any version jump that crosses a minor (e.g. `0.1.x → 0.2.0`).

---

## Uninstalling

Order matters — remove the *config entries* before removing the
*integration code*, otherwise HA will log "config entry references
unknown integration" warnings on every startup until you clean it
up manually.

### Step 1 — Remove all UI config entries

**Settings → Devices & Services → Multi-Split Zone Controller**.
For each config entry listed, click **⋮ → Delete**.

### Step 2 — Remove any YAML configuration

Delete the `multisplit_zone_controller:` block (and any
`!include`d zone files) from `configuration.yaml`.

### Step 3 — Remove the integration code

```bash
rm -rf /path/to/ha/config/custom_components/multisplit_zone_controller
```

### Step 4 — Restart Home Assistant

**Settings → System → Restart Home Assistant**.

The managed `climate.*`, diagnostic `sensor.*`, and
`binary_sensor.*` entities will no longer appear. The upstream head
climates, room sensors, fan entities, and aux switches are
untouched — this integration is a coordinator over existing
entities; it does not own them.

### (Optional) Remove from HACS

**HACS → Multi-Split Zone Controller → ⋮ → Remove**.

Removing from HACS does not delete the integration directory if
you've already removed it manually.

---

## Troubleshooting

### "Multi-Split Zone Controller" doesn't appear in the Add Integration list

* Did you restart HA after copying the files? The integration
  registry only re-scans on startup.
* Check the log on restart for `ERROR` lines mentioning
  `multisplit_zone_controller` — a malformed `manifest.json` or
  syntax error in any `.py` file blocks the integration from
  loading.
* Verify the directory layout: `manifest.json` must be at
  `<config>/custom_components/multisplit_zone_controller/manifest.json`,
  *not* one level deeper or shallower.
* Browser cache: hard-refresh or open in a private window.

### `ERROR ... config flow could not be loaded`

Usually means `config_flow.py` raised an exception at import time.
Look up the actual traceback in the log. Common causes:

* HA version is older than 2024.1 (some `selector` types missing).
  Upgrade HA.
* Partial copy — only some files made it across. Re-copy the
  full directory.

### `WARNING ... requires extra ... not installed`

This integration declares no external requirements
(`"requirements": []` in `manifest.json`). If you see this warning
referencing `multisplit_zone_controller`, you've likely got a
corrupted or hand-edited copy. Reinstall.

### Existing climate / sensor entities went `unavailable` after install

The integration does not modify upstream entities. If installing
caused a cascade of unavailable entities, it's almost certainly a
coincident HA upgrade or a YAML typo elsewhere. Disable the
integration (Settings → Devices & Services → Multi-Split Zone
Controller → ⋮ → Disable) and confirm the upstream entities
recover; if they do not, the issue is upstream.

### "Detected legacy switch / sensor template" warnings

Not from this integration — those are HA's own warnings about the
legacy `platform: template` syntax under `switch:` / `sensor:`.
The integration's test sandboxes already use the modern
`template:` block syntax; if you're seeing the warning in your
own config, migrate per HA's [template integration migration
guide](https://www.home-assistant.io/integrations/template/).

### Coordinator log spam after configuring zones

A new install with mis-targeted entity IDs will log warnings on
every coordinator tick (default every 5 seconds). Check for:

* `head_climate` entity IDs that don't exist or are `unavailable`.
* Aux device entity IDs that are unreachable.

These warnings are rate-limited per (entity, mode) — they will
log once per distinct missing target rather than spam every
tick. See *Per-zone dispatch resilience* in the design overview
for the full behaviour.

### Filing a bug

Open an issue at the
[issue tracker](https://github.com/matt-blackstone/ha-zone-climate/issues).
Include:

* Home Assistant version (Settings → About → version line).
* Install type (HAOS / Supervised / Container / Core).
* Integration version (Settings → Devices & Services →
  Multi-Split Zone Controller → ⋮ → System info, or read
  `manifest.json:version`).
* Relevant log lines (Settings → System → Logs → Download).
* The minimum YAML / UI config that reproduces the issue.

---

## What's next

* [`SETUP.md`](SETUP.md) — first-config walkthrough (UI and YAML).
* [`ADVANCED.md`](ADVANCED.md) — every YAML-only feature with
  worked examples.
* [`design-overview.md`](design-overview.md) — architecture,
  decision rationale, future psychrometric work.
* [`README.md`](README.md) — project status, repository layout,
  UI roadmap, hardware-safety roadmap.
