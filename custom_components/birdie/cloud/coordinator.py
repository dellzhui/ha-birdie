"""Coordinator for cloud-connected Birdie devices of one Birdie account."""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HassJob, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
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
from .pedestal import MODEL_PEDESTAL, Command, PedestalState, apply_commands

_LOGGER = logging.getLogger(__name__)


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
        self._unsub_refresh: CALLBACK_TYPE | None = None
        self._ignored_models: set[str] = set()

    async def _async_update_data(self) -> dict[str, CloudDevice]:
        """Fetch all devices and their status in one bridge request."""
        try:
            devices = await self.client.async_list_devices(include_state=True)
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
            data[device.id] = CloudDevice(device, PedestalState.from_api(device.state))
        return data

    async def async_send_commands(self, device_id: str, commands: list[Command]) -> None:
        """Send commands; the bridge validates them against the device."""
        try:
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

    @callback
    def _async_apply_optimistic(self, device_id: str, commands: list[Command]) -> None:
        """Show the commanded power, mode and speed until the next poll confirms them."""
        current = (self.data or {}).get(device_id)
        if current is None:
            return
        updated = replace(current, state=apply_commands(current.state, commands))
        if updated != current:
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
        # Refresh directly: the request-refresh debouncer would otherwise delay
        # the confirmation by its cooldown after a preceding refresh.
        self._unsub_refresh = None
        await self.async_refresh()

    async def async_shutdown(self) -> None:
        """Cancel a pending post-command refresh."""
        if self._unsub_refresh:
            self._unsub_refresh()
            self._unsub_refresh = None
        await super().async_shutdown()
