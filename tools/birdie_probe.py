#!/usr/bin/env python3
"""Standalone BLE probe for Birdie devices."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from bleak import BleakClient, BleakScanner
from bleak.exc import BleakError

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from custom_components.birdie.ble import (  # noqa: E402
    bytes_to_hex,
    is_birdie_advertisement,
    parse_known_characteristic,
)
from custom_components.birdie.const import (  # noqa: E402
    ENVIRONMENTAL_SERVICE_UUID,
    KNOWN_CHARACTERISTICS,
    NOTIFY_CHARACTERISTIC_UUIDS,
    READ_CHARACTERISTIC_UUIDS,
)

LOGGER = logging.getLogger("birdie_probe")


def _device_name(device: Any, advertisement: Any | None = None) -> str:
    """Return the best available BLE device name."""
    return (
        getattr(device, "name", None)
        or getattr(advertisement, "local_name", None)
        or "(unknown)"
    )


def _service_uuids(advertisement: Any | None) -> list[str]:
    """Return advertisement service UUIDs."""
    return list(getattr(advertisement, "service_uuids", None) or [])


def _format_properties(properties: Iterable[str]) -> str:
    """Format characteristic properties."""
    values = list(properties)
    return ", ".join(values) if values else "-"


def _parse_or_error(uuid: str, data: bytes | bytearray | memoryview) -> str:
    """Parse a known characteristic and return errors as text."""
    try:
        return parse_known_characteristic(uuid, data)
    except Exception as err:  # noqa: BLE001 - probe should keep running.
        return f"parse error: {err}"


async def scan_devices(timeout: float) -> list[tuple[Any, Any | None, bool]]:
    """Scan nearby BLE devices."""
    LOGGER.info("Scanning for BLE devices for %.1f seconds...", timeout)
    try:
        discovered = await BleakScanner.discover(timeout=timeout, return_adv=True)
        devices = [
            (device, advertisement, is_birdie_advertisement(_device_name(device, advertisement), _service_uuids(advertisement)))
            for device, advertisement in discovered.values()
        ]
    except TypeError:
        raw_devices = await BleakScanner.discover(timeout=timeout)
        devices = [
            (device, None, is_birdie_advertisement(_device_name(device), []))
            for device in raw_devices
        ]

    devices.sort(key=lambda item: (not item[2], _device_name(item[0], item[1]), item[0].address))
    if not devices:
        LOGGER.info("No BLE devices found.")
        return devices

    LOGGER.info("Found %d BLE device(s):", len(devices))
    for device, advertisement, is_candidate in devices:
        marker = "*" if is_candidate else " "
        uuids = ", ".join(_service_uuids(advertisement)) or "-"
        LOGGER.info(
            "%s %s  %s  RSSI=%s  services=%s",
            marker,
            device.address,
            _device_name(device, advertisement),
            getattr(advertisement, "rssi", getattr(device, "rssi", "?")),
            uuids,
        )

    candidates = [item for item in devices if item[2]]
    if candidates:
        LOGGER.info("Birdie candidate(s): %s", ", ".join(item[0].address for item in candidates))
    else:
        LOGGER.info(
            "No Birdie candidates found. Matching prefers Environmental Service %s, then names starting with Birdie.",
            ENVIRONMENTAL_SERVICE_UUID,
        )
    return devices


async def find_device(address: str, timeout: float) -> Any:
    """Find a BLE device by address, returning the address string as a fallback."""
    LOGGER.info("Looking for %s for %.1f seconds...", address, timeout)
    try:
        device = await BleakScanner.find_device_by_address(address, timeout=timeout)
    except (BleakError, OSError) as err:
        LOGGER.warning("Address lookup failed: %s", err)
        return address
    if device is None:
        LOGGER.warning("Device was not seen during scan; connecting by address anyway.")
        return address
    LOGGER.info("Found %s (%s)", device.address, _device_name(device))
    return device


async def _get_services(client: BleakClient) -> Any:
    """Return GATT services across Bleak versions."""
    services = getattr(client, "services", None)
    if services:
        return services
    get_services = getattr(client, "get_services", None)
    if get_services is None:
        raise RuntimeError("Bleak client did not expose GATT services")
    return await get_services()


async def dump_services(client: BleakClient) -> Any:
    """Dump GATT services and characteristics."""
    services = await _get_services(client)
    LOGGER.info("GATT services:")
    for service in services:
        LOGGER.info("Service %s %s", service.uuid, getattr(service, "description", ""))
        for characteristic in service.characteristics:
            name = KNOWN_CHARACTERISTICS.get(characteristic.uuid.lower(), "")
            LOGGER.info(
                "  Characteristic %s %s properties=[%s]",
                characteristic.uuid,
                f"({name})" if name else "",
                _format_properties(characteristic.properties),
            )
    return services


async def read_known_characteristics(client: BleakClient, services: Any) -> None:
    """Read all known readable Birdie characteristics."""
    LOGGER.info("Reading known characteristics...")
    for uuid in READ_CHARACTERISTIC_UUIDS:
        characteristic = services.get_characteristic(uuid)
        name = KNOWN_CHARACTERISTICS.get(uuid, uuid)
        if characteristic is None:
            LOGGER.info("  %-24s missing", name)
            continue
        if "read" not in characteristic.properties:
            LOGGER.info("  %-24s not readable", name)
            continue
        try:
            data = await asyncio.wait_for(client.read_gatt_char(characteristic), timeout=10)
        except (BleakError, asyncio.TimeoutError, OSError) as err:
            LOGGER.warning("  %-24s read failed: %s", name, err)
            continue
        LOGGER.info(
            "  %-24s raw=[%s] parsed=%s",
            name,
            bytes_to_hex(data),
            _parse_or_error(uuid, data),
        )


async def subscribe_notifications(
    client: BleakClient, services: Any, notify_timeout: float
) -> None:
    """Subscribe to Birdie notification characteristics."""
    active: list[Any] = []

    async def stop_notifications() -> None:
        for characteristic in active:
            try:
                await client.stop_notify(characteristic)
            except (BleakError, OSError) as err:
                LOGGER.debug("Failed to stop notify for %s: %s", characteristic.uuid, err)

    LOGGER.info("Subscribing to notifications for %.1f seconds...", notify_timeout)
    for uuid in NOTIFY_CHARACTERISTIC_UUIDS:
        characteristic = services.get_characteristic(uuid)
        name = KNOWN_CHARACTERISTICS.get(uuid, uuid)
        if characteristic is None:
            LOGGER.info("  %-24s missing", name)
            continue
        if "notify" not in characteristic.properties:
            LOGGER.info("  %-24s not notifiable", name)
            continue

        def callback(sender: Any, data: bytearray, char_uuid: str = uuid, char_name: str = name) -> None:
            LOGGER.info(
                "notify %-24s raw=[%s] parsed=%s",
                char_name,
                bytes_to_hex(data),
                _parse_or_error(char_uuid, data),
            )

        try:
            await asyncio.wait_for(client.start_notify(characteristic, callback), timeout=10)
        except (BleakError, asyncio.TimeoutError, OSError) as err:
            LOGGER.warning("  %-24s subscribe failed: %s", name, err)
            continue
        active.append(characteristic)
        LOGGER.info("  %-24s subscribed", name)

    if not active:
        LOGGER.info("No notification subscriptions are active.")
        return

    try:
        await asyncio.sleep(notify_timeout)
    finally:
        await stop_notifications()


async def probe_address(args: argparse.Namespace) -> None:
    """Connect to a Birdie device and inspect GATT."""
    target = await find_device(args.address, args.scan_timeout)
    LOGGER.info("Connecting to %s...", args.address)
    try:
        async with BleakClient(target, timeout=args.connect_timeout) as client:
            if not client.is_connected:
                raise RuntimeError("client did not report connected state")
            LOGGER.info("Connected.")
            services = await dump_services(client)
            await read_known_characteristics(client, services)
            if args.notify:
                await subscribe_notifications(client, services, args.notify_timeout)
    except (BleakError, asyncio.TimeoutError, OSError, RuntimeError) as err:
        LOGGER.error("Probe failed: %s", err)
        raise SystemExit(2) from err
    finally:
        LOGGER.info("Done.")


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="Probe Birdie / Birdie Pro BLE devices.")
    parser.add_argument("--address", help="BLE MAC/address to connect to.")
    parser.add_argument("--scan", action="store_true", help="Scan for nearby BLE devices.")
    parser.add_argument(
        "--notify",
        action="store_true",
        help="Subscribe to CO2, temperature, humidity, Birdie state, and battery notifications.",
    )
    parser.add_argument("--scan-timeout", type=float, default=10.0, help="BLE scan timeout in seconds.")
    parser.add_argument("--connect-timeout", type=float, default=20.0, help="BLE connect timeout in seconds.")
    parser.add_argument("--notify-timeout", type=float, default=60.0, help="Notification listen time in seconds.")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        help="Logging verbosity.",
    )
    args = parser.parse_args()
    if not args.scan and not args.address:
        args.scan = True
    return args


async def async_main() -> None:
    """Run the probe."""
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)-7s %(message)s",
    )

    if args.scan:
        await scan_devices(args.scan_timeout)
    if args.address:
        await probe_address(args)


def main() -> None:
    """Entrypoint."""
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        LOGGER.info("Interrupted.")


if __name__ == "__main__":
    main()
