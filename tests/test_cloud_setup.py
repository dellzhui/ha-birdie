"""Setup, entity and command tests for Birdie cloud accounts (synthetic data)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from homeassistant.components.button import SERVICE_PRESS
from homeassistant.components.fan import (
    ATTR_PERCENTAGE,
    ATTR_PRESET_MODE,
    SERVICE_SET_PERCENTAGE,
    SERVICE_SET_PRESET_MODE,
)
from homeassistant.components.fan import (
    DOMAIN as FAN_DOMAIN,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.birdie.cloud.bridge import (
    BridgeAuthError,
    BridgeDeviceOfflineError,
    BridgeUnavailableError,
)
from custom_components.birdie.const import DOMAIN, is_cloud_entry_data

from .helpers import DEVICE_ID, FakeBridgeClient, mock_cloud_entry

FAN = "fan.living_room_purifier"


@pytest.fixture
async def setup_entry(hass: HomeAssistant, fake_bridge: FakeBridgeClient):
    entry = mock_cloud_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _entity_id(hass: HomeAssistant, platform: str, key: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(platform, DOMAIN, f"{DEVICE_ID}_{key}")
    assert entity_id, f"{platform} {key} not registered"
    return entity_id


async def test_entities_and_device(hass: HomeAssistant, setup_entry) -> None:
    assert setup_entry.state is ConfigEntryState.LOADED

    fan = hass.states.get(FAN)
    assert fan.state == STATE_ON
    assert fan.attributes[ATTR_PRESET_MODE] == "auto"
    assert fan.attributes[ATTR_PERCENTAGE] is None

    assert hass.states.get(_entity_id(hass, "sensor", "pm25")).state == "12"
    assert hass.states.get(_entity_id(hass, "sensor", "air_quality")).state == "good"
    assert hass.states.get(_entity_id(hass, "sensor", "filter_life")).state == "75.0"
    remaining = hass.states.get(_entity_id(hass, "sensor", "filter_remaining"))
    assert float(remaining.state) == pytest.approx(67.5)  # 97200 min shown in days
    assert hass.states.get(_entity_id(hass, "sensor", "device_status")).state == "normal"
    assert (
        hass.states.get(_entity_id(hass, "binary_sensor", "filter_replacement")).state == STATE_OFF
    )
    assert hass.states.get(_entity_id(hass, "binary_sensor", "filter_cover")).state == STATE_OFF
    assert _entity_id(hass, "button", "reset_filter")

    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, DEVICE_ID), setup_entry.entry_id
    )
    assert device.manufacturer == "Birdie"
    assert device.model == "Birdie Pedestal"
    assert device.name == "Living Room Purifier"
    assert device.hw_version == "1.0.0"


async def test_fan_speed_and_presets(
    hass: HomeAssistant, setup_entry, fake_bridge: FakeBridgeClient
) -> None:
    await hass.services.async_call(
        FAN_DOMAIN,
        SERVICE_SET_PERCENTAGE,
        {ATTR_ENTITY_ID: FAN, ATTR_PERCENTAGE: 50},
        blocking=True,
    )
    assert fake_bridge.commands[-1] == (DEVICE_ID, [{"code": "mode", "value": "manual2"}])
    state = hass.states.get(FAN)
    assert state.attributes[ATTR_PERCENTAGE] == 50  # optimistic until the next poll
    assert state.attributes[ATTR_PRESET_MODE] is None

    await hass.services.async_call(
        FAN_DOMAIN, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: FAN}, blocking=True
    )
    assert fake_bridge.commands[-1] == (DEVICE_ID, [{"code": "switch", "value": False}])
    assert hass.states.get(FAN).state == STATE_OFF

    await hass.services.async_call(
        FAN_DOMAIN,
        SERVICE_SET_PRESET_MODE,
        {ATTR_ENTITY_ID: FAN, ATTR_PRESET_MODE: "sleep"},
        blocking=True,
    )
    assert fake_bridge.commands[-1] == (
        DEVICE_ID,
        [{"code": "switch", "value": True}, {"code": "mode", "value": "sleep"}],
    )

    await hass.services.async_call(
        FAN_DOMAIN, SERVICE_TURN_ON, {ATTR_ENTITY_ID: FAN, ATTR_PERCENTAGE: 100}, blocking=True
    )
    assert fake_bridge.commands[-1] == (
        DEVICE_ID,
        [{"code": "switch", "value": True}, {"code": "mode", "value": "manual4"}],
    )
    assert fake_bridge.spec_requests == 1  # specification fetched once and cached


async def test_post_command_refresh(
    hass: HomeAssistant, setup_entry, fake_bridge: FakeBridgeClient
) -> None:
    before = fake_bridge.list_requests
    await hass.services.async_call(
        FAN_DOMAIN, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: FAN}, blocking=True
    )
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=3))
    await hass.async_block_till_done()
    assert fake_bridge.list_requests == before + 1


async def test_reset_filter_button(
    hass: HomeAssistant, setup_entry, fake_bridge: FakeBridgeClient
) -> None:
    await hass.services.async_call(
        "button",
        SERVICE_PRESS,
        {ATTR_ENTITY_ID: _entity_id(hass, "button", "reset_filter")},
        blocking=True,
    )
    assert fake_bridge.commands[-1] == (DEVICE_ID, [{"code": "reset_filter", "value": True}])


async def test_command_to_offline_device(
    hass: HomeAssistant, setup_entry, fake_bridge: FakeBridgeClient
) -> None:
    fake_bridge.fail_command = BridgeDeviceOfflineError(409, "device_offline")
    with pytest.raises(HomeAssistantError) as info:
        await hass.services.async_call(
            FAN_DOMAIN, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: FAN}, blocking=True
        )
    assert info.value.translation_key == "device_offline"
    assert hass.states.get(FAN).state == STATE_ON  # no optimistic change on failure


async def test_offline_device_is_unavailable(
    hass: HomeAssistant, fake_bridge: FakeBridgeClient
) -> None:
    fake_bridge.devices[DEVICE_ID]["online"] = False
    entry = mock_cloud_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(FAN).state == STATE_UNAVAILABLE


async def test_poll_failure_marks_unavailable_then_recovers(
    hass: HomeAssistant, setup_entry, fake_bridge: FakeBridgeClient
) -> None:
    fake_bridge.fail_list = BridgeUnavailableError(503, "upstream_unavailable")
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61))
    await hass.async_block_till_done()
    assert hass.states.get(FAN).state == STATE_UNAVAILABLE

    fake_bridge.fail_list = None
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=122))
    await hass.async_block_till_done()
    assert hass.states.get(FAN).state == STATE_ON


async def test_new_device_added_on_poll(
    hass: HomeAssistant, setup_entry, fake_bridge: FakeBridgeClient
) -> None:
    fake_bridge.add_device("bfsecond")
    fake_bridge.devices["bfsecond"]["name"] = "Bedroom Purifier"
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61))
    await hass.async_block_till_done()
    assert hass.states.get("fan.bedroom_purifier").state == STATE_ON


async def test_unsupported_model_ignored(
    hass: HomeAssistant, fake_bridge: FakeBridgeClient
) -> None:
    fake_bridge.add_device("bfother", model="future_product")
    entry = mock_cloud_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    registry = dr.async_get(hass)
    assert registry.async_get_device_by_identifier((DOMAIN, "bfother"), entry.entry_id) is None
    assert registry.async_get_device_by_identifier((DOMAIN, DEVICE_ID), entry.entry_id)


async def test_setup_retry_when_bridge_unavailable(
    hass: HomeAssistant, fake_bridge: FakeBridgeClient
) -> None:
    fake_bridge.fail_list = BridgeUnavailableError(503, "upstream_unavailable")
    entry = mock_cloud_entry()
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_auth_failure_starts_reauth(
    hass: HomeAssistant, fake_bridge: FakeBridgeClient
) -> None:
    fake_bridge.fail_list = BridgeAuthError(401, "unauthorized")
    entry = mock_cloud_entry()
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == ["reauth"]


async def test_unload(hass: HomeAssistant, setup_entry) -> None:
    assert await hass.config_entries.async_unload(setup_entry.entry_id)
    await hass.async_block_till_done()
    assert setup_entry.state is ConfigEntryState.NOT_LOADED


def test_legacy_bluetooth_entries_are_not_cloud() -> None:
    assert not is_cloud_entry_data({"address": "AA:BB:CC:DD:EE:FF", "name": "Birdie Pro"})
