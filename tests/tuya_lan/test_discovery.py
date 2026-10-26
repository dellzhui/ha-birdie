"""Discovery of devices from their UDP broadcasts (synthetic packets, localhost)."""

from __future__ import annotations

import asyncio
import socket

import pytest

from custom_components.birdie.tuya_lan import DiscoveryListener, async_discover
from custom_components.birdie.tuya_lan.discovery import _request_payload, parse_broadcast

from .fake_device import FakeTuyaDevice

DEVICE_ID = "bf3a9c71d2e04b58f6qz1x"
KEY = "0123456789abcdef"


@pytest.mark.parametrize("version", ["3.1", "3.3", "3.4", "3.5"])
def test_parse_broadcast(version: str) -> None:
    packet = FakeTuyaDevice(DEVICE_ID, KEY, version).broadcast_packet("192.168.1.40")
    device = parse_broadcast(packet, "192.168.1.40")
    assert device is not None
    assert device.device_id == DEVICE_ID
    assert device.host == "192.168.1.40"
    assert device.protocol_version == version
    assert device.product_key == "a2zf7q0zxcutn5dy"


@pytest.mark.parametrize("packet", [b"", b"garbage", b"\x00\x00U\xaa" + b"\x00" * 40])
def test_parse_ignores_foreign_packets(packet: bytes) -> None:
    assert parse_broadcast(packet, "10.0.0.9") is None


def test_parse_ignores_app_requests() -> None:
    assert parse_broadcast(_request_payload("10.0.0.2"), "10.0.0.2") is None


def _send(port: int, data: bytes) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.sendto(data, ("127.0.0.1", port))


@pytest.mark.usefixtures("socket_enabled")
async def test_listener_reports_devices() -> None:
    listener = DiscoveryListener(ports=(0, 0), bind_host="127.0.0.1", request_port=None)
    seen = []
    remove = listener.add_callback(seen.append)
    await listener.async_start()
    try:
        first, second = listener.bound_ports
        _send(first, FakeTuyaDevice(DEVICE_ID, KEY, "3.1").broadcast_packet("10.0.0.5"))
        _send(second, b"not a tuya packet")
        _send(second, FakeTuyaDevice("bfother00000000000000", KEY, "3.3").broadcast_packet())
        async with asyncio.timeout(2):
            while len(listener.devices) < 2:
                await asyncio.sleep(0.01)
    finally:
        remove()
        await listener.async_stop()
    assert listener.devices[DEVICE_ID].host == "10.0.0.5"
    assert [device.device_id for device in seen] == [DEVICE_ID, "bfother00000000000000"]
    assert not listener.listening


@pytest.mark.usefixtures("socket_enabled")
async def test_discover_returns_when_device_is_seen() -> None:
    listener = DiscoveryListener(ports=(0,), bind_host="127.0.0.1", request_port=None)
    await listener.async_start()
    try:
        (port,) = listener.bound_ports
        loop = asyncio.get_running_loop()
        loop.call_later(
            0.05, _send, port, FakeTuyaDevice(DEVICE_ID, KEY, "3.5").broadcast_packet("10.0.0.7")
        )
        async with asyncio.timeout(1):
            found = await async_discover(30, device_id=DEVICE_ID, listener=listener)
    finally:
        await listener.async_stop()
    assert found[DEVICE_ID].protocol_version == "3.5"


@pytest.mark.usefixtures("socket_enabled")
async def test_discover_times_out_and_cleans_up() -> None:
    found = await async_discover(
        0.1, device_id=DEVICE_ID, ports=(0,), bind_host="127.0.0.1", request_port=None
    )
    assert found == {}


@pytest.mark.usefixtures("socket_enabled")
async def test_request_broadcast_is_sent() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver:
        receiver.bind(("127.0.0.1", 0))
        receiver.setblocking(False)
        port = receiver.getsockname()[1]
        listener = DiscoveryListener(
            ports=(0,), bind_host="127.0.0.1", request_port=port, request_host="127.0.0.1"
        )
        await listener.async_start()
        try:
            loop = asyncio.get_running_loop()
            async with asyncio.timeout(2):
                data = await loop.sock_recv(receiver, 1024)
        finally:
            await listener.async_stop()
    assert data.startswith(b"\x00\x00\x66\x99")
