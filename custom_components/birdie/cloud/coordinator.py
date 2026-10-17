"""Coordinator for cloud-connected Birdie devices of one Birdie account."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HassJob, HomeAssistant, callback
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from ..const import (
    CLOUD_COMMAND_REFRESH_DELAY_SECONDS,
    CLOUD_SCAN_INTERVAL_SECONDS,
    DOMAIN,
)
from .bridge import (
    BirdieBridgeClient,
    BridgeApiError,
    BridgeAuthError,
    BridgeCommandRejectedError,
    BridgeDevice,
    BridgeDeviceNotFoundError,
    BridgeDeviceOfflineError,
    BridgeError,
)
from .pedestal import DP, MODEL_PEDESTAL, Command, CommandError, DeviceSpecification, PedestalState

_LOGGER = logging.getLogger(__name__)

# DPs whose commanded value can be shown immediately, before the next poll.
_OPTIMISTIC_CODES = frozenset({DP.SWITCH, DP.MODE})


@dataclass(frozen=True)
class CloudDevice:
    """One cloud device: bridge metadata plus decoded state."""

    info: BridgeDevice
    state: PedestalState


type BirdieCloudConfigEntry = ConfigEntry[BirdieCloudCoordinator]


class BirdieCloudCoordinator(DataUpdateCoordinator[dict[str, CloudDevice]]):
    """Poll the Birdie Cloud Bridge for all devices of one account."""

    config_entry: BirdieCloudConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: BirdieCloudConfigEntry,
        client: BirdieBridgeClient,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_cloud",
            update_interval=timedelta(seconds=CLOUD_SCAN_INTERVAL_SECONDS),
            always_update=False,
        )
        self.client = client
        self._specs: dict[str, DeviceSpecification] = {}
        self._unsub_refresh: CALLBACK_TYPE | None = None
        self._ignored_models: set[str] = set()

    async def _async_update_data(self) -> dict[str, CloudDevice]:
        """Fetch all devices and their status in one bridge request."""
        try:
            devices = await self.client.async_list_devices(include_status=True)
        except BridgeAuthError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="auth_failed"
            ) from err
        except BridgeApiError as err:
            raise UpdateFailed(
                f"Birdie cloud request failed: {err}", retry_after=err.retry_after
            ) from err
        except BridgeError as err:
            raise UpdateFailed(f"Birdie cloud is unreachable: {err}") from err

        data: dict[str, CloudDevice] = {}
        for device in devices:
            if device.model != MODEL_PEDESTAL:
                if device.model not in self._ignored_models:
                    self._ignored_models.add(device.model)
                    _LOGGER.info("Ignoring unsupported Birdie cloud model %s", device.model)
                continue
            data[device.id] = CloudDevice(device, PedestalState.from_status(device.status))
        return data

    async def async_send_commands(
        self,
        device_id: str,
        build: Callable[[DeviceSpecification], list[Command]],
    ) -> None:
        """Validate and send commands built against the device specification."""
        try:
            spec = await self._async_get_specification(device_id)
            try:
                commands = build(spec)
            except CommandError as err:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="invalid_command",
                    translation_placeholders={"error": str(err)},
                ) from err
            await self.client.async_send_commands(device_id, commands)
        except BridgeAuthError as err:
            self.config_entry.async_start_reauth(self.hass)
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="auth_failed"
            ) from err
        except BridgeDeviceOfflineError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="device_offline"
            ) from err
        except BridgeDeviceNotFoundError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="device_not_found"
            ) from err
        except BridgeCommandRejectedError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="command_rejected"
            ) from err
        except BridgeError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="cloud_error",
                translation_placeholders={"error": str(err)},
            ) from err

        self._async_apply_optimistic(device_id, commands)
        self._async_schedule_refresh()

    def cached_specification(self, device_id: str) -> DeviceSpecification | None:
        """Return the specification if it has been fetched already."""
        return self._specs.get(device_id)

    async def _async_get_specification(self, device_id: str) -> DeviceSpecification:
        """Return the cached device specification, fetching it on first use."""
        if device_id not in self._specs:
            result = await self.client.async_get_specification(device_id)
            self._specs[device_id] = DeviceSpecification.from_api(result)
        return self._specs[device_id]

    @callback
    def _async_apply_optimistic(self, device_id: str, commands: list[Command]) -> None:
        """Reflect accepted switch/mode commands until the next poll confirms them."""
        current = (self.data or {}).get(device_id)
        updates: dict[str, Any] = {
            command["code"]: command["value"]
            for command in commands
            if command["code"] in _OPTIMISTIC_CODES
        }
        if current is None or not updates:
            return
        status = {**current.state.raw, **updates}
        updated = replace(current, state=PedestalState.from_status(status))
        self.async_set_updated_data({**self.data, device_id: updated})

    @callback
    def _async_schedule_refresh(self) -> None:
        """Refresh shortly after a command so the confirmed state is shown."""
        if self._unsub_refresh:
            self._unsub_refresh()
        self._unsub_refresh = async_call_later(
            self.hass,
            CLOUD_COMMAND_REFRESH_DELAY_SECONDS,
            HassJob(self._async_delayed_refresh, cancel_on_shutdown=True),
        )

    async def _async_delayed_refresh(self, _now: Any) -> None:
        self._unsub_refresh = None
        await self.async_request_refresh()

    async def async_shutdown(self) -> None:
        """Cancel a pending post-command refresh."""
        if self._unsub_refresh:
            self._unsub_refresh()
            self._unsub_refresh = None
        await super().async_shutdown()
