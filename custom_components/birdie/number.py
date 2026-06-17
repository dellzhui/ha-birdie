"""Number platform for the Birdie integration."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import (
    CONCENTRATION_PARTS_PER_MILLION,
    EntityCategory,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import BirdieConfigEntry, BirdieCoordinator
from .entity import BirdieEntity
from .models import BirdieData


@dataclass(frozen=True, kw_only=True)
class BirdieNumberEntityDescription(NumberEntityDescription):
    """Description for a Birdie number."""

    value_fn: Callable[[BirdieData], int | None]
    set_value_fn: Callable[[BirdieCoordinator, int], Awaitable[None]]


NUMBERS: tuple[BirdieNumberEntityDescription, ...] = (
    BirdieNumberEntityDescription(
        key="co2_threshold",
        name="CO2 threshold",
        native_unit_of_measurement=CONCENTRATION_PARTS_PER_MILLION,
        native_min_value=400,
        native_max_value=5000,
        native_step=1,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda data: data.co2_threshold_ppm,
        set_value_fn=lambda coordinator, value: coordinator.async_set_co2_threshold(value),
    ),
    BirdieNumberEntityDescription(
        key="cool_down_period",
        name="Cool down period",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        native_min_value=1,
        native_max_value=240,
        native_step=1,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda data: data.cool_down_minutes,
        set_value_fn=lambda coordinator, value: coordinator.async_set_cool_down(value),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BirdieConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Birdie numbers."""
    coordinator = entry.runtime_data
    async_add_entities(BirdieNumber(coordinator, description) for description in NUMBERS)


class BirdieNumber(BirdieEntity, NumberEntity):
    """Birdie number entity."""

    entity_description: BirdieNumberEntityDescription

    def __init__(
        self,
        coordinator: BirdieCoordinator,
        description: BirdieNumberEntityDescription,
    ) -> None:
        """Initialize the number."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> int | None:
        """Return the current number value."""
        return self.entity_description.value_fn(self.coordinator.data)

    async def async_set_native_value(self, value: float) -> None:
        """Write a new value to the device."""
        await self.entity_description.set_value_fn(self.coordinator, int(value))
