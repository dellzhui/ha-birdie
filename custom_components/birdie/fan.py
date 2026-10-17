"""Fan platform for the Birdie integration (cloud-connected air purifiers)."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .cloud.coordinator import BirdieCloudConfigEntry
from .cloud.fan import async_setup_cloud_fans


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BirdieCloudConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Birdie fans."""
    await async_setup_cloud_fans(hass, entry, async_add_entities)
