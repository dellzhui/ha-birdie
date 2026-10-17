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
        state = device.state
        spec = coordinator.cached_specification(device_id)
        devices[device_id] = {
            "info": {
                "name": info.name,
                "model": info.model,
                "model_name": info.model_name,
                "product_id": info.product_id,
                "online": info.online,
                "fetched_at": info.fetched_at,
            },
            "status": dict(info.status),
            "decoded": {
                **{key: value for key, value in asdict(state).items() if key != "raw"},
                "filter_life_percent": state.filter_life_percent,
                "filter_remaining_minutes": state.filter_remaining_minutes,
                "filter_replace_needed": state.filter_replace_needed,
                "filter_cover_open": state.filter_cover_open,
            },
            "specification": asdict(spec) if spec else None,
        }
    result["devices"] = devices
    return result
