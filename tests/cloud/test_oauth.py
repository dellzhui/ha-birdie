"""OAuth token handling: refresh, error mapping, revocation (synthetic)."""

from __future__ import annotations

import time

import pytest
from aiohttp import ClientConnectionError
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers.config_entry_oauth2_flow import (
    OAuth2Session,
    async_get_config_entry_implementation,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.birdie.cloud.bridge import (
    BridgeAuthError,
    BridgeConnectionError,
    BridgeUnavailableError,
)
from custom_components.birdie.cloud.oauth import (
    OAuth2TokenProvider,
    async_register_bridge_implementation,
)

from ..helpers import BRIDGE_URL, FakeBridgeClient, mock_cloud_entry

TOKEN_URL = f"{BRIDGE_URL}/oauth/token"
REVOKE_URL = f"{BRIDGE_URL}/oauth/revoke"


def _tokens(access: str, refresh: str) -> dict:
    return {"access_token": access, "refresh_token": refresh, "expires_in": 3600}


async def _provider(hass: HomeAssistant, *, expired: bool) -> tuple[OAuth2TokenProvider, object]:
    entry = mock_cloud_entry(oauth=True)
    if expired:
        token = {**entry.data["token"], "expires_at": time.time() - 10}
        entry = mock_cloud_entry(oauth=True, data={**entry.data, "token": token})
    entry.add_to_hass(hass)
    async_register_bridge_implementation(hass, BRIDGE_URL)
    implementation = await async_get_config_entry_implementation(hass, entry)
    return OAuth2TokenProvider(OAuth2Session(hass, entry, implementation)), entry


async def test_valid_token_used_without_refresh(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    provider, _ = await _provider(hass, expired=False)
    assert await provider.async_get_access_token() == "bat_current"
    assert aioclient_mock.call_count == 0
    assert "bat_current" not in repr(provider)


async def test_expired_token_is_refreshed_and_stored(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    provider, entry = await _provider(hass, expired=True)
    aioclient_mock.post(TOKEN_URL, json=_tokens("bat_new", "brt_grant.secret2"))
    assert await provider.async_get_access_token() == "bat_new"
    request = aioclient_mock.mock_calls[0][2]
    assert request["grant_type"] == "refresh_token"
    assert request["refresh_token"] == "brt_grant.secret1"
    assert request["client_id"] == "home-assistant"
    assert entry.data["token"]["refresh_token"] == "brt_grant.secret2"  # rotation kept


async def test_unauthorized_forces_one_refresh(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    provider, _ = await _provider(hass, expired=False)
    aioclient_mock.post(TOKEN_URL, json=_tokens("bat_forced", "brt_grant.secret3"))
    assert await provider.async_handle_unauthorized() is True
    assert await provider.async_get_access_token() == "bat_forced"
    assert aioclient_mock.call_count == 1


@pytest.mark.parametrize(
    ("mock_kwargs", "expected"),
    [
        ({"status": 400, "json": {"error": "invalid_grant"}}, BridgeAuthError),
        ({"status": 401, "json": {"error": "invalid_client"}}, BridgeAuthError),
        ({"status": 503}, BridgeUnavailableError),
        ({"status": 429}, BridgeUnavailableError),
        ({"exc": ClientConnectionError()}, BridgeConnectionError),
    ],
)
async def test_refresh_errors_are_mapped(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_kwargs: dict,
    expected: type[Exception],
) -> None:
    provider, _ = await _provider(hass, expired=True)
    aioclient_mock.post(TOKEN_URL, **mock_kwargs)
    with pytest.raises(expected):
        await provider.async_get_access_token()


async def test_oauth_entry_setup_and_removal_revokes(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, fake_bridge: FakeBridgeClient
) -> None:
    entry = mock_cloud_entry(oauth=True)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    provider = fake_bridge.created_with[-1][2]
    assert isinstance(provider, OAuth2TokenProvider)

    aioclient_mock.post(REVOKE_URL, status=200)
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    (call,) = [c for c in aioclient_mock.mock_calls if str(c[1]) == REVOKE_URL]
    assert call[2] == {"token": "brt_grant.secret1", "client_id": "home-assistant"}


async def test_removal_survives_unreachable_bridge(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, fake_bridge: FakeBridgeClient
) -> None:
    entry = mock_cloud_entry(oauth=True)
    entry.add_to_hass(hass)
    aioclient_mock.post(REVOKE_URL, exc=ClientConnectionError())
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.config_entries.async_get_entry(entry.entry_id) is None
