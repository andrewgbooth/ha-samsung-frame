"""HABackend._on_art_message parsing, with Home Assistant stubbed out."""

import asyncio
import json
import sys
import types

import pytest

if "homeassistant.core" not in sys.modules:
    ha = types.ModuleType("homeassistant")
    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = object
    ha.core = core
    sys.modules.setdefault("homeassistant", ha)
    sys.modules["homeassistant.core"] = core

from custom_components.samsung_frame.backend import HABackend  # noqa: E402


def _backend():
    b = object.__new__(HABackend)
    b._art = None
    b._on_change = None
    return b


def _d2d(inner):
    return json.dumps({"event": "d2d_service_message", "data": json.dumps(inner)})


@pytest.mark.parametrize(
    "inner,expected",
    [
        ({"event": "get_artmode_status", "request_id": "x", "value": "off"}, False),
        ({"event": "get_artmode_status", "request_id": "x", "value": "on"}, True),
        ({"event": "artmode_status", "value": "on"}, True),
        ({"event": "art_mode_changed", "status": "on"}, True),
        ({"event": "art_mode_changed", "status": "off"}, False),
    ],
)
def test_art_status_messages(inner, expected):
    b = _backend()
    asyncio.run(b._on_art_message(None, _d2d(inner)))
    assert b._art is expected
