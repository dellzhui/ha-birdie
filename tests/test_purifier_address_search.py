"""A purifier that stays unreachable is looked for with its key at other addresses."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant

from custom_components.birdie.lan import DeviceUnreachableError, FoundDevice, InvalidLocalKeyError
from custom_components.birdie.purifier.coordinator import (
    ADDRESS_SEARCH_AFTER,
    ADDRESS_SEARCH_FIRST_INTERVAL,
)

from .helpers import DEVICE_ID, HOST, LOCAL_KEY, FakeLan, mock_purifier_entry

NEW_HOST = "192.168.1.88"
UNREACHABLE = DeviceUnreachableError("no answer")


class Clock:
    """Stands in for time.monotonic in the coordinator."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Iterator[Clock]:
    clock = Clock()
    with patch("custom_components.birdie.purifier.coordinator._monotonic", clock):
        yield clock


@pytest.fixture
def search() -> Iterator[AsyncMock]:
    with patch(
        "custom_components.birdie.purifier.discovery.async_search_network",
        AsyncMock(return_value=None),
    ) as search:
        yield search


@pytest.fixture
async def entry(hass: HomeAssistant, fake_lan: FakeLan, clock: Clock, search: AsyncMock):
    entry = mock_purifier_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _fail(hass: HomeAssistant, fake_lan: FakeLan, error=UNREACHABLE) -> None:
    fake_lan.device.fail_reconnect(error)
    await hass.async_block_till_done()


async def test_found_at_a_new_address(
    hass: HomeAssistant, entry, fake_lan: FakeLan, clock: Clock, search: AsyncMock
) -> None:
    search.return_value = FoundDevice(NEW_HOST, "3.3", {"1": True})
    fake_lan.device.go_offline()
    clock.now += ADDRESS_SEARCH_AFTER - 1
    await _fail(hass, fake_lan)
    search.assert_not_awaited()

    clock.now += 2
    await _fail(hass, fake_lan)
    search.assert_awaited_once_with(hass, DEVICE_ID, LOCAL_KEY, "auto")
    assert entry.data[CONF_HOST] == NEW_HOST
    assert fake_lan.device.config.host == NEW_HOST


async def test_searches_back_off_and_reset(
    hass: HomeAssistant, entry, fake_lan: FakeLan, clock: Clock, search: AsyncMock
) -> None:
    fake_lan.device.go_offline()
    clock.now += ADDRESS_SEARCH_AFTER
    await _fail(hass, fake_lan)
    assert search.await_count == 1

    clock.now += ADDRESS_SEARCH_FIRST_INTERVAL - 1
    await _fail(hass, fake_lan)
    assert search.await_count == 1
    clock.now += 1
    await _fail(hass, fake_lan)
    assert search.await_count == 2

    # The next wait is twice as long.
    clock.now += ADDRESS_SEARCH_FIRST_INTERVAL
    await _fail(hass, fake_lan)
    assert search.await_count == 2
    clock.now += ADDRESS_SEARCH_FIRST_INTERVAL
    await _fail(hass, fake_lan)
    assert search.await_count == 3
    assert entry.data[CONF_HOST] == HOST

    # Back again: the next outage starts from scratch.
    fake_lan.device.come_back()
    await hass.async_block_till_done()
    fake_lan.device.go_offline()
    clock.now += ADDRESS_SEARCH_AFTER
    await _fail(hass, fake_lan)
    assert search.await_count == 4


async def test_rejected_key_is_not_searched_for(
    hass: HomeAssistant, entry, fake_lan: FakeLan, clock: Clock, search: AsyncMock
) -> None:
    fake_lan.device.go_offline(InvalidLocalKeyError("rejected"))
    clock.now += ADDRESS_SEARCH_AFTER * 2
    await _fail(hass, fake_lan, InvalidLocalKeyError("rejected"))
    search.assert_not_awaited()


async def test_found_while_setup_is_retried(
    hass: HomeAssistant, fake_lan: FakeLan, clock: Clock, search: AsyncMock
) -> None:
    """Home Assistant started while the purifier had a new, unannounced address."""
    fake_lan.unreachable_hosts.add(HOST)
    search.return_value = FoundDevice(NEW_HOST, "3.3", {"1": True})
    entry = mock_purifier_entry()
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY
    search.assert_not_awaited()

    clock.now += ADDRESS_SEARCH_AFTER
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    search.assert_awaited_once()
    assert entry.data[CONF_HOST] == NEW_HOST
    assert entry.state is ConfigEntryState.LOADED
