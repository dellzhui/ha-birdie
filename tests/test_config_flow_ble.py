"""Regression tests: Bluetooth (Birdie Pro) setup still works next to cloud accounts."""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from homeassistant import config_entries
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.birdie.const import (
    CONF_ENTRY_TYPE,
    CONF_NAME,
    DOMAIN,
    ENVIRONMENTAL_SERVICE_UUID,
)

ADDRESS = "AA:BB:CC:11:22:33"


def _birdie_pro_service_info() -> BluetoothServiceInfoBleak:
    return BluetoothServiceInfoBleak(
        name="Birdie Pro",
        address=ADDRESS,
        rssi=-60,
        manufacturer_data={},
        service_data={},
        service_uuids=[ENVIRONMENTAL_SERVICE_UUID],
        source="local",
        device=BLEDevice(ADDRESS, "Birdie Pro", None),
        advertisement=AdvertisementData(
            local_name="Birdie Pro",
            manufacturer_data={},
            service_data={},
            service_uuids=[ENVIRONMENTAL_SERVICE_UUID],
            tx_power=None,
            rssi=-60,
            platform_data=(),
        ),
        connectable=True,
        time=time.monotonic(),
        tx_power=None,
    )


@pytest.fixture(autouse=True)
def no_ble_setup() -> object:
    """Do not open real BLE connections when an entry is created."""
    with patch("custom_components.birdie.async_setup_entry", return_value=True) as mock:
        yield mock


@pytest.mark.usefixtures("mock_bluetooth")
async def test_bluetooth_discovery_creates_ble_entry(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_BLUETOOTH},
        data=_birdie_pro_service_info(),
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "bluetooth_confirm"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_ADDRESS: ADDRESS, CONF_NAME: "Birdie Pro"}
    assert CONF_ENTRY_TYPE not in result["data"]
    assert result["result"].unique_id == ADDRESS


@pytest.mark.usefixtures("mock_bluetooth")
async def test_manual_ble_setup_via_menu(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.MENU

    with patch("custom_components.birdie.config_flow.DISCOVERY_TIMEOUT", 0):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "ble"}
        )
        if result["type"] is FlowResultType.SHOW_PROGRESS:
            await hass.async_block_till_done()
            result = await hass.config_entries.flow.async_configure(result["flow_id"])
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "ble"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADDRESS: ADDRESS.lower(), CONF_NAME: "Office Birdie"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_ADDRESS: ADDRESS, CONF_NAME: "Office Birdie"}
