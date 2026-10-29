"""Samsung Frame: one media_player for a Samsung The Frame TV."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .backend import HABackend
from .const import (
    CONF_HOST,
    CONF_MAC,
    CONF_SMARTTHINGS_ENTITY,
    CONF_TOKEN,
    build_inputs,
)
from .controller import FrameController

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.MEDIA_PLAYER]
SCAN_INTERVAL = timedelta(seconds=5)


@dataclass
class RuntimeData:
    controller: FrameController
    coordinator: DataUpdateCoordinator[None]


type FrameConfigEntry = ConfigEntry[RuntimeData]


async def async_setup_entry(hass: HomeAssistant, entry: FrameConfigEntry) -> bool:
    def save_token(token: str) -> None:
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_TOKEN: token}
        )

    changed: list = []  # filled once the coordinator exists

    backend = HABackend(
        hass,
        host=entry.data[CONF_HOST],
        token=entry.data[CONF_TOKEN],
        smartthings_entity=entry.data[CONF_SMARTTHINGS_ENTITY],
        mac=entry.data.get(CONF_MAC) or None,
        on_token=save_token,
        on_auth_failed=lambda: entry.async_start_reauth(hass),
        on_change=lambda: [cb() for cb in changed],
    )
    controller = FrameController(backend, build_inputs(entry.options))

    coordinator: DataUpdateCoordinator[None] = DataUpdateCoordinator(
        hass,
        _LOGGER,
        config_entry=entry,
        name="samsung_frame",
        update_method=controller.async_refresh,
        update_interval=SCAN_INTERVAL,
    )
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = RuntimeData(controller, coordinator)
    changed.append(
        lambda: hass.async_create_task(coordinator.async_request_refresh())
    )
    entry.async_on_unload(backend.start())

    @callback
    def _smartthings_changed(event: Event) -> None:
        # Refresh now instead of waiting for the next poll.
        hass.async_create_task(coordinator.async_request_refresh())

    entry.async_on_unload(
        async_track_state_change_event(
            hass, [entry.data[CONF_SMARTTHINGS_ENTITY]], _smartthings_changed
        )
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: FrameConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
