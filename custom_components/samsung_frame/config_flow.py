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
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector

from homeassistant.helpers.service_info.ssdp import SsdpServiceInfo

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
from .matching import StCandidate, pick_smartthings_entity
from .discovery import FoundTv, async_probe, async_scan, host_from_location

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


MANUAL = "manual"


def smartthings_candidates(hass) -> list[StCandidate]:
    """SmartThings media_players with the device details used for matching."""
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    out: list[StCandidate] = []
    for ent in ent_reg.entities.values():
        if (
            ent.platform != "smartthings"
            or ent.domain != "media_player"
            or ent.disabled_by
        ):
            continue
        dev = dev_reg.async_get(ent.device_id) if ent.device_id else None
        out.append(
            StCandidate(
                entity_id=ent.entity_id,
                macs=frozenset(v for k, v in (dev.connections if dev else ()) if k == "mac"),
                model=dev.model if dev else None,
                name=(dev.name_by_user or dev.name) if dev else ent.original_name,
            )
        )
    return out


class SamsungFrameConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._found: dict[str, FoundTv] = {}
        self._tv: FoundTv | None = None

    # ----- finding the TV: scan, then fall back to manual ------------

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is None:
            configured = {
                e.data.get(CONF_HOST) for e in self._async_current_entries()
            }
            self._found = {
                t.host: t
                for t in await async_scan(self.hass)
                if t.host not in configured
            }
            if not self._found:
                return await self.async_step_manual()
        elif user_input["device"] == MANUAL:
            return await self.async_step_manual()
        else:
            tv = self._tv = self._found[user_input["device"]]
            self._data.update({CONF_HOST: tv.host, CONF_MAC: tv.mac or ""})
            self._data[CONF_NAME] = tv.name
            return await self._async_match_smartthings()
        options = [
            selector.SelectOptionDict(value=h, label=t.label)
            for h, t in self._found.items()
        ]
        options.append(selector.SelectOptionDict(value=MANUAL, label="Enter manually"))
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required("device"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=options,
                            mode=selector.SelectSelectorMode.LIST,
                        )
                    )
                }
            ),
        )

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            mac = (user_input.get(CONF_MAC) or "").strip().lower()
            tv = self._tv = await async_probe(self.hass, host)
            if tv is None and not mac:
                # Not answering on the info port; pairing will tell us more.
                _LOGGER.debug("No device info from %s", host)
            self._data.update(
                {CONF_HOST: host, CONF_MAC: mac or (tv.mac if tv and tv.mac else "")}
            )
            self._data[CONF_NAME] = tv.name if tv else "Samsung Frame"
            return await self._async_match_smartthings()
        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST): str,
                    vol.Optional(CONF_MAC): str,
                }
            ),
            errors=errors,
        )

    async def async_step_ssdp(
        self, discovery_info: SsdpServiceInfo
    ) -> ConfigFlowResult:
        host = host_from_location(discovery_info.ssdp_location)
        tv = await async_probe(self.hass, host) if host else None
        if tv is None:
            return self.async_abort(reason="not_frame")
        await self.async_set_unique_id((tv.mac or tv.host).lower())
        self._abort_if_unique_id_configured(updates={CONF_HOST: tv.host})
        self._tv = tv
        self._data.update(
            {CONF_HOST: tv.host, CONF_MAC: tv.mac or "", CONF_NAME: tv.name}
        )
        self.context["title_placeholders"] = {"name": tv.label}
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return await self._async_match_smartthings()
        return self.async_show_form(
            step_id="confirm",
            description_placeholders={"name": self._data[CONF_NAME],
                                      "host": self._data[CONF_HOST]},
        )

    # ----- the rest --------------------------------------------------

    async def _async_match_smartthings(self) -> ConfigFlowResult:
        """Find the matching SmartThings entity automatically, or stop."""
        tv = self._tv
        entity = pick_smartthings_entity(
            smartthings_candidates(self.hass),
            self._data.get(CONF_MAC) or (tv.mac if tv else None),
            tv.model if tv else None,
            self._data.get(CONF_NAME),
        )
        if entity is None:
            return self.async_abort(reason="smartthings_not_found")
        self._data[CONF_SMARTTHINGS_ENTITY] = entity
        return await self.async_step_pair()

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
