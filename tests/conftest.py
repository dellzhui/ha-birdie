"""Shared fixtures for the Birdie integration tests."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

import pytest

# Import the patched modules up front so ``patch`` can resolve them.
import custom_components.birdie.config_flow
import custom_components.birdie.purifier.coordinator
import custom_components.birdie.purifier.discovery
import custom_components.birdie.purifier.flow  # noqa: F401
from custom_components.birdie.tuya_lan import DiscoveredDevice

from .helpers import FakeLan, FakeListener


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> Iterator[None]:
    """Allow Home Assistant to load custom_components/birdie in every test."""
    yield


@pytest.fixture(autouse=True)
def lan_broadcasts() -> Iterator[dict[str, DiscoveredDevice]]:
    """Devices the (fake, socket-free) discovery listener will report."""
    devices: dict[str, DiscoveredDevice] = {}
    FakeListener.instances.clear()

    async def create(hass: Any) -> FakeListener:
        return FakeListener(devices)

    with patch(
        "custom_components.birdie.purifier.discovery._async_create_listener", side_effect=create
    ):
        yield devices


@pytest.fixture
def fake_lan() -> Iterator[FakeLan]:
    """Replace the Tuya LAN client used by the config flow and the coordinator."""
    lan = FakeLan()
    with (
        patch("custom_components.birdie.purifier.coordinator.TuyaLanDevice", lan.factory),
        patch("custom_components.birdie.purifier.flow.TuyaLanDevice", lan.factory),
    ):
        yield lan
