"""Config flow for the Birdie integration."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

import voluptuous as vol

from homeassistant.components.bluetooth import (
    BluetoothScanningMode,
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
    async_register_callback,
)
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_ADDRESS
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .ble import is_birdie_advertisement, normalize_address
from .cloud.bridge import (
    BirdieBridgeClient,
    BridgeAccount,
    BridgeAuthError,
    BridgeError,
    StaticTokenProvider,
)
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_ACCOUNT_ID,
    CONF_BRIDGE_URL,
    CONF_ENTRY_TYPE,
    CONF_NAME,
    DOMAIN,
    ENTRY_TYPE_CLOUD,
    ENVIRONMENTAL_SERVICE_UUID,
)

DISCOVERY_TIMEOUT = 10

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ADDRESS): str,
        vol.Optional(CONF_NAME): str,
    }
)


TOKEN_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))

CLOUD_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_BRIDGE_URL): str,
        vol.Required(CONF_ACCESS_TOKEN): TOKEN_SELECTOR,
    }
)

REAUTH_SCHEMA = vol.Schema({vol.Required(CONF_ACCESS_TOKEN): TOKEN_SELECTOR})


def _normalize_bridge_url(value: str) -> str | None:
    """Return the bridge base URL without trailing slash, or None if invalid."""
    url = value.strip().rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    return url


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
        """Let the user choose between a Bluetooth device and a Birdie account."""
        return self.async_show_menu(step_id="user", menu_options=["ble", "cloud"])

    async def async_step_ble(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
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

    async def async_step_cloud(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add a Birdie account (cloud-connected devices such as Birdie Pedestal).

        Uses a bridge URL and access token until Birdie's sign-in flow is final.
        """
        errors: dict[str, str] = {}
        if user_input is not None:
            url = _normalize_bridge_url(user_input[CONF_BRIDGE_URL])
            token = user_input[CONF_ACCESS_TOKEN].strip()
            if url is None:
                errors[CONF_BRIDGE_URL] = "invalid_url"
            else:
                account, error = await self._async_validate_cloud(url, token)
                if account is not None:
                    await self.async_set_unique_id(f"cloud_{account.id}")
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title=account.display_name or "Birdie account",
                        data={
                            CONF_ENTRY_TYPE: ENTRY_TYPE_CLOUD,
                            CONF_BRIDGE_URL: url,
                            CONF_ACCESS_TOKEN: token,
                            CONF_ACCOUNT_ID: account.id,
                        },
                    )
                errors["base"] = error

        return self.async_show_form(
            step_id="cloud",
            data_schema=self.add_suggested_values_to_schema(CLOUD_SCHEMA, user_input),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Handle a rejected Birdie account token."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new access token for the same Birdie account."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            token = user_input[CONF_ACCESS_TOKEN].strip()
            account, error = await self._async_validate_cloud(
                entry.data[CONF_BRIDGE_URL], token
            )
            if account is not None:
                if account.id != entry.data.get(CONF_ACCOUNT_ID):
                    return self.async_abort(reason="wrong_account")
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_ACCESS_TOKEN: token}
                )
            errors["base"] = error

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=REAUTH_SCHEMA,
            errors=errors,
            description_placeholders={"account": entry.title},
        )

    async def _async_validate_cloud(
        self, url: str, token: str
    ) -> tuple[BridgeAccount | None, str]:
        """Check the bridge URL and token; return the account or an error key."""
        client = BirdieBridgeClient(
            async_get_clientsession(self.hass), url, StaticTokenProvider(token)
        )
        try:
            account = await client.async_get_account()
            await client.async_list_devices(include_status=False)
        except BridgeAuthError:
            return None, "invalid_auth"
        except BridgeError:
            return None, "cannot_connect"
        return account, ""

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
