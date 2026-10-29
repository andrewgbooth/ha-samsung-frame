# Samsung Frame for Home Assistant

A single Home Assistant `media_player` for your Samsung **The Frame** TV that gets the awkward bits right: proper power, Art Mode, and a picker for each HDMI input. No helpers, scripts or YAML needed.

> **Status: early release.** The logic is unit tested, but it has not yet been verified on real hardware. See [Known limitations](#known-limitations) and please report what you find.

## Features

- **One entity** (device class `tv`) with power and an input picker.
- **Per-input control:** HDMI 1, HDMI 2, HDMI 3 and **Art Mode**, each with a name you choose.
- **Optional TV (tuner) input**, off by default. Turn it on if you use the built-in tuner.
- **Correct power behaviour:** turning off fully powers the display off (never drops into Art Mode). Turning on resumes the last input, including Art Mode.
- **Reliable switching:** it wakes the TV from Art Mode or standby first, then selects the input, and confirms the change instead of trusting an optimistic status.
- **Stays in sync** with changes made using the physical remote.
- **Works as a HomeKit TV accessory** (power and input picker).
- **Automatic setup:** finds your Frame on the network and the matching SmartThings device.
- **Everything is configured in the UI.** Rename inputs or toggle the TV input at any time; changes apply without a restart.

## Requirements

- Home Assistant 2026.10 or newer.
- A Samsung The Frame (2024 Tizen model tested: QE43LS03D) on the same network as Home Assistant.
- The core **SmartThings** integration, signed in to your Samsung account, with your TV showing up as a media player. Setup checks this and stops with instructions if it is missing.

## Installation

### HACS (custom repository)

1. In HACS, open the menu and choose **Custom repositories**.
2. Add `https://github.com/andrewgbooth/ha-samsung-frame` as an **Integration**.
3. Install **Samsung Frame** and restart Home Assistant.

### Manual

1. Copy the `custom_components/samsung_frame` folder into your Home Assistant `config/custom_components/` folder.
2. Restart Home Assistant.

## Setup

1. Go to **Settings > Devices & services > Add integration** and choose **Samsung Frame**. (If your TV is discovered automatically, you may see it under **Discovered** instead.)
2. Pick your TV from the list of Frames found on your network. If none is found, choose **Enter manually** and type its IP address (the MAC address is optional but enables fast Wake-on-LAN).
3. The matching SmartThings media player is selected automatically. There is no manual picker; if no unique match exists, setup stops and tells you why.
4. Turn the TV on, then press **Allow** on the TV when it asks to let Home Assistant connect.
5. Name your inputs and choose whether to enable the TV input.

To change names or the TV toggle later, open the integration and choose **Configure**.

## HomeKit

To show the TV properly in Apple Home, add it in the HomeKit Bridge in **accessory mode** (a separate paired accessory). The input picker follows the names you set here. After renaming inputs or toggling TV, you may need to reload the HomeKit accessory (this is not yet verified).

## How it works

Where possible everything runs locally for speed.

| Task | Method |
|---|---|
| Detect Art Mode, wake from Art Mode | Local connection to the TV |
| Wake from fully off | Wake-on-LAN (if you gave a MAC), otherwise SmartThings |
| Choose a specific HDMI input | SmartThings (existing integration, no extra login) |
| Fully power off | SmartThings |

As far as we know, this TV model cannot select a specific HDMI input or fully power off locally, which is why SmartThings is still involved for those two actions. The TV's power key only toggles between Art Mode and TV, so the integration never presses it blindly.

## Known limitations

Unit tests pass, but these are **not yet verified on a real TV**:

- Art Mode detection and key sending over the local connection.
- Wake-on-LAN from the fully off state.
- Whether SmartThings accepts the TV input when no tuner is connected.
- Automatic discovery and SmartThings matching. If SmartThings does not expose your TV's MAC address, matching falls back to the model number, then the device name.
- HDMI-CEC devices (such as an Apple TV) pulling the input back. A reverted switch is retried once, then reported as failed.
- Switching timings are based on measured delays and may need tuning.

## Troubleshooting

- **Setup stops saying SmartThings is missing or has no devices:** add or fix the core SmartThings integration, then start again.
- **No TV found automatically:** choose **Enter manually**. Automatic scanning only covers the same /24 subnet as Home Assistant.
- **Pairing fails:** make sure the TV is on and press **Allow** when prompted.
- **Switching input does nothing:** the TV may be in Art Mode or slow to wake; check the Home Assistant log for warnings from `samsung_frame`.

## Manual test checklist

1. From fully off: select each HDMI input and Art Mode.
2. From Art Mode: select each HDMI input.
3. From each HDMI input: select Art Mode, turn off (must be fully off), then turn on (must resume the last input).
4. Change input with the physical remote: the entity updates.
5. HomeKit accessory: power, input picker and names.
6. Rename an input in **Configure**: the new name appears everywhere without a restart.
7. Enable the TV input in **Configure**: "TV" appears and can be selected; disable it again and it disappears.

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-test.txt
.venv/bin/python -m pytest
```

The state machine lives in `custom_components/samsung_frame/controller.py` and is pure Python, tested against a fake TV that mimics the quirks measured on a real Frame.

## Licence

MIT. See [LICENSE](LICENSE).
