# Brief for Claude Code: a single Home Assistant integration for a Samsung Frame TV

> Revision note: this version adds (a) fully customisable input names and (b) an enable/disable option for the TV (digital tuner) input. Changed sections are marked **[CHANGED]**.

## The ask

Design and (once the architecture is agreed) build the simplest possible Home Assistant integration that exposes my Samsung The Frame TV as ONE media_player entity which behaves correctly, with no helper, script, automation or YAML "universal" media player gluing things together.

FIRST: before writing code, investigate and propose the simplest architecture (see "Architecture questions"). Show me the options with trade-offs and a recommendation, then wait for my go-ahead. Prefer the least code, fewest moving parts and fewest external dependencies that still meet the behaviour below.

## Environment

- Home Assistant 2026.10.4, Home Assistant OS on a Raspberry Pi (aarch64). Custom components and HACS are in use.
- TV: Samsung The Frame 43", model QE43LS03DAUXXU (Tizen, 2024 model), IP 10.22.153.106, MAC 20:15:de:3d:b9:06. Local WebSocket API on port 8002 (token auth). Wi-Fi connected.
- HDMI wiring: HDMI 1 = CCTV, HDMI 2 = Kodi, HDMI 3 = Apple TV. **[CHANGED]** There is NO TV tuner/receiver connected, so the "TV" (digital_tv) input is **disabled by default**, but it must be available as an opt-in option (see contract item 1).
- The TV is also exposed to Apple Home (HomeKit). Constraints for that are below.

## Desired behaviour (the contract)

One media_player entity, device_class tv, with:

1. **[CHANGED]** source_list is built from the *enabled* inputs, in this order: HDMI 1, HDMI 2, HDMI 3, optional TV, Art Mode.
   - Default with my setup: CCTV, Kodi, Apple TV, Art Mode.
   - **Customisable names:** every input's display name is user-editable in the config flow and options flow (no YAML). Defaults: "HDMI 1", "HDMI 2", "HDMI 3", "TV", "Art Mode" (the first-run flow pre-fills my CCTV / Kodi / Apple TV names if simple to do, otherwise I enter them once).
   - **TV input toggle:** a boolean option "Enable TV input" (default off). When off, the TV input is absent from source_list and cannot be selected. When on, a "TV" entry (SmartThings `digital_tv` / local equivalent, to be investigated) appears and selecting it makes the TV show its tuner input, with the same wake / power-on handling as HDMI inputs.
   - Should HDMI ports be individually enable/disable-able too? Nice to have if free; not required.
   - Names must be unique (validate in the flow). Renaming or toggling takes effect without a Home Assistant restart (reload the entry on options update). If the TV is on a source that gets disabled or renamed, the entity must report the correct (new) name or no source rather than a stale one.
   - The HDMI mapping (which name belongs to which physical port) is configurable in the same flow.
2. select_source on an HDMI input (or TV, if enabled): whatever state the TV is in (fully off, Art mode, on another input), end up showing that input. Fully off means power on first. Art mode means wake it first.
3. select_source "Art Mode": end up in Art mode (from fully off, or from any input).
4. turn_off = FULLY off (display off). It must never leave the TV in Art mode.
5. turn_on = power on and resume the last selected input (including Art Mode if that was last). If the last input has since been disabled, fall back to the first enabled input.
6. state/source must reflect reality: on/off, and source = the current input (using its custom name) or Art Mode. Changes made with the physical remote must be reflected. If the TV is on its tuner input while TV is disabled, source should be reported as unknown/None, not hidden or mislabelled.
7. Works well as a HomeKit TV accessory (see below): power toggle plus an input picker with the enabled names.
8. Quiet failure: no notifications. Log at debug/warning, and make sure a failed switch does not leave the entity lying about the source.

## What we verified (real tests on this TV)

Integrations and what each can/can't do:

- Core "samsungtv" integration (local):
  - source_list is only "TV" and "HDMI". No per-HDMI selection, no Art mode state.
  - remote entity KEY_POWER works. KEY_HDMI cycles inputs. KEY_HDMI2 (direct key) did NOTHING on this model. KEY_HDMI1/3 not tested but assume the same.
  - turn_off sends a power key which, on a Frame, goes to Art mode rather than fully off.
