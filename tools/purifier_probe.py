"""Probe a Birdie air purifier on the local network (run inside the customer's LAN).

Commands:

  discover   List Tuya devices announcing themselves on the network.
  read       Connect, print the raw DPs and the decoded state, watch pushed
             changes for a while.
  test       Like read, then send a fixed sequence of commands and record
             how the device answers. Changes the purifier's state; restores
             power and mode at the end. Requires --yes.

The local key is read from the BIRDIE_LOCAL_KEY environment variable or
asked for interactively; it is never printed or written to the report.
Reports are JSON and meant to be shared with the developers: the device IP
is redacted unless --keep-ip is given.

Run from the repository root:

  python -m tools.purifier_probe discover
  python -m tools.purifier_probe read --device-id bf... --report probe.json
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import logging
import os
import sys
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from custom_components.birdie.purifier.model import (
    DP_MODE,
    DP_SWITCH,
    PRODUCT_KEYS,
    PurifierState,
    reset_filter_dps,
)
from custom_components.birdie.tuya_lan import (
    PROTOCOL_AUTO,
    PROTOCOL_VERSIONS,
    DeviceConfig,
    TuyaLanDevice,
    TuyaLanError,
    async_discover,
)

KEY_ENV = "BIRDIE_LOCAL_KEY"
COMMAND_SEQUENCE: list[tuple[str, dict[str, Any]]] = [
    ("power off", {DP_SWITCH: False}),
    ("power on", {DP_SWITCH: True}),
    ("mode auto", {DP_MODE: "auto"}),
    ("mode sleep", {DP_MODE: "sleep"}),
    ("speed 1", {DP_MODE: "manual1"}),
    ("speed 2", {DP_MODE: "manual2"}),
    ("speed 3", {DP_MODE: "manual3"}),
    ("speed 4", {DP_MODE: "manual4"}),
]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _decoded(dps: dict[str, Any]) -> dict[str, Any]:
    state = asdict(PurifierState.from_dps(dps))
    state.pop("dps")
    return state


def _types(dps: dict[str, Any]) -> dict[str, str]:
    return {key: type(value).__name__ for key, value in dps.items()}


class Recorder:
    """Collects everything the device reports, with timestamps."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self._last: dict[str, Any] = {}

    def status(self, dps: dict[str, Any]) -> None:
        changed = {key: value for key, value in dps.items() if self._last.get(key) != value}
        self._last = dict(dps)
        if changed:
            self.events.append({"at": _now(), "type": "dps", "changed": changed})
            print(f"{_now()}  dps changed: {json.dumps(changed)}")

    def availability(self, available: bool, error: TuyaLanError | None) -> None:
        self.events.append(
            {"at": _now(), "type": "availability", "available": available, "error": repr(error)}
        )
        print(f"{_now()}  {'connected' if available else f'disconnected: {error!r}'}")


async def _discover(args: argparse.Namespace) -> int:
    print(f"Listening for {args.seconds:.0f} s on UDP 6666/6667 ...")
    found = await async_discover(args.seconds)
    if not found:
        print("No Tuya devices announced themselves. Is this host on the same network?")
        return 1
    for device in found.values():
        birdie = "Birdie purifier" if device.product_key in PRODUCT_KEYS else ""
        print(
            f"{device.device_id}  {device.host:<15}  v{device.protocol_version or '?'}  "
            f"product {device.product_key or '?'}  {birdie}"
        )
    return 0


async def _resolve_host(args: argparse.Namespace, report: dict[str, Any]) -> str | None:
    if args.host:
        return str(args.host)
    print(f"Searching for {args.device_id} ...")
    found = (await async_discover(args.seconds, device_id=args.device_id)).get(args.device_id)
    if found is None:
        print("Device not found; pass --host.")
        return None
    report["discovery"] = {
        "protocol_version": found.protocol_version,
        "product_key": found.product_key,
    }
    return found.host


def _local_key() -> str:
    key = os.environ.get(KEY_ENV) or getpass.getpass("Local key (input hidden): ")
    return key.strip()


