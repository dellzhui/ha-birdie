"""Base entity for the Birdie air purifier."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from ..const import DOMAIN
from .coordinator import BirdiePurifierCoordinator
from .model import MODEL_NAME, PurifierState


class BirdiePurifierEntity(CoordinatorEntity[BirdiePurifierCoordinator]):
    """Entity of a Birdie air purifier.

    ``required_dps`` lists the DPs the entity shows. It stays unavailable
    until the device has reported them, because the LAN protocol may not
    expose every DP of the product definition.
    """

    _attr_has_entity_name = True
    required_dps: tuple[str, ...] = ()

    def __init__(self, coordinator: BirdiePurifierCoordinator, key: str) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        device_id = coordinator.device_id
        self._attr_unique_id = f"{device_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            manufacturer="Birdie",
            model=MODEL_NAME,
            name=coordinator.config_entry.title,
            hw_version=self.state_data.hardware_version,
        )

    @property
    def state_data(self) -> PurifierState:
        """Return the decoded state; empty until the first report."""
        return self.coordinator.data or PurifierState()

    @property
    def available(self) -> bool:
        """Available while connected and the shown DPs have been reported."""
        return (
            super().available
            and self.coordinator.data is not None
            and self.coordinator.data.reported(*self.required_dps)
        )
