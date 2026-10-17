"""Shared test helpers for the Birdie integration."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


class FakeTransport:
    """In-memory ``CloudTransport`` backed by the synthetic Pedestal fixtures."""

    def __init__(self, *, online: bool = True) -> None:
        self.details = load_fixture("pedestal_device.json") | {"is_online": online}
        self.status = load_fixture("pedestal_status.json")
        self.spec = load_fixture("pedestal_specification.json")
        self.commands: list[tuple[str, list[dict[str, Any]]]] = []
        self.spec_requests = 0

    async def async_get_device(self, device_id: str) -> dict[str, Any]:
        return copy.deepcopy(self.details)

    async def async_get_device_status(self, device_id: str) -> list[dict[str, Any]]:
        return copy.deepcopy(self.status)

    async def async_get_device_specification(self, device_id: str) -> dict[str, Any]:
        self.spec_requests += 1
        return copy.deepcopy(self.spec)

    async def async_send_commands(self, device_id: str, commands: list[dict[str, Any]]) -> bool:
        self.commands.append((device_id, copy.deepcopy(commands)))
        return True