- Core "smartthings" integration (cloud):
  - source_list = digital_tv, hdmi1, hdmi2, hdmi3. select_source works for HDMI inputs when the TV is awake (not in Art mode).
  - select_source is silently IGNORED while the TV is in Art mode.
  - State stays "on" while in Art mode, so it cannot detect Art mode.
  - The `source` attribute is updated OPTIMISTICALLY straight after a command, then corrected by the next poll roughly 10-20 s later. A check that reads `source` immediately after select_source gives a false pass.
  - turn_off FULLY powers the display off (verified, state goes to "off"); turn_on powers it back on.
  - Power/energy sensors are stale (read 0 W while on) and are not usable as a signal.
  - This SmartThings entry is a whole-home integration serving other devices; it cannot simply be removed.
- HACS "samsungtv_smart" (ollo69/ha-samsungtv-smart v0.14.5, local, set up with host only, no SmartThings token):
  - Exposes attribute art_mode_status = "on"/"off". This is the only reliable Art mode signal found. It flips within a few seconds.
  - source_list only "TV"/"HDMI" (per-HDMI names would need a SmartThings token, see below).
  - media_player.play_media with media_content_type "send_key" and media_content_id "KEY_POWER" works.
  - KEY_POWER while awake on an input -> enters Art mode (art_mode_status goes "on"). KEY_POWER while in Art mode -> wakes to the last input. So the power key is a TOGGLE between Art mode and TV, not an off switch.
  - The service samsungtv_smart.set_art_mode was called once and did NOT visibly change the TV (single test, not conclusive).
- Dead ends: DLNA (dlna_dmr) entity went unavailable; the old "samsungtv_tizen" integration is gone; the local power sensors are not useful.

Timings observed: wake from Art mode via power key ~4 s; SmartThings poll correction ~15 s; cold power-on from fully off to ready ~8-10 s.

Other behaviours seen:

- A switch to HDMI 2 was once apparently reverted to HDMI 3 while the Apple TV was awake. HDMI-CEC from the Apple TV may pull the active input. Not confirmed. Please check whether CEC needs handling.
- The SmartThings personal access token route (samsungtv_smart "ST_" sources) was not tried because SmartThings PATs reportedly expire after 24 hours. Verify this before relying on or ruling out a PAT.

## The glue we built (reference implementation to replace)

1. input_select.master_bedroom_tv_input: options CCTV, Kodi, Apple TV, Art Mode.
2. script.master_bedroom_tv_set_input(input):
   - If SmartThings state is not "on": SmartThings media_player.turn_on, wait up to 30 s for "on", delay 8 s.
   - If input == Art Mode: up to 2 times, if art_mode_status != "on": send KEY_POWER (samsungtv_smart), wait up to 15 s for art_mode_status "on", delay 3 s. If still not in Art mode, fail.
   - Else (HDMI): if art_mode_status == "on", send KEY_POWER and wait up to 15 s for it to clear, delay 4 s. Then SmartThings select_source(hdmiN), wait 15 s (poll correction), and if source != hdmiN or still in Art mode, fix (power key if Art mode) and resend once. If still wrong, fail quietly.
   - Finally set the helper to the chosen input.
3. Automation: mirrors TV state into the helper (art_mode_status "on" -> Art Mode; source hdmi1/2/3 -> CCTV/Kodi/Apple TV), skipped while the script is running.
4. YAML universal media_player "Master Bedroom TV Controller" (media_player.master_bedroom_tv_controller): state on if SmartThings is "on"; turn_on calls the script with the helper's value; turn_off calls SmartThings media_player.turn_off; select_source calls the script; source and source_list come from the helper. It needs a restart to load (no reload action exists for universal).

Lessons to build in:

- Never press power blindly: it toggles Art mode. Press it only when art_mode_status says Art mode is on (to wake), or when Art Mode is wanted and the TV is not in it.
- Do not trust the optimistic `source` echo. Verify after the poll window or from a source that is not optimistic.
- Order for HDMI from Art mode: wake, wait for Art mode to clear, short settle delay, then select input.
- Full off should use whatever cleanly powers the display off (SmartThings off worked). If a local-only way to do true off exists, say so.

