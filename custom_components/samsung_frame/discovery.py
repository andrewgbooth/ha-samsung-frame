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
PROBE_TIMEOUT = 1.5


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


MDNS_TYPES = ("_airplay._tcp.local.", "_samsungmsf._tcp.local.", "_raop._tcp.local.")
MDNS_WAIT = 3.0
PROBE_CONCURRENCY = 64


def subnet_candidates(ip: Optional[str]) -> list[str]:
    """Every other address in the /24 containing ip."""
    if not ip or ip.startswith("127."):
        return []
    prefix = ip.rsplit(".", 1)[0]
    return [f"{prefix}.{i}" for i in range(1, 255) if f"{prefix}.{i}" != ip]


async def _ssdp_hosts(hass) -> set[str]:
    from homeassistant.components import ssdp

    infos = await ssdp.async_get_discovery_info_by_st(hass, SSDP_ST)
    return {h for i in infos if (h := host_from_location(i.ssdp_location))}


async def _mdns_hosts(hass) -> set[str]:
    """Browse AirPlay / Samsung MSF via HA's shared zeroconf instance."""
    from homeassistant.components import zeroconf
    from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo

    azc = await zeroconf.async_get_async_instance(hass)
    names: set[tuple[str, str]] = set()

    def on_change(zeroconf, service_type, name, state_change) -> None:
        names.add((service_type, name))

    browser = AsyncServiceBrowser(azc.zeroconf, list(MDNS_TYPES), handlers=[on_change])
    try:
        await asyncio.sleep(MDNS_WAIT)
    finally:
        await browser.async_cancel()
    hosts: set[str] = set()
    for service_type, name in names:
        info = AsyncServiceInfo(service_type, name)
        if await info.async_request(azc.zeroconf, 1500):
            hosts.update(a for a in info.parsed_addresses() if "." in a)
    return hosts


async def async_scan(hass) -> list[FoundTv]:
    """Frames found via SSDP, mDNS and a /24 probe, each confirmed by REST."""
    from homeassistant.components import network

    hosts: set[str] = set()
    for name, coro in (
        ("ssdp", _ssdp_hosts(hass)),
        ("mdns", _mdns_hosts(hass)),
    ):
        try:
            hosts |= await coro
        except Exception:  # noqa: BLE001 - one method failing must not stop the rest
            _LOGGER.debug("%s discovery failed", name, exc_info=True)
    try:
        hosts.update(subnet_candidates(await network.async_get_source_ip(hass)))
    except Exception:  # noqa: BLE001
        _LOGGER.debug("Subnet detection failed", exc_info=True)

    gate = asyncio.Semaphore(PROBE_CONCURRENCY)

    async def check(host: str) -> Optional[FoundTv]:
        async with gate:
            return await async_probe(hass, host)

    results = await asyncio.gather(*(check(h) for h in sorted(hosts)))
    return sorted((r for r in results if r), key=lambda t: t.name)
