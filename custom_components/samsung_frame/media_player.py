"""The single Samsung Frame media_player entity."""

from __future__ import annotations

from typing import Any, Optional

from homeassistant.components.media_player import (
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import FrameConfigEntry
from .const import CONF_HOST, CONF_MAC, DOMAIN

_LAST_INPUT = "last_input"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FrameConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([FrameMediaPlayer(entry)])


class FrameMediaPlayer(CoordinatorEntity, MediaPlayerEntity, RestoreEntity):
    _attr_has_entity_name = True
    _attr_name = None
    _attr_device_class = MediaPlayerDeviceClass.TV
    _attr_supported_features = (
        MediaPlayerEntityFeature.TURN_ON
        | MediaPlayerEntityFeature.TURN_OFF
        | MediaPlayerEntityFeature.SELECT_SOURCE
    )

    def __init__(self, entry: FrameConfigEntry) -> None:
        super().__init__(entry.runtime_data.coordinator)
        self._ctl = entry.runtime_data.controller
        self._attr_unique_id = entry.unique_id or entry.entry_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._attr_unique_id)},
            name=entry.title,
            manufacturer="Samsung",
            model="The Frame",
            connections=(
                {("mac", entry.data[CONF_MAC])} if entry.data.get(CONF_MAC) else set()
            ),
            configuration_url=f"http://{entry.data[CONF_HOST]}",
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None:
            self._ctl.restore_last_input(last.attributes.get(_LAST_INPUT))

    @property
    def state(self) -> MediaPlayerState:
        return MediaPlayerState.ON if self._ctl.is_on else MediaPlayerState.OFF

    @property
    def source(self) -> Optional[str]:
        return self._ctl.source

    @property
    def source_list(self) -> list[str]:
        return self._ctl.source_list

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {_LAST_INPUT: self._ctl.last_input_name}

    async def _finish(self) -> None:
        self.coordinator.async_set_updated_data(None)

    async def async_select_source(self, source: str) -> None:
        await self._ctl.async_select_source(source)
        await self._finish()

    async def async_turn_on(self) -> None:
        await self._ctl.async_turn_on()
        await self._finish()

    async def async_turn_off(self) -> None:
        await self._ctl.async_turn_off()
        await self._finish()
