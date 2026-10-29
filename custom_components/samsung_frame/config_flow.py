"""UI config and options flow. Input names and the TV toggle live here."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.core import callback
from homeassistant.helpers import selector

from .backend import async_pair
from .const import (
    CONF_ART_NAME,
    CONF_ENABLE_TV,
    CONF_HDMI1_NAME,
    CONF_HDMI2_NAME,
    CONF_HDMI3_NAME,
    CONF_HOST,
    CONF_MAC,
    CONF_NAME,
    CONF_SMARTTHINGS_ENTITY,
    CONF_TOKEN,
    CONF_TV_NAME,
    DEFAULT_NAMES,
    DOMAIN,
    names_are_valid,
)

_LOGGER = logging.getLogger(__name__)


def _inputs_schema(defaults: dict[str, Any]) -> vol.Schema:
    text = selector.TextSelector()
    return vol.Schema(
        {
            vol.Required(CONF_HDMI1_NAME, default=defaults[CONF_HDMI1_NAME]): text,
            vol.Required(CONF_HDMI2_NAME, default=defaults[CONF_HDMI2_NAME]): text,
            vol.Required(CONF_HDMI3_NAME, default=defaults[CONF_HDMI3_NAME]): text,
            vol.Required(CONF_ENABLE_TV, default=defaults[CONF_ENABLE_TV]): bool,
            vol.Required(CONF_TV_NAME, default=defaults[CONF_TV_NAME]): text,
            vol.Required(CONF_ART_NAME, default=defaults[CONF_ART_NAME]): text,
        }
    )


class SamsungFrameConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self._data = dict(user_input)
            return await self.async_step_pair()
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_NAME, default="Samsung Frame"): str,
                    vol.Required(CONF_HOST): str,
                    vol.Optional(CONF_MAC): str,
                    vol.Required(
                        CONF_SMARTTHINGS_ENTITY
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(
                            domain="media_player", integration="smartthings"
                        )
                    ),
                }
            ),
        )

    async def async_step_pair(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                self._data[CONF_TOKEN] = await async_pair(self._data[CONF_HOST])
            except Exception:  # noqa: BLE001
                _LOGGER.debug("Pairing failed", exc_info=True)
                errors["base"] = "cannot_connect"
            else:
                return await self.async_step_inputs()
        return self.async_show_form(
            step_id="pair", data_schema=vol.Schema({}), errors=errors
        )

    async def async_step_inputs(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if names_are_valid(user_input):
                await self.async_set_unique_id(
                    (self._data.get(CONF_MAC) or self._data[CONF_HOST]).lower()
                )
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=self._data[CONF_NAME], data=self._data, options=user_input
                )
            errors["base"] = "duplicate_names"
        return self.async_show_form(
            step_id="inputs",
            data_schema=_inputs_schema(user_input or DEFAULT_NAMES),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> "SamsungFrameOptionsFlow":
        return SamsungFrameOptionsFlow()


class SamsungFrameOptionsFlow(OptionsFlowWithReload):
    """Rename inputs or toggle TV; the entry reloads automatically."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if names_are_valid(user_input):
                return self.async_create_entry(data=user_input)
            errors["base"] = "duplicate_names"
        return self.async_show_form(
            step_id="init",
            data_schema=_inputs_schema(
                user_input or {**DEFAULT_NAMES, **self.config_entry.options}
            ),
            errors=errors,
        )
