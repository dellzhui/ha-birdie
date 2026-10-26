"""End to end in Home Assistant: real Tuya LAN client against the simulated device.

Exercises the actual protocol and encryption over localhost; it is a
simulation, not a test against Birdie hardware.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import replace
from unittest.mock import patch

import pytest
from homeassistant.components.fan import ATTR_PERCENTAGE, SERVICE_SET_PERCENTAGE
from homeassistant.components.fan import DOMAIN as FAN_DOMAIN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_ENTITY_ID, CONF_HOST, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant

from custom_components.birdie.purifier import coordinator as coordinator_module
from custom_components.birdie.tuya_lan import Timings

from .helpers import (
    DEVICE_ID,
    FAN,
    LOCAL_KEY,
    PURIFIER_DPS,
    mock_purifier_entry,
    purifier_entry_data,
)
from .tuya_lan.fake_device import FakeTuyaDevice

pytestmark = pytest.mark.usefixtures("socket_enabled")

FAST = Timings(
    connect_timeout=1.0,
    receive_timeout=0.1,
    heartbeat_interval=0.3,
    dead_after=1.5,
    reconnect_min=0.1,
    reconnect_max=0.3,
)


@pytest.fixture(params=["3.3", "3.5"])
async def device(request: pytest.FixtureRequest) -> AsyncIterator[FakeTuyaDevice]:
    fake = FakeTuyaDevice(DEVICE_ID, LOCAL_KEY, request.param, dict(PURIFIER_DPS))
    await fake.start()
    yield fake
    await fake.stop()


@pytest.fixture
def route_to(device: FakeTuyaDevice) -> Iterator[None]:
    """Point the integration at the simulated device and use short timings."""
    original = coordinator_module.device_config
    real_device = coordinator_module.TuyaLanDevice

    def config(data):  # type: ignore[no-untyped-def]
        return replace(original(data), port=device.port)

    def make(*args, **kwargs):  # type: ignore[no-untyped-def]
        return real_device(*args, timings=FAST, **kwargs)

    with (
        patch.object(coordinator_module, "device_config", config),
        patch.object(coordinator_module, "TuyaLanDevice", make),
    ):
        yield


async def wait_for(hass: HomeAssistant, condition: Callable[[], bool]) -> None:
    async with asyncio.timeout(5):
        while not condition():
            await asyncio.sleep(0.05)
            await hass.async_block_till_done()


@pytest.mark.usefixtures("route_to")
async def test_control_and_reconnect(hass: HomeAssistant, device: FakeTuyaDevice) -> None:
    entry = mock_purifier_entry(data=purifier_entry_data(**{CONF_HOST: "127.0.0.1"}))
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get(FAN).state == STATE_ON

    await hass.services.async_call(
        FAN_DOMAIN,
        SERVICE_SET_PERCENTAGE,
        {ATTR_ENTITY_ID: FAN, ATTR_PERCENTAGE: 25},
        blocking=True,
    )
    await wait_for(hass, lambda: device.dps["3"] == "manual1")

    await device.push({"2": 55})
    await wait_for(hass, lambda: hass.states.get("sensor.birdie_podium_pm2_5").state == "55")

    device.silent = True  # hangs: heartbeats go unanswered
    await wait_for(hass, lambda: hass.states.get(FAN).state == STATE_UNAVAILABLE)
    device.silent = False
    await wait_for(hass, lambda: hass.states.get(FAN).state == STATE_ON)

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    await wait_for(hass, lambda: device.open_connections == 0)
