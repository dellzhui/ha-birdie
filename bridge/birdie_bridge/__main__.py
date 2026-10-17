"""Run the Birdie Cloud Bridge: ``python -m birdie_bridge``.

Configuration (environment variables only; never commit values):

    TUYA_ACCESS_ID, TUYA_ACCESS_SECRET     Tuya cloud project credentials
    TUYA_REGION or TUYA_ENDPOINT           data center, e.g. eu
    BRIDGE_ENV                             "development" or "production" (default)
    BRIDGE_DEV_TOKENS                      development only: token=tuya_uid[,...]
    BRIDGE_DEV_USERS                       development only: OAuth sign-in users,
                                           email:password=tuya_uid[,...]
    BRIDGE_TRUSTED_PROXY_HOPS              proxies in front of the bridge that append to
                                           X-Forwarded-For (Cloud Run: 1; default 0)
    BRIDGE_EXTRA_PRODUCTS                  extra pid=model mappings, e.g. a clone PID
    BRIDGE_HOST / PORT or BRIDGE_PORT      listen address (default 127.0.0.1:8080)
    BRIDGE_RATE_LIMIT                      requests per minute per account (default 60)
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass

import aiohttp
from aiohttp import web

from .app import create_app
from .auth import Authenticator, ChainAuthenticator, DevTokenAuthenticator, parse_dev_tokens
from .devtool import credentials_from_env
from .oauth import DevIdentityProvider, MemoryOAuthStore, OAuthServer, parse_dev_users
from .products import DEFAULT_PRODUCT_MAP, ProductCatalog, parse_product_map
from .service import BridgeService
from .tuya import TuyaCloudClient

_LOGGER = logging.getLogger("birdie_bridge")


@dataclass(frozen=True)
class AuthSetup:
    authenticator: Authenticator
    oauth: OAuthServer | None


def build_auth(env: dict[str, str]) -> AuthSetup:
    """Select authentication for the configured environment."""
    mode = env.get("BRIDGE_ENV", "production").strip().lower()
    dev_tokens = env.get("BRIDGE_DEV_TOKENS", "").strip()
    dev_users = env.get("BRIDGE_DEV_USERS", "").strip()
    if mode == "development":
        authenticators: list[Authenticator] = []
        oauth: OAuthServer | None = None
        if dev_users:
            oauth = OAuthServer(MemoryOAuthStore(), DevIdentityProvider(parse_dev_users(dev_users)))
            authenticators.append(oauth)
        if dev_tokens:
            authenticators.append(DevTokenAuthenticator(parse_dev_tokens(dev_tokens)))
        if not authenticators:
            raise SystemExit(
                "BRIDGE_DEV_USERS and/or BRIDGE_DEV_TOKENS is required when BRIDGE_ENV=development"
            )
        return AuthSetup(ChainAuthenticator(authenticators), oauth)
    if dev_tokens or dev_users:
        raise SystemExit(
            "BRIDGE_DEV_TOKENS and BRIDGE_DEV_USERS are only allowed when BRIDGE_ENV=development"
        )
    raise SystemExit(
        "No production identity provider is configured yet (Birdie sign-in is pending). "
        "Use BRIDGE_ENV=development with BRIDGE_DEV_USERS or BRIDGE_DEV_TOKENS for testing."
    )


def build_catalog(env: dict[str, str]) -> ProductCatalog:
    extra = parse_product_map(env.get("BRIDGE_EXTRA_PRODUCTS", ""))
    return ProductCatalog({**DEFAULT_PRODUCT_MAP, **extra})


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    env = dict(os.environ)
    auth = build_auth(env)
    catalog = build_catalog(env)
    credentials = credentials_from_env()
    host = env.get("BRIDGE_HOST", "127.0.0.1")
    port = int(env.get("PORT") or env.get("BRIDGE_PORT") or 8080)
    rate_limit = int(env.get("BRIDGE_RATE_LIMIT", "60"))
    proxy_hops = int(env.get("BRIDGE_TRUSTED_PROXY_HOPS", "0"))

    async def app_factory() -> web.Application:
        session = aiohttp.ClientSession()
        service = BridgeService(TuyaCloudClient(session, credentials), catalog)
        app = create_app(
            service,
            auth.authenticator,
            rate_limit=rate_limit,
            oauth=auth.oauth,
            trusted_proxy_hops=proxy_hops,
        )

        async def close_session(_app: web.Application) -> None:
            await session.close()

        app.on_cleanup.append(close_session)
        return app

    _LOGGER.info("Starting Birdie Cloud Bridge on %s:%s (%s)", host, port, credentials.endpoint)
    web.run_app(app_factory(), host=host, port=port, access_log_format='%a "%r" %s %Tf')
    return 0


if __name__ == "__main__":
    sys.exit(main())
