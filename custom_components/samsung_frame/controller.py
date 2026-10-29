"""State machine for a Samsung Frame TV.

Pure Python (no Home Assistant imports) so it can be unit tested with a fake
backend. The backend hides *how* the TV is read and driven; the controller
decides *what* to do and verifies it actually worked.

Hard-won rules from testing on a real Frame:
* KEY_POWER is a toggle between Art mode and TV, never an off switch. Only
  press it when the Art mode state is known and a toggle is what we want.
* select_input is silently ignored in Art mode, so wake first.
* The cloud `source` echo is optimistic. Only trust it after the poll window.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional, Protocol

_LOGGER = logging.getLogger(__name__)

KIND_HDMI = "hdmi"
KIND_TV = "tv"
KIND_ART = "art"

KEY_POWER = "KEY_POWER"


@dataclass(frozen=True)
class TvInput:
    """One selectable input. `key` is the backend source id (None for Art)."""

    name: str
    kind: str
    key: Optional[str]


@dataclass(frozen=True)
class Snapshot:
    """What the TV is doing right now.

    art is None when Art mode could not be determined.
    source is the raw backend source id (lower case) or None.
    """

    power: bool
    art: Optional[bool]
    source: Optional[str]


class Backend(Protocol):
    async def snapshot(self, refresh: bool = False) -> Snapshot: ...

    async def power_on(self) -> None: ...

    async def power_off(self) -> None: ...

    async def send_key(self, key: str) -> None: ...

    async def select_input(self, key: str) -> None: ...


@dataclass(frozen=True)
class Timings:
    poll: float = 2.0
    power_timeout: float = 30.0
    cold_boot_settle: float = 8.0
    art_timeout: float = 15.0
    art_settle: float = 3.0
    wake_settle: float = 4.0
    verify_delay: float = 15.0
    attempts: int = 2


class FrameController:
    """Select inputs, power on/off, and report the verified source."""

    def __init__(
        self,
        backend: Backend,
        inputs: list[TvInput],
        timings: Timings = Timings(),
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._backend = backend
        self._inputs = list(inputs)
        self._by_name = {i.name: i for i in inputs}
        self._t = timings
        self._sleep = sleep
        self._monotonic = monotonic
        self._lock = asyncio.Lock()
        self._snap = Snapshot(False, False, None)
        self._source: Optional[str] = None
        self._last: Optional[TvInput] = None
        self._optimistic: Optional[str] = None
        self._listeners: list[Callable[[], None]] = []

    def add_listener(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Be told when the reported state changes outside a poll."""
        self._listeners.append(callback)
        return lambda: self._listeners.remove(callback)

    def _notify(self) -> None:
        for callback in list(self._listeners):
            callback()

    # ----- read side -------------------------------------------------

    @property
    def source_list(self) -> list[str]:
        return [i.name for i in self._inputs]

    @property
    def is_on(self) -> bool:
        return self._snap.power or self._optimistic is not None

    @property
    def source(self) -> Optional[str]:
        """The verified source, or the requested one while switching."""
        return self._optimistic or self._source

    @property
    def source_unconfirmed(self) -> bool:
        return self._optimistic is not None

    @property
    def transitioning(self) -> bool:
        return self._lock.locked()

    @property
    def last_input_name(self) -> Optional[str]:
        return self._last.name if self._last else None

    def restore_last_input(self, name: Optional[str]) -> None:
        if name in self._by_name and self._last is None:
            self._last = self._by_name[name]

    async def async_refresh(self) -> None:
        """Poll the TV. Skipped mid-transition so we never report echoes."""
        if self._lock.locked():
            return
        await self._apply(await self._backend.snapshot())

    async def _apply(self, snap: Snapshot) -> None:
        self._snap = snap
        current = self._input_for(snap)
        self._source = current.name if current else None
        if current is not None:
            self._last = current

    def _input_for(self, snap: Snapshot) -> Optional[TvInput]:
        if not snap.power:
            return None
        if snap.art:
            return next((i for i in self._inputs if i.kind == KIND_ART), None)
        return next(
            (i for i in self._inputs if i.key and i.key == snap.source), None
        )

    # ----- commands --------------------------------------------------

    async def async_select_source(self, name: str) -> bool:
        target = self._by_name.get(name)
        if target is None:
            raise ValueError(f"Unknown source: {name}")
        async with self._lock:
            # Show the request straight away, flagged as unconfirmed; it is
            # replaced by the verified state when the switch finishes.
            self._optimistic = target.name
            self._notify()
            try:
                ok = await self._switch(target)
            except Exception:  # noqa: BLE001 - quiet failure by design
                _LOGGER.warning("Switching to %s failed", name, exc_info=True)
                ok = False
            await self._resync()
            self._optimistic = None
            self._notify()
            if ok:
                self._last = target
            else:
                _LOGGER.warning("Could not reach %s; reporting actual state", name)
            return ok

    async def async_turn_on(self) -> bool:
        target = self._last or (self._inputs[0] if self._inputs else None)
        if target is None:
            return False
        return await self.async_select_source(target.name)

    async def async_turn_off(self) -> bool:
        async with self._lock:
            try:
                await self._backend.power_off()
                snap = await self._wait(lambda s: not s.power, self._t.power_timeout)
                ok = snap is not None
                if not ok:
                    _LOGGER.warning("TV did not power off")
            except Exception:  # noqa: BLE001
                _LOGGER.warning("Power off failed", exc_info=True)
                ok = False
            await self._resync()
            return ok

    # ----- internals -------------------------------------------------

    async def _resync(self) -> None:
        try:
            await self._apply(await self._backend.snapshot(refresh=True))
        except Exception:  # noqa: BLE001
            _LOGGER.debug("Resync failed", exc_info=True)

    async def _wait(
        self, predicate: Callable[[Snapshot], bool], timeout: float
    ) -> Optional[Snapshot]:
        deadline = self._monotonic() + timeout
        while True:
            snap = await self._backend.snapshot(refresh=True)
            if predicate(snap):
                return snap
            if self._monotonic() >= deadline:
                return None
            await self._sleep(self._t.poll)

    async def _switch(self, target: TvInput) -> bool:
        snap = await self._backend.snapshot(refresh=True)
        if not snap.power:
            await self._backend.power_on()
            woke = await self._wait(lambda s: s.power, self._t.power_timeout)
            if woke is None:
                _LOGGER.warning("TV did not power on")
                return False
            await self._sleep(self._t.cold_boot_settle)
            snap = await self._backend.snapshot(refresh=True)
        if target.kind == KIND_ART:
            return await self._to_art(snap)
        return await self._to_input(target, snap)

    async def _to_art(self, snap: Snapshot) -> bool:
        if snap.art is None:
            _LOGGER.warning("Art mode state unknown; not pressing power blindly")
            return False
        for _ in range(self._t.attempts):
            if snap.art:
                return True
            await self._backend.send_key(KEY_POWER)
            reached = await self._wait(lambda s: bool(s.art), self._t.art_timeout)
            if reached is not None:
                await self._sleep(self._t.art_settle)
                return True
            snap = await self._backend.snapshot(refresh=True)
        return bool(snap.art)

    async def _wake_from_art(self) -> bool:
        await self._backend.send_key(KEY_POWER)
        woke = await self._wait(lambda s: s.art is False, self._t.art_timeout)
        if woke is None:
            _LOGGER.warning("TV stayed in Art mode after power key")
            return False
        await self._sleep(self._t.wake_settle)
        return True

    async def _to_input(self, target: TvInput, snap: Snapshot) -> bool:
        if snap.art and not await self._wake_from_art():
            return False
        for _ in range(self._t.attempts):
            await self._backend.select_input(target.key)
            # The cloud source echo is optimistic; wait out the poll window.
            await self._sleep(self._t.verify_delay)
            snap = await self._backend.snapshot(refresh=True)
            if snap.art:
                if not await self._wake_from_art():
                    return False
                continue
            if snap.source == target.key:
                return True
        return False
