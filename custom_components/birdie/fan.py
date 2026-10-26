"""Fan platform for the Birdie integration (air purifiers)."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .purifier.coordinator import BirdiePurifierConfigEntry
from .purifier.fan import async_setup_purifier_fans


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BirdiePurifierConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Birdie fans."""
    await async_setup_purifier_fans(hass, entry, async_add_entities)
