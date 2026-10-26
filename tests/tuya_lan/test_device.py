"""TuyaLanDevice against the simulated device (real tinytuya encryption, localhost)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest

from custom_components.birdie.tuya_lan import (
    DeviceConfig,
    DeviceUnreachableError,
    InvalidLocalKeyError,
    Timings,
    TuyaLanDevice,
)

from .fake_device import FakeTuyaDevice

pytestmark = pytest.mark.usefixtures("socket_enabled")

DEVICE_ID = "bf3a9c71d2e04b58f6qz1x"
LOCAL_KEY = "0123456789abcdef"
WRONG_KEY = "fedcba9876543210"
INITIAL_DPS = {"1": False, "2": 12, "3": "auto"}
FAST = Timings(
    connect_timeout=1.0,
    receive_timeout=0.1,
    heartbeat_interval=0.3,
    status_interval=60.0,
    dead_after=1.5,
    refresh_after_command=60.0,
    reconnect_min=0.1,
    reconnect_max=0.5,
)


async def wait_for(condition: Callable[[], bool], seconds: float = 5.0) -> None:
    async with asyncio.timeout(seconds):
        while not condition():
            await asyncio.sleep(0.02)


@pytest.fixture(params=["3.1", "3.3", "3.4", "3.5"])
async def device(request: pytest.FixtureRequest) -> AsyncIterator[FakeTuyaDevice]:
    fake = FakeTuyaDevice(DEVICE_ID, LOCAL_KEY, request.param, dict(INITIAL_DPS))
    await fake.start()
    yield fake
    await fake.stop()


class Client:
    """A TuyaLanDevice that records callbacks."""

    def __init__(self, fake: FakeTuyaDevice, **config: Any) -> None:
        self.statuses: list[dict[str, Any]] = []
        self.availability: list[tuple[bool, Exception | None]] = []
        options: dict[str, Any] = {"local_key": LOCAL_KEY, "port": fake.port} | config
        self.device = TuyaLanDevice(
            DeviceConfig(DEVICE_ID, "127.0.0.1", **options),
            on_status=self.statuses.append,
            on_availability=lambda up, err: self.availability.append((up, err)),
            timings=FAST,
        )


@pytest.fixture
async def make_client() -> AsyncIterator[Callable[..., Client]]:
    clients: list[Client] = []

    def factory(fake: FakeTuyaDevice, **config: Any) -> Client:
        client = Client(fake, **config)
        clients.append(client)
        return client

    yield factory
    for client in clients:
        await client.device.async_close()


async def test_connect_detects_version_and_reads_status(
    device: FakeTuyaDevice, make_client: Callable[..., Client]
) -> None:
    client = make_client(device)
    assert await client.device.async_connect() == INITIAL_DPS
    assert client.device.protocol_version == device.version
    assert client.device.available
    assert client.availability == [(True, None)]


async def test_configured_version_is_used_directly(
    device: FakeTuyaDevice, make_client: Callable[..., Client]
) -> None:
    client = make_client(device, protocol_version=device.version)
    await client.device.async_connect()
    assert device.connections_total == 1


async def test_commands_and_pushes_update_state(
    device: FakeTuyaDevice, make_client: Callable[..., Client]
) -> None:
    client = make_client(device)
    await client.device.async_connect()
    client.device.start()

    await client.device.async_set_dps({"1": True, 3: "manual2"})
    await wait_for(lambda: client.device.dps["3"] == "manual2")
    assert device.dps == {"1": True, "2": 12, "3": "manual2"}

    await device.push({"2": 35})
    await wait_for(lambda: client.device.dps["2"] == 35)
    assert client.statuses[-1] == {"1": True, "2": 35, "3": "manual2"}


async def test_reconnects_after_device_drops_connection(
    device: FakeTuyaDevice, make_client: Callable[..., Client]
) -> None:
    client = make_client(device)
    await client.device.async_connect()
    client.device.start()
    await wait_for(lambda: device.open_connections == 1)

    await device.drop_connections()
    await wait_for(lambda: device.connections_total >= 2 and device.open_connections == 1)
    await device.push({"2": 99})
    await wait_for(lambda: client.device.dps["2"] == 99)
    assert client.device.available


async def test_unanswered_heartbeats_mark_device_unavailable(
    make_client: Callable[..., Client],
) -> None:
    fake = FakeTuyaDevice(DEVICE_ID, LOCAL_KEY, "3.3", dict(INITIAL_DPS))
    await fake.start()
    try:
        client = make_client(fake)
        await client.device.async_connect()
        client.device.start()
        fake.silent = True
        await wait_for(lambda: not client.device.available)
        assert isinstance(client.availability[-1][1], DeviceUnreachableError)

        fake.silent = False
        await wait_for(lambda: client.device.available)
    finally:
        await fake.stop()


async def test_heartbeats_keep_an_idle_connection(
    device: FakeTuyaDevice, make_client: Callable[..., Client]
) -> None:
    client = make_client(device)
    await client.device.async_connect()
    connections = device.connections_total  # auto-detection probes count too
    client.device.start()
    await asyncio.sleep(FAST.dead_after * 2)
    assert client.device.available
    assert device.connections_total == connections
    assert any(item["cmd"] == 9 for item in device.received)


async def test_full_status_poll_catches_missed_changes(make_client: Callable[..., Client]) -> None:
    fake = FakeTuyaDevice(DEVICE_ID, LOCAL_KEY, "3.3", dict(INITIAL_DPS))
    await fake.start()
    try:
        client = make_client(fake)
        await client.device.async_connect()
        fake.dps["2"] = 77  # changed without a push
        assert (await client.device.async_refresh())["2"] == 77
    finally:
        await fake.stop()


async def test_command_without_push_is_read_back(make_client: Callable[..., Client]) -> None:
    fake = FakeTuyaDevice(DEVICE_ID, LOCAL_KEY, "3.3", dict(INITIAL_DPS), push_on_control=False)
    await fake.start()
    try:
        client = make_client(fake)
        client.device._t = Timings(**{**FAST.__dict__, "refresh_after_command": 0.2})
        await client.device.async_connect()
        client.device.start()
        await client.device.async_set_dps({"1": True})
        await wait_for(lambda: client.device.dps["1"] is True)
    finally:
        await fake.stop()


@pytest.mark.parametrize("version", ["3.3", "3.4", "3.5"])
@pytest.mark.parametrize("configured", ["auto", "same"])
async def test_wrong_local_key(
    version: str, configured: str, make_client: Callable[..., Client]
) -> None:
    fake = FakeTuyaDevice(DEVICE_ID, LOCAL_KEY, version, dict(INITIAL_DPS))
    await fake.start()
    try:
        client = make_client(
            fake,
            local_key=WRONG_KEY,
            protocol_version=version if configured == "same" else "auto",
        )
        with pytest.raises(InvalidLocalKeyError):
            await client.device.async_connect()
        assert not client.device.available
    finally:
        await fake.stop()


async def test_nothing_listening(make_client: Callable[..., Client]) -> None:
    fake = FakeTuyaDevice(DEVICE_ID, LOCAL_KEY, "3.3")
    await fake.start()
    await fake.stop()  # the port is now closed
    client = make_client(fake)
    with pytest.raises(DeviceUnreachableError):
        await client.device.async_connect()


async def test_commands_need_a_connection(make_client: Callable[..., Client]) -> None:
    fake = FakeTuyaDevice(DEVICE_ID, LOCAL_KEY, "3.3")
    client = make_client(fake, port=1)
    with pytest.raises(DeviceUnreachableError):
        await client.device.async_set_dps({"1": True})


async def test_background_loop_connects_and_retries(make_client: Callable[..., Client]) -> None:
    fake = FakeTuyaDevice(DEVICE_ID, LOCAL_KEY, "3.4", dict(INITIAL_DPS))
    await fake.start()
    port = fake.port
    await fake.stop()
    client = make_client(fake, port=port)
    client.device.start()
    await wait_for(lambda: client.device.last_error is not None)
    assert not client.device.available

    await fake.start(port)
    try:
        await wait_for(lambda: client.device.available)
        assert client.device.dps == INITIAL_DPS
    finally:
        await fake.stop()


async def test_new_local_key_reconnects(make_client: Callable[..., Client]) -> None:
    """After re-pairing the key changes; the client picks up the new one."""
    fake = FakeTuyaDevice(DEVICE_ID, LOCAL_KEY, "3.4", dict(INITIAL_DPS))
    await fake.start()
    try:
        client = make_client(fake)
        await client.device.async_connect()
        client.device.start()

        fake.local_key = WRONG_KEY  # device paired again
        await fake.drop_connections()
        await wait_for(lambda: isinstance(client.device.last_error, InvalidLocalKeyError))

        await client.device.async_reconfigure(local_key=WRONG_KEY)
        await wait_for(lambda: client.device.available)
        assert client.device.config.local_key == WRONG_KEY
    finally:
        await fake.stop()


async def test_new_address_reconnects(make_client: Callable[..., Client]) -> None:
    """The device moved (new DHCP lease): the client follows the new address."""
    old = FakeTuyaDevice(DEVICE_ID, LOCAL_KEY, "3.3", {"1": False})
    new = FakeTuyaDevice(DEVICE_ID, LOCAL_KEY, "3.3", {"1": True})
    await old.start()
    await new.start()
    try:
        client = make_client(old)
        await client.device.async_connect()
        client.device.start()
        await old.stop()

        await client.device.async_reconfigure(host="127.0.0.1", port=new.port)
        await wait_for(lambda: client.device.dps["1"] is True)
        assert client.device.available
    finally:
        await new.stop()


async def test_close_is_quick_and_idempotent(
    device: FakeTuyaDevice, make_client: Callable[..., Client]
) -> None:
    client = make_client(device)
    await client.device.async_connect()
    client.device.start()
    async with asyncio.timeout(2):
        await client.device.async_close()
        await client.device.async_close()
    await wait_for(lambda: device.open_connections == 0)


async def test_local_key_not_logged(
    make_client: Callable[..., Client], caplog: pytest.LogCaptureFixture
) -> None:
    """tinytuya logs the key at debug level; the redaction filter removes it."""
    fake = FakeTuyaDevice(DEVICE_ID, LOCAL_KEY, "3.3", dict(INITIAL_DPS))
    await fake.start()
    caplog.set_level(logging.DEBUG)
    try:
        client = make_client(fake, local_key=WRONG_KEY, protocol_version="3.3")
        with pytest.raises(InvalidLocalKeyError):
            await client.device.async_connect()
        assert "local key" in caplog.text  # tinytuya's message is there...
        assert WRONG_KEY not in caplog.text  # ...without the key
        assert WRONG_KEY not in repr(client.device)
    finally:
        await fake.stop()


def test_config_validation() -> None:
    with pytest.raises(ValueError):
        DeviceConfig(DEVICE_ID, "", LOCAL_KEY)
    with pytest.raises(ValueError):
        DeviceConfig(DEVICE_ID, "10.0.0.2", LOCAL_KEY, protocol_version="3.9")
    assert LOCAL_KEY not in repr(DeviceConfig(DEVICE_ID, "10.0.0.2", LOCAL_KEY))
