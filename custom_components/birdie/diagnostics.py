"""Diagnostics for the Birdie integration (downloadable from the device page)."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, CONF_HOST
from homeassistant.core import HomeAssistant

from .const import CONF_LOCAL_KEY, is_purifier_entry_data

TO_REDACT = {CONF_ADDRESS, CONF_HOST, CONF_LOCAL_KEY}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry; keys and addresses are redacted."""
    coordinator = entry.runtime_data
    result: dict[str, Any] = {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "last_update_success": coordinator.last_update_success,
    }
    if not is_purifier_entry_data(entry.data):
        result["data"] = async_redact_data(asdict(coordinator.data), TO_REDACT)
        return result

    device = coordinator.device
    state = coordinator.data
    result["connection"] = {
        "available": device.available,
        "protocol_version": device.protocol_version,
        "last_error": repr(device.last_error) if device.last_error else None,
    }
    # Raw DPs as reported over the LAN: what is needed to verify the DP mapping.
    result["dps"] = device.dps
    result["decoded"] = (
        {key: value for key, value in asdict(state).items() if key != "dps"} if state else None
    )
    return result
