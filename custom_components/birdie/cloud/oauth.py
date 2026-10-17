"""OAuth 2.0 sign-in to a Birdie account (docs/sign-in.md, section 6).

The bridge is a public OAuth client's authorization server: the integration
ships its client id, uses PKCE instead of a client secret and always
redirects through my.home-assistant.io, the only redirect URI the bridge
accepts. Users never enter client credentials.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import Any

from aiohttp import ClientError, ClientResponseError
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.config_entry_oauth2_flow import (
    MY_AUTH_CALLBACK_PATH,
    LocalOAuth2ImplementationWithPkce,
    OAuth2Session,
    async_register_implementation,
)

from ..const import CONF_BRIDGE_URL, DOMAIN
from .bridge import BridgeAuthError, BridgeConnectionError, BridgeUnavailableError

_LOGGER = logging.getLogger(__name__)

CLIENT_ID = "home-assistant"
CONF_TOKEN = "token"
REVOKE_TIMEOUT_SECONDS = 10


def implementation_key(bridge_url: str) -> str:
    """Stable implementation id per bridge, stored as the entry's auth_implementation."""
    return f"{DOMAIN}_{hashlib.sha256(bridge_url.encode()).hexdigest()[:12]}"


class BirdieOAuth2Implementation(LocalOAuth2ImplementationWithPkce):
    """OAuth implementation for one Birdie cloud bridge."""

    def __init__(self, hass: HomeAssistant, bridge_url: str) -> None:
        """Initialize the implementation."""
        super().__init__(
            hass,
            implementation_key(bridge_url),
            CLIENT_ID,
            f"{bridge_url}/oauth/authorize",
            f"{bridge_url}/oauth/token",
        )

    @property
    def name(self) -> str:
        """Name shown if several implementations are registered."""
        return "Birdie account"

    @property
    def redirect_uri(self) -> str:
        """The bridge only accepts the My Home Assistant redirect."""
        return MY_AUTH_CALLBACK_PATH


@callback
def async_register_bridge_implementation(hass: HomeAssistant, bridge_url: str) -> str:
    """Register the OAuth implementation for a bridge and return its key."""
    implementation = BirdieOAuth2Implementation(hass, bridge_url)
    async_register_implementation(hass, DOMAIN, implementation)
    return implementation.domain


def is_oauth_entry_data(data: dict[str, Any] | Any) -> bool:
    return CONF_TOKEN in data


class OAuth2TokenProvider:
    """Bridge ``TokenProvider`` backed by Home Assistant's OAuth2 session."""

    def __init__(self, session: OAuth2Session) -> None:
        """Initialize the provider."""
        self._session = session

    def __repr__(self) -> str:
        return "OAuth2TokenProvider(<redacted>)"

    async def async_get_access_token(self) -> str:
        """Return a valid access token, refreshing it if needed."""
        try:
            await self._session.async_ensure_token_valid()
        except ClientResponseError as err:
            # Any 4xx from the token endpoint means "sign in again"; HA releases
            # before 2026.8 raise a plain ClientResponseError for it.
            if 400 <= err.status < 500 and err.status != 429:
                raise BridgeAuthError(err.status, "unauthorized", "Sign-in expired") from err
            raise BridgeUnavailableError(err.status, "token_refresh_failed") from err
        except (ClientError, TimeoutError) as err:
            raise BridgeConnectionError(f"Token refresh failed: {err}") from err
        return str(self._session.token["access_token"])

    async def async_handle_unauthorized(self) -> bool:
        """Force one refresh after the bridge rejected the access token."""
        entry = self._session.config_entry
        token = {**self._session.token, "expires_at": 0}
        self._session.hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_TOKEN: token}
        )
        return True


async def async_revoke(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Revoke the entry's grant on the bridge; best effort."""
    refresh_token = entry.data.get(CONF_TOKEN, {}).get("refresh_token")
    if not refresh_token:
        return
    session = async_get_clientsession(hass)
    try:
        async with asyncio.timeout(REVOKE_TIMEOUT_SECONDS):
            resp = await session.post(
                f"{entry.data[CONF_BRIDGE_URL]}/oauth/revoke",
                data={"token": refresh_token, "client_id": CLIENT_ID},
            )
            resp.release()
    except (ClientError, TimeoutError) as err:
        _LOGGER.debug("Could not revoke the Birdie sign-in: %s", err)
