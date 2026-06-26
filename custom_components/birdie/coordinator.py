"""Coordinator for the Birdie integration."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .ble import normalize_address
from .client import BirdieBleClient, BirdieBleError
from .const import BIRDIE_STATE_VALUES, CONF_NAME, DOMAIN, UNKNOWN_STATE
from .models import BirdieData

_LOGGER = logging.getLogger(__name__)


class _ManualUpdateFilter(logging.Filter):
    """Suppress noisy DataUpdateCoordinator debug updates."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Filter only the automatic coordinator data update debug line."""
        return not (
            record.msg == "Manually updated %s data" and record.args == (DOMAIN,)
        )


_LOGGER.addFilter(_ManualUpdateFilter())


BirdieConfigEntry = ConfigEntry["BirdieCoordinator"]


class BirdieCoordinator(DataUpdateCoordinator[BirdieData]):
    """Coordinate Birdie BLE data for Home Assistant entities."""

    config_entry: BirdieConfigEntry

    def __init__(self, hass: HomeAssistant, entry: BirdieConfigEntry) -> None:
        """Initialize the coordinator."""
        address = normalize_address(entry.data[CONF_ADDRESS])
        name = entry.data.get(CONF_NAME) or entry.title or address
        super().__init__(hass, _LOGGER, config_entry=entry, name=DOMAIN)
        self.address = address
        self.device_name = name
        self.data = BirdieData(address=address, name=name)
        self.client = BirdieBleClient(
            hass,
            address,
            self._async_handle_values,
            self._async_handle_disconnect,
        )
        self._connect_lock = asyncio.Lock()
        self._expected_connected = False

    @property
    def model(self) -> str:
        """Return the best known model name."""
        if self.device_name.startswith("Birdie Pro"):
            return "Birdie Pro"
        return "Birdie"

    async def async_connect(self) -> bool:
        """Connect, read initial values, and subscribe to notifications."""
        async with self._connect_lock:
            self._expected_connected = True
            if not await self.client.async_connect():
                self._set_available(False)
                return False

            initial_values = await self.client.async_read_initial()
            self._async_handle_values({**initial_values, "available": True})
            await self.client.async_start_notify()
            return True

    async def async_connect_if_expected(self) -> None:
        """Reconnect if this config entry still expects a BLE connection."""
        if self._expected_connected and not self.client.is_connected:
            await self.async_connect()

    async def async_disconnect(self) -> None:
        """Disconnect from the device and mark data unavailable."""
        self._expected_connected = False
        await self.client.async_disconnect()
        self._set_available(False)

    async def async_set_co2_threshold(self, value: int) -> None:
        """Write CO2 threshold, read it back, and update coordinator data."""
        if not 400 <= value <= 5000:
            raise HomeAssistantError("CO2 threshold must be between 400 and 5000 ppm")
        await self._async_ensure_connected()
        try:
            read_back = await self.client.async_write_co2_threshold(value)
        except BirdieBleError as err:
            raise HomeAssistantError(f"Unable to write CO2 threshold: {err}") from err
        self._async_handle_values({"co2_threshold_ppm": read_back, "available": True})

    async def async_set_cool_down(self, value: int) -> None:
        """Write cool down minutes, read it back, and update coordinator data."""
        if not 1 <= value <= 240:
            raise HomeAssistantError("Cool down period must be between 1 and 240 minutes")
        await self._async_ensure_connected()
        try:
            read_back = await self.client.async_write_cool_down(value)
        except BirdieBleError as err:
            raise HomeAssistantError(f"Unable to write cool down period: {err}") from err
        self._async_handle_values({"cool_down_minutes": read_back, "available": True})

    async def _async_ensure_connected(self) -> None:
        """Ensure the BLE client is connected before a write."""
        if self.client.is_connected:
            return
        if not await self.async_connect():
            raise HomeAssistantError(f"Unable to connect to Birdie {self.address}")

    @callback
    def _async_handle_values(self, values: dict[str, Any]) -> None:
        """Merge parsed BLE values into coordinator data."""
        previous_data = self.data
        updated_data = previous_data.updated(**values)
        if "birdie_state" in values:
            _LOGGER.debug(
                (
                    "Birdie state coordinator update for %s: %s -> %s; "
                    "co2=%s ppm threshold=%s ppm alarm=%s available=%s"
                ),
                self.address,
                _state_value(previous_data.birdie_state),
                _state_value(updated_data.birdie_state),
                updated_data.co2_ppm,
                updated_data.co2_threshold_ppm,
                updated_data.co2_alarm,
                updated_data.available,
            )
        self.async_set_updated_data(updated_data)

    @callback
    def _async_handle_disconnect(self) -> None:
        """Mark the device unavailable after an unexpected disconnect."""
        self._set_available(False)

    @callback
    def _set_available(self, available: bool) -> None:
        """Update availability while preserving the latest values."""
        if self.data.available == available:
            return
        self.async_set_updated_data(self.data.updated(available=available))


def _state_value(state: int | None) -> str:
    """Return a readable Birdie state for debug logging."""
    if state is None:
        return UNKNOWN_STATE
    return BIRDIE_STATE_VALUES.get(state, UNKNOWN_STATE)
