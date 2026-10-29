"""Real backend: local WebSocket (keys, Art mode, WoL) + SmartThings entity.

Local, fast:   Art mode state, power key, Wake-on-LAN.
Via the existing core SmartThings entity (no extra login, no new cloud code):
               power state, full power off, HDMI input selection.

The local Tizen API has no verified way to pick a specific HDMI input or to
power fully off on this model (KEY_HDMI1-3 do nothing, KEY_POWER toggles Art
mode), which is why SmartThings is still used for those two things.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from homeassistant.core import HomeAssistant

from .const import PORT
from .controller import Snapshot

_LOGGER = logging.getLogger(__name__)

_CLIENT_NAME = "HomeAssistant"


async def async_pair(host: str) -> str:
    """Connect once to obtain a token. The TV shows an Allow prompt."""
    from samsungtvws.async_remote import SamsungTVWSAsyncRemote

    remote = SamsungTVWSAsyncRemote(
        host=host, port=PORT, name=_CLIENT_NAME, timeout=30
    )
    try:
        await remote.start_listening()
        token = remote.token
    finally:
        await remote.close()
    if not token:
        raise ConnectionError("TV did not return a token")
    return token


class HABackend:
    def __init__(
        self,
        hass: HomeAssistant,
        host: str,
        token: str,
        smartthings_entity: str,
        mac: Optional[str] = None,
    ) -> None:
        self._hass = hass
        self._host = host
        self._token = token
        self._st = smartthings_entity
        self._mac = mac

    # ----- SmartThings (core entity) --------------------------------

    async def _st_call(self, service: str, data: Optional[dict] = None) -> None:
        await self._hass.services.async_call(
            "media_player",
            service,
            {"entity_id": self._st, **(data or {})},
            blocking=True,
        )

    async def _st_refresh(self) -> None:
        await self._hass.services.async_call(
            "homeassistant", "update_entity", {"entity_id": self._st}, blocking=True
        )

    # ----- local -----------------------------------------------------

    async def _port_open(self, timeout: float = 1.0) -> bool:
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(self._host, PORT), timeout
            )
        except (OSError, asyncio.TimeoutError):
            return False
        writer.close()
        return True

    def _art_mode_blocking(self) -> Optional[bool]:
        """Blocking art-channel query (samsungtvws has no async art client)."""
        from samsungtvws.art import SamsungTVArt

        art = SamsungTVArt(
            host=self._host, port=PORT, token=self._token, name=_CLIENT_NAME,
            timeout=5,
        )
        try:
            return str(art.get_artmode()).lower() == "on"
        except Exception:  # noqa: BLE001
            _LOGGER.debug("Art mode query failed", exc_info=True)
            return None
        finally:
            try:
                art.close()
            except Exception:  # noqa: BLE001
                pass

    async def _art_mode(self) -> Optional[bool]:
        """UNVERIFIED on the target TV: art channel get_artmode."""
        return await self._hass.async_add_executor_job(self._art_mode_blocking)

    # ----- Backend protocol ------------------------------------------

    async def snapshot(self, refresh: bool = False) -> Snapshot:
        if refresh:
            try:
                await self._st_refresh()
            except Exception:  # noqa: BLE001
                _LOGGER.debug("SmartThings refresh failed", exc_info=True)
        state = self._hass.states.get(self._st)
        power = state is not None and state.state == "on"
        source = None
        if power and state is not None:
            raw = state.attributes.get("source")
            source = str(raw).lower() if raw else None
        art: Optional[bool] = False
        if power:
            art = await self._art_mode()
        return Snapshot(power=power, art=art, source=source)

    async def power_on(self) -> None:
        if self._mac:
            from wakeonlan import send_magic_packet

            await self._hass.async_add_executor_job(send_magic_packet, self._mac)
            for _ in range(8):
                await asyncio.sleep(0.5)
                if await self._port_open():
                    return
        await self._st_call("turn_on")

    async def power_off(self) -> None:
        await self._st_call("turn_off")

    async def send_key(self, key: str) -> None:
        from samsungtvws.async_remote import SamsungTVWSAsyncRemote
        from samsungtvws.remote import SendRemoteKey

        remote = SamsungTVWSAsyncRemote(
            host=self._host, port=PORT, token=self._token, name=_CLIENT_NAME,
            timeout=5,
        )
        try:
            await remote.start_listening()
            await remote.send_command(SendRemoteKey.click(key))
        finally:
            await remote.close()

    async def select_input(self, key: str) -> None:
        await self._st_call("select_source", {"source": key})
