"""Run a fake Tuya cloud plus a real Birdie Cloud Bridge for local demos.

    .venv/bin/python tools/mock_cloud.py [--port 8080]

Starts the in-process Tuya fake from bridge/tests (one synthetic Birdie
Pedestal) and the bridge in development mode in front of it. In Home
Assistant, add "Birdie account" with the cloud URL http://<host>:<port> under
Advanced, then either sign in on the development sign-in page as
``demo@example.com`` / ``demo-password``, or enter the development token
``demo-token``. Everything is synthetic; no real cloud is contacted.

The sign-in redirects through my.home-assistant.io like the real service, so
the browser must have My Home Assistant pointed at your Home Assistant.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import sys
from pathlib import Path

import aiohttp
from aiohttp import web

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bridge"))

from birdie_bridge.app import create_app  # noqa: E402
from birdie_bridge.auth import ChainAuthenticator, DevTokenAuthenticator  # noqa: E402
from birdie_bridge.oauth import (  # noqa: E402
    DevIdentityProvider,
    MemoryOAuthStore,
    OAuthServer,
)
from birdie_bridge.products import ProductCatalog  # noqa: E402
from birdie_bridge.service import BridgeService  # noqa: E402
from birdie_bridge.tuya import TuyaCloudClient, TuyaCredentials  # noqa: E402
from tests.fake_tuya import ACCESS_ID, ACCESS_SECRET, FakeTuyaCloud  # noqa: E402

DEMO_TOKEN = "demo-token"
DEMO_UID = "synthuser0001"
DEMO_EMAIL = "demo@example.com"
DEMO_PASSWORD = "demo-password"


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
    oauth = OAuthServer(
        MemoryOAuthStore(), DevIdentityProvider({DEMO_EMAIL: (DEMO_PASSWORD, DEMO_UID)})
    )
    authenticator = ChainAuthenticator([oauth, DevTokenAuthenticator({DEMO_TOKEN: DEMO_UID})])
    bridge_runner = web.AppRunner(create_app(service, authenticator, oauth=oauth))
    await bridge_runner.setup()
    await web.TCPSite(bridge_runner, host, port).start()
    print(f"Fake Tuya cloud on 127.0.0.1:{tuya_port}")
    print(f"Bridge on http://{host}:{port}")
    print(f"  sign in: {DEMO_EMAIL} / {DEMO_PASSWORD}", flush=True)
    print(f"  development token: {DEMO_TOKEN}", flush=True)
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
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run(args.host, args.port, args.tuya_port))


if __name__ == "__main__":
    main()
