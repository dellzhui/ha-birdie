"""Set up and unload a Birdie cloud account config entry."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.config_entry_oauth2_flow import (
    OAuth2Session,
    async_get_config_entry_implementation,
)

from ..const import CLOUD_PLATFORMS, CONF_ACCESS_TOKEN, CONF_BRIDGE_URL
from .bridge import BirdieBridgeClient, StaticTokenProvider, TokenProvider
from .coordinator import BirdieCloudConfigEntry, BirdieCloudCoordinator
from .oauth import OAuth2TokenProvider, async_register_bridge_implementation, is_oauth_entry_data


async def async_setup_cloud_entry(hass: HomeAssistant, entry: BirdieCloudConfigEntry) -> bool:
    """Set up a Birdie cloud account."""
    bridge_url = entry.data[CONF_BRIDGE_URL]
    tokens: TokenProvider
    if is_oauth_entry_data(entry.data):
        async_register_bridge_implementation(hass, bridge_url)
        implementation = await async_get_config_entry_implementation(hass, entry)
        tokens = OAuth2TokenProvider(OAuth2Session(hass, entry, implementation))
    else:
        tokens = StaticTokenProvider(entry.data[CONF_ACCESS_TOKEN])
    client = BirdieBridgeClient(async_get_clientsession(hass), bridge_url, tokens)
    coordinator = BirdieCloudCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, CLOUD_PLATFORMS)
    return True


async def async_unload_cloud_entry(hass: HomeAssistant, entry: BirdieCloudConfigEntry) -> bool:
    """Unload a Birdie cloud account."""
    return await hass.config_entries.async_unload_platforms(entry, CLOUD_PLATFORMS)
