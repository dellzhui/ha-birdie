"""Set up and unload a Birdie cloud account config entry."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ..const import CLOUD_PLATFORMS, CONF_ACCESS_TOKEN, CONF_BRIDGE_URL
from .bridge import BirdieBridgeClient, StaticTokenProvider
from .coordinator import BirdieCloudConfigEntry, BirdieCloudCoordinator


async def async_setup_cloud_entry(hass: HomeAssistant, entry: BirdieCloudConfigEntry) -> bool:
    """Set up a Birdie cloud account."""
    client = BirdieBridgeClient(
        async_get_clientsession(hass),
        entry.data[CONF_BRIDGE_URL],
        StaticTokenProvider(entry.data[CONF_ACCESS_TOKEN]),
    )
    coordinator = BirdieCloudCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, CLOUD_PLATFORMS)
    return True


async def async_unload_cloud_entry(hass: HomeAssistant, entry: BirdieCloudConfigEntry) -> bool:
    """Unload a Birdie cloud account."""
    return await hass.config_entries.async_unload_platforms(entry, CLOUD_PLATFORMS)
