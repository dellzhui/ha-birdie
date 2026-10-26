"""Fan entity for the Birdie air purifier.

Home Assistant models air purifiers as fans, mapped as HA recommends:

* Manual mode with fan speed 1..4 -> percentage (25/50/75/100 %).
* ``auto`` and ``sleep`` are device-managed modes -> preset modes.
"""

from __future__ import annotations

import math
from typing import Any

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util.percentage import (
    percentage_to_ranged_value,
    ranged_value_to_percentage,
)
from homeassistant.util.scaling import int_states_in_range

from ..const import DOMAIN
from .coordinator import BirdiePurifierConfigEntry, BirdiePurifierCoordinator
from .entity import BirdiePurifierEntity
from .model import (
    DP_MODE,
    DP_SWITCH,
    FAN_SPEED_MAX,
    FAN_SPEED_MIN,
    MODE_MANUAL,
    PRESET_MODES,
    CommandError,
    fan_speed_dps,
    mode_dps,
    power_dps,
)

SPEED_RANGE = (FAN_SPEED_MIN, FAN_SPEED_MAX)
PRESET_MODE_LIST: list[str] = list(PRESET_MODES)


def _speed_for(percentage: int) -> int:
    return math.ceil(percentage_to_ranged_value(SPEED_RANGE, percentage))


async def async_setup_purifier_fans(
    hass: HomeAssistant,
    entry: BirdiePurifierConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the purifier fan."""
    async_add_entities([BirdiePurifierFan(entry.runtime_data)])


class BirdiePurifierFan(BirdiePurifierEntity, FanEntity):
    """Power, speed and mode of the Birdie air purifier."""

    _attr_name = None
    _attr_translation_key = "purifier"
    _attr_preset_modes = PRESET_MODE_LIST
    _attr_speed_count = int_states_in_range(SPEED_RANGE)
    _attr_supported_features = (
        FanEntityFeature.SET_SPEED
        | FanEntityFeature.PRESET_MODE
        | FanEntityFeature.TURN_ON
        | FanEntityFeature.TURN_OFF
    )
    required_dps = (DP_SWITCH,)

    def __init__(self, coordinator: BirdiePurifierCoordinator) -> None:
        """Initialize the fan."""
        super().__init__(coordinator, "fan")

    @property
    def is_on(self) -> bool | None:
        """Return true if the purifier is running."""
        return self.state_data.power

    @property
    def percentage(self) -> int | None:
        """Return the manual speed as a percentage, or None in a preset mode."""
        state = self.state_data
        if state.power is False:
            return 0
        if state.mode == MODE_MANUAL and state.fan_speed is not None:
            return ranged_value_to_percentage(SPEED_RANGE, state.fan_speed)
        return None

    @property
    def preset_mode(self) -> str | None:
        """Return the active preset mode, if any."""
        mode = self.state_data.mode
        return mode if mode in PRESET_MODES else None

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Turn on, optionally selecting a speed or preset in the same request."""
        speed = _speed_for(percentage) if percentage and preset_mode is None else None
        await self._async_send(power=True, mode=preset_mode, speed=speed)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the purifier off."""
        await self._async_send(power=False)

    async def async_set_percentage(self, percentage: int) -> None:
        """Select a manual speed; 0 turns the purifier off."""
        if percentage == 0:
            await self.async_turn_off()
            return
        await self._async_send(power=None if self.is_on else True, speed=_speed_for(percentage))

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Select auto or sleep mode."""
        await self._async_send(power=None if self.is_on else True, mode=preset_mode)

    async def _async_send(
        self, *, power: bool | None, mode: str | None = None, speed: int | None = None
    ) -> None:
        dps: dict[str, Any] = {}
        try:
            if power is not None:
                dps.update(power_dps(power))
            if mode is not None:
                dps.update(mode_dps(mode))
            if speed is not None:
                dps.update(fan_speed_dps(speed))
        except CommandError as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_command",
                translation_placeholders={"error": str(err)},
            ) from err
        if (mode is not None or speed is not None) and not self.state_data.reported(DP_MODE):
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="mode_not_supported"
            )
        await self.coordinator.async_send_dps(dps)
