"""Coordinator for one Birdie air purifier on the local network.

The device pushes its status over a persistent LAN connection, so this
coordinator does not poll: the Tuya LAN client calls back on every change
and on connection loss, and reconnects on its own.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Mapping
from typing import Any

from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from ..const import (
    CONF_DETECTED_VERSION,
    CONF_DEVICE_ID,
    CONF_LOCAL_KEY,
    CONF_PROTOCOL_VERSION,
    DOMAIN,
    PURIFIER_DISCOVERY_SECONDS,
    PURIFIER_KEY_FAILURES_BEFORE_REAUTH,
)
from ..tuya_lan import (
    PROTOCOL_AUTO,
    DeviceConfig,
    DeviceUnreachableError,
    InvalidLocalKeyError,
    TuyaLanDevice,
    TuyaLanError,
)
from .model import PurifierState, apply_dps

_LOGGER = logging.getLogger(__name__)

type BirdiePurifierConfigEntry = ConfigEntry[BirdiePurifierCoordinator]

# Key rejections while setting up, per config entry; kept across setup retries.
DATA_SETUP_KEY_FAILURES = f"{DOMAIN}_setup_key_failures"


def device_config(data: Mapping[str, Any]) -> DeviceConfig:
    """LAN connection settings from config entry data."""
    return DeviceConfig(
        device_id=data[CONF_DEVICE_ID],
        host=data[CONF_HOST],
        local_key=data[CONF_LOCAL_KEY],
        protocol_version=data.get(CONF_PROTOCOL_VERSION, PROTOCOL_AUTO),
    )


class BirdiePurifierCoordinator(DataUpdateCoordinator[PurifierState]):
    """Keep one purifier connected and expose its decoded state."""

    config_entry: BirdiePurifierConfigEntry

    def __init__(self, hass: HomeAssistant, entry: BirdiePurifierConfigEntry) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_purifier",
            update_interval=None,
            always_update=False,
        )
        self.device_id: str = entry.data[CONF_DEVICE_ID]
        self.device = TuyaLanDevice(
            device_config(entry.data),
            on_status=self._async_on_status,
            on_availability=self._async_on_availability,
            on_error=self._async_on_error,
            version_hint=entry.data.get(CONF_DETECTED_VERSION),
        )
        self._key_failures = 0

    async def _async_setup(self) -> None:
        """Connect once before the first refresh.

        If nothing answers at the stored address, the purifier may have a new
        one (DHCP): look for its announcement and connect there. A rejected
        key asks for a new one only after repeated rejections, because a busy
        or slow purifier fails the protocol 3.4/3.5 handshake the same way.
        """
        entry_id = self.config_entry.entry_id
        key_failures: Counter[str] = self.hass.data.setdefault(DATA_SETUP_KEY_FAILURES, Counter())
        try:
            try:
                await self.device.async_connect()
            except DeviceUnreachableError:
                if not await self._async_follow_announced_host():
                    raise
                await self.device.async_connect()
        except InvalidLocalKeyError as err:
            key_failures[entry_id] += 1
            if key_failures[entry_id] >= PURIFIER_KEY_FAILURES_BEFORE_REAUTH:
                del key_failures[entry_id]
                raise ConfigEntryAuthFailed(
                    translation_domain=DOMAIN, translation_key="invalid_local_key"
                ) from err
            raise UpdateFailed(
                translation_domain=DOMAIN, translation_key="invalid_local_key"
            ) from err
        except TuyaLanError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="device_unreachable",
                translation_placeholders={"host": self.device.config.host},
            ) from err
        key_failures.pop(entry_id, None)
        self._async_store_detected_version()

    async def _async_follow_announced_host(self) -> bool:
        """Switch to the address the purifier announces, if it changed."""
        from .discovery import async_scan  # late: discovery imports this module

        found = await async_scan(self.hass, PURIFIER_DISCOVERY_SECONDS, device_id=self.device_id)
        announced = found.get(self.device_id)
        if announced is None or announced.host == self.device.config.host:
            return False
        await self.async_set_host(announced.host)
        return True

    async def _async_update_data(self) -> PurifierState:
        """Read the full status (first refresh and manual entity updates)."""
        try:
            dps = await self.device.async_refresh()
        except TuyaLanError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="device_unreachable",
                translation_placeholders={"host": self.device.config.host},
            ) from err
        return PurifierState.from_dps(dps)

    @callback
    def async_start(self) -> None:
        """Start the background connection after the first refresh."""
        self.device.start()

    async def async_shutdown(self) -> None:
        """Close the LAN connection."""
        await super().async_shutdown()
        await self.device.async_close()

    async def async_send_dps(self, dps: Mapping[str, Any]) -> None:
        """Write DPs and show the expected result until the device confirms it."""
        try:
            await self.device.async_set_dps(dps)
        except TuyaLanError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="device_unreachable",
                translation_placeholders={"host": self.device.config.host},
            ) from err
        if self.data is not None:
            self.async_set_updated_data(apply_dps(self.data, dps))

    async def async_set_host(self, host: str) -> None:
        """The device announced a new address; reconnect there."""
        if host == self.device.config.host:
            return
        _LOGGER.info("Birdie air purifier %s moved to %s", self.device_id, host)
        self.hass.config_entries.async_update_entry(
            self.config_entry, data={**self.config_entry.data, CONF_HOST: host}
        )
        await self.device.async_reconfigure(host=host)

    # ------------------------------------------------- LAN client callbacks

    @callback
    def _async_on_status(self, dps: dict[str, Any]) -> None:
        self.async_set_updated_data(PurifierState.from_dps(dps))

    @callback
    def _async_on_availability(self, available: bool, error: TuyaLanError | None) -> None:
        if available:
            self._key_failures = 0
            self._async_store_detected_version()
            # The key works (again): a reauth started while the purifier was
            # busy or rebooting is no longer needed.
            for flow in self.config_entry.async_get_active_flows(self.hass, {SOURCE_REAUTH}):
                self.hass.config_entries.flow.async_abort(flow["flow_id"])
            if self.data is not None:
                self.async_set_updated_data(PurifierState.from_dps(self.device.dps))
            return
        _LOGGER.debug("Birdie air purifier %s unavailable: %s", self.device_id, error)
        if isinstance(error, InvalidLocalKeyError):
            failure = UpdateFailed(translation_domain=DOMAIN, translation_key="invalid_local_key")
        else:
            failure = UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="device_unreachable",
                translation_placeholders={"host": self.device.config.host},
            )
        self.async_set_update_error(failure)

    @callback
    def _async_on_error(self, error: TuyaLanError) -> None:
        if not isinstance(error, InvalidLocalKeyError):
            self._key_failures = 0
            return
        self._key_failures += 1
        if self._key_failures == PURIFIER_KEY_FAILURES_BEFORE_REAUTH:
            _LOGGER.warning(
                "Birdie air purifier %s keeps rejecting its local key; "
                "it may have been paired again",
                self.device_id,
            )
            self.config_entry.async_start_reauth(self.hass)

    @callback
    def _async_store_detected_version(self) -> None:
        version = self.device.protocol_version
        if version and version != self.config_entry.data.get(CONF_DETECTED_VERSION):
            self.hass.config_entries.async_update_entry(
                self.config_entry,
                data={**self.config_entry.data, CONF_DETECTED_VERSION: version},
            )
