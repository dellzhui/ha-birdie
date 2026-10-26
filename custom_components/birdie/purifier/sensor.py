"""Sensors for the Birdie air purifier."""

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
from .coordinator import BirdiePurifierConfigEntry, BirdiePurifierCoordinator
from .entity import BirdiePurifierEntity
from .model import (
    AIR_QUALITY_LEVELS,
    DP_AIR_QUALITY,
    DP_DEVICE_ERROR,
    DP_FILTER_TOTAL_MINUTES,
    DP_FILTER_USED_MINUTES,
    DP_PM25,
    FAULTS,
    PurifierState,
)

_FILTER_DPS = (DP_FILTER_TOTAL_MINUTES, DP_FILTER_USED_MINUTES)


@dataclass(frozen=True, kw_only=True)
class BirdiePurifierSensorEntityDescription(SensorEntityDescription):
    """Description for a purifier sensor."""

    value_fn: Callable[[PurifierState], StateType]
    required_dps: tuple[str, ...]


PURIFIER_SENSORS: tuple[BirdiePurifierSensorEntityDescription, ...] = (
    BirdiePurifierSensorEntityDescription(
        key="pm25",
        device_class=SensorDeviceClass.PM25,
        native_unit_of_measurement=MICROGRAMS_PER_CUBIC_METER,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda state: state.pm25,
        required_dps=(DP_PM25,),
    ),
    BirdiePurifierSensorEntityDescription(
        key="air_quality",
        translation_key="air_quality",
        device_class=SensorDeviceClass.ENUM,
        options=list(AIR_QUALITY_LEVELS),
        value_fn=lambda state: state.air_quality,
        required_dps=(DP_AIR_QUALITY,),
    ),
    BirdiePurifierSensorEntityDescription(
        key="filter_life",
        translation_key="filter_life",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda state: state.filter_life_percent,
        required_dps=_FILTER_DPS,
    ),
    BirdiePurifierSensorEntityDescription(
        key="filter_remaining",
        translation_key="filter_remaining",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        suggested_unit_of_measurement=UnitOfTime.DAYS,
        suggested_display_precision=0,
        value_fn=lambda state: state.filter_remaining_minutes,
        required_dps=_FILTER_DPS,
    ),
    BirdiePurifierSensorEntityDescription(
        key="device_status",
        translation_key="device_status",
        device_class=SensorDeviceClass.ENUM,
        options=list(FAULTS),
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda state: state.fault,
        required_dps=(DP_DEVICE_ERROR,),
    ),
)


async def async_setup_purifier_sensors(
    hass: HomeAssistant,
    entry: BirdiePurifierConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the purifier sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        BirdiePurifierSensor(coordinator, description) for description in PURIFIER_SENSORS
    )


class BirdiePurifierSensor(BirdiePurifierEntity, SensorEntity):
    """Sensor of the Birdie air purifier."""

    entity_description: BirdiePurifierSensorEntityDescription

    def __init__(
        self,
        coordinator: BirdiePurifierCoordinator,
        description: BirdiePurifierSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        self.required_dps = description.required_dps
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> StateType:
        """Return the sensor value."""
        return self.entity_description.value_fn(self.state_data)
