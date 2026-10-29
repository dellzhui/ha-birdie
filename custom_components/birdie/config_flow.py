"""Config flow for the Birdie integration.

Two kinds of entries: Birdie Bluetooth devices (Birdie Pro) and Birdie air
purifiers, which are Tuya-based Wi-Fi devices controlled over the local
network with their device id and local key.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.components.bluetooth import (
    BluetoothScanningMode,
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
    async_register_callback,
)
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_ADDRESS, CONF_HOST

from .ble import is_birdie_advertisement, normalize_address
from .const import (
    CONF_DETECTED_VERSION,
    CONF_DEVICE_ID,
    CONF_ENTRY_TYPE,
    CONF_LOCAL_KEY,
    CONF_NAME,
    CONF_PRODUCT_KEY,
    CONF_PROTOCOL_VERSION,
    DOMAIN,
    ENTRY_TYPE_PURIFIER,
    ENVIRONMENTAL_SERVICE_UUID,
    PURIFIER_DISCOVERY_SECONDS,
    is_purifier_entry_data,
)
from .purifier.discovery import async_scan, is_birdie_purifier
from .purifier.flow import (
    KEY_SCHEMA,
    MANUAL_SCHEMA,
    RECONFIGURE_SCHEMA,
    SECTION_ADVANCED,
    ConnectionResult,
    async_check_connection,
    clean_local_key,
)
from .purifier.model import MODEL_NAME
from .tuya_lan import PROTOCOL_AUTO, DeviceConfig, DiscoveredDevice

_LOGGER = logging.getLogger(__name__)

DISCOVERY_TIMEOUT = 10
PURIFIER_TITLE = f"Birdie {MODEL_NAME}"
CHOICE_MANUAL = "manual"

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ADDRESS): str,
        vol.Optional(CONF_NAME): str,
    }
)


def _form_input(user_input: dict[str, Any] | None, required: str) -> dict[str, Any] | None:
    """Input for this step's form, or None.

    After a progress step Home Assistant passes the progress step's input on
    to the next step, which then has not shown its form yet.
    """
    if user_input is None or required not in user_input:
        return None
    return user_input


def _discovery_title(discovery_info: BluetoothServiceInfoBleak) -> str:
    """Return a friendly title for a discovered Birdie device."""
    return discovery_info.name or discovery_info.address


def _is_supported(discovery_info: BluetoothServiceInfoBleak) -> bool:
    """Return true when a Bluetooth discovery result looks like Birdie."""
    return is_birdie_advertisement(discovery_info.name, discovery_info.service_uuids)


class BirdieConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Birdie Bluetooth devices and air purifiers."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize flow state."""
        self._discovery_info: BluetoothServiceInfoBleak | None = None
        self._discovered_devices: dict[str, str] = {}
        self._scan_complete = False
        self._scan_task: asyncio.Task[None] | None = None
        self._lan_task: asyncio.Task[dict[str, DiscoveredDevice]] | None = None
        self._lan_devices: dict[str, DiscoveredDevice] = {}
        self._purifier: DiscoveredDevice | None = None

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

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Let the user choose between a Bluetooth device and an air purifier."""
        return self.async_show_menu(step_id="user", menu_options=["ble", "purifier"])

    async def async_step_ble(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle manual setup of a Bluetooth device."""
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
                step_id="ble",
                data_schema=vol.Schema(
                    {
                        vol.Required(CONF_ADDRESS): vol.In(self._discovered_devices),
                        vol.Optional(CONF_NAME): str,
                    }
                ),
            )

        return self.async_show_form(
            step_id="ble",
            data_schema=USER_SCHEMA,
            description_placeholders={"manual": "true"},
        )

    # ------------------------------------------------------- air purifier

    async def async_step_purifier(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Search the local network for Birdie air purifiers."""
        if self._lan_task is None:
            self._lan_task = self.hass.async_create_task(
                async_scan(self.hass, PURIFIER_DISCOVERY_SECONDS)
            )
        if not self._lan_task.done():
            return self.async_show_progress(
                step_id="purifier",
                progress_action="lan_scan",
                progress_task=self._lan_task,
            )
        try:
            found = self._lan_task.result()
        except Exception:
            _LOGGER.exception("Searching the network for air purifiers failed")
            found = {}
        configured = self._async_current_ids(include_ignore=False)
        self._lan_devices = {
            device_id: device
            for device_id, device in found.items()
            if is_birdie_purifier(device) and device_id not in configured
        }
        return self.async_show_progress_done(
            next_step_id="purifier_pick" if self._lan_devices else "purifier_not_found"
        )

    async def async_step_purifier_pick(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick a discovered purifier or enter one manually."""
        user_input = _form_input(user_input, CONF_DEVICE_ID)
        if user_input is not None:
            choice = user_input[CONF_DEVICE_ID]
            if choice == CHOICE_MANUAL:
                return await self.async_step_purifier_manual()
            self._purifier = self._lan_devices[choice]
            return await self.async_step_purifier_key()
        options = {
            device_id: f"{PURIFIER_TITLE} {device_id} ({device.host})"
            for device_id, device in self._lan_devices.items()
        }
        options[CHOICE_MANUAL] = "Enter manually"
        return self.async_show_form(
            step_id="purifier_pick",
            data_schema=vol.Schema({vol.Required(CONF_DEVICE_ID): vol.In(options)}),
        )

    async def async_step_integration_discovery(
        self, discovery_info: dict[str, Any]
    ) -> ConfigFlowResult:
        """A Birdie purifier announced itself on the network."""
        device_id = discovery_info[CONF_DEVICE_ID]
        await self.async_set_unique_id(device_id)
        self._abort_if_unique_id_configured(updates={CONF_HOST: discovery_info[CONF_HOST]})
        self._purifier = DiscoveredDevice(
            device_id=device_id,
            host=discovery_info[CONF_HOST],
            protocol_version=discovery_info.get(CONF_PROTOCOL_VERSION),
            product_key=discovery_info.get(CONF_PRODUCT_KEY),
            seen_at=0.0,
        )
        self.context["title_placeholders"] = {"name": PURIFIER_TITLE}
        return await self.async_step_purifier_key()

    async def async_step_purifier_key(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the local key of a discovered purifier."""
        device = self._purifier
        assert device is not None
        errors: dict[str, str] = {}
        if user_input is not None:
            key = clean_local_key(user_input[CONF_LOCAL_KEY])
            if key is None:
                errors[CONF_LOCAL_KEY] = "invalid_local_key_format"
            else:
                config = DeviceConfig(device.device_id, device.host, key)
                result = await async_check_connection(config, device.protocol_version)
                if result.error is None:
                    return await self._async_create_purifier(
                        config, result, product_key=device.product_key
                    )
                errors["base"] = result.error
        return self.async_show_form(
            step_id="purifier_key",
            data_schema=KEY_SCHEMA,
            errors=errors,
            description_placeholders={"device_id": device.device_id, "host": device.host},
        )

    async def async_step_purifier_not_found(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """The search found no new purifier; the manual form says so."""
        return await self._async_purifier_manual("purifier_not_found", user_input)

    async def async_step_purifier_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Enter device id, local key and optionally the IP address."""
        return await self._async_purifier_manual("purifier_manual", user_input)

    async def _async_purifier_manual(
        self, step_id: str, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        user_input = _form_input(user_input, CONF_DEVICE_ID)
        errors: dict[str, str] = {}
        if user_input is not None:
            device_id = user_input[CONF_DEVICE_ID].strip()
            key = clean_local_key(user_input[CONF_LOCAL_KEY])
            host = (user_input.get(CONF_HOST) or "").strip()
            version = (user_input.get(SECTION_ADVANCED) or {}).get(
                CONF_PROTOCOL_VERSION, PROTOCOL_AUTO
            )
            await self.async_set_unique_id(device_id, raise_on_progress=False)
            self._abort_if_unique_id_configured()
            hint: str | None = None
            product_key: str | None = None
            if not device_id:
                errors[CONF_DEVICE_ID] = "invalid_device_id"
            elif key is None:
                errors[CONF_LOCAL_KEY] = "invalid_local_key_format"
            elif not host:
                found = self._lan_devices.get(device_id) or (
                    await async_scan(self.hass, PURIFIER_DISCOVERY_SECONDS, device_id=device_id)
                ).get(device_id)
                if found is None:
                    errors[CONF_HOST] = "device_not_found"
                else:
                    host, hint, product_key = found.host, found.protocol_version, found.product_key
            if not errors:
                assert key is not None
                config = DeviceConfig(device_id, host, key, version)
                result = await async_check_connection(config, hint)
                if result.error is None:
                    return await self._async_create_purifier(
                        config, result, product_key=product_key
                    )
                errors["base"] = result.error
        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(MANUAL_SCHEMA, user_input),
            errors=errors,
        )

    async def _async_create_purifier(
        self, config: DeviceConfig, result: ConnectionResult, *, product_key: str | None
    ) -> ConfigFlowResult:
        await self.async_set_unique_id(config.device_id, raise_on_progress=False)
        self._abort_if_unique_id_configured()
        data: dict[str, Any] = {
            CONF_ENTRY_TYPE: ENTRY_TYPE_PURIFIER,
            CONF_DEVICE_ID: config.device_id,
            CONF_HOST: config.host,
            CONF_LOCAL_KEY: config.local_key,
            CONF_PROTOCOL_VERSION: config.protocol_version,
            CONF_DETECTED_VERSION: result.version,
        }
        if product_key:
            data[CONF_PRODUCT_KEY] = product_key
        return self.async_create_entry(title=PURIFIER_TITLE, data=data)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """The purifier rejects its local key, usually after it was paired again."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the new local key."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            key = clean_local_key(user_input[CONF_LOCAL_KEY])
            if key is None:
                errors[CONF_LOCAL_KEY] = "invalid_local_key_format"
            else:
                config = DeviceConfig(
                    entry.data[CONF_DEVICE_ID],
                    entry.data[CONF_HOST],
                    key,
                    entry.data.get(CONF_PROTOCOL_VERSION, PROTOCOL_AUTO),
                )
                result = await async_check_connection(config, entry.data.get(CONF_DETECTED_VERSION))
                if result.error is None:
                    return self.async_update_reload_and_abort(
                        entry,
                        data_updates={CONF_LOCAL_KEY: key, CONF_DETECTED_VERSION: result.version},
                    )
                errors["base"] = result.error
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=KEY_SCHEMA,
            errors=errors,
            description_placeholders={"name": entry.title, "device_id": entry.data[CONF_DEVICE_ID]},
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the local key, IP address or protocol version."""
        entry = self._get_reconfigure_entry()
        if not is_purifier_entry_data(entry.data):
            return self.async_abort(reason="reconfigure_not_supported")
        errors: dict[str, str] = {}
        if user_input is not None:
            entered_key = (user_input.get(CONF_LOCAL_KEY) or "").strip()
            key = clean_local_key(entered_key) if entered_key else entry.data[CONF_LOCAL_KEY]
            host = user_input[CONF_HOST].strip()
            version = (user_input.get(SECTION_ADVANCED) or {}).get(
                CONF_PROTOCOL_VERSION, PROTOCOL_AUTO
            )
            if key is None:
                errors[CONF_LOCAL_KEY] = "invalid_local_key_format"
            elif not host:
                errors[CONF_HOST] = "invalid_host"
            else:
                config = DeviceConfig(entry.data[CONF_DEVICE_ID], host, key, version)
                result = await async_check_connection(config, entry.data.get(CONF_DETECTED_VERSION))
                if result.error is None:
                    return self.async_update_reload_and_abort(
                        entry,
                        data_updates={
                            CONF_LOCAL_KEY: key,
                            CONF_HOST: host,
                            CONF_PROTOCOL_VERSION: version,
                            CONF_DETECTED_VERSION: result.version,
                        },
                    )
                errors["base"] = result.error
        suggested = {
            CONF_HOST: entry.data[CONF_HOST],
            SECTION_ADVANCED: {
                CONF_PROTOCOL_VERSION: entry.data.get(CONF_PROTOCOL_VERSION, PROTOCOL_AUTO)
            },
        }
        if user_input is not None:
            entered = {k: v for k, v in user_input.items() if k != CONF_LOCAL_KEY}
            suggested = {**suggested, **entered}
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(RECONFIGURE_SCHEMA, suggested),
            errors=errors,
            description_placeholders={"device_id": entry.data[CONF_DEVICE_ID]},
        )

    # ---------------------------------------------------------- Bluetooth

    async def async_step_scan(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Scan briefly for Birdie Bluetooth advertisements."""
        if self._scan_task is None:
            self._scan_task = self.hass.async_create_task(self._async_scan_for_devices())

        if not self._scan_task.done():
            return self.async_show_progress(
                step_id="scan",
                progress_action="scan",
                progress_task=self._scan_task,
            )

        await self._scan_task
        self._scan_task = None
        self._scan_complete = True
        return self.async_show_progress_done(next_step_id="ble")

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
            for discovery_info in async_discovered_service_info(self.hass, connectable=connectable):
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
