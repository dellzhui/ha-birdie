"""Sensor platform for the Birdie integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    CONCENTRATION_PARTS_PER_MILLION,
    PERCENTAGE,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from .const import UNKNOWN_STATE
from .coordinator import BirdieConfigEntry, BirdieCoordinator
from .entity import BirdieEntity
from .models import BirdieData


@dataclass(frozen=True, kw_only=True)
class BirdieSensorEntityDescription(SensorEntityDescription):
    """Description for a Birdie sensor."""

    value_fn: Callable[[BirdieData], StateType]


SENSORS: tuple[BirdieSensorEntityDescription, ...] = (
    BirdieSensorEntityDescription(
        key="co2",
        name="CO2",
        device_class=SensorDeviceClass.CO2,
        native_unit_of_measurement=CONCENTRATION_PARTS_PER_MILLION,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda data: data.co2_ppm,
    ),
    BirdieSensorEntityDescription(
        key="temperature",
        name="Temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=3,
        value_fn=lambda data: data.temperature_celsius,
    ),
    BirdieSensorEntityDescription(
        key="humidity",
        name="Humidity",
        device_class=SensorDeviceClass.HUMIDITY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=3,
        value_fn=lambda data: data.humidity_percent,
    ),
    BirdieSensorEntityDescription(
        key="state",
        name="State",
        device_class=SensorDeviceClass.ENUM,
        options=["up", "down", "cooldown", UNKNOWN_STATE],
        value_fn=lambda data: data.birdie_state_value,
    ),
    BirdieSensorEntityDescription(
        key="battery",
        name="Battery",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda data: data.battery_percent,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BirdieConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Birdie sensors."""
    coordinator = entry.runtime_data
    async_add_entities(BirdieSensor(coordinator, description) for description in SENSORS)


class BirdieSensor(BirdieEntity, SensorEntity):
    """Birdie sensor entity."""

    entity_description: BirdieSensorEntityDescription

    def __init__(
        self,
        coordinator: BirdieCoordinator,
        description: BirdieSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> StateType:
        """Return the sensor value."""
        return self.entity_description.value_fn(self.coordinator.data)
