"""Config flow helpers for the Birdie air purifier: forms and connection checks."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import voluptuous as vol
from homeassistant.const import CONF_HOST
from homeassistant.data_entry_flow import section
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from ..const import CONF_DEVICE_ID, CONF_LOCAL_KEY, CONF_PROTOCOL_VERSION
from ..tuya_lan import (
    PROTOCOL_AUTO,
    PROTOCOL_VERSIONS,
    DeviceConfig,
    DeviceUnreachableError,
    InvalidLocalKeyError,
    TuyaLanDevice,
)
from ..tuya_lan.device import LOCAL_KEY_LENGTH

_LOGGER = logging.getLogger(__name__)

SECTION_ADVANCED = "advanced"

KEY_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))
# Version numbers cannot be translation keys, so the labels are given here.
VERSION_SELECTOR = SelectSelector(
    SelectSelectorConfig(
        options=[
            SelectOptionDict(value=PROTOCOL_AUTO, label="Automatic"),
            *(SelectOptionDict(value=version, label=version) for version in PROTOCOL_VERSIONS),
        ],
        mode=SelectSelectorMode.DROPDOWN,
    )
)


def _advanced_section() -> section:
    return section(
        vol.Schema({vol.Optional(CONF_PROTOCOL_VERSION, default=PROTOCOL_AUTO): VERSION_SELECTOR}),
        {"collapsed": True},
    )


MANUAL_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_DEVICE_ID): str,
        vol.Required(CONF_LOCAL_KEY): KEY_SELECTOR,
        vol.Optional(CONF_HOST): str,
        vol.Required(SECTION_ADVANCED): _advanced_section(),
    }
)
KEY_SCHEMA = vol.Schema({vol.Required(CONF_LOCAL_KEY): KEY_SELECTOR})
RECONFIGURE_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_LOCAL_KEY): KEY_SELECTOR,
        vol.Required(CONF_HOST): str,
        vol.Required(SECTION_ADVANCED): _advanced_section(),
    }
)


def clean_local_key(value: str | None) -> str | None:
    """The key as entered, or None if it cannot be a Tuya local key."""
    key = (value or "").strip()
    return key if len(key) == LOCAL_KEY_LENGTH else None


@dataclass(frozen=True)
class ConnectionResult:
    """Outcome of a connection check: the working version or an error key."""

    version: str | None = None
    dps: dict[str, Any] | None = None
    error: str | None = None


async def async_check_connection(
    config: DeviceConfig, version_hint: str | None
) -> ConnectionResult:
    """Connect once and read the status; never raises."""
    device = TuyaLanDevice(config, version_hint=version_hint)
    try:
        dps = await device.async_connect()
    except InvalidLocalKeyError:
        return ConnectionResult(error="invalid_local_key")
    except DeviceUnreachableError:
        return ConnectionResult(error="cannot_connect")
    except Exception:
        _LOGGER.exception("Unexpected error connecting to %s", config.device_id)
        return ConnectionResult(error="unknown")
    finally:
        await device.async_close()
    return ConnectionResult(version=device.protocol_version, dps=dps)
