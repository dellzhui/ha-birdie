"""Developer tool for probing a Tuya Cloud project from the command line.

Credentials are read from environment variables only (see ``.env.example``):
TUYA_ACCESS_ID, TUYA_ACCESS_SECRET, TUYA_REGION or TUYA_ENDPOINT, and
optionally TUYA_DEVICE_ID. Tokens and secrets are never printed; sensitive
device fields (local_key, ip, coordinates) are redacted from output.

Examples:
    birdie-tuya token
    birdie-tuya devices
    birdie-tuya device <device_id>
    birdie-tuya status <device_id>
    birdie-tuya spec <device_id>
    birdie-tuya send <device_id> mode '"auto"' --yes
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from typing import Any

import aiohttp

from .tuya import TuyaCloudClient, TuyaCredentials, TuyaError, resolve_endpoint

REDACTED_KEYS = frozenset(
    {"local_key", "localKey", "ip", "lat", "lon", "access_token", "refresh_token"}
)


def redact(value: Any) -> Any:
    """Recursively replace sensitive values with a placeholder."""
    if isinstance(value, dict):
        return {k: "**REDACTED**" if k in REDACTED_KEYS else redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def credentials_from_env() -> TuyaCredentials:
    access_id = os.environ.get("TUYA_ACCESS_ID", "").strip()
    secret = os.environ.get("TUYA_ACCESS_SECRET", "").strip()
    endpoint = os.environ.get("TUYA_ENDPOINT") or os.environ.get("TUYA_REGION", "")
    missing = [
        name
        for name, value in (
            ("TUYA_ACCESS_ID", access_id),
            ("TUYA_ACCESS_SECRET", secret),
            ("TUYA_REGION or TUYA_ENDPOINT", endpoint),
        )
        if not value
    ]
    if missing:
        raise SystemExit(f"Missing environment variables: {', '.join(missing)}")
    return TuyaCredentials(access_id, secret, resolve_endpoint(endpoint))


def _print(data: Any) -> None:
    print(json.dumps(redact(data), indent=2, ensure_ascii=False, default=str))


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="birdie-tuya", description=__doc__.splitlines()[0])
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("token", help="verify credentials by obtaining an access token")
    devices = sub.add_parser("devices", help="list devices visible to the cloud project")
    devices.add_argument("--product-id", action="append", default=[], help="filter (repeatable)")
    default_device = os.environ.get("TUYA_DEVICE_ID")
    for name, text in (
        ("device", "device details"),
        ("status", "raw DP status"),
        ("spec", "DP specification (functions/status with types)"),
    ):
        p = sub.add_parser(name, help=text)
        p.add_argument("device_id", nargs="?", default=default_device)
    users = sub.add_parser("user-devices", help="devices bound to an app user id")
    users.add_argument("uid")
    send = sub.add_parser("send", help="send one DP command, value given as JSON")
    send.add_argument("device_id")
    send.add_argument("code", help="DP code, e.g. switch or mode")
    send.add_argument("value", help="JSON value, e.g. true or '\"auto\"'")
    send.add_argument("--yes", action="store_true", help="confirm sending to a real device")
    return parser.parse_args(argv)


async def _run(args: argparse.Namespace) -> int:
    creds = credentials_from_env()
    async with aiohttp.ClientSession() as session:
        client = TuyaCloudClient(session, creds)
        cmd = args.command
        if cmd in ("device", "status", "spec") and not args.device_id:
            raise SystemExit("device_id is required (argument or TUYA_DEVICE_ID)")
        if cmd == "send" and not args.yes:
            raise SystemExit("Refusing to send a command without --yes")

        if cmd == "token":
            token = await client.async_ensure_token()
            print(f"OK: token obtained, uid={token.uid!r}, expires_at={token.expires_at:.0f}")
        elif cmd == "devices":
            _print(await client.async_list_project_devices(args.product_id or None))
        elif cmd == "device":
            _print(await client.async_get_device(args.device_id))
        elif cmd == "status":
            _print(await client.async_get_device_status(args.device_id))
        elif cmd == "spec":
            _print(await client.async_get_device_specification(args.device_id))
        elif cmd == "user-devices":
            _print(await client.async_get_user_devices(args.uid))
        elif cmd == "send":
            try:
                value = json.loads(args.value)
            except json.JSONDecodeError as err:
                raise SystemExit(f"value must be JSON: {err}") from None
            command = {"code": args.code, "value": value}
            await client.async_send_commands(args.device_id, [command])
            print("OK")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING)
    try:
        return asyncio.run(_run(args))
    except TuyaError as err:
        print(f"ERROR: {err}", file=sys.stderr)
        return 1
    except ValueError as err:
        print(f"ERROR: {err}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