## HomeKit constraints

- A TV media_player in HA's HomeKit Bridge must be created in ACCESSORY mode (a separate paired accessory) to show properly as a TV in Apple Home. The entity must expose device_class tv, a power state, source and source_list.
- Apple Home lets me rename inputs, but I want the names correct from the source.
- **[CHANGED]** The HomeKit input list is derived from source_list, so enabling/disabling TV or renaming inputs changes what Apple Home shows. Document whether the HomeKit accessory needs a reload (or re-pair) after such a change, and test it.
- I only want power and inputs. No extra controls beyond what the TV tile gives.

## Architecture questions to answer first

Investigate and report; keep it as simple as possible:

A. Can everything be done locally with no cloud? In particular: is there ANY local way to select a specific HDMI input on this Tizen model (WebSocket remote channel, ed.apps.launch with a source app id, UPnP/DMR MainTVAgent2 SetInputSource, a hidden REST endpoint in /api/v2, or the art/"samsung-frame-connect" channels)? Check samsungtvws and the sources of samsungtv_smart for what they do for sources. **[CHANGED]** Also check how to select the TV/tuner input specifically (e.g. KEY_TV, SmartThings `digital_tv`), since it is now an optional source.
B. Is there a local way to power the display fully off (not Art mode)? E.g. a long-press power key, KEY_POWEROFF, or an API call. Compare with SmartThings off.
C. If a cloud call is unavoidable for input selection, what is the minimum: SmartThings OAuth via the existing core integration's tokens (no second login), or a direct API client? Check how token refresh works and whether a PAT is a viable alternative.
D. Candidate architectures (rank by simplicity, then robustness):
   1. A small custom integration whose single media_player COMPOSES the existing core smartthings entity and the samsungtv_smart entity, using the HA service/state APIs (no new network code), with the state machine inside it.
   2. A standalone integration with its own local WebSocket client (art channel + remote keys) plus SmartThings only for input selection.
   3. Pure local (only if A and B have good answers).
   4. Contributing the missing pieces (e.g. a Frame/Art mode option) to samsungtv_smart, versus a separate integration.
E. Reliability design: a single state machine with explicit states (off, art, on-input-N, transitioning), event-driven verification instead of fixed delays where possible, bounded retries, and handling of the optimistic SmartThings echo and CEC. **[CHANGED]** The state machine must be driven by the configured input list (names and enabled flags are data, not hard-coded), so renaming or toggling TV needs no code path changes.

## Deliverable and constraints

- Config flow in the UI only. **[CHANGED]** Initial flow: host, then input setup (per-input name, HDMI port mapping, "Enable TV input" toggle). An **options flow** lets me change all names, the mapping and the TV toggle later, with the entry reloading automatically. No YAML, no helpers or scripts required.
- unique_id on the entity and device registry info; follow current Home Assistant integration quality guidelines and 2026.10 APIs.
- Unit tests for the state machine (fake TV clients), plus a manual test checklist I can run. **[CHANGED]** Tests must also cover: custom names appearing in source_list and as the reported source; TV disabled (absent from source_list, select_source rejected) and enabled (selectable, reported correctly); duplicate-name validation; and a disabled/renamed current source.
  1. From fully off: select each of CCTV, Kodi, Apple TV, Art Mode.
  2. From Art mode: select each HDMI input.
  3. From each HDMI input: select Art Mode, then turn off (must be fully off), then turn on (must resume the last input).
  4. Change input with the physical remote: entity source updates.
  5. HomeKit accessory: power, input picker, input names.
  6. **[CHANGED]** Rename an input in the options flow: source_list, current source and the HomeKit picker show the new name without a restart.
  7. **[CHANGED]** Enable the TV input in the options flow: "TV" appears and selecting it works (expect no signal, as no tuner is connected); disable it again: it disappears.
- Do not run anything that powers the TV or changes its settings without telling me first; I am at home and can watch it. Do not commit secrets or tokens.
- Where something is unverified, say so, and test it on the TV rather than assuming.
