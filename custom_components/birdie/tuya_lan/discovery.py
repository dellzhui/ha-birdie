"""Find Tuya devices on the local network from their UDP broadcasts.

Tuya Wi-Fi devices announce themselves every few seconds: protocol 3.1 in
plain text on UDP port 6666, 3.3 and later encrypted with a well-known key on
port 6667. Some 3.5 devices only answer a request broadcast to port 7000;
every Tuya device on the network answers such a request, so it is sent only
while actively searching (``async_discover``), never by a listener on its own.
Decryption is tinytuya's. Sockets are opened with SO_REUSEADDR/SO_REUSEPORT so
other Tuya integrations can listen on the same ports.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import socket
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

import tinytuya

_LOGGER = logging.getLogger(__name__)

BROADCAST_PORTS: tuple[int, ...] = (6666, 6667)
REQUEST_PORT = 7000
REQUEST_INTERVAL = 6.0


@dataclass(frozen=True)
class DiscoveredDevice:
    """One device announcement."""

    device_id: str
    host: str
    protocol_version: str | None
    product_key: str | None
    seen_at: float


def parse_broadcast(data: bytes, source_ip: str) -> DiscoveredDevice | None:
    """Decode one broadcast datagram; None if it is not a device announcement."""
    try:
        payload = json.loads(tinytuya.decrypt_udp(data))
    except Exception:  # foreign or corrupt datagrams are expected
        return None
    if not isinstance(payload, dict) or payload.get("from") == "app":
        return None
    device_id = payload.get("gwId") or payload.get("id")
    if not isinstance(device_id, str) or not device_id:
        return None
    host = payload.get("ip") if isinstance(payload.get("ip"), str) else source_ip
    version = payload.get("version")
    product_key = payload.get("productKey")
    return DiscoveredDevice(
        device_id=device_id,
        host=host or source_ip,
        protocol_version=str(version) if version else None,
        product_key=str(product_key) if product_key else None,
        seen_at=time.monotonic(),
    )


def _request_payload(source_ip: str | None) -> bytes:
    body: dict[str, str] = {"from": "app"}
    if source_ip:
        body["ip"] = source_ip
    message = tinytuya.TuyaMessage(
        0,
        tinytuya.REQ_DEVINFO,
        None,
        json.dumps(body).encode(),
        0,
        True,
        tinytuya.PREFIX_6699_VALUE,
        True,
    )
    return tinytuya.pack_message(message, hmac_key=tinytuya.udpkey)


class _Protocol(asyncio.DatagramProtocol):
    def __init__(self, listener: DiscoveryListener) -> None:
        self._listener = listener

    def datagram_received(self, data: bytes, addr: tuple[str | Any, int]) -> None:
        device = parse_broadcast(data, str(addr[0]))
        if device is not None:
            self._listener.handle(device)


class DiscoveryListener:
    """Listen for device broadcasts until stopped.

    ``devices`` holds the latest announcement per device id. Callbacks run in
    the event loop.
    """

    def __init__(
        self,
        *,
        ports: Iterable[int] = BROADCAST_PORTS,
        bind_host: str = "",
        request_port: int | None = REQUEST_PORT,
        request_host: str = "255.255.255.255",
        request_source_ip: str | None = None,
    ) -> None:
        self._ports = tuple(ports)
        self._bind_host = bind_host
        self._request_port = request_port
        self._request_host = request_host
        self._request_source_ip = request_source_ip
        self._transports: list[asyncio.DatagramTransport] = []
        self._callbacks: list[Callable[[DiscoveredDevice], None]] = []
        self.devices: dict[str, DiscoveredDevice] = {}

    @property
    def listening(self) -> bool:
        return bool(self._transports)

    @property
    def bound_ports(self) -> list[int]:
        return [transport.get_extra_info("sockname")[1] for transport in self._transports]

    def add_callback(self, callback: Callable[[DiscoveredDevice], None]) -> Callable[[], None]:
        """Call ``callback`` for every announcement; returns a remover."""
        self._callbacks.append(callback)

        def remove() -> None:
            with contextlib.suppress(ValueError):
                self._callbacks.remove(callback)

        return remove

    def handle(self, device: DiscoveredDevice) -> None:
        self.devices[device.device_id] = device
        for callback in list(self._callbacks):
            try:
                callback(device)
            except Exception:
                _LOGGER.exception("Error in discovery callback")

    async def async_start(self) -> None:
        """Bind the broadcast ports; ports already taken without reuse are skipped."""
        loop = asyncio.get_running_loop()
        for port in self._ports:
            try:
                sock = _udp_socket(self._bind_host, port)
            except OSError as err:
                _LOGGER.warning("Cannot listen for Tuya broadcasts on UDP %s: %s", port, err)
                continue
            transport, _ = await loop.create_datagram_endpoint(lambda: _Protocol(self), sock=sock)
            self._transports.append(transport)

    async def async_stop(self) -> None:
        for transport in self._transports:
            transport.close()
        self._transports.clear()

    async def async_request(self) -> None:
        """Broadcast one discovery request; devices answer with an announcement."""
        if self._request_port is None or not self._transports:
            return
        payload = _request_payload(self._request_source_ip)
        loop = asyncio.get_running_loop()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.setblocking(False)
            try:
                await loop.sock_sendto(sock, payload, (self._request_host, self._request_port))
            except OSError as err:
                _LOGGER.debug("Discovery request broadcast failed: %s", err)


def _udp_socket(host: str, port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            with contextlib.suppress(OSError):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setblocking(False)
        sock.bind((host, port))
    except OSError:
        sock.close()
        raise
    return sock


async def async_discover(
    seconds: float,
    *,
    device_id: str | None = None,
    listener: DiscoveryListener | None = None,
    request: bool = True,
    **listener_kwargs: Any,
) -> dict[str, DiscoveredDevice]:
    """Collect announcements for ``seconds``.

    With ``device_id``, return as soon as that device is seen. An already
    running ``listener`` is reused (its cached announcements included). With
    ``request``, a discovery request is broadcast at the start and every
    ``REQUEST_INTERVAL`` seconds of the search; otherwise this only listens.
    """
    own = listener is None
    active = listener or DiscoveryListener(**listener_kwargs)
    found: dict[str, DiscoveredDevice] = dict(active.devices)
    done = asyncio.Event()

    def on_device(device: DiscoveredDevice) -> None:
        found[device.device_id] = device
        if device_id is not None and device.device_id == device_id:
            done.set()

    remove = active.add_callback(on_device)
    try:
        if own:
            await active.async_start()
        if device_id is None or device_id not in found:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + seconds
            while True:
                if request:
                    await active.async_request()
                remaining = max(0.0, deadline - loop.time())
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(done.wait(), min(REQUEST_INTERVAL, remaining))
                if done.is_set() or loop.time() >= deadline:
                    break
    finally:
        remove()
        if own:
            await active.async_stop()
    return found
