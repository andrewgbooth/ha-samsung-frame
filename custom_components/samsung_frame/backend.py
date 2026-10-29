"""Real backend: local WebSocket (keys, Art mode, WoL) + SmartThings entity.

Local, fast:   power state, Art mode state, power key, Wake-on-LAN.
Via the existing core SmartThings entity (no extra login, no new cloud code):
               full power off, HDMI input selection, current HDMI input.

The local Tizen API has no verified way to pick a specific HDMI input or to
power fully off on this model (KEY_HDMI1-3 do nothing, KEY_POWER toggles Art
mode), which is why SmartThings is still used for those two things.

Be gentle with the TV: every art-channel query is a new WebSocket connection,
so results are cached briefly, serialised, and only forced during switches.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
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

BLOCK_SECONDS = 60       # pause local calls after the TV rejects us
ART_CACHE_SECONDS = 15   # reuse an art-mode result this long unless forced
ART_MIN_GAP = 2.0        # never open art connections closer together
ST_MIN_GAP = 3.0         # never force a SmartThings refresh more often
CALL_TIMEOUT = 20        # no single backend call may hang the controller


@dataclass(frozen=True)
class Tokens:
    remote: str
    art: str


def _art_client(host: str, token: Optional[str], timeout: float):
    from samsungtvws.art import SamsungTVArt

    return SamsungTVArt(
        host=host, port=PORT, token=token, name=CLIENT_NAME, timeout=timeout
    )


def _pair_art(host: str, token: str) -> str:
    """Authorise the art channel during setup and return its token.

    Tokens may be issued per channel, so the art token is kept separately.
    """
    art = _art_client(host, token, 30)
    try:
        art.get_artmode()
        return getattr(art, "token", None) or token
    except Exception:  # noqa: BLE001
        _LOGGER.debug("Art channel authorisation during pairing failed", exc_info=True)
        return token
    finally:
        try:
            art.close()
        except Exception:  # noqa: BLE001
            pass


async def async_pair(hass: HomeAssistant, host: str) -> Tokens:
    """Connect once to obtain tokens. The TV shows Allow prompts."""
    from samsungtvws.async_remote import SamsungTVWSAsyncRemote

    remote = SamsungTVWSAsyncRemote(host=host, port=PORT, name=CLIENT_NAME, timeout=30)
    try:
        await remote.start_listening()
        token = remote.token
    finally:
        await remote.close()
    if not token:
        raise ConnectionError("TV did not return a token")
    art_token = await hass.async_add_executor_job(_pair_art, host, token)
    return Tokens(remote=token, art=art_token)


class HABackend:
    def __init__(
        self,
        hass: HomeAssistant,
        host: str,
        tokens: Tokens,
        smartthings_entity: str,
        mac: Optional[str] = None,
        on_tokens: Optional[Callable[[Tokens], None]] = None,
        on_auth_failed: Optional[Callable[[], None]] = None,
    ) -> None:
        self._hass = hass
        self._host = host
        self._tokens = tokens
        self._st = smartthings_entity
        self._mac = mac
        self._on_tokens = on_tokens
        self._on_auth_failed = on_auth_failed
        self._blocked_until = 0.0
        self._art_lock = asyncio.Lock()
        self._art_cache: tuple[float, Optional[bool]] = (0.0, None)
        self._last_art_try = 0.0
        self._last_st_refresh = 0.0

    # ----- SmartThings (core entity) --------------------------------

    async def _st_call(self, service: str, data: Optional[dict] = None) -> None:
        await asyncio.wait_for(
            self._hass.services.async_call(
                "media_player",
                service,
                {"entity_id": self._st, **(data or {})},
                blocking=True,
            ),
            CALL_TIMEOUT,
        )

    async def _st_refresh(self) -> None:
        if time.monotonic() - self._last_st_refresh < ST_MIN_GAP:
            return
        self._last_st_refresh = time.monotonic()
        await asyncio.wait_for(
            self._hass.services.async_call(
                "homeassistant", "update_entity", {"entity_id": self._st},
                blocking=True,
            ),
            CALL_TIMEOUT,
        )

    # ----- token / auth handling -------------------------------------

    def _check_not_blocked(self) -> None:
        if time.monotonic() < self._blocked_until:
            raise ConnectionError("TV rejected our token; local calls paused")

    def _block(self) -> None:
        """Stop knocking after a rejection: each attempt pops an Allow prompt."""
        if time.monotonic() >= self._blocked_until:
            if self._on_auth_failed is not None:
                self._hass.loop.call_soon_threadsafe(self._on_auth_failed)
            _LOGGER.warning(
                "The TV did not accept Home Assistant's saved permission. Local "
                "control is paused for %d s to avoid repeated Allow prompts. Use "
                "the Repair notification to pair again, and on the TV check "
                "Settings > General > External Device Manager > Device Connection "
                "Manager that 'HA Samsung Frame' is allowed.",
                BLOCK_SECONDS,
            )
        self._blocked_until = time.monotonic() + BLOCK_SECONDS

    def _remember(self, remote_tok: Optional[str] = None, art_tok: Optional[str] = None) -> None:
        """Keep tokens the TV re-issues; each channel keeps its own."""
        new = Tokens(
            remote=remote_tok or self._tokens.remote,
            art=art_tok or self._tokens.art,
        )
        if new != self._tokens:
            self._tokens = new
            if self._on_tokens is not None:
                self._hass.loop.call_soon_threadsafe(self._on_tokens, new)

    # ----- local -----------------------------------------------------

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
        from samsungtvws.exceptions import UnauthorizedError

        art = _art_client(self._host, self._tokens.art, 5)
        try:
            result = str(art.get_artmode()).lower() == "on"
            self._remember(art_tok=getattr(art, "token", None))
            return result
        except UnauthorizedError:
            self._block()
            return None
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Art mode query failed", exc_info=True)
            if "timeOut" in str(err) or "unauthorized" in str(err).lower():
                self._block()  # the TV is showing an Allow prompt
            return None
        finally:
            try:
                art.close()
            except Exception:  # noqa: BLE001
                pass

    async def _art_mode(self, force: bool) -> Optional[bool]:
        """Art mode, cached briefly so we do not hammer the TV."""
        cached_at, cached = self._art_cache
        if not force and time.monotonic() - cached_at < ART_CACHE_SECONDS:
            return cached
        if time.monotonic() < self._blocked_until:
            return None
        async with self._art_lock:
            # Someone may have just refreshed it while we waited.
            cached_at, cached = self._art_cache
            if time.monotonic() - cached_at < ART_MIN_GAP:
                return cached
            try:
                value = await asyncio.wait_for(
                    self._hass.async_add_executor_job(self._art_mode_blocking),
                    CALL_TIMEOUT,
                )
            except asyncio.TimeoutError:
                value = None
            if value is not None:
                self._art_cache = (time.monotonic(), value)
            return value

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
            art = await self._art_mode(force=refresh)
        else:
            self._art_cache = (0.0, None)
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
        self._art_cache = (0.0, None)
        await self._st_call("turn_off")

    async def send_key(self, key: str) -> None:
        from samsungtvws.async_remote import SamsungTVWSAsyncRemote
        from samsungtvws.exceptions import UnauthorizedError
        from samsungtvws.remote import SendRemoteKey

        self._check_not_blocked()
        remote = SamsungTVWSAsyncRemote(
            host=self._host, port=PORT, token=self._tokens.remote,
            name=CLIENT_NAME, timeout=5,
        )

        async def _send() -> None:
            await remote.start_listening()
            await remote.send_command(SendRemoteKey.click(key))
            self._remember(remote_tok=getattr(remote, "token", None))

        try:
            await asyncio.wait_for(_send(), CALL_TIMEOUT)
            self._art_cache = (0.0, None)  # a key press may change Art mode
        except UnauthorizedError:
            self._block()
            raise
        except Exception as err:
            if "timeOut" in str(err):
                self._block()
            raise
        finally:
            try:
                await asyncio.wait_for(remote.close(), 5)
            except Exception:  # noqa: BLE001
                pass

    async def select_input(self, key: str) -> None:
        self._art_cache = (0.0, None)
        await self._st_call("select_source", {"source": key})
