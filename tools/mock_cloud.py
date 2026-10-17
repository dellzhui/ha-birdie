"""Run a fake Tuya cloud plus a real Birdie Cloud Bridge for local demos.

    .venv/bin/python tools/mock_cloud.py [--port 8080]

Starts the in-process Tuya fake from bridge/tests (one synthetic Birdie
Pedestal) and the bridge in development mode in front of it. Point Home
Assistant's "Birdie account" setup at http://<host>:<port> with token
``demo-token``. Everything is synthetic; no real cloud is contacted.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import aiohttp
from aiohttp import web

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bridge"))

from birdie_bridge.app import create_app  # noqa: E402
from birdie_bridge.auth import DevTokenAuthenticator  # noqa: E402
from birdie_bridge.products import ProductCatalog  # noqa: E402
from birdie_bridge.service import BridgeService  # noqa: E402
from birdie_bridge.tuya import TuyaCloudClient, TuyaCredentials  # noqa: E402
from tests.fake_tuya import ACCESS_ID, ACCESS_SECRET, FakeTuyaCloud  # noqa: E402

DEMO_TOKEN = "demo-token"
DEMO_UID = "synthuser0001"


async def run(host: str, port: int, tuya_port: int) -> None:
    cloud = FakeTuyaCloud()
    cloud.add_pedestal()
    tuya_runner = web.AppRunner(cloud.app())
    await tuya_runner.setup()
    await web.TCPSite(tuya_runner, "127.0.0.1", tuya_port).start()

    session = aiohttp.ClientSession()
    tuya = TuyaCloudClient(
        session, TuyaCredentials(ACCESS_ID, ACCESS_SECRET, f"http://127.0.0.1:{tuya_port}")
    )
    service = BridgeService(tuya, ProductCatalog({"synthpid00000001": "pedestal"}))
    bridge_runner = web.AppRunner(
        create_app(service, DevTokenAuthenticator({DEMO_TOKEN: DEMO_UID}))
    )
    await bridge_runner.setup()
    await web.TCPSite(bridge_runner, host, port).start()
    print(f"Fake Tuya cloud on 127.0.0.1:{tuya_port}")
    print(f"Bridge on http://{host}:{port}  token: {DEMO_TOKEN}", flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await bridge_runner.cleanup()
        await tuya_runner.cleanup()
        await session.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--tuya-port", type=int, default=8081)
    args = parser.parse_args()
    try:
        asyncio.run(run(args.host, args.port, args.tuya_port))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
