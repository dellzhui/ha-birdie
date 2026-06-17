"""Base entities for the Birdie integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import BirdieCoordinator


class BirdieEntity(CoordinatorEntity[BirdieCoordinator]):
    """Base class for Birdie entities."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: BirdieCoordinator, key: str) -> None:
        """Initialize a Birdie entity."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.address}_{key}"

    @property
    def available(self) -> bool:
        """Return if the entity is available."""
        return super().available and self.coordinator.data.available

    @property
    def device_info(self) -> DeviceInfo:
        """Return device registry information for this Birdie device."""
        data = self.coordinator.data
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.address)},
            connections={(CONNECTION_BLUETOOTH, self.coordinator.address)},
            manufacturer="Birdie",
            model=self.coordinator.model,
            name=self.coordinator.device_name,
            sw_version=data.firmware_version,
            hw_version=data.hardware_version,
        )
