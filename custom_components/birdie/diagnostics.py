"""Diagnostics for the Birdie integration (downloadable from the device page)."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant

from .const import CONF_ACCESS_TOKEN, is_cloud_entry_data

TO_REDACT = {CONF_ACCESS_TOKEN, CONF_ADDRESS}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry; credentials are redacted."""
    coordinator = entry.runtime_data
    result: dict[str, Any] = {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "last_update_success": coordinator.last_update_success,
    }
    if not is_cloud_entry_data(entry.data):
        result["data"] = async_redact_data(asdict(coordinator.data), TO_REDACT)
        return result

    devices: dict[str, Any] = {}
    for device_id, device in (coordinator.data or {}).items():
        info = device.info
        devices[device_id] = {
            "info": {
                "name": info.name,
                "model": info.model,
                "model_name": info.model_name,
                "online": info.online,
                "hardware_version": info.hardware_version,
                "fetched_at": info.fetched_at,
            },
            "state": dict(info.state),
            "decoded": {key: value for key, value in asdict(device.state).items() if key != "raw"},
        }
    result["devices"] = devices
    return result
