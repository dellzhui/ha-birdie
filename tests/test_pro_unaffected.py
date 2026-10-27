"""Regression tests: adding the air purifier must not change Birdie Pro setups."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.birdie.const import CONF_NAME, DOMAIN
from custom_components.birdie.purifier import discovery

ADDRESS = "4C:5B:B3:43:A8:19"
# What 0.1.x stored for a Birdie Pro; upgrades must keep working with it.
PRO_ENTRY_DATA = {CONF_ADDRESS: ADDRESS, CONF_NAME: "Birdie Pro"}


async def test_menu_offers_birdie_pro_first(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"][0] == "ble"


@pytest.mark.usefixtures("mock_bluetooth")
async def test_pro_entry_from_0_1_loads_without_lan_listener(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, data=dict(PRO_ENTRY_DATA), unique_id=ADDRESS, version=1
    )
    entry.add_to_hass(hass)

    with (
        patch(
            "custom_components.birdie.coordinator.BirdieCoordinator.async_connect",
            return_value=False,
        ),
        patch.object(
            discovery.DiscoveryListener,
            "async_start",
            side_effect=AssertionError("a Birdie Pro must not open the purifier listener"),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert entry.data == PRO_ENTRY_DATA
    assert entry.version == 1
    assert discovery.get_discovery(hass).listener is None
