"""Button platform for the Birdie integration (cloud-connected devices)."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .cloud.button import async_setup_cloud_buttons
from .cloud.coordinator import BirdieCloudConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BirdieCloudConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Birdie buttons."""
    await async_setup_cloud_buttons(hass, entry, async_add_entities)
