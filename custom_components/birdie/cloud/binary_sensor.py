"""Binary sensors for cloud-connected Birdie devices."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import BirdieCloudConfigEntry, BirdieCloudCoordinator
from .entity import BirdieCloudEntity, async_add_device_entities
from .pedestal import PedestalState


@dataclass(frozen=True, kw_only=True)
class BirdieCloudBinarySensorEntityDescription(BinarySensorEntityDescription):
    """Description for a cloud device binary sensor."""

    is_on_fn: Callable[[PedestalState], bool | None]


PEDESTAL_BINARY_SENSORS: tuple[BirdieCloudBinarySensorEntityDescription, ...] = (
    BirdieCloudBinarySensorEntityDescription(
        key="filter_replacement",
        translation_key="filter_replacement",
        device_class=BinarySensorDeviceClass.PROBLEM,
        is_on_fn=lambda state: state.filter_replace_needed,
    ),
    BirdieCloudBinarySensorEntityDescription(
        key="filter_cover",
        translation_key="filter_cover",
        device_class=BinarySensorDeviceClass.OPENING,
        entity_category=EntityCategory.DIAGNOSTIC,
        is_on_fn=lambda state: state.filter_cover_open,
    ),
)


async def async_setup_cloud_binary_sensors(
    hass: HomeAssistant,
    entry: BirdieCloudConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up binary sensors for all devices of a Birdie account."""
    async_add_device_entities(
        entry,
        async_add_entities,
        lambda coordinator, device_id: (
            BirdieCloudBinarySensor(coordinator, device_id, description)
            for description in PEDESTAL_BINARY_SENSORS
        ),
    )


class BirdieCloudBinarySensor(BirdieCloudEntity, BinarySensorEntity):
    """Binary sensor of a cloud-connected Birdie device."""

    entity_description: BirdieCloudBinarySensorEntityDescription

    def __init__(
        self,
        coordinator: BirdieCloudCoordinator,
        device_id: str,
        description: BirdieCloudBinarySensorEntityDescription,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator, device_id, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        """Return the binary state."""
        return self.entity_description.is_on_fn(self.state_data)
