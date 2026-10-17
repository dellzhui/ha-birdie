"""Config flow tests for Birdie accounts: OAuth sign-in and development tokens."""

from __future__ import annotations

from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.config_entry_oauth2_flow import MY_AUTH_CALLBACK_PATH
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.birdie.cloud.bridge import (
    BridgeAccount,
    BridgeAuthError,
    BridgeConnectionError,
)
from custom_components.birdie.cloud.oauth import implementation_key
from custom_components.birdie.const import CONF_ACCESS_TOKEN, DOMAIN

from .helpers import (
    ACCOUNT_ID,
    BRIDGE_URL,
    TOKEN,
    FakeBridgeClient,
    cloud_entry_data,
    mock_cloud_entry,
)

TOKEN_URL = f"{BRIDGE_URL}/oauth/token"
NEW_TOKENS = {
    "access_token": "bat_new",
    "refresh_token": "brt_grant.secret2",
    "token_type": "Bearer",
    "expires_in": 3600,
}


@pytest.fixture(autouse=True)
def no_entry_setup(fake_bridge: FakeBridgeClient) -> None:
    """The fake bridge also lets created entries set up cleanly."""


def _advanced(**values: str) -> dict:
    return {"advanced": values}


async def _start_cloud_flow(hass: HomeAssistant) -> str:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == ["ble", "cloud"]
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "cloud"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "cloud"
    return result["flow_id"]


async def _sign_in(
    hass: HomeAssistant,
    flow_id: str,
    result: dict,
    hass_client_no_auth: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
    *,
    callback_query: str = "code=bac_test",
) -> dict:
    """Complete the browser part of the OAuth flow and the token exchange."""
    assert result["type"] is FlowResultType.EXTERNAL_STEP
    url = urlsplit(result["url"])
    assert f"{url.scheme}://{url.netloc}{url.path}" == f"{BRIDGE_URL}/oauth/authorize"
    query = parse_qs(url.query)
    assert query["client_id"] == ["home-assistant"]
    assert query["redirect_uri"] == [MY_AUTH_CALLBACK_PATH]
    assert query["code_challenge_method"] == ["S256"]
    assert len(query["code_challenge"][0]) == 43

    client = await hass_client_no_auth()
    resp = await client.get(f"/auth/external/callback?{callback_query}&state={query['state'][0]}")
    assert resp.status == 200
    aioclient_mock.post(TOKEN_URL, json=NEW_TOKENS)
    return await hass.config_entries.flow.async_configure(flow_id)


# ------------------------------------------------------------------- OAuth


async def test_oauth_sign_in_creates_entry(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
    fake_bridge: FakeBridgeClient,
) -> None:
    flow_id = await _start_cloud_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id, _advanced(bridge_url=f" {BRIDGE_URL}/ ")
    )
    result = await _sign_in(hass, flow_id, result, hass_client_no_auth, aioclient_mock)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "jane@example.com"
    assert result["result"].unique_id == f"cloud_{ACCOUNT_ID}"
    data = result["data"]
    assert data["bridge_url"] == BRIDGE_URL
    assert data["account_id"] == ACCOUNT_ID
    assert data["auth_implementation"] == implementation_key(BRIDGE_URL)
    assert data["token"]["access_token"] == "bat_new"
    assert data["token"]["refresh_token"] == "brt_grant.secret2"
    assert CONF_ACCESS_TOKEN not in data

    token_request = aioclient_mock.mock_calls[-1][2]
    assert token_request["grant_type"] == "authorization_code"
    assert token_request["code"] == "bac_test"
    assert token_request["client_id"] == "home-assistant"
    assert token_request["redirect_uri"] == MY_AUTH_CALLBACK_PATH
    assert len(token_request["code_verifier"]) >= 43
    assert "client_secret" not in token_request
    await hass.async_block_till_done()


async def test_oauth_uses_default_cloud_without_asking(
    hass: HomeAssistant,
) -> None:
    flow_id = await _start_cloud_flow(hass)
    with patch("custom_components.birdie.config_flow.DEFAULT_BRIDGE_URL", BRIDGE_URL):
        result = await hass.config_entries.flow.async_configure(flow_id, _advanced())
    assert result["type"] is FlowResultType.EXTERNAL_STEP
    assert result["url"].startswith(f"{BRIDGE_URL}/oauth/authorize?")


async def test_oauth_cancelled_by_user(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    flow_id = await _start_cloud_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id, _advanced(bridge_url=BRIDGE_URL)
    )
    result = await _sign_in(
        hass,
        flow_id,
        result,
        hass_client_no_auth,
        aioclient_mock,
        callback_query="error=access_denied",
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "user_rejected_authorize"


async def test_oauth_token_rejected(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    flow_id = await _start_cloud_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id, _advanced(bridge_url=BRIDGE_URL)
    )
    url = parse_qs(urlsplit(result["url"]).query)
    client = await hass_client_no_auth()
    await client.get(f"/auth/external/callback?code=bac_x&state={url['state'][0]}")
    aioclient_mock.post(TOKEN_URL, status=400, json={"error": "invalid_grant"})
    result = await hass.config_entries.flow.async_configure(flow_id)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] in ("oauth_unauthorized", "oauth_error", "oauth_failed")


