"""State machine tests against a fake TV that mimics the quirks we measured."""

import asyncio

import pytest

from custom_components.samsung_frame.const import build_inputs, names_are_valid
from custom_components.samsung_frame.controller import (
    FrameController,
    Snapshot,
    Timings,
)

NAMES = {
    "hdmi1_name": "CCTV",
    "hdmi2_name": "Kodi",
    "hdmi3_name": "Apple TV",
    "art_name": "Art Mode",
}


class Clock:
    def __init__(self):
        self.now = 0.0

    async def sleep(self, s):
        self.now += s

    def monotonic(self):
        return self.now


class FakeTV:
    """Quirks: power key toggles art; select ignored in art; optimistic echo."""

    def __init__(self, clock, power=True, art=False, source="hdmi3",
                 boot_art=False, cec_revert=False, art_known=True):
        self.clock = clock
        self.power, self.art, self.source = power, art, source
        self.boot_art, self.cec_revert, self.art_known = boot_art, cec_revert, art_known
        self.echo = None
        self.echo_until = 0.0
        self.keys = []

    async def snapshot(self, refresh=False):
        src = self.source
        if self.clock.now < self.echo_until and self.echo:
            src = self.echo
        art = (self.art if self.art_known else None) if self.power else False
        return Snapshot(self.power, art, src if self.power else None)

    async def power_on(self):
        self.power, self.art = True, self.boot_art

    async def power_off(self):
        self.power, self.art = False, False

    async def send_key(self, key):
        self.keys.append(key)
        if key == "KEY_POWER" and self.power:
            self.art = not self.art

    async def select_input(self, key):
        self.echo, self.echo_until = key, self.clock.now + 15
        if self.power and not self.art and not self.cec_revert:
            self.source = key


def make(tv_kwargs=None, **opts):
    clock = Clock()
    tv = FakeTV(clock, **(tv_kwargs or {}))
    ctl = FrameController(
        tv, build_inputs({**NAMES, **opts}),
        timings=Timings(), sleep=clock.sleep, monotonic=clock.monotonic,
    )
    return tv, ctl


def run(coro):
    return asyncio.run(coro)


@pytest.mark.parametrize("name,key", [("CCTV", "hdmi1"), ("Kodi", "hdmi2"), ("Apple TV", "hdmi3")])
def test_from_fully_off_each_hdmi(name, key):
    async def go():
        tv, ctl = make(dict(power=False, art=False, source="hdmi3"))
        assert await ctl.async_select_source(name)
        assert tv.power and not tv.art and tv.source == key
        assert ctl.source == name and ctl.is_on
    run(go())


def test_from_fully_off_cold_boot_lands_in_art_then_input():
    async def go():
        tv, ctl = make(dict(power=False, boot_art=True))
        assert await ctl.async_select_source("Kodi")
        assert tv.source == "hdmi2" and not tv.art
        assert tv.keys == ["KEY_POWER"]  # one wake press, no blind toggles
    run(go())


def test_from_art_wakes_then_selects():
    async def go():
        tv, ctl = make(dict(art=True, source="hdmi3"))
        assert await ctl.async_select_source("CCTV")
        assert tv.source == "hdmi1" and not tv.art
        assert tv.keys == ["KEY_POWER"]
    run(go())


def test_art_mode_from_input_and_from_off():
    async def go():
        tv, ctl = make()
        assert await ctl.async_select_source("Art Mode")
        assert tv.art and ctl.source == "Art Mode"
        tv2, ctl2 = make(dict(power=False))
        assert await ctl2.async_select_source("Art Mode")
        assert tv2.art
    run(go())


def test_art_not_pressed_when_already_art():
    async def go():
        tv, ctl = make(dict(art=True))
        assert await ctl.async_select_source("Art Mode")
        assert tv.keys == []
    run(go())


def test_art_unknown_never_presses_power():
    async def go():
        tv, ctl = make(dict(art_known=False))
        assert not await ctl.async_select_source("Art Mode")
        assert tv.keys == []
    run(go())


def test_turn_off_is_full_off_and_turn_on_resumes_last():
    async def go():
        tv, ctl = make(dict(source="hdmi3"))
        await ctl.async_refresh()
        assert await ctl.async_select_source("Kodi")
        assert await ctl.async_turn_off()
        assert not tv.power and not tv.art and not ctl.is_on and ctl.source is None
        assert await ctl.async_turn_on()
        assert tv.source == "hdmi2" and ctl.source == "Kodi"
    run(go())


