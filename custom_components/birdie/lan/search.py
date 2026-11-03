"""Find a device by its device ID and local key when it does not announce itself.

Some routers drop the UDP broadcasts devices announce themselves with, while
TCP connections to the device still work. The device can then only be found
by looking for it: list the addresses on the local network that accept
connections on the device port, and try the device ID and local key on each.
Only the device the key belongs to accepts it. Other devices refuse the
handshake, so they never receive a command.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from .device import DEFAULT_PORT, PROTOCOL_AUTO, DeviceConfig, LanDevice, Timings
from .errors import LanError

_LOGGER = logging.getLogger(__name__)

# Never look at more addresses than this, whatever the networks.
MAX_ADDRESSES = 1024
# Networks larger than this are narrowed to the /24 around our own address.
SMALLEST_PREFIX = 24

PORT_TIMEOUT = 1.0
PORT_CONCURRENCY = 64
# Devices tried at once; each try runs in its own worker thread.
TRY_CONCURRENCY = 8
TRY_TIMINGS = Timings(connect_timeout=3.0)


@dataclass(frozen=True)
class FoundDevice:
    """Where the device answered, and what it reported."""

    host: str
    protocol_version: str
    dps: dict[str, Any] = field(default_factory=dict)


def addresses_to_search(interfaces: Iterable[ipaddress.IPv4Interface]) -> list[str]:
    """Addresses around this machine's private IPv4 addresses, without its own.

    Each interface contributes its network, narrowed to the /24 around its
    address when the network is larger. Public, loopback and link-local
    addresses are skipped.
    """
    interfaces = list(interfaces)
    own = {str(interface.ip) for interface in interfaces}
    addresses: list[str] = []
    seen: set[str] = set()
    for interface in interfaces:
        ip = interface.ip
        if not ip.is_private or ip.is_loopback or ip.is_link_local:
            continue
        network = interface.network
        if network.prefixlen < SMALLEST_PREFIX:
            network = ipaddress.ip_network(f"{ip}/{SMALLEST_PREFIX}", strict=False)
        for host in network.hosts():
            address = str(host)
            if address in own or address in seen:
                continue
            seen.add(address)
            addresses.append(address)
            if len(addresses) >= MAX_ADDRESSES:
                return addresses
    return addresses


async def async_open_ports(
    addresses: Iterable[str],
    port: int = DEFAULT_PORT,
    *,
    connect_timeout: float = PORT_TIMEOUT,
    concurrency: int = PORT_CONCURRENCY,
) -> list[str]:
    """Addresses that accept a TCP connection on ``port``. Nothing is sent."""
    limit = asyncio.Semaphore(concurrency)

    async def accepts(address: str) -> bool:
        async with limit:
            try:
                _, writer = await asyncio.wait_for(
                    asyncio.open_connection(address, port), connect_timeout
                )
            except (OSError, TimeoutError):
                return False
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()
            return True

    addresses = list(addresses)
    results = await asyncio.gather(*(accepts(address) for address in addresses))
    return [address for address, ok in zip(addresses, results, strict=True) if ok]


async def async_find_device(
    device_id: str,
    local_key: str,
    addresses: Sequence[str],
    *,
    protocol_version: str = PROTOCOL_AUTO,
    port: int = DEFAULT_PORT,
    port_timeout: float = PORT_TIMEOUT,
    timings: Timings = TRY_TIMINGS,
    concurrency: int = TRY_CONCURRENCY,
) -> FoundDevice | None:
    """Look for the device at ``addresses``; None if no address accepts the key."""
    candidates = await async_open_ports(addresses, port, connect_timeout=port_timeout)
    _LOGGER.debug(
        "Looking for %s: %d of %d addresses accept connections",
        device_id,
        len(candidates),
        len(addresses),
    )
    if not candidates:
        return None
    limit = asyncio.Semaphore(concurrency)

    async def attempt(host: str) -> FoundDevice | None:
        async with limit:
            device = LanDevice(
                DeviceConfig(device_id, host, local_key, protocol_version, port),
                timings=timings,
            )
            try:
                dps = await device.async_connect()
            except LanError:
                return None
            finally:
                await device.async_close()
            return FoundDevice(host, device.protocol_version or protocol_version, dps)

    tasks = [asyncio.ensure_future(attempt(host)) for host in candidates]
    try:
        for next_done in asyncio.as_completed(tasks):
            found = await next_done
            if found is not None:
                _LOGGER.debug("Found %s at %s", device_id, found.host)
                return found
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    return None
