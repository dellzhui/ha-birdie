"""Config flow for Birdie air purifiers: discovery, manual entry, reauth, reconfigure."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.birdie.const import (
    CONF_DETECTED_VERSION,
    CONF_DEVICE_ID,
    CONF_LOCAL_KEY,
    CONF_PRODUCT_KEY,
    CONF_PROTOCOL_VERSION,
    DOMAIN,
)
from custom_components.birdie.tuya_lan import DeviceUnreachableError, DiscoveredDevice

from .helpers import (
    DEVICE_ID,
    HOST,
    LOCAL_KEY,
    NEW_KEY,
    PRODUCT_KEY,
    FakeLan,
    discovered,
    mock_purifier_entry,
    purifier_entry_data,
)

pytestmark = pytest.mark.usefixtures("fake_lan")


@pytest.fixture(autouse=True)
def short_scan() -> Iterator[None]:
    with patch("custom_components.birdie.config_flow.PURIFIER_DISCOVERY_SECONDS", 0):
        yield


async def _start(hass: HomeAssistant) -> dict[str, Any]:
    """Open the menu, choose the purifier and finish the network search."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == ["ble", "purifier"]
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "purifier"}
    )
    assert result["type"] is FlowResultType.SHOW_PROGRESS
    await hass.async_block_till_done()
    # Like the frontend's request after the progress event; {} must not break it.
    return await hass.config_entries.flow.async_configure(result["flow_id"], {})


def _manual(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        CONF_DEVICE_ID: f" {DEVICE_ID} ",
        CONF_LOCAL_KEY: LOCAL_KEY,
        CONF_HOST: HOST,
        "advanced": {CONF_PROTOCOL_VERSION: "auto"},
    }
    values.update(overrides)
    return values


async def test_discovered_purifier(
    hass: HomeAssistant, fake_lan: FakeLan, lan_broadcasts: dict[str, DiscoveredDevice]
) -> None:
    lan_broadcasts[DEVICE_ID] = discovered(protocol_version="3.4")
    lan_broadcasts["bfsomeplug00000000000"] = discovered(
        device_id="bfsomeplug00000000000", product_key="otherproduct"
    )
    fake_lan.version = "3.4"

    result = await _start(hass)
    assert result["step_id"] == "purifier_pick"
    options = result["data_schema"].schema[CONF_DEVICE_ID].container
    assert list(options) == [DEVICE_ID, "manual"]  # only Birdie purifiers

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DEVICE_ID: DEVICE_ID}
    )
    assert result["step_id"] == "purifier_key"
    assert result["description_placeholders"] == {"device_id": DEVICE_ID, "host": HOST}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LOCAL_KEY: "short"}
    )
    assert result["errors"] == {CONF_LOCAL_KEY: "invalid_local_key_format"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LOCAL_KEY: NEW_KEY}
    )
    assert result["errors"] == {"base": "invalid_local_key"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LOCAL_KEY: f" {LOCAL_KEY} "}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Birdie Podium"
    assert result["result"].unique_id == DEVICE_ID
    assert result["data"] == purifier_entry_data(**{CONF_DETECTED_VERSION: "3.4"})
    assert fake_lan.devices[-1].version_hint == "3.4"
    assert all(device.closed for device in fake_lan.devices[:-1])  # flow checks closed


async def test_pick_manual_entry(
    hass: HomeAssistant, lan_broadcasts: dict[str, DiscoveredDevice]
) -> None:
    lan_broadcasts[DEVICE_ID] = discovered()
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DEVICE_ID: "manual"}
    )
    assert result["step_id"] == "purifier_manual"


async def test_manual_entry(hass: HomeAssistant, fake_lan: FakeLan) -> None:
    result = await _start(hass)
    assert result["step_id"] == "purifier_not_found"

    fake_lan.connect_error = DeviceUnreachableError("no route")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], _manual())
    assert result["errors"] == {"base": "cannot_connect"}

    fake_lan.connect_error = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _manual(advanced={CONF_PROTOCOL_VERSION: "3.3"})
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    data = result["data"]
    assert data[CONF_DEVICE_ID] == DEVICE_ID
    assert data[CONF_HOST] == HOST
    assert data[CONF_PROTOCOL_VERSION] == "3.3"
    assert CONF_PRODUCT_KEY not in data