def test_turn_on_resumes_art_mode():
    async def go():
        tv, ctl = make()
        assert await ctl.async_select_source("Art Mode")
        assert await ctl.async_turn_off()
        assert await ctl.async_turn_on()
        assert tv.art and ctl.source == "Art Mode"
    run(go())


def test_remote_changes_are_reflected_and_remembered():
    async def go():
        tv, ctl = make(dict(source="hdmi3"))
        await ctl.async_refresh()
        assert ctl.source == "Apple TV"
        tv.source = "hdmi1"
        await ctl.async_refresh()
        assert ctl.source == "CCTV"
        tv.art = True
        await ctl.async_refresh()
        assert ctl.source == "Art Mode"
        tv.power = tv.art = False
        await ctl.async_refresh()
        assert ctl.source is None and not ctl.is_on
        assert ctl.last_input_name == "Art Mode"
    run(go())


def test_failed_switch_does_not_lie_about_source():
    """CEC pulls the input back: optimistic echo must not become the source."""
    async def go():
        tv, ctl = make(dict(source="hdmi3", cec_revert=True))
        await ctl.async_refresh()
        assert not await ctl.async_select_source("Kodi")
        assert ctl.source == "Apple TV"
        assert ctl.last_input_name == "Apple TV"
    run(go())


def test_source_not_reported_during_transition():
    async def go():
        tv, ctl = make(dict(source="hdmi3"))
        await ctl.async_refresh()
        seen = []
        orig = tv.select_input

        async def spy(key):
            await orig(key)
            await ctl.async_refresh()  # skipped: lock held
            seen.append(ctl.source)

        tv.select_input = spy
        await ctl.async_select_source("Kodi")
        assert seen == ["Apple TV"]
    run(go())


def test_tv_input_disabled_by_default():
    async def go():
        tv, ctl = make()
        assert ctl.source_list == ["CCTV", "Kodi", "Apple TV", "Art Mode"]
        with pytest.raises(ValueError):
            await ctl.async_select_source("TV")
    run(go())


def test_tv_input_enabled_and_custom_names():
    async def go():
        tv, ctl = make(enable_tv=True, tv_name="Freeview", hdmi1_name="Cameras")
        assert ctl.source_list == ["Cameras", "Kodi", "Apple TV", "Freeview", "Art Mode"]
        assert await ctl.async_select_source("Freeview")
        assert tv.source == "digital_tv" and ctl.source == "Freeview"
    run(go())


def test_tv_source_hidden_when_tv_disabled():
    async def go():
        tv, ctl = make(dict(source="digital_tv"))
        await ctl.async_refresh()
        assert ctl.source is None and ctl.is_on
    run(go())


def test_turn_on_falls_back_when_last_input_gone():
    async def go():
        tv, ctl = make(dict(power=False))
        ctl.restore_last_input("Freeview")  # no longer an input
        assert ctl.last_input_name is None
        assert await ctl.async_turn_on()
        assert tv.source == "hdmi1"
    run(go())


def test_name_validation():
    assert names_are_valid(NAMES)
    assert not names_are_valid({**NAMES, "hdmi2_name": "cctv"})
    assert not names_are_valid({**NAMES, "hdmi2_name": " "})
    # a clashing TV name only matters when TV is enabled
    assert names_are_valid({**NAMES, "tv_name": "Kodi"})
    assert not names_are_valid({**NAMES, "tv_name": "Kodi", "enable_tv": True})


def test_discovery_parsing_keeps_only_frames():
    from custom_components.samsung_frame.discovery import (
        host_from_location,
        parse_device_info,
    )

    frame = {"name": "Master Bedroom", "device": {
        "FrameTVSupport": "true", "modelName": "QE43LS03DAU",
        "wifiMac": "20:15:DE:3D:B9:06"}}
    tv = parse_device_info("10.0.0.5", frame)
    assert tv.mac == "20:15:de:3d:b9:06" and tv.name == "Master Bedroom"
    assert parse_device_info("10.0.0.6", {"device": {"FrameTVSupport": "false"}}) is None
    assert parse_device_info("10.0.0.7", {}) is None
    assert host_from_location("http://10.0.0.5:9197/dmr") == "10.0.0.5"
    assert host_from_location(None) is None


def test_subnet_candidates():
    from custom_components.samsung_frame.discovery import subnet_candidates

    hosts = subnet_candidates("10.22.153.7")
    assert len(hosts) == 253 and "10.22.153.7" not in hosts and "10.22.153.106" in hosts
    assert subnet_candidates(None) == [] and subnet_candidates("127.0.0.1") == []
