"""Binary sensor platform for the Birdie integration."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import BirdieConfigEntry, BirdieCoordinator
from .entity import BirdieEntity


BINARY_SENSORS: tuple[BinarySensorEntityDescription, ...] = (
    BinarySensorEntityDescription(
        key="co2_alarm",
        name="CO2 alarm",
        device_class=BinarySensorDeviceClass.PROBLEM,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BirdieConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Birdie binary sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        BirdieCO2AlarmBinarySensor(coordinator, description)
        for description in BINARY_SENSORS
    )


class BirdieCO2AlarmBinarySensor(BirdieEntity, BinarySensorEntity):
    """Birdie CO2 alarm binary sensor."""

    entity_description: BinarySensorEntityDescription

    def __init__(
        self,
        coordinator: BirdieCoordinator,
        description: BinarySensorEntityDescription,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        """Return true when Birdie state is down."""
        return self.coordinator.data.co2_alarm
