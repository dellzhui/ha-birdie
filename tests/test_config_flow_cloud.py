"""Config flow tests for Birdie cloud accounts."""

from __future__ import annotations

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.birdie.cloud.bridge import (
    BridgeAccount,
    BridgeAuthError,
    BridgeConnectionError,
)
from custom_components.birdie.const import CONF_ACCESS_TOKEN, CONF_BRIDGE_URL, DOMAIN

from .helpers import (
    ACCOUNT_ID,
    BRIDGE_URL,
    TOKEN,
    FakeBridgeClient,
    cloud_entry_data,
    mock_cloud_entry,
)


@pytest.fixture(autouse=True)
def no_entry_setup(fake_bridge: FakeBridgeClient) -> None:
    """The fake bridge also lets created entries set up cleanly."""


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


async def test_cloud_flow_creates_entry(hass: HomeAssistant, fake_bridge: FakeBridgeClient) -> None:
    flow_id = await _start_cloud_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_BRIDGE_URL: f" {BRIDGE_URL}/ ", CONF_ACCESS_TOKEN: f" {TOKEN} "}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "jane@example.com"
    assert result["data"] == cloud_entry_data()
    assert result["result"].unique_id == f"cloud_{ACCOUNT_ID}"
    await hass.async_block_till_done()


async def test_cloud_flow_invalid_url(hass: HomeAssistant) -> None:
    flow_id = await _start_cloud_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_BRIDGE_URL: "bridge.test", CONF_ACCESS_TOKEN: TOKEN}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_BRIDGE_URL: "invalid_url"}


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (BridgeAuthError(401, "unauthorized"), "invalid_auth"),
        (BridgeConnectionError("down"), "cannot_connect"),
    ],
)
async def test_cloud_flow_errors_then_recover(
    hass: HomeAssistant, fake_bridge: FakeBridgeClient, error: Exception, expected: str
) -> None:
    flow_id = await _start_cloud_flow(hass)
    fake_bridge.fail_account = error
    user_input = {CONF_BRIDGE_URL: BRIDGE_URL, CONF_ACCESS_TOKEN: TOKEN}
    result = await hass.config_entries.flow.async_configure(flow_id, user_input)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": expected}

    fake_bridge.fail_account = None
    result = await hass.config_entries.flow.async_configure(flow_id, user_input)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()


async def test_cloud_flow_already_configured(hass: HomeAssistant) -> None:
    mock_cloud_entry().add_to_hass(hass)
    flow_id = await _start_cloud_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_BRIDGE_URL: BRIDGE_URL, CONF_ACCESS_TOKEN: TOKEN}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth_updates_token(hass: HomeAssistant, fake_bridge: FakeBridgeClient) -> None:
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


async def test_reauth_wrong_account(hass: HomeAssistant, fake_bridge: FakeBridgeClient) -> None:
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


async def test_reauth_invalid_token(hass: HomeAssistant, fake_bridge: FakeBridgeClient) -> None:
    entry = mock_cloud_entry()
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    fake_bridge.fail_account = BridgeAuthError(401, "unauthorized")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ACCESS_TOKEN: "bad"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_menu_offers_bluetooth(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert "ble" in result["menu_options"]