@pytest.mark.parametrize(
    ("values", "errors"),
    [
        ({CONF_DEVICE_ID: "  "}, {CONF_DEVICE_ID: "invalid_device_id"}),
        ({CONF_LOCAL_KEY: "0123456789abcdef0"}, {CONF_LOCAL_KEY: "invalid_local_key_format"}),
        ({CONF_LOCAL_KEY: NEW_KEY}, {"base": "invalid_local_key"}),
    ],
)
async def test_manual_entry_errors(
    hass: HomeAssistant, values: dict[str, Any], errors: dict[str, str]
) -> None:
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], _manual(**values))
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == errors


async def test_manual_without_ip_searches_network(
    hass: HomeAssistant, fake_lan: FakeLan, lan_broadcasts: dict[str, DiscoveredDevice]
) -> None:
    result = await _start(hass)
    assert result["step_id"] == "purifier_not_found"

    values = _manual()
    del values[CONF_HOST]
    result = await hass.config_entries.flow.async_configure(result["flow_id"], values)
    assert result["errors"] == {CONF_HOST: "device_not_found"}

    # Purifier with another product id (not offered in the list) found by id.
    lan_broadcasts[DEVICE_ID] = discovered(host="192.168.1.55", product_key="unlisted")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], values)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_HOST] == "192.168.1.55"
    assert result["data"][CONF_PRODUCT_KEY] == "unlisted"


async def test_already_configured(
    hass: HomeAssistant, lan_broadcasts: dict[str, DiscoveredDevice]
) -> None:
    mock_purifier_entry().add_to_hass(hass)
    lan_broadcasts[DEVICE_ID] = discovered()
    result = await _start(hass)
    assert result["step_id"] == "purifier_not_found"  # configured device not offered
    result = await hass.config_entries.flow.async_configure(result["flow_id"], _manual())
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_integration_discovery(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_INTEGRATION_DISCOVERY},
        data={
            CONF_DEVICE_ID: DEVICE_ID,
            CONF_HOST: HOST,
            CONF_PROTOCOL_VERSION: "3.3",
            CONF_PRODUCT_KEY: PRODUCT_KEY,
        },
    )
    assert result["step_id"] == "purifier_key"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LOCAL_KEY: LOCAL_KEY}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_PRODUCT_KEY] == PRODUCT_KEY


async def test_integration_discovery_updates_known_address(hass: HomeAssistant) -> None:
    entry = mock_purifier_entry()
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_INTEGRATION_DISCOVERY},
        data={CONF_DEVICE_ID: DEVICE_ID, CONF_HOST: "192.168.1.99"},
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_HOST] == "192.168.1.99"


async def test_reauth_with_new_key(hass: HomeAssistant, fake_lan: FakeLan) -> None:
    entry = mock_purifier_entry()
    entry.add_to_hass(hass)
    fake_lan.accepted_key = NEW_KEY  # paired again: the key changed
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    assert result["description_placeholders"]["device_id"] == DEVICE_ID

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LOCAL_KEY: LOCAL_KEY}
    )
    assert result["errors"] == {"base": "invalid_local_key"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LOCAL_KEY: NEW_KEY}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_LOCAL_KEY] == NEW_KEY
    await hass.async_block_till_done()
    assert fake_lan.device.config.local_key == NEW_KEY  # reloaded with the new key


async def test_reconfigure(hass: HomeAssistant, fake_lan: FakeLan) -> None:
    entry = mock_purifier_entry()
    entry.add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_LOCAL_KEY: "bad", CONF_HOST: HOST, "advanced": {CONF_PROTOCOL_VERSION: "auto"}},
    )
    assert result["errors"] == {CONF_LOCAL_KEY: "invalid_local_key_format"}

    # An empty key keeps the current one.
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_HOST: " 10.0.0.8 ", "advanced": {CONF_PROTOCOL_VERSION: "3.5"}},
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_HOST] == "10.0.0.8"
    assert entry.data[CONF_PROTOCOL_VERSION] == "3.5"
    assert entry.data[CONF_LOCAL_KEY] == LOCAL_KEY


async def test_reconfigure_bluetooth_entry_is_refused(hass: HomeAssistant) -> None:
    entry = mock_purifier_entry(data={"address": "AA:BB:CC:11:22:33"}, unique_id="AA:BB")
    entry.add_to_hass(hass)
    with patch("custom_components.birdie.async_setup_entry", return_value=True):
        result = await entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_not_supported"
