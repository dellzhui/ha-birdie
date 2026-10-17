"""Shared test helpers for the Birdie integration."""

from __future__ import annotations

import copy
import json
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

DEVICE_ID = "bfsynthetic0000001"
ACCOUNT_ID = "acct-synthetic-1"
BRIDGE_URL = "https://bridge.test"
TOKEN = "dev-token-1"


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


def pedestal_status() -> dict[str, Any]:
    """Synthetic Pedestal status in bridge format ({code: value})."""
    return {item["code"]: item["value"] for item in load_fixture("pedestal_status.json")}


def cloud_entry_data(token: str = TOKEN) -> dict[str, Any]:
    return {
        CONF_ENTRY_TYPE: ENTRY_TYPE_CLOUD,
        CONF_BRIDGE_URL: BRIDGE_URL,
        CONF_ACCESS_TOKEN: token,
        CONF_ACCOUNT_ID: ACCOUNT_ID,
    }


def mock_cloud_entry(**kwargs: Any) -> Any:
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    return MockConfigEntry(
        domain=DOMAIN,
        title="Birdie account",
        unique_id=f"cloud_{ACCOUNT_ID}",
        data=cloud_entry_data(),
        **kwargs,
    )


class FakeBridgeClient:
    """In-memory stand-in for ``BirdieBridgeClient`` with one Pedestal."""

    def __init__(self) -> None:
        self.account = BridgeAccount(id=ACCOUNT_ID, display_name="jane@example.com")
        self.devices: dict[str, dict[str, Any]] = {}
        self.spec = load_fixture("pedestal_specification.json")
        self.commands: list[tuple[str, list[dict[str, Any]]]] = []
        self.spec_requests = 0
        self.list_requests = 0
        self.created_with: list[tuple[object, ...]] = []
        # Set to an exception instance to make the next calls fail.
        self.fail_list: Exception | None = None
        self.fail_account: Exception | None = None
        self.fail_command: Exception | None = None
        self.add_device(DEVICE_ID)

    def add_device(self, device_id: str, *, online: bool = True, model: str = "pedestal") -> None:
        self.devices[device_id] = {
            "id": device_id,
            "name": "Living Room Purifier",
            "model": model,
            "model_name": "Birdie Pedestal",
            "product_id": "synthpid00000001",
            "online": online,
            "status": pedestal_status(),
        }

    async def async_get_account(self) -> BridgeAccount:
        if self.fail_account:
            raise self.fail_account
        return self.account

    async def async_list_devices(self, *, include_status: bool = True) -> list[BridgeDevice]:
        self.list_requests += 1
        if self.fail_list:
            raise self.fail_list
        return [BridgeDevice.from_json(copy.deepcopy(d)) for d in self.devices.values()]

    async def async_get_specification(self, device_id: str) -> dict[str, Any]:
        self.spec_requests += 1
        if device_id not in self.devices:
            raise BridgeDeviceNotFoundError(404, "device_not_found")
        return copy.deepcopy(self.spec)

    async def async_send_commands(self, device_id: str, commands: list[dict[str, Any]]) -> None:
        if self.fail_command:
            raise self.fail_command
        if device_id not in self.devices:
            raise BridgeDeviceNotFoundError(404, "device_not_found")
        self.commands.append((device_id, copy.deepcopy(commands)))
        status = self.devices[device_id]["status"]
        for command in commands:
            if command["code"] == "reset_filter":
                status["filter_used_minutes"] = 0
            else:
                status[command["code"]] = command["value"]


__all__ = ["BridgeApiError", "FakeBridgeClient"]
