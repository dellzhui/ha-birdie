"""Set up and unload a Birdie air purifier config entry."""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from ..const import PURIFIER_PLATFORMS
from .coordinator import BirdiePurifierConfigEntry, BirdiePurifierCoordinator
from .discovery import get_discovery


async def async_setup_purifier_entry(hass: HomeAssistant, entry: BirdiePurifierConfigEntry) -> bool:
    """Connect to the purifier, then create its entities."""
    coordinator = BirdiePurifierCoordinator(hass, entry)
    try:
        await coordinator.async_config_entry_first_refresh()
    except BaseException:
        await coordinator.device.async_close()
        raise
    entry.runtime_data = coordinator
    coordinator.async_start()

    discovery = get_discovery(hass)
    await discovery.async_acquire()
    entry.async_on_unload(discovery.async_release)

    await hass.config_entries.async_forward_entry_setups(entry, PURIFIER_PLATFORMS)
    return True


async def async_unload_purifier_entry(
    hass: HomeAssistant, entry: BirdiePurifierConfigEntry
) -> bool:
    """Unload the entities; the coordinator shutdown closes the connection."""
    return await hass.config_entries.async_unload_platforms(entry, PURIFIER_PLATFORMS)
