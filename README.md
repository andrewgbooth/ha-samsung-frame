# Samsung Frame for Home Assistant

One `media_player` (device class `tv`) for a Samsung The Frame, with correct
power, Art mode and per-HDMI input handling. No helpers, scripts or YAML.

- Inputs: HDMI 1-3, optional **TV** (tuner, off by default) and **Art Mode**.
- **Every input name is editable** in the config and options flow. Renames and
  the TV toggle apply immediately (the entry reloads, no restart).
- `turn_off` is a full power off; `turn_on` resumes the last input (including Art Mode).
- `source` reflects reality, including changes from the physical remote, and a
  failed switch never leaves a made-up source.

## How it works (local first)

| Need | How | Where |
|---|---|---|
| Art mode state, wake/sleep key | samsungtvws WebSocket, port 8002 | local |
| Wake from fully off | Wake-on-LAN (if MAC set), fallback SmartThings | local, then cloud |
| Select a specific HDMI input | core SmartThings media_player entity | cloud |
| Full power off | core SmartThings media_player entity | cloud |

Local HDMI selection is not available on this model as far as we know
(`KEY_HDMI1-3` do nothing, `KEY_HDMI` only cycles, `KEY_POWER` toggles Art mode),
so HDMI selection and full off still go through the SmartThings entity you
already have. No second login: the integration just calls that entity.

The logic lives in `controller.py` (pure Python, unit tested). Key rules:
never press power blindly, wake from Art mode before selecting, and verify after
the SmartThings poll window instead of trusting its optimistic echo.

## Install

1. Copy `custom_components/samsung_frame` into your HA config, or add this repo to HACS as a custom repository.
2. Restart Home Assistant.
3. Add the **Samsung Frame** integration, enter host (and MAC for Wake-on-LAN), pick the SmartThings media player, press Allow on the TV, then name your inputs.

Before adding the HomeKit accessory, create it in **accessory mode** so it appears as a TV.

## Status: untested on hardware

Unit tests pass (`pytest`), but this has **not** yet been run against the TV or a
real Home Assistant. Unverified:

- samsungtvws async art-channel calls (`get_artmode`) and key sending on this TV
- Wake-on-LAN from the fully-off state after a SmartThings off
- whether `digital_tv` is accepted by SmartThings with no tuner connected
- HDMI-CEC from the Apple TV pulling the input (a reverted switch is detected and
  retried once, then reported as failed)
- whether the HomeKit accessory needs a reload after renaming or toggling TV
- Timings (`Timings` in `controller.py`) are taken from measured delays and may need tuning

## Manual test checklist

1. From fully off: select each of CCTV, Kodi, Apple TV, Art Mode.
2. From Art mode: select each HDMI input.
3. From each HDMI input: select Art Mode, turn off (must be fully off), turn on (must resume last input).
4. Change input with the physical remote: the entity source updates.
5. HomeKit accessory: power, input picker, input names.
6. Rename an input in options: source list, current source and HomeKit show the new name without a restart.
7. Enable TV in options: "TV" appears and is selectable; disable it again: it disappears.

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-test.txt
.venv/bin/python -m pytest
```
