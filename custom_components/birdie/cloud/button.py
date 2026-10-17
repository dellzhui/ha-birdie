"""Buttons for cloud-connected Birdie devices."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import BirdieCloudConfigEntry, BirdieCloudCoordinator
from .entity import BirdieCloudEntity, async_add_device_entities
from .pedestal import reset_filter_command


async def async_setup_cloud_buttons(
    hass: HomeAssistant,
    entry: BirdieCloudConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up buttons for all devices of a Birdie account."""
    async_add_device_entities(
        entry,
        async_add_entities,
        lambda coordinator, device_id: (BirdieResetFilterButton(coordinator, device_id),),
    )


class BirdieResetFilterButton(BirdieCloudEntity, ButtonEntity):
    """Reset the filter lifetime counter after replacing the filter."""

    _attr_translation_key = "reset_filter"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: BirdieCloudCoordinator, device_id: str) -> None:
        """Initialize the button."""
        super().__init__(coordinator, device_id, "reset_filter")

    async def async_press(self) -> None:
        """Send the filter reset command."""
        await self.coordinator.async_send_commands(
            self.device_id, lambda spec: [reset_filter_command(spec)]
        )
