"""Binary sensors for the Birdie air purifier."""

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

from .coordinator import BirdiePurifierConfigEntry, BirdiePurifierCoordinator
from .entity import BirdiePurifierEntity
from .model import DP_FILTER_COVER, DP_FILTER_STATUS, PurifierState


@dataclass(frozen=True, kw_only=True)
class BirdiePurifierBinarySensorEntityDescription(BinarySensorEntityDescription):
    """Description for a purifier binary sensor."""

    is_on_fn: Callable[[PurifierState], bool | None]
    required_dps: tuple[str, ...]


PURIFIER_BINARY_SENSORS: tuple[BirdiePurifierBinarySensorEntityDescription, ...] = (
    BirdiePurifierBinarySensorEntityDescription(
        key="filter_replacement",
        translation_key="filter_replacement",
        device_class=BinarySensorDeviceClass.PROBLEM,
        is_on_fn=lambda state: state.filter_replace_needed,
        required_dps=(DP_FILTER_STATUS,),
    ),
    BirdiePurifierBinarySensorEntityDescription(
        key="filter_cover",
        translation_key="filter_cover",
        device_class=BinarySensorDeviceClass.OPENING,
        entity_category=EntityCategory.DIAGNOSTIC,
        is_on_fn=lambda state: state.filter_cover_open,
        required_dps=(DP_FILTER_COVER,),
    ),
)


async def async_setup_purifier_binary_sensors(
    hass: HomeAssistant,
    entry: BirdiePurifierConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the purifier binary sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        BirdiePurifierBinarySensor(coordinator, description)
        for description in PURIFIER_BINARY_SENSORS
    )


class BirdiePurifierBinarySensor(BirdiePurifierEntity, BinarySensorEntity):
    """Binary sensor of the Birdie air purifier."""

    entity_description: BirdiePurifierBinarySensorEntityDescription

    def __init__(
        self,
        coordinator: BirdiePurifierCoordinator,
        description: BirdiePurifierBinarySensorEntityDescription,
    ) -> None:
        """Initialize the binary sensor."""
        self.required_dps = description.required_dps
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        """Return the binary state."""
        return self.entity_description.is_on_fn(self.state_data)
