"""Diagnostics and device removal for Birdie cloud accounts."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.components.fan import DOMAIN as FAN_DOMAIN
from homeassistant.components.fan import SERVICE_TURN_OFF
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.birdie import async_remove_config_entry_device
from custom_components.birdie.const import DOMAIN

from .helpers import DEVICE_ID, TOKEN, FakeBridgeClient, mock_cloud_entry


async def _setup(hass: HomeAssistant):
    entry = mock_cloud_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_diagnostics_redacts_token(
    hass: HomeAssistant, hass_client: ClientSessionGenerator, fake_bridge: FakeBridgeClient
) -> None:
    assert await async_setup_component(hass, "diagnostics", {})
    entry = await _setup(hass)
    await hass.services.async_call(
        FAN_DOMAIN, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: "fan.living_room_purifier"}, blocking=True
    )

    diagnostics = await get_diagnostics_for_config_entry(hass, hass_client, entry)
    assert TOKEN not in str(diagnostics)
    assert diagnostics["entry"]["access_token"] == "**REDACTED**"
    device = diagnostics["devices"][DEVICE_ID]
    assert device["info"]["model"] == "pedestal"
    assert device["status"]["pm25"] == 12
    assert device["decoded"]["filter_life_percent"] == 75.0
    assert device["specification"]["category"] == "kj"  # fetched by the command above


async def test_remove_stale_device_only(hass: HomeAssistant, fake_bridge: FakeBridgeClient) -> None:
    entry = await _setup(hass)
    registry = dr.async_get(hass)
    device = registry.async_get_device_by_identifier((DOMAIN, DEVICE_ID), entry.entry_id)
    assert not await async_remove_config_entry_device(hass, entry, device)

    fake_bridge.devices.clear()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61))
    await hass.async_block_till_done()
    assert await async_remove_config_entry_device(hass, entry, device)
