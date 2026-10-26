"""Run a simulated Birdie air purifier on this machine (for demos and local testing).

Listens on TCP 6668 like a Tuya Wi-Fi device, announces itself on UDP 6667
every few seconds, drifts its PM2.5 value and counts filter minutes. The DPs
mirror what a real purifier reported (protocol 3.4, no DP 108); the values
are synthetic. --pm25-fault behaves like the real unit with a broken PM2.5
sensor. The local key comes from BIRDIE_LOCAL_KEY (default: a fixed demo key).

  python -m tools.fake_purifier --version 3.4
"""

from __future__ import annotations

import argparse
import asyncio
import os
import random
import socket
import time

from tests.tuya_lan.fake_device import FakeTuyaDevice

DEMO_KEY = "demo0local0key00"
DEMO_DPS = {
    "1": True,
    "2": 12,
    "3": "auto",
    "21": "good",
    "105": 129600,
    "106": 32400,
    "107": False,
    "109": True,
    "110": "sim-1.0",
    "111": "normal",
}


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--device-id", default="bfsimulatedpurifier01")
    parser.add_argument("--version", default="3.4", choices=["3.1", "3.3", "3.4", "3.5"])
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=6668)
    parser.add_argument("--announce-ip", help="IP to announce (default: this host's)")
    parser.add_argument("--broadcast", default="255.255.255.255")
    parser.add_argument("--pm25-fault", action="store_true", help="broken PM2.5 sensor")
    parser.add_argument(
        "--filter-minute", type=float, default=60, help="seconds per filter minute counted"
    )
    args = parser.parse_args()

    key = os.environ.get("BIRDIE_LOCAL_KEY", DEMO_KEY)
    dps = dict(DEMO_DPS)
    if args.pm25_fault:
        dps.update({"2": 1, "111": "pm25"})
    device = FakeTuyaDevice(args.device_id, key, args.version, dps, host=args.bind)
    await device.start(args.port)
    announce_ip = args.announce_ip or _own_ip()
    print(f"Simulated purifier {args.device_id} v{args.version} on {announce_ip}:{device.port}")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    next_minute = time.monotonic() + args.filter_minute
    try:
        while True:
            sock.sendto(device.broadcast_packet(announce_ip), (args.broadcast, 6667))
            if not args.pm25_fault:
                pm25 = max(0, int(device.dps["2"]) + random.randint(-3, 3))
                await device.push({"2": pm25})
            if time.monotonic() >= next_minute:
                next_minute += args.filter_minute
                if device.dps["1"]:
                    await device.push({"106": int(device.dps["106"]) + 1})
            await asyncio.sleep(min(5, args.filter_minute))
    finally:
        sock.close()
        await device.stop()


def _own_ip() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            probe.connect(("192.0.2.1", 9))  # no packet is sent
            return str(probe.getsockname()[0])
        except OSError:
            return "127.0.0.1"


if __name__ == "__main__":
    asyncio.run(main())
