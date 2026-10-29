"""Find Samsung Frame TVs on the network.

Uses Home Assistant's SSDP cache (instant, no extra scanning code), then asks
each candidate's REST endpoint (port 8001) who it is. Only Frames are kept.
HA imports are lazy so the parsing is unit-testable.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
from typing import Any, Optional
from urllib.parse import urlparse

_LOGGER = logging.getLogger(__name__)

SSDP_ST = "urn:samsung.com:device:RemoteControlReceiver:1"
INFO_PORT = 8001
PROBE_TIMEOUT = 3


@dataclass(frozen=True)
class FoundTv:
    host: str
    mac: Optional[str]
    model: str
    name: str

    @property
    def label(self) -> str:
        return f"{self.name} ({self.model}, {self.host})"


def parse_device_info(host: str, payload: dict[str, Any]) -> Optional[FoundTv]:
    """Return a FoundTv if the payload describes a Frame, else None."""
    device = payload.get("device") or {}
    if str(device.get("FrameTVSupport", "")).lower() != "true":
        return None
    mac = device.get("wifiMac")
    return FoundTv(
        host=host,
        mac=str(mac).lower() if mac else None,
        model=str(device.get("modelName") or "Frame"),
        name=str(payload.get("name") or device.get("name") or "Samsung Frame"),
    )


def host_from_location(location: Optional[str]) -> Optional[str]:
    return urlparse(location).hostname if location else None


async def async_probe(hass, host: str) -> Optional[FoundTv]:
    """Ask a host whether it is a Frame. None if unreachable or not a Frame."""
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    try:
        async with async_get_clientsession(hass).get(
            f"http://{host}:{INFO_PORT}/api/v2/", timeout=PROBE_TIMEOUT
        ) as resp:
            payload = await resp.json(content_type=None)
    except (OSError, asyncio.TimeoutError, ValueError):
        _LOGGER.debug("Probe of %s failed", host, exc_info=True)
        return None
    except Exception:  # noqa: BLE001 - aiohttp errors, keep discovery quiet
        _LOGGER.debug("Probe of %s failed", host, exc_info=True)
        return None
    return parse_device_info(host, payload)


async def async_scan(hass) -> list[FoundTv]:
    """Frames currently known to HA's SSDP listener."""
    from homeassistant.components import ssdp

    infos = await ssdp.async_get_discovery_info_by_st(hass, SSDP_ST)
    hosts = sorted(
        {h for i in infos if (h := host_from_location(i.ssdp_location))}
    )
    results = await asyncio.gather(*(async_probe(hass, h) for h in hosts))
    return [r for r in results if r is not None]
