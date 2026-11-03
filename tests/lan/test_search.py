"""Finding a device by its key at the addresses of the local network."""

from __future__ import annotations

import ipaddress
from collections.abc import AsyncIterator

import pytest
import pytest_socket

from custom_components.birdie.lan import Timings, addresses_to_search, async_find_device
from custom_components.birdie.lan import search as search_module

from .fake_device import FakeLanDevice

DEVICE_ID = "bf3a9c71d2e04b58f6qz1x"
LOCAL_KEY = "0123456789abcdef"
OTHER_KEY = "fedcba9876543210"
FAST = Timings(connect_timeout=1.0)

LOOPBACK_HOSTS = ["127.0.0.1", "127.0.0.2", "127.0.0.3", "127.0.0.4", "127.0.0.5", "127.0.0.9"]

pytestmark = pytest.mark.usefixtures("socket_enabled")


@pytest.fixture(autouse=True)
def loopback_only() -> None:
    """Only these loopback addresses may be contacted (the plugin allows just .1)."""
    pytest_socket.socket_allow_hosts(LOOPBACK_HOSTS, allow_unix_socket=True)


def _interfaces(*specs: str) -> list[ipaddress.IPv4Interface]:
    return [ipaddress.IPv4Interface(spec) for spec in specs]


def test_addresses_of_a_home_network() -> None:
    addresses = addresses_to_search(_interfaces("192.168.1.20/24"))
    assert len(addresses) == 253
    assert "192.168.1.20" not in addresses
    assert addresses[0] == "192.168.1.1"
    assert addresses[-1] == "192.168.1.254"


def test_large_networks_are_narrowed_to_our_slash_24() -> None:
    addresses = addresses_to_search(_interfaces("10.20.30.40/8"))
    assert len(addresses) == 253
    assert all(address.startswith("10.20.30.") for address in addresses)


def test_public_loopback_and_link_local_addresses_are_skipped() -> None:
    assert addresses_to_search(_interfaces("8.8.8.8/24", "127.0.0.1/8", "169.254.3.4/16")) == []


def test_small_networks_and_duplicates() -> None:
    addresses = addresses_to_search(_interfaces("172.17.0.2/30", "172.17.0.2/30"))
    assert addresses == ["172.17.0.1"]


def test_address_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(search_module, "MAX_ADDRESSES", 10)
    assert len(addresses_to_search(_interfaces("192.168.1.20/24", "10.0.0.5/24"))) == 10


@pytest.fixture
async def devices() -> AsyncIterator[dict[str, FakeLanDevice]]:
    """Three devices on one port at three loopback addresses; only one is ours."""
    fakes = {
        "127.0.0.2": FakeLanDevice("bfotherplug000000000a", OTHER_KEY, "3.3", {"1": True}),
        "127.0.0.3": FakeLanDevice(DEVICE_ID, LOCAL_KEY, "3.4", {"1": False, "2": 12}),
        "127.0.0.4": FakeLanDevice("bfotherlight00000000b", OTHER_KEY, "3.5", {"20": True}),
    }
    port = 0
    for host, fake in fakes.items():
        fake.host = host
        port = await fake.start(port)
    yield fakes
    for fake in fakes.values():
        await fake.stop()


async def test_finds_the_device_the_key_belongs_to(devices: dict[str, FakeLanDevice]) -> None:
    port = devices["127.0.0.3"].port
    addresses = ["127.0.0.2", "127.0.0.3", "127.0.0.4", "127.0.0.5"]  # .5: nothing there
    found = await async_find_device(
        DEVICE_ID, LOCAL_KEY, addresses, port=port, timings=FAST, port_timeout=0.5
    )
    assert found is not None
    assert found.host == "127.0.0.3"
    assert found.protocol_version == "3.4"
    assert found.dps == {"1": False, "2": 12}
    # The other devices could not read anything they were sent: the key is not theirs.
    assert devices["127.0.0.2"].received == []
    assert devices["127.0.0.4"].received == []


async def test_wrong_key_finds_nothing(devices: dict[str, FakeLanDevice]) -> None:
    port = devices["127.0.0.3"].port
    found = await async_find_device(
        DEVICE_ID,
        OTHER_KEY,
        ["127.0.0.3"],
        port=port,
        timings=FAST,
        port_timeout=0.5,
    )
    assert found is None


async def test_no_open_port_finds_nothing() -> None:
    found = await async_find_device(
        DEVICE_ID, LOCAL_KEY, ["127.0.0.9"], port=1, timings=FAST, port_timeout=0.5
    )
    assert found is None