async def _probe(args: argparse.Namespace) -> int:
    report: dict[str, Any] = {
        "tool": "purifier_probe",
        "started_at": _now(),
        "device_id": args.device_id,
        "configured_protocol": args.protocol,
    }
    host = await _resolve_host(args, report)
    if host is None:
        return 1
    report["host"] = host if args.keep_ip else "<redacted>"
    recorder = Recorder()
    device = TuyaLanDevice(
        DeviceConfig(args.device_id, host, _local_key(), args.protocol, args.port),
        on_status=recorder.status,
        on_availability=recorder.availability,
    )
    try:
        try:
            started = time.monotonic()
            dps = await device.async_connect()
        except TuyaLanError as err:
            print(f"Connection failed: {err!r}")
            report["connect_error"] = repr(err)
            return 2
        report["connect_seconds"] = round(time.monotonic() - started, 2)
        report["protocol_version"] = device.protocol_version
        report["initial_dps"] = dps
        report["initial_dp_types"] = _types(dps)
        report["initial_decoded"] = _decoded(dps)
        print(f"Connected with protocol {device.protocol_version}")
        print("Raw DPs:", json.dumps(dps, indent=2))
        print("Decoded:", json.dumps(report["initial_decoded"], indent=2))

        device.start()
        print(f"Watching pushed changes for {args.watch:.0f} s (change something in the app) ...")
        await asyncio.sleep(args.watch)

        if args.command == "test":
            report["commands"] = await _run_commands(device, args, dps)
        report["final_dps"] = device.dps
    finally:
        await device.async_close()
        report["events"] = recorder.events
        report["finished_at"] = _now()
        if args.report:
            await asyncio.to_thread(Path(args.report).write_text, json.dumps(report, indent=2))
            print(f"Report written to {args.report}")
    return 0


async def _run_commands(
    device: TuyaLanDevice, args: argparse.Namespace, initial: dict[str, Any]
) -> list[dict[str, Any]]:
    sequence = list(COMMAND_SEQUENCE)
    if args.include_filter_reset:
        sequence.append(("reset filter (resets the filter counter)", reset_filter_dps()))
    restore = {key: initial[key] for key in (DP_SWITCH, DP_MODE) if key in initial}
    sequence.append(("restore power and mode", restore))
    results: list[dict[str, Any]] = []
    for label, dps in sequence:
        if not dps:
            continue
        before = device.dps
        sent_at = _now()
        print(f"{sent_at}  sending {label}: {json.dumps(dps)}")
        entry: dict[str, Any] = {"label": label, "sent": dps, "sent_at": sent_at}
        try:
            await device.async_set_dps(dps)
        except TuyaLanError as err:
            entry["error"] = repr(err)
            results.append(entry)
            print(f"  failed: {err!r}")
            continue
        await asyncio.sleep(args.pause)
        after = device.dps
        entry["changed"] = {k: v for k, v in after.items() if before.get(k) != v}
        entry["confirmed"] = all(after.get(k) == v for k, v in dps.items())
        results.append(entry)
        print(f"  confirmed: {entry['confirmed']}  changed: {json.dumps(entry['changed'])}")
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    discover = sub.add_parser("discover", help="list devices announcing themselves")
    discover.add_argument("--seconds", type=float, default=12)
    for name in ("read", "test"):
        probe = sub.add_parser(name)
        probe.add_argument("--device-id", required=True)
        probe.add_argument("--host", help="IP address; searched on the network if omitted")
        probe.add_argument(
            "--protocol", default=PROTOCOL_AUTO, choices=[PROTOCOL_AUTO, *PROTOCOL_VERSIONS]
        )
        probe.add_argument("--port", type=int, default=6668, help=argparse.SUPPRESS)
        probe.add_argument("--seconds", type=float, default=12, help="search time")
        probe.add_argument("--watch", type=float, default=30, help="seconds to watch pushes")
        probe.add_argument("--report", help="write a JSON report to this file")
        probe.add_argument("--keep-ip", action="store_true", help="keep the IP in the report")
        probe.add_argument("--debug", action="store_true", help="tinytuya debug log (key redacted)")
        if name == "test":
            probe.add_argument("--pause", type=float, default=4, help="seconds after each command")
            probe.add_argument("--include-filter-reset", action="store_true")
            probe.add_argument("--yes", action="store_true", help="confirm changing device state")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if getattr(args, "debug", False) else logging.WARNING)
    if args.command == "discover":
        return asyncio.run(_discover(args))
    if args.command == "test" and not args.yes:
        print("The test changes power and mode of the purifier; rerun with --yes.")
        return 1
    return asyncio.run(_probe(args))


if __name__ == "__main__":
    sys.exit(main())
