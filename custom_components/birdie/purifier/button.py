"""Buttons for the Birdie air purifier."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import BirdiePurifierConfigEntry, BirdiePurifierCoordinator
from .entity import BirdiePurifierEntity
from .model import reset_filter_dps


async def async_setup_purifier_buttons(
    hass: HomeAssistant,
    entry: BirdiePurifierConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the purifier buttons."""
    async_add_entities([BirdieResetFilterButton(entry.runtime_data)])


class BirdieResetFilterButton(BirdiePurifierEntity, ButtonEntity):
    """Reset the filter lifetime counter after replacing the filter.

    Not tied to a reported DP: a write-only trigger DP may never be reported.
    Whether the device accepts it over the LAN is still to be verified.
    """

    _attr_translation_key = "reset_filter"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: BirdiePurifierCoordinator) -> None:
        """Initialize the button."""
        super().__init__(coordinator, "reset_filter")

    async def async_press(self) -> None:
        """Send the filter reset command."""
        await self.coordinator.async_send_dps(reset_filter_dps())
