<p align="center">
  <img src="custom_components/samsung_frame/brand/icon@2x.png" alt="Samsung Frame" width="160">
</p>

<h1 align="center">Samsung Frame for Home Assistant</h1>

<p align="center">
  <a href="https://github.com/hacs/integration"><img alt="HACS Custom" src="https://img.shields.io/badge/HACS-Custom-orange.svg"></a>
  <a href="https://github.com/andrewgbooth/ha-samsung-frame/releases"><img alt="Release" src="https://img.shields.io/github/v/release/andrewgbooth/ha-samsung-frame"></a>
  <a href="LICENSE"><img alt="License" src="https://img.shields.io/github/license/andrewgbooth/ha-samsung-frame"></a>
</p>

One Home Assistant `media_player` for your Samsung **The Frame** TV that gets the awkward bits right: real power on and off, Art Mode, and a picker for each HDMI input. No helpers, scripts or YAML.

> Not affiliated with or endorsed by Samsung. "Samsung" and "The Frame" are trademarks of their owner.

## Features

- **One entity** (device class `tv`) with power and an input picker, linked to your SmartThings TV device.
- **Your own input names:** HDMI 1, 2 and 3 plus **Art Mode**, each renameable.
- **Optional TV (tuner) input,** off by default.
- **Correct power:** turn off is a full power off (never Art Mode); turn on resumes your last input, including Art Mode.
- **Responsive:** your choice shows immediately (marked `source_unconfirmed`), is confirmed in the background and corrected if the TV did not switch. A new command cancels an unfinished one.
- **Instant Art Mode state,** pushed by the TV, with no polling.
- **Stays in sync** with the physical remote.
- **Works as a HomeKit TV accessory** (power and input picker).
- **Automatic setup:** finds your Frame and the matching SmartThings device.
- **All configured in the UI.** Rename inputs or toggle TV at any time without a restart.

## Requirements

- Home Assistant 2026.10 or newer.
- A Samsung The Frame (developed against a 2024 model, QE43LS03D) on the same network as Home Assistant.
- The core **SmartThings** integration, signed in to your Samsung account, with your TV as a media player. Setup checks this and stops with instructions if it is missing.

## Installation

### HACS (recommended)

1. In HACS, open the menu and choose **Custom repositories**.
2. Add `https://github.com/andrewgbooth/ha-samsung-frame` with type **Integration**.
3. Install **Samsung Frame** and restart Home Assistant.

### Manual

Copy `custom_components/samsung_frame` into your Home Assistant `config/custom_components/` folder and restart.

## Setup

1. Go to **Settings > Devices & services > Add integration** and choose **Samsung Frame** (or accept the TV under **Discovered**).
2. Pick your TV from the Frames found on your network, or choose **Enter manually** and type its IP address. A MAC address is optional but enables fast Wake-on-LAN.
3. The matching SmartThings media player is chosen automatically. If there is no unique match, setup stops and tells you why.
4. Turn the TV on and press **Allow** on it when asked (once).
5. Name your inputs and choose whether to enable the TV input.

Change names or the TV toggle later with **Configure** on the integration.

## HomeKit

Add the TV in the HomeKit Bridge in **accessory mode** so it appears as a TV in Apple Home. The input picker follows the names you set here. After renaming inputs or toggling TV you may need to reload the HomeKit accessory.

## How it works

| Task | Method |
|---|---|
| Art Mode state | Local, pushed by the TV over a persistent connection |
| Wake from Art Mode, send keys | Local remote channel |
| Know if the TV is on | Local status check, plus SmartThings |
| Wake from fully off | Wake-on-LAN (if you gave a MAC), then the power key, then SmartThings |
| Choose an HDMI input | SmartThings (your existing integration, no extra login) |
| Fully power off | SmartThings |

To our knowledge this TV cannot select a specific HDMI input or fully power off locally, which is why SmartThings is still used for those two actions. The TV's power key only toggles between Art Mode and TV, so the integration never presses it blindly.

## Known limitations

- Tested by the author on one TV (2024 Frame). Other models and firmware may behave differently; please open an issue.
- Wake-on-LAN from fully off and the TV (tuner) input are lightly tested.
- HDMI-CEC devices (such as an Apple TV) can pull the input back. A reverted switch is retried once, then reported as failed.
- SmartThings can be slow to report a changed input. Your choice shows immediately and is confirmed after about 16 seconds.

## Troubleshooting

- **Setup says SmartThings is missing or has no devices:** add or fix the core SmartThings integration, then start again.
- **No TV found automatically:** choose **Enter manually**. Automatic scanning covers the same /24 subnet as Home Assistant.
- **Repeated Allow prompts for "HA Samsung Frame":** use the **Repair** notification to pair again, and check the TV's **Settings > General > External Device Manager > Device Connection Manager**.
- **An Allow prompt for a different name** (for example "[Home Assistant]") comes from another integration, not this one.
- **Art Mode or switching misbehaves:** enable debug logging for `custom_components.samsung_frame` and open an issue with the log.

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-test.txt
.venv/bin/python -m pytest
```

The state machine is in `custom_components/samsung_frame/controller.py`. It is pure Python and tested against a fake TV that mimics quirks measured on a real Frame.

## Licence

MIT. See [LICENSE](LICENSE).
