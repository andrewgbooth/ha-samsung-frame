"""Real backend: local WebSocket (keys, Art mode, WoL) + SmartThings entity.

Local, fast:   power state, Art mode (pushed by the TV), power key, Wake-on-LAN.
Via the existing core SmartThings entity (no extra login, no new cloud code):
               full power off, HDMI input selection, current HDMI input.

The local Tizen API has no verified way to pick a specific HDMI input or to
power fully off on this model (KEY_HDMI1-3 do nothing, KEY_POWER toggles Art
mode), which is why SmartThings is still used for those two things.

Connections: one persistent, token-authenticated remote channel (also used to
send keys), and, only while that is up, one token-less art channel on which
the TV pushes `art_mode_changed` events. The TV closes the art channel with
`ms.channel.timeOut` unless the remote channel of the same client is
connected, and passing a token to the art channel makes it prompt Allow.
So: no polling, no repeated Allow prompts, instant Art mode changes.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from typing import Callable, Optional
from urllib.parse import quote
import uuid

from homeassistant.core import HomeAssistant

from .const import PORT
from .controller import Snapshot

_LOGGER = logging.getLogger(__name__)

# Must be unique to this integration. The TV keeps ONE token per client name,
# so sharing "HomeAssistant" with the core samsungtv / samsungtv_smart
# integrations makes them keep invalidating each other's tokens and the TV
# shows an Allow prompt over and over.
CLIENT_NAME = "HA Samsung Frame"

BLOCK_SECONDS = 60       # pause key sending after an unanswered Allow prompt
MAX_STRIKES = 2          # unanswered prompts in a row before asking to re-pair
ST_MIN_GAP = 3.0         # never force a SmartThings refresh more often
CALL_TIMEOUT = 20        # no single backend call may hang the controller
ART_RECONNECT_MAX = 15   # seconds between art channel reconnect attempts

ART_PATH = "/api/v2/channels/com.samsung.art-app"


def _name_param() -> str:
    return quote(base64.b64encode(CLIENT_NAME.encode()).decode(), safe="")


async def async_pair(host: str) -> str:
    """Connect once to obtain a remote token. The TV shows an Allow prompt."""
    from samsungtvws.async_remote import SamsungTVWSAsyncRemote

    remote = SamsungTVWSAsyncRemote(host=host, port=PORT, name=CLIENT_NAME, timeout=30)
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
        on_token: Optional[Callable[[str], None]] = None,
        on_auth_failed: Optional[Callable[[], None]] = None,
        on_change: Optional[Callable[[], None]] = None,
    ) -> None:
        self._hass = hass
        self._host = host
        self._token = token
        self._st = smartthings_entity
        self._mac = mac
        self._on_token = on_token
        self._on_auth_failed = on_auth_failed
        self._on_change = on_change
        self._blocked_until = 0.0
        self._strikes = 0
        self._last_st_refresh = 0.0
        self._art: Optional[bool] = None
        self._art_task: Optional[asyncio.Task] = None
        self._art_kick = asyncio.Event()
        self._remote_task: Optional[asyncio.Task] = None
        self._remote = None
        self._remote_ready = asyncio.Event()

    # ----- lifecycle --------------------------------------------------

    def start(self) -> Callable[[], None]:
        """Start the remote and art channel listeners; returns a stop function."""
        self._remote_task = self._hass.async_create_background_task(
            self._remote_loop(), "samsung_frame remote channel"
        )
        self._art_task = self._hass.async_create_background_task(
            self._art_loop(), "samsung_frame art channel"
        )
        return self.stop

    def stop(self) -> None:
        for task in (self._art_task, self._remote_task):
            if task is not None:
                task.cancel()
        self._art_task = self._remote_task = None

    # ----- remote channel (persistent) --------------------------------

    async def _remote_loop(self) -> None:
        """Hold the remote channel open while the TV is awake.

        The art channel only works while this is connected, and keys are
        sent over it without reconnecting.
        """
        from samsungtvws.async_remote import SamsungTVWSAsyncRemote
        from samsungtvws.exceptions import UnauthorizedError

        while True:
            if time.monotonic() < self._blocked_until or not await self._local_power():
                await asyncio.sleep(5)  # TV asleep, or we must not knock
                continue
            remote = SamsungTVWSAsyncRemote(
                host=self._host, port=PORT, token=self._token,
                name=CLIENT_NAME, timeout=10,
            )
            try:
                await remote.start_listening()
                self._strikes = 0
                self._remember(getattr(remote, "token", None))
                self._remote = remote
                self._remote_ready.set()
                _LOGGER.debug("Remote channel connected")
                while remote.is_alive():
                    await asyncio.sleep(2)
                _LOGGER.debug("Remote channel closed")
            except asyncio.CancelledError:
                raise
            except UnauthorizedError:
                self._block(rejected=True)
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("Remote channel failed: %s", err)
                if "timeOut" in str(err):
                    self._block()
            finally:
                self._remote_ready.clear()
                self._remote = None
                try:
                    await asyncio.wait_for(remote.close(), 5)
                except Exception:  # noqa: BLE001
                    pass
            await asyncio.sleep(3)

    # ----- art channel (persistent, push events) ----------------------

    def _set_art(self, value: Optional[bool]) -> None:
        if value != self._art:
            _LOGGER.debug("Art mode: %s -> %s", self._art, value)
            self._art = value
            if self._on_change is not None:
                self._on_change()

    async def _art_loop(self) -> None:
        try:
            from websockets.asyncio.client import connect
        except ImportError:  # older websockets
            from websockets import connect  # type: ignore[no-redef]
        from samsungtvws.helper import get_ssl_context

        url = f"wss://{self._host}:{PORT}{ART_PATH}?name={_name_param()}"
        ssl_ctx = await self._hass.async_add_executor_job(get_ssl_context)
        delay = 3.0
        while True:
            await self._remote_ready.wait()  # art only works beside the remote
            try:
                async with connect(
                    url, ssl=ssl_ctx, open_timeout=5, ping_interval=None, max_size=None
                ) as ws:
                    _LOGGER.debug("Art channel connected")
                    delay = 3.0
                    async for raw in ws:
                        await self._on_art_message(ws, raw)
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("Art channel closed: %s", err)
            self._set_art(None)
            self._art_kick.clear()
            try:
                await asyncio.wait_for(self._art_kick.wait(), delay)
            except asyncio.TimeoutError:
                pass
            delay = min(delay * 2, ART_RECONNECT_MAX)

    async def _request_art_status(self, ws) -> None:
        await ws.send(
            json.dumps(
                {
                    "method": "ms.channel.emit",
                    "params": {
                        "data": json.dumps(
                            {"request": "get_artmode_status", "id": str(uuid.uuid4())}
                        ),
                        "to": "host",
                        "event": "art_app_request",
                    },
                }
            )
        )

    async def _on_art_message(self, ws, raw) -> None:
        try:
            msg = json.loads(raw)
        except ValueError:
            return
        event = msg.get("event")
        if event == "ms.channel.ready":
            await self._request_art_status(ws)
        elif event == "d2d_service_message":
            try:
                data = json.loads(msg.get("data") or "{}")
            except ValueError:
                return
            kind = data.get("event")
            if kind == "art_mode_changed":
                self._set_art(data.get("status") == "on")
            elif kind == "artmode_status":
                self._set_art(data.get("value") == "on")
            elif kind == "go_to_standby":
                self._set_art(None)
            elif kind == "wakeup":
                await self._request_art_status(ws)
        elif event in ("ms.channel.timeOut", "ms.channel.unauthorized"):
            _LOGGER.debug("Art channel refused: %s", event)

    # ----- SmartThings (core entity) ----------------------------------

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

    # ----- remote key handling ----------------------------------------

    def _check_not_blocked(self) -> None:
        if time.monotonic() < self._blocked_until:
            raise ConnectionError("TV rejected our token; key sending paused")

    def _block(self, rejected: bool = False) -> None:
        """Stop knocking: each attempt pops an Allow prompt on the TV.

        An unanswered prompt (timeOut) only pauses key sending briefly. We ask
        for re-pairing only when the TV explicitly rejects us, or after
        repeated unanswered prompts, and then stay silent until reloaded.
        """
        self._strikes += 1
        if rejected or self._strikes >= MAX_STRIKES:
            if self._blocked_until != float("inf"):
                _LOGGER.warning(
                    "The TV is not accepting Home Assistant's saved permission. "
                    "Key sending stays off until you re-pair (see the Repair "
                    "notification). On the TV, check Settings > General > "
                    "External Device Manager > Device Connection Manager that "
                    "'%s' is allowed.",
                    CLIENT_NAME,
                )
                if self._on_auth_failed is not None:
                    self._hass.loop.call_soon_threadsafe(self._on_auth_failed)
            self._blocked_until = float("inf")
            return
        _LOGGER.debug("TV did not answer the Allow prompt (%d)", self._strikes)
        self._blocked_until = time.monotonic() + BLOCK_SECONDS

    def _remember(self, token: Optional[str]) -> None:
        """Keep a token the TV re-issues."""
        if token and token != self._token:
            self._token = token
            if self._on_token is not None:
                self._hass.loop.call_soon_threadsafe(self._on_token, token)

    # ----- local power ------------------------------------------------

    async def _local_power(self) -> Optional[bool]:
        """Local PowerState from the TV's info endpoint (fast, no cloud).

        True = on, False = reachable but in standby, None = unreachable.
        It reads "on" in Art mode too, and a TV turned "off" via SmartThings
        stays in network standby, so open ports do not mean awake.
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

    # ----- Backend protocol -------------------------------------------

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
            art = self._art
            if art is None:
                self._art_kick.set()  # (re)connect now that the TV is awake
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

        live = self._remote
        if live is not None and live.is_alive():
            await asyncio.wait_for(
                live.send_command(SendRemoteKey.click(key)), CALL_TIMEOUT
            )
            return

        self._check_not_blocked()
        remote = SamsungTVWSAsyncRemote(
            host=self._host, port=PORT, token=self._token,
            name=CLIENT_NAME, timeout=5,
        )

        async def _send() -> None:
            await remote.start_listening()
            await remote.send_command(SendRemoteKey.click(key))
            self._strikes = 0
            self._remember(getattr(remote, "token", None))

        try:
            await asyncio.wait_for(_send(), CALL_TIMEOUT)
        except UnauthorizedError:
            self._block(rejected=True)
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
        await self._st_call("select_source", {"source": key})
