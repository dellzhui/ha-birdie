"""Config flow for the Birdie integration."""

from __future__ import annotations

import asyncio
from typing import Any

import voluptuous as vol

from homeassistant.components.bluetooth import (
    BluetoothScanningMode,
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
    async_register_callback,
)
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_ADDRESS

from .ble import is_birdie_advertisement, normalize_address
from .const import CONF_NAME, DOMAIN, ENVIRONMENTAL_SERVICE_UUID

DISCOVERY_TIMEOUT = 10

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ADDRESS): str,
        vol.Optional(CONF_NAME): str,
    }
)


def _discovery_title(discovery_info: BluetoothServiceInfoBleak) -> str:
    """Return a friendly title for a discovered Birdie device."""
    return discovery_info.name or discovery_info.address


def _is_supported(discovery_info: BluetoothServiceInfoBleak) -> bool:
    """Return true when a Bluetooth discovery result looks like Birdie."""
    return is_birdie_advertisement(discovery_info.name, discovery_info.service_uuids)


class BirdieConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Birdie."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize flow state."""
        self._discovery_info: BluetoothServiceInfoBleak | None = None
        self._discovered_devices: dict[str, str] = {}
        self._scan_complete = False
        self._scan_task: asyncio.Task[None] | None = None

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle Bluetooth discovery."""
        if not _is_supported(discovery_info):
            return self.async_abort(reason="not_supported")

        address = normalize_address(discovery_info.address)
        await self.async_set_unique_id(address)
        self._abort_if_unique_id_configured()
        self._discovery_info = discovery_info
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm a discovered Birdie device."""
        assert self._discovery_info is not None
        title = _discovery_title(self._discovery_info)

        if user_input is not None:
            address = normalize_address(self._discovery_info.address)
            return self.async_create_entry(
                title=title,
                data={
                    CONF_ADDRESS: address,
                    CONF_NAME: self._discovery_info.name or title,
                },
            )

        self._set_confirm_only()
        self.context["title_placeholders"] = {"name": title}
        return self.async_show_form(
            step_id="bluetooth_confirm",
            description_placeholders={"name": title},
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle manual setup."""
        if user_input is not None:
            address = normalize_address(user_input[CONF_ADDRESS])
            title = user_input.get(CONF_NAME) or self._discovered_devices.get(address) or address
            await self.async_set_unique_id(address, raise_on_progress=False)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=title,
                data={
                    CONF_ADDRESS: address,
                    CONF_NAME: title,
                },
            )

        if not self._scan_complete:
            return await self.async_step_scan()

        current_ids = self._async_current_ids(include_ignore=False)
        self._async_collect_cached_discoveries(current_ids)

        if self._discovered_devices:
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema(
                    {
                        vol.Required(CONF_ADDRESS): vol.In(self._discovered_devices),
                        vol.Optional(CONF_NAME): str,
                    }
                ),
            )

        return self.async_show_form(
            step_id="user",
            data_schema=USER_SCHEMA,
            description_placeholders={"manual": "true"},
        )

    async def async_step_scan(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Scan briefly for Birdie Bluetooth advertisements."""
        if self._scan_task is None:
            self._scan_task = self.hass.async_create_task(
                self._async_scan_for_devices()
            )

        if not self._scan_task.done():
            return self.async_show_progress(
                step_id="scan",
                progress_action="scan",
                progress_task=self._scan_task,
            )

        await self._scan_task
        self._scan_task = None
        self._scan_complete = True
        return self.async_show_progress_done(next_step_id="user")

    async def _async_scan_for_devices(self) -> None:
        """Wait for Birdie advertisements and collect candidates."""
        current_ids = self._async_current_ids(include_ignore=False)
        self._async_collect_cached_discoveries(current_ids)

        if self._discovered_devices:
            return

        done = self.hass.loop.create_future()

        def _async_discovered_device(
            discovery_info: BluetoothServiceInfoBleak,
            change: Any,
        ) -> None:
            if self._async_add_discovery(discovery_info, current_ids) and not done.done():
                done.set_result(None)

        unloads = (
            async_register_callback(
                self.hass,
                _async_discovered_device,
                {
                    "connectable": True,
                    "service_uuid": ENVIRONMENTAL_SERVICE_UUID,
                },
                BluetoothScanningMode.ACTIVE,
            ),
            async_register_callback(
                self.hass,
                _async_discovered_device,
                {
                    "connectable": True,
                    "local_name": "Birdie*",
                },
                BluetoothScanningMode.ACTIVE,
            ),
            async_register_callback(
                self.hass,
                _async_discovered_device,
                {
                    "connectable": False,
                    "service_uuid": ENVIRONMENTAL_SERVICE_UUID,
                },
                BluetoothScanningMode.ACTIVE,
            ),
            async_register_callback(
                self.hass,
                _async_discovered_device,
                {
                    "connectable": False,
                    "local_name": "Birdie*",
                },
                BluetoothScanningMode.ACTIVE,
            ),
            async_register_callback(
                self.hass,
                _async_discovered_device,
                {
                    "connectable": False,
                    "local_name": "Birdie Pro*",
                },
                BluetoothScanningMode.ACTIVE,
            ),
        )

        try:
            await asyncio.wait_for(done, timeout=DISCOVERY_TIMEOUT)
        except TimeoutError:
            pass
        finally:
            for unload in unloads:
                unload()

    def _async_collect_cached_discoveries(self, current_ids: set[str]) -> None:
        """Collect Birdie candidates already cached by Home Assistant Bluetooth."""
        for connectable in (True, False):
            for discovery_info in async_discovered_service_info(
                self.hass, connectable=connectable
            ):
                self._async_add_discovery(discovery_info, current_ids)

    def _async_add_discovery(
        self,
        discovery_info: BluetoothServiceInfoBleak,
        current_ids: set[str],
    ) -> bool:
        """Add a discovered Birdie device if it is a new supported candidate."""
        address = normalize_address(discovery_info.address)
        if address in current_ids or address in self._discovered_devices:
            return False
        if not _is_supported(discovery_info):
            return False
        self._discovered_devices[address] = _discovery_title(discovery_info)
        return True
