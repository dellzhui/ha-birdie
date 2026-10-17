"""Base entity and helpers for cloud-connected Birdie devices."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from ..const import DOMAIN
from .coordinator import BirdieCloudConfigEntry, BirdieCloudCoordinator, CloudDevice
from .pedestal import PedestalState


class BirdieCloudEntity(CoordinatorEntity[BirdieCloudCoordinator]):
    """Base class for entities of a cloud-connected Birdie device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: BirdieCloudCoordinator, device_id: str, key: str) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self.device_id = device_id
        self._attr_unique_id = f"{device_id}_{key}"
        device = coordinator.data[device_id]
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            manufacturer="Birdie",
            model=device.info.model_name,
            name=device.info.name,
            hw_version=device.state.hw_version,
        )

    @property
    def device(self) -> CloudDevice | None:
        """Return the latest data for this device, if the account still has it."""
        return self.coordinator.data.get(self.device_id)

    @property
    def state_data(self) -> PedestalState:
        """Return the decoded state; only valid while the entity is available."""
        device = self.device
        return device.state if device else PedestalState()

    @property
    def available(self) -> bool:
        """Available while the bridge is reachable and the device is online."""
        device = self.device
        return super().available and device is not None and device.info.online


def async_add_device_entities(
    entry: BirdieCloudConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
    factory: Callable[[BirdieCloudCoordinator, str], Iterable[Entity]],
) -> None:
    """Add entities for current devices and for devices that appear later."""
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _async_add_new_devices() -> None:
        new_ids = [device_id for device_id in coordinator.data if device_id not in known]
        if not new_ids:
            return
        known.update(new_ids)
        async_add_entities(
            entity for device_id in new_ids for entity in factory(coordinator, device_id)
        )

    _async_add_new_devices()
    entry.async_on_unload(coordinator.async_add_listener(_async_add_new_devices))
