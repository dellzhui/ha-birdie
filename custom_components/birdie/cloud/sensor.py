"""Sensors for cloud-connected Birdie devices."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from ..compat import MICROGRAMS_PER_CUBIC_METER
from .coordinator import BirdieCloudConfigEntry, BirdieCloudCoordinator
from .entity import BirdieCloudEntity, async_add_device_entities
from .pedestal import AIR_QUALITY_LEVELS, FAULTS, PedestalState


def _enum_value(value: str | None, options: tuple[str, ...]) -> str | None:
    """Return the value only if it is a known option (HA rejects unknown enum states)."""
    return value if value in options else None


@dataclass(frozen=True, kw_only=True)
class BirdieCloudSensorEntityDescription(SensorEntityDescription):
    """Description for a cloud device sensor."""

    value_fn: Callable[[PedestalState], StateType]


PEDESTAL_SENSORS: tuple[BirdieCloudSensorEntityDescription, ...] = (
    BirdieCloudSensorEntityDescription(
        key="pm25",
        device_class=SensorDeviceClass.PM25,
        native_unit_of_measurement=MICROGRAMS_PER_CUBIC_METER,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda state: state.pm25,
    ),
    BirdieCloudSensorEntityDescription(
        key="air_quality",
        translation_key="air_quality",
        device_class=SensorDeviceClass.ENUM,
        options=list(AIR_QUALITY_LEVELS),
        value_fn=lambda state: _enum_value(state.air_quality, AIR_QUALITY_LEVELS),
    ),
    BirdieCloudSensorEntityDescription(
        key="filter_life",
        translation_key="filter_life",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda state: state.filter_life_percent,
    ),
    BirdieCloudSensorEntityDescription(
        key="filter_remaining",
        translation_key="filter_remaining",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        suggested_unit_of_measurement=UnitOfTime.DAYS,
        suggested_display_precision=0,
        value_fn=lambda state: state.filter_remaining_minutes,
    ),
    BirdieCloudSensorEntityDescription(
        key="device_status",
        translation_key="device_status",
        device_class=SensorDeviceClass.ENUM,
        options=list(FAULTS),
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda state: _enum_value(state.fault, FAULTS),
    ),
)


async def async_setup_cloud_sensors(
    hass: HomeAssistant,
    entry: BirdieCloudConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensors for all devices of a Birdie account."""
    async_add_device_entities(
        entry,
        async_add_entities,
        lambda coordinator, device_id: (
            BirdieCloudSensor(coordinator, device_id, description)
            for description in PEDESTAL_SENSORS
        ),
    )


class BirdieCloudSensor(BirdieCloudEntity, SensorEntity):
    """Sensor of a cloud-connected Birdie device."""

    entity_description: BirdieCloudSensorEntityDescription

    def __init__(
        self,
        coordinator: BirdieCloudCoordinator,
        device_id: str,
        description: BirdieCloudSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device_id, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> StateType:
        """Return the sensor value."""
        return self.entity_description.value_fn(self.state_data)
