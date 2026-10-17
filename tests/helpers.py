"""Shared test helpers for the Birdie integration."""

from __future__ import annotations

import copy
import json
import time
from pathlib import Path
from typing import Any

from custom_components.birdie.cloud.bridge import (
    BridgeAccount,
    BridgeApiError,
    BridgeDevice,
    BridgeDeviceNotFoundError,
)
from custom_components.birdie.const import (
    CONF_ACCESS_TOKEN,
    CONF_ACCOUNT_ID,
    CONF_BRIDGE_URL,
    CONF_ENTRY_TYPE,
    DOMAIN,
    ENTRY_TYPE_CLOUD,
)

FIXTURES = Path(__file__).parent / "fixtures"

DEVICE_ID = "5b0c9e7d2a4f4e1c8a3d6b90"
ACCOUNT_ID = "acct-synthetic-1"
BRIDGE_URL = "https://bridge.test"
TOKEN = "dev-token-1"


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


def pedestal_device(device_id: str = DEVICE_ID) -> dict[str, Any]:
    """Synthetic Pedestal device object as returned by the bridge."""
    device = load_fixture("pedestal_device.json")
    device["id"] = device_id
    return device


def cloud_entry_data(token: str = TOKEN) -> dict[str, Any]:
    return {
        CONF_ENTRY_TYPE: ENTRY_TYPE_CLOUD,
        CONF_BRIDGE_URL: BRIDGE_URL,
        CONF_ACCESS_TOKEN: token,
        CONF_ACCOUNT_ID: ACCOUNT_ID,
    }


def oauth_entry_data(refresh_token: str = "brt_grant.secret1") -> dict[str, Any]:
    from custom_components.birdie.cloud.oauth import implementation_key

    return {
        CONF_ENTRY_TYPE: ENTRY_TYPE_CLOUD,
        CONF_BRIDGE_URL: BRIDGE_URL,
        CONF_ACCOUNT_ID: ACCOUNT_ID,
        "auth_implementation": implementation_key(BRIDGE_URL),
        "token": {
            "access_token": "bat_current",
            "refresh_token": refresh_token,
            "token_type": "Bearer",
            "expires_in": 3600,
            "expires_at": time.time() + 3600,
        },
    }


def mock_cloud_entry(*, oauth: bool = False, **kwargs: Any) -> Any:
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    kwargs.setdefault("data", oauth_entry_data() if oauth else cloud_entry_data())
    return MockConfigEntry(
        domain=DOMAIN,
        title="jane@example.com",
        unique_id=f"cloud_{ACCOUNT_ID}",
        **kwargs,
    )


class FakeBridgeClient:
    """In-memory stand-in for ``BirdieBridgeClient`` with one Pedestal."""

    def __init__(self) -> None:
        self.account = BridgeAccount(id=ACCOUNT_ID, display_name="jane@example.com")
        self.devices: dict[str, dict[str, Any]] = {}
        self.commands: list[tuple[str, list[dict[str, Any]]]] = []
        self.list_requests = 0
        self.created_with: list[tuple[object, ...]] = []
        # Set to an exception instance to make the next calls fail.
        self.fail_list: Exception | None = None
        self.fail_account: Exception | None = None
        self.fail_command: Exception | None = None
        self.add_device(DEVICE_ID)

    def add_device(self, device_id: str, *, online: bool = True, model: str = "pedestal") -> None:
        device = pedestal_device(device_id)
        device.update(online=online, model=model)
        self.devices[device_id] = device

    async def async_get_account(self) -> BridgeAccount:
        if self.fail_account:
            raise self.fail_account
        return self.account

    async def async_list_devices(self, *, include_state: bool = True) -> list[BridgeDevice]:
        self.list_requests += 1
        if self.fail_list:
            raise self.fail_list
        devices = copy.deepcopy(list(self.devices.values()))
        if not include_state:
            for device in devices:
                del device["state"], device["fetched_at"]
        return [BridgeDevice.from_json(device) for device in devices]

    async def async_send_commands(self, device_id: str, commands: list[dict[str, Any]]) -> None:
        if self.fail_command:
            raise self.fail_command
        if device_id not in self.devices:
            raise BridgeDeviceNotFoundError(404, "device_not_found")
        self.commands.append((device_id, copy.deepcopy(commands)))
        state = self.devices[device_id]["state"]
        for command in commands:
            params = command["params"]
            if command["name"] == "set_power":
                state["power"] = params["on"]
            elif command["name"] == "set_mode":
                state.update(mode=params["mode"], fan_speed=None)
            elif command["name"] == "set_fan_speed":
                state.update(mode="manual", fan_speed=params["speed"])
            elif command["name"] == "reset_filter":
                state.update(filter_life_percent=100, filter_replace_needed=False)


__all__ = ["BridgeApiError", "FakeBridgeClient"]
