"""Constants and input list construction."""

from __future__ import annotations

from typing import Any, Mapping

from .controller import KIND_ART, KIND_HDMI, KIND_TV, TvInput

DOMAIN = "samsung_frame"

CONF_HOST = "host"
CONF_MAC = "mac"
CONF_TOKEN = "token"
CONF_ART_TOKEN = "art_token"
CONF_NAME = "name"
CONF_SMARTTHINGS_ENTITY = "smartthings_entity"

CONF_HDMI1_NAME = "hdmi1_name"
CONF_HDMI2_NAME = "hdmi2_name"
CONF_HDMI3_NAME = "hdmi3_name"
CONF_TV_NAME = "tv_name"
CONF_ENABLE_TV = "enable_tv"
CONF_ART_NAME = "art_name"

DEFAULT_NAMES: dict[str, Any] = {
    CONF_HDMI1_NAME: "HDMI 1",
    CONF_HDMI2_NAME: "HDMI 2",
    CONF_HDMI3_NAME: "HDMI 3",
    CONF_TV_NAME: "TV",
    CONF_ENABLE_TV: False,
    CONF_ART_NAME: "Art Mode",
}

PORT = 8002


def build_inputs(options: Mapping[str, Any]) -> list[TvInput]:
    """Ordered inputs: HDMI 1-3, optional TV, Art Mode. Names are data."""
    cfg = {**DEFAULT_NAMES, **options}
    inputs = [
        TvInput(cfg[CONF_HDMI1_NAME], KIND_HDMI, "hdmi1"),
        TvInput(cfg[CONF_HDMI2_NAME], KIND_HDMI, "hdmi2"),
        TvInput(cfg[CONF_HDMI3_NAME], KIND_HDMI, "hdmi3"),
    ]
    if cfg[CONF_ENABLE_TV]:
        inputs.append(TvInput(cfg[CONF_TV_NAME], KIND_TV, "digital_tv"))
    inputs.append(TvInput(cfg[CONF_ART_NAME], KIND_ART, None))
    return inputs


def names_are_valid(options: Mapping[str, Any]) -> bool:
    names = [i.name.strip() for i in build_inputs(options)]
    return all(names) and len(set(n.casefold() for n in names)) == len(names)
