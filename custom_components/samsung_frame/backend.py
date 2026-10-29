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
import time
from typing import Callable, Optional

from homeassistant.core import HomeAssistant

from .const import PORT
from .controller import Snapshot

_LOGGER = logging.getLogger(__name__)

# Must be unique to this integration. The TV keeps ONE token per client name,
# so sharing "HomeAssistant" with the core samsungtv / samsungtv_smart
# integrations makes them keep invalidating each other's tokens and the TV
# shows an Allow prompt over and over.
CLIENT_NAME = "HA Samsung Frame"
_CLIENT_NAME = CLIENT_NAME
BLOCK_SECONDS = 300


def _authorize_art_channel(host: str, token: str) -> None:
    """Best effort: surface any art-channel Allow prompt during setup."""
    from samsungtvws.art import SamsungTVArt

    art = SamsungTVArt(host=host, port=PORT, token=token, name=CLIENT_NAME, timeout=30)
    try:
        art.get_artmode()
    except Exception:  # noqa: BLE001
        _LOGGER.debug("Art channel authorisation during pairing failed", exc_info=True)
    finally:
        try:
            art.close()
        except Exception:  # noqa: BLE001
            pass


async def async_pair(hass: HomeAssistant, host: str) -> str:
    """Connect once to obtain a token. The TV shows an Allow prompt."""
    from samsungtvws.async_remote import SamsungTVWSAsyncRemote

    remote = SamsungTVWSAsyncRemote(
        host=host, port=PORT, name=CLIENT_NAME, timeout=30
    )
    try:
        await remote.start_listening()
        token = remote.token
    finally:
        await remote.close()
    if not token:
        raise ConnectionError("TV did not return a token")
    await hass.async_add_executor_job(_authorize_art_channel, host, token)
    return token


class HABackend:
    def __init__(
        self,
        hass: HomeAssistant,
        host: str,
        token: str,
        smartthings_entity: str,
        mac: Optional[str] = None,
        on_token: Optional[Callable[[str], None]] = None,
        on_auth_failed: Optional[Callable[[], None]] = None,
    ) -> None:
        self._on_token = on_token
        self._on_auth_failed = on_auth_failed
        self._blocked_until = 0.0
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

    def _check_not_blocked(self) -> None:
        """After the TV rejects our token, stop knocking: every attempt
        pops an Allow prompt on the screen."""
        if time.monotonic() < self._blocked_until:
            raise ConnectionError("TV rejected the saved token; local calls paused")

    def _block(self) -> None:
        if time.monotonic() >= self._blocked_until:
            if self._on_auth_failed is not None:
                self._hass.loop.call_soon_threadsafe(self._on_auth_failed)
            _LOGGER.warning(
                "The TV rejected Home Assistant's saved token. Local control is "
                "paused for %d s to avoid repeated Allow prompts. On the TV, check "
                "Settings > General > External Device Manager > Device Connection "
                "Manager, allow 'HomeAssistant', and set Access Notification to "
                "'First Time Only'.",
                BLOCK_SECONDS,
            )
        self._blocked_until = time.monotonic() + BLOCK_SECONDS

    def _remember_token(self, client) -> None:
        """The TV may hand out a fresh token on connect; keep and persist it."""
        token = getattr(client, "token", None)
        if token and token != self._token:
            self._token = token
            if self._on_token is not None:
                self._hass.loop.call_soon_threadsafe(self._on_token, token)

    async def _local_power(self) -> Optional[bool]:
        """Local PowerState from the TV's info endpoint (fast, no cloud).

        True = on, False = reachable but in standby, None = unreachable.
        A TV turned "off" via SmartThings stays in network standby, so
        port-open is NOT a sign it is awake.
        """
        from aiohttp import ClientTimeout

        from homeassistant.helpers.aiohttp_client import async_get_clientsession

        try:
            async with async_get_clientsession(self._hass).get(
                f"http://{self._host}:8001/api/v2/", timeout=ClientTimeout(total=2)
            ) as resp:
                data = await resp.json(content_type=None)
            state = str((data.get("device") or {}).get("PowerState", "")).lower()
        except Exception:  # noqa: BLE001
            return None
        return state == "on"

    async def _wait_local_on(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if await self._local_power():
                return True
            await asyncio.sleep(0.5)
        return False

    def _art_mode_blocking(self) -> Optional[bool]:
        """Blocking art-channel query (samsungtvws has no async art client)."""
        from samsungtvws.art import SamsungTVArt
        from samsungtvws.exceptions import UnauthorizedError

        if time.monotonic() < self._blocked_until:
            return None
        art = SamsungTVArt(
            host=self._host, port=PORT, token=self._token, name=_CLIENT_NAME,
            timeout=5,
        )
        try:
            result = str(art.get_artmode()).lower() == "on"
            self._remember_token(art)
            return result
        except UnauthorizedError:
            self._block()
            return None
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Art mode query failed", exc_info=True)
            if "timeOut" in str(err) or "unauthorized" in str(err).lower():
                # The TV is showing an Allow prompt we cannot answer.
                self._block()
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
        st_on = state is not None and state.state == "on"
        # Local PowerState lets us see "on" without waiting for the cloud.
        power = st_on or bool(await self._local_power())
        source = None
        if st_on and state is not None:
            raw = state.attributes.get("source")
            source = str(raw).lower() if raw else None
        art: Optional[bool] = False
        if power:
            art = await self._art_mode()
        return Snapshot(power=power, art=art, source=source)

    async def power_on(self) -> None:
        """Wake locally first (WoL, then power key), SmartThings last.

        The power key only toggles Art mode on a TV that is already awake,
        so it is sent only when the TV is reachable and reports standby.
        """
        if await self._local_power():
            return
        if self._mac:
            from wakeonlan import send_magic_packet

            await self._hass.async_add_executor_job(send_magic_packet, self._mac)
            if await self._wait_local_on(5):
                return
        if await self._local_power() is False:
            try:
                await self.send_key("KEY_POWER")
            except Exception:  # noqa: BLE001
                _LOGGER.debug("Wake by power key failed", exc_info=True)
            else:
                if await self._wait_local_on(5):
                    return
        await self._st_call("turn_on")

    async def power_off(self) -> None:
        await self._st_call("turn_off")

    async def send_key(self, key: str) -> None:
        from samsungtvws.async_remote import SamsungTVWSAsyncRemote
        from samsungtvws.exceptions import UnauthorizedError
        from samsungtvws.remote import SendRemoteKey

        self._check_not_blocked()
        remote = SamsungTVWSAsyncRemote(
            host=self._host, port=PORT, token=self._token, name=_CLIENT_NAME,
            timeout=5,
        )
        try:
            await remote.start_listening()
            await remote.send_command(SendRemoteKey.click(key))
            self._remember_token(remote)
        except UnauthorizedError:
            self._block()
            raise
        except Exception as err:
            if "timeOut" in str(err):
                self._block()
            raise
        finally:
            await remote.close()

    async def select_input(self, key: str) -> None:
        await self._st_call("select_source", {"source": key})