async def test_oauth_account_check_fails(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
    fake_bridge: FakeBridgeClient,
) -> None:
    flow_id = await _start_cloud_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id, _advanced(bridge_url=BRIDGE_URL)
    )
    fake_bridge.fail_account = BridgeConnectionError("down")
    result = await _sign_in(hass, flow_id, result, hass_client_no_auth, aioclient_mock)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "cannot_connect"


async def test_oauth_already_configured(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    mock_cloud_entry(oauth=True).add_to_hass(hass)
    flow_id = await _start_cloud_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id, _advanced(bridge_url=BRIDGE_URL)
    )
    result = await _sign_in(hass, flow_id, result, hass_client_no_auth, aioclient_mock)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_oauth_reauth(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    entry = mock_cloud_entry(oauth=True)
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_oauth"
    assert result["description_placeholders"]["account"] == "jane@example.com"
    flow_id = result["flow_id"]
    result = await hass.config_entries.flow.async_configure(flow_id, {})
    result = await _sign_in(hass, flow_id, result, hass_client_no_auth, aioclient_mock)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data["token"]["access_token"] == "bat_new"
    assert entry.data["bridge_url"] == BRIDGE_URL
    await hass.async_block_till_done()


async def test_oauth_reauth_wrong_account(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
    fake_bridge: FakeBridgeClient,
) -> None:
    entry = mock_cloud_entry(oauth=True)
    entry.add_to_hass(hass)
    fake_bridge.account = BridgeAccount(id="someone-else")
    result = await entry.start_reauth_flow(hass)
    flow_id = result["flow_id"]
    result = await hass.config_entries.flow.async_configure(flow_id, {})
    result = await _sign_in(hass, flow_id, result, hass_client_no_auth, aioclient_mock)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_account"
    assert entry.data["token"]["access_token"] == "bat_current"


# -------------------------------------------------------- development tokens


async def test_token_flow_creates_entry(hass: HomeAssistant) -> None:
    flow_id = await _start_cloud_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id, _advanced(bridge_url=BRIDGE_URL, access_token=f" {TOKEN} ")
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "jane@example.com"
    assert result["data"] == cloud_entry_data()
    assert result["result"].unique_id == f"cloud_{ACCOUNT_ID}"
    await hass.async_block_till_done()


@pytest.mark.parametrize("values", [{"bridge_url": "bridge.test"}, {}])
async def test_cloud_flow_invalid_or_missing_url(hass: HomeAssistant, values: dict) -> None:
    flow_id = await _start_cloud_flow(hass)
    result = await hass.config_entries.flow.async_configure(flow_id, _advanced(**values))
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_url"}


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (BridgeAuthError(401, "unauthorized"), "invalid_auth"),
        (BridgeConnectionError("down"), "cannot_connect"),
    ],
)
async def test_token_flow_errors_then_recover(
    hass: HomeAssistant, fake_bridge: FakeBridgeClient, error: Exception, expected: str
) -> None:
    flow_id = await _start_cloud_flow(hass)
    fake_bridge.fail_account = error
    user_input = _advanced(bridge_url=BRIDGE_URL, access_token=TOKEN)
    result = await hass.config_entries.flow.async_configure(flow_id, user_input)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": expected}

    fake_bridge.fail_account = None
    result = await hass.config_entries.flow.async_configure(flow_id, user_input)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()


async def test_token_flow_already_configured(hass: HomeAssistant) -> None:
    mock_cloud_entry().add_to_hass(hass)
    flow_id = await _start_cloud_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id, _advanced(bridge_url=BRIDGE_URL, access_token=TOKEN)
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_token_reauth_updates_token(hass: HomeAssistant) -> None:
    entry = mock_cloud_entry()
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ACCESS_TOKEN: "new-token"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_ACCESS_TOKEN] == "new-token"
    await hass.async_block_till_done()


async def test_token_reauth_wrong_account(
    hass: HomeAssistant, fake_bridge: FakeBridgeClient
) -> None:
    entry = mock_cloud_entry()
    entry.add_to_hass(hass)
    fake_bridge.account = BridgeAccount(id="someone-else")
    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ACCESS_TOKEN: "new-token"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_account"
    assert entry.data[CONF_ACCESS_TOKEN] == TOKEN


async def test_token_reauth_invalid_token(
    hass: HomeAssistant, fake_bridge: FakeBridgeClient
) -> None:
    entry = mock_cloud_entry()
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    fake_bridge.fail_account = BridgeAuthError(401, "unauthorized")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ACCESS_TOKEN: "bad"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
