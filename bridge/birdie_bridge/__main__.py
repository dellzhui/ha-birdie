"""Run the Birdie Cloud Bridge: ``python -m birdie_bridge``.

Configuration (environment variables only; never commit values):

    TUYA_ACCESS_ID, TUYA_ACCESS_SECRET     Tuya cloud project credentials
    TUYA_REGION or TUYA_ENDPOINT           data center, e.g. eu
    BRIDGE_ENV                             "development" or "production" (default)
    BRIDGE_DEV_TOKENS                      development only: token=tuya_uid[,...]
    BRIDGE_EXTRA_PRODUCTS                  extra pid=model mappings, e.g. a clone PID
    BRIDGE_HOST / PORT or BRIDGE_PORT      listen address (default 127.0.0.1:8080)
    BRIDGE_RATE_LIMIT                      requests per minute per account (default 60)
"""

from __future__ import annotations

import logging
import os
import sys

import aiohttp
from aiohttp import web

from .app import create_app
from .auth import Authenticator, DevTokenAuthenticator, parse_dev_tokens
from .devtool import credentials_from_env
from .products import DEFAULT_PRODUCT_MAP, ProductCatalog, parse_product_map
from .service import BridgeService
from .tuya import TuyaCloudClient

_LOGGER = logging.getLogger("birdie_bridge")


def build_authenticator(env: dict[str, str]) -> Authenticator:
    """Select the authenticator for the configured environment."""
    mode = env.get("BRIDGE_ENV", "production").strip().lower()
    dev_tokens = env.get("BRIDGE_DEV_TOKENS", "").strip()
    if mode == "development":
        if not dev_tokens:
            raise SystemExit("BRIDGE_DEV_TOKENS is required when BRIDGE_ENV=development")
        return DevTokenAuthenticator(parse_dev_tokens(dev_tokens))
    if dev_tokens:
        raise SystemExit("BRIDGE_DEV_TOKENS is only allowed when BRIDGE_ENV=development")
    raise SystemExit(
        "No production authenticator is configured yet (Birdie sign-in is pending). "
        "Use BRIDGE_ENV=development with BRIDGE_DEV_TOKENS for local testing."
    )


def build_catalog(env: dict[str, str]) -> ProductCatalog:
    extra = parse_product_map(env.get("BRIDGE_EXTRA_PRODUCTS", ""))
    return ProductCatalog({**DEFAULT_PRODUCT_MAP, **extra})


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    env = dict(os.environ)
    authenticator = build_authenticator(env)
    catalog = build_catalog(env)
    credentials = credentials_from_env()
    host = env.get("BRIDGE_HOST", "127.0.0.1")
    port = int(env.get("PORT") or env.get("BRIDGE_PORT") or 8080)
    rate_limit = int(env.get("BRIDGE_RATE_LIMIT", "60"))

    async def app_factory() -> web.Application:
        session = aiohttp.ClientSession()
        service = BridgeService(TuyaCloudClient(session, credentials), catalog)
        app = create_app(service, authenticator, rate_limit=rate_limit)

        async def close_session(_app: web.Application) -> None:
            await session.close()

        app.on_cleanup.append(close_session)
        return app

    _LOGGER.info("Starting Birdie Cloud Bridge on %s:%s (%s)", host, port, credentials.endpoint)
    web.run_app(app_factory(), host=host, port=port, access_log_format='%a "%r" %s %Tf')
    return 0


if __name__ == "__main__":
    sys.exit(main())
