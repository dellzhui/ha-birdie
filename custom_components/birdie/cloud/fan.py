"""Fan entity for the Birdie Pedestal air purifier.

Home Assistant models air purifiers as fans. The Pedestal's ``mode`` DP mixes
two concepts, mapped as HA recommends:

* ``manual1``..``manual4`` are fixed speeds -> percentage (25/50/75/100 %).
* ``auto`` and ``sleep`` are device-managed modes -> preset modes.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util.percentage import (
    ordered_list_item_to_percentage,
    percentage_to_ordered_list_item,
)

from .coordinator import BirdieCloudConfigEntry, BirdieCloudCoordinator
from .entity import BirdieCloudEntity, async_add_device_entities
from .pedestal import MANUAL_MODES, Command, DeviceSpecification, mode_command, power_command

PRESET_MODES: list[str] = ["auto", "sleep"]
SPEED_MODES: list[str] = list(MANUAL_MODES)


async def async_setup_cloud_fans(
    hass: HomeAssistant,
    entry: BirdieCloudConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the purifier fan for all devices of a Birdie account."""
    async_add_device_entities(
        entry,
        async_add_entities,
        lambda coordinator, device_id: (BirdiePurifierFan(coordinator, device_id),),
    )


class BirdiePurifierFan(BirdieCloudEntity, FanEntity):
    """Power, speed and mode of the Birdie Pedestal."""

    _attr_name = None
    _attr_translation_key = "purifier"
    _attr_preset_modes = PRESET_MODES
    _attr_speed_count = len(SPEED_MODES)
    _attr_supported_features = (
        FanEntityFeature.SET_SPEED
        | FanEntityFeature.PRESET_MODE
        | FanEntityFeature.TURN_ON
        | FanEntityFeature.TURN_OFF
    )

    def __init__(self, coordinator: BirdieCloudCoordinator, device_id: str) -> None:
        """Initialize the fan."""
        super().__init__(coordinator, device_id, "fan")

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
        if state.mode in SPEED_MODES:
            return ordered_list_item_to_percentage(SPEED_MODES, state.mode)
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
        mode: str | None = None
        if preset_mode is not None:
            mode = preset_mode
        elif percentage:
            mode = percentage_to_ordered_list_item(SPEED_MODES, percentage)
        await self._async_send(power=True, mode=mode)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the purifier off."""
        await self._async_send(power=False)

    async def async_set_percentage(self, percentage: int) -> None:
        """Select a manual speed; 0 turns the purifier off."""
        if percentage == 0:
            await self.async_turn_off()
            return
        mode = percentage_to_ordered_list_item(SPEED_MODES, percentage)
        await self._async_send(power=None if self.is_on else True, mode=mode)

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Select auto or sleep mode."""
        await self._async_send(power=None if self.is_on else True, mode=preset_mode)

    async def _async_send(self, *, power: bool | None, mode: str | None = None) -> None:
        def build(spec: DeviceSpecification) -> list[Command]:
            commands: list[Command] = []
            if power is not None:
                commands.append(power_command(power, spec))
            if mode is not None:
                commands.append(mode_command(mode, spec))
            return commands

        await self.coordinator.async_send_commands(self.device_id, build)
