"""Button platform for the Birdie integration (air purifiers)."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .purifier.button import async_setup_purifier_buttons
from .purifier.coordinator import BirdiePurifierConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BirdiePurifierConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Birdie buttons."""
    await async_setup_purifier_buttons(hass, entry, async_add_entities)
