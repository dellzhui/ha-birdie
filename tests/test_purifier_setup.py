"""Setup, entities, commands and connection handling of the air purifier (synthetic data)."""

from __future__ import annotations

import pytest
from homeassistant.components.button import SERVICE_PRESS
from homeassistant.components.fan import (
    ATTR_PERCENTAGE,
    ATTR_PRESET_MODE,
    SERVICE_SET_PERCENTAGE,
    SERVICE_SET_PRESET_MODE,
)
from homeassistant.components.fan import DOMAIN as FAN_DOMAIN
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import (
    ATTR_ENTITY_ID,
    CONF_HOST,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.birdie.const import (
    CONF_DETECTED_VERSION,
    DOMAIN,
    PURIFIER_KEY_FAILURES_BEFORE_REAUTH,
)
from custom_components.birdie.purifier.coordinator import DATA_SETUP_KEY_FAILURES
from custom_components.birdie.tuya_lan import (
    DeviceUnreachableError,
    DiscoveredDevice,
    InvalidLocalKeyError,
)

from .helpers import (
    DEVICE_ID,
    FAN,
    HOST,
    LOCAL_KEY,
    FakeLan,
    FakeListener,
    discovered,
    mock_purifier_entry,
)


@pytest.fixture
async def entry(hass: HomeAssistant, fake_lan: FakeLan):
    entry = mock_purifier_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _entity_id(hass: HomeAssistant, platform: str, key: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(platform, DOMAIN, f"{DEVICE_ID}_{key}")
    assert entity_id, f"{platform} {key} not registered"
    return entity_id


def _state(hass: HomeAssistant, platform: str, key: str) -> str:
    return hass.states.get(_entity_id(hass, platform, key)).state


async def test_entities_and_device(hass: HomeAssistant, entry, fake_lan: FakeLan) -> None:
    assert entry.state is ConfigEntryState.LOADED
    assert fake_lan.device.started
    assert fake_lan.device.version_hint == "3.3"

    fan = hass.states.get(FAN)
    assert fan.state == STATE_ON
    assert fan.attributes[ATTR_PRESET_MODE] == "auto"
    assert fan.attributes[ATTR_PERCENTAGE] is None
    assert fan.attributes["preset_modes"] == ["auto", "sleep"]

    assert _state(hass, "sensor", "pm25") == "12"
    assert _state(hass, "sensor", "air_quality") == "good"
    assert _state(hass, "sensor", "filter_life") == "75"
    assert float(_state(hass, "sensor", "filter_remaining")) == pytest.approx(67.5)
    assert _state(hass, "sensor", "device_status") == "none"
    assert _state(hass, "binary_sensor", "filter_replacement") == STATE_OFF
    assert _state(hass, "binary_sensor", "filter_cover") == STATE_OFF
    assert _entity_id(hass, "button", "reset_filter")

    registry = dr.async_get(hass)
    (device,) = dr.async_entries_for_config_entry(registry, entry.entry_id)
    assert device.identifiers == {(DOMAIN, DEVICE_ID)}
    assert device.manufacturer == "Birdie"
    assert device.model == "Air Purifier"
    assert device.name == "Birdie Air Purifier"
    assert device.hw_version == "1.2"


async def test_pushed_changes_update_entities(
    hass: HomeAssistant, entry, fake_lan: FakeLan
) -> None:
    fake_lan.device.push({"2": 80, "21": "poor", "3": "manual3"})
    await hass.async_block_till_done()
    assert _state(hass, "sensor", "pm25") == "80"
    assert _state(hass, "sensor", "air_quality") == "poor"
    assert hass.states.get(FAN).attributes[ATTR_PERCENTAGE] == 75


async def test_fan_commands(hass: HomeAssistant, entry, fake_lan: FakeLan) -> None:
    async def call(service: str, **data: object) -> None:
        await hass.services.async_call(
            FAN_DOMAIN, service, {ATTR_ENTITY_ID: FAN, **data}, blocking=True
        )

    await call(SERVICE_SET_PERCENTAGE, percentage=50)
    assert fake_lan.sent[-1] == {"3": "manual2"}
    assert hass.states.get(FAN).attributes[ATTR_PERCENTAGE] == 50  # optimistic

    await call(SERVICE_SET_PRESET_MODE, preset_mode="sleep")
    assert fake_lan.sent[-1] == {"3": "sleep"}

    await call(SERVICE_TURN_OFF)
    assert fake_lan.sent[-1] == {"1": False}
    assert hass.states.get(FAN).state == STATE_OFF

    await call(SERVICE_TURN_ON, percentage=100)
    assert fake_lan.sent[-1] == {"1": True, "3": "manual4"}

    await call(SERVICE_TURN_OFF)
    await call(SERVICE_SET_PRESET_MODE, preset_mode="auto")
    assert fake_lan.sent[-1] == {"1": True, "3": "auto"}  # turns on with the preset

    await call(SERVICE_SET_PERCENTAGE, percentage=0)
    assert fake_lan.sent[-1] == {"1": False}

    with pytest.raises(ServiceValidationError):
        await call(SERVICE_SET_PRESET_MODE, preset_mode="turbo")


async def test_reset_filter_button(hass: HomeAssistant, entry, fake_lan: FakeLan) -> None:
    await hass.services.async_call(
        "button",
        SERVICE_PRESS,
        {ATTR_ENTITY_ID: _entity_id(hass, "button", "reset_filter")},
        blocking=True,
    )
    assert fake_lan.sent[-1] == {"108": True}


async def test_command_while_unreachable(hass: HomeAssistant, entry, fake_lan: FakeLan) -> None:
    fake_lan.command_error = DeviceUnreachableError("gone")
    with pytest.raises(HomeAssistantError, match="Cannot reach the air purifier"):
        await hass.services.async_call(
            FAN_DOMAIN, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: FAN}, blocking=True
        )


async def test_offline_and_back(hass: HomeAssistant, entry, fake_lan: FakeLan) -> None:
    device = fake_lan.device
    device.go_offline()
    await hass.async_block_till_done()
    assert hass.states.get(FAN).state == STATE_UNAVAILABLE
    assert _state(hass, "sensor", "pm25") == STATE_UNAVAILABLE

    fake_lan.dps["2"] = 30
    device.come_back()
    await hass.async_block_till_done()
    assert hass.states.get(FAN).state == STATE_ON
    assert _state(hass, "sensor", "pm25") == "30"


async def test_dps_not_reported_make_entities_unavailable(
    hass: HomeAssistant, fake_lan: FakeLan
) -> None:
    """Only switch and mode reported: the other entities stay unavailable."""
    fake_lan.dps = {"1": True, "3": "auto"}
    entry = mock_purifier_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get(FAN).state == STATE_ON
    assert _state(hass, "sensor", "pm25") == STATE_UNAVAILABLE
    assert _state(hass, "sensor", "filter_life") == STATE_UNAVAILABLE
    assert _state(hass, "binary_sensor", "filter_cover") == STATE_UNAVAILABLE

    fake_lan.device.push({"2": 5})
    await hass.async_block_till_done()
    assert _state(hass, "sensor", "pm25") == "5"


async def test_modes_need_the_mode_dp(hass: HomeAssistant, fake_lan: FakeLan) -> None:
    fake_lan.dps = {"1": True}
    entry = mock_purifier_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            FAN_DOMAIN,
            SERVICE_SET_PRESET_MODE,
            {ATTR_ENTITY_ID: FAN, "preset_mode": "sleep"},
            blocking=True,
        )
    await hass.services.async_call(
        FAN_DOMAIN, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: FAN}, blocking=True
    )
    assert fake_lan.sent == [{"1": False}]


async def test_unreachable_at_startup_retries(hass: HomeAssistant, fake_lan: FakeLan) -> None:
    fake_lan.connect_error = DeviceUnreachableError("no route")
    entry = mock_purifier_entry()
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert all(device.closed for device in fake_lan.devices)


async def test_wrong_key_at_startup_starts_reauth(hass: HomeAssistant, fake_lan: FakeLan) -> None:
    fake_lan.accepted_key = "another-key-0000"
    entry = mock_purifier_entry()
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    # A busy purifier fails the 3.4/3.5 handshake like a wrong key: retry first.
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)

    for _ in range(PURIFIER_KEY_FAILURES_BEFORE_REAUTH - 1):
        await hass.config_entries.async_reload(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == [SOURCE_REAUTH]
    assert all(device.closed for device in fake_lan.devices)


async def test_key_rejected_once_at_startup(hass: HomeAssistant, fake_lan: FakeLan) -> None:
    fake_lan.accepted_key = "another-key-0000"  # e.g. busy during the handshake
    entry = mock_purifier_entry()
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY

    fake_lan.accepted_key = LOCAL_KEY
    await hass.config_entries.async_reload(entry.entry_id)
    assert entry.state is ConfigEntryState.LOADED
    assert entry.entry_id not in hass.data[DATA_SETUP_KEY_FAILURES]


async def test_moved_purifier_is_found_at_startup(
    hass: HomeAssistant, fake_lan: FakeLan, lan_broadcasts: dict[str, DiscoveredDevice]
) -> None:
    """New address while Home Assistant was off: follow the announcement."""
    fake_lan.unreachable_hosts.add(HOST)
    lan_broadcasts[DEVICE_ID] = discovered(host="192.168.1.77")
    entry = mock_purifier_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.LOADED
    assert entry.data[CONF_HOST] == "192.168.1.77"


async def test_reauth_dismissed_when_the_key_works_again(
    hass: HomeAssistant, entry, fake_lan: FakeLan
) -> None:
    device = fake_lan.device
    device.go_offline()
    for _ in range(PURIFIER_KEY_FAILURES_BEFORE_REAUTH):
        device.fail_reconnect(InvalidLocalKeyError("busy during the handshake"))
    await hass.async_block_till_done()
    assert hass.config_entries.flow.async_progress_by_handler(DOMAIN)

    device.come_back()
    await hass.async_block_till_done()
    assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)


async def test_repeated_key_rejection_starts_reauth(
    hass: HomeAssistant, entry, fake_lan: FakeLan
) -> None:
    device = fake_lan.device
    device.go_offline()
    rejected = InvalidLocalKeyError("rejected")
    device.fail_reconnect(rejected)
    device.fail_reconnect(DeviceUnreachableError("busy"))  # resets the count
    device.fail_reconnect(rejected)
    device.fail_reconnect(rejected)
    await hass.async_block_till_done()
    assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)

    device.fail_reconnect(rejected)
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == [SOURCE_REAUTH]


async def test_detected_version_is_stored(hass: HomeAssistant, fake_lan: FakeLan) -> None:
    fake_lan.version = "3.5"
    entry = mock_purifier_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    assert entry.data[CONF_DETECTED_VERSION] == "3.5"


async def test_follows_new_ip_address(hass: HomeAssistant, entry, fake_lan: FakeLan) -> None:
    (listener,) = [instance for instance in FakeListener.instances if instance.started]
    listener.handle(discovered(host="192.168.1.77"))
    await hass.async_block_till_done()
    assert entry.data[CONF_HOST] == "192.168.1.77"
    assert fake_lan.device.config.host == "192.168.1.77"


async def test_new_purifier_is_offered(hass: HomeAssistant, entry, fake_lan: FakeLan) -> None:
    (listener,) = [instance for instance in FakeListener.instances if instance.started]
    listener.handle(discovered(device_id="bfnewdevice0000000000", host="192.168.1.90"))
    listener.handle(discovered(device_id="bfnewdevice0000000000", host="192.168.1.90"))
    listener.handle(discovered(device_id="bfsomeplug00000000000", product_key="keyxyz"))
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [(flow["step_id"], flow["context"]["unique_id"]) for flow in flows] == [
        ("purifier_key", "bfnewdevice0000000000")
    ]


async def test_unload_closes_connection_and_listener(
    hass: HomeAssistant, entry, fake_lan: FakeLan
) -> None:
    device = fake_lan.device
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert device.closed
    assert not any(instance.started for instance in FakeListener.instances)


async def test_two_purifiers_share_the_listener(hass: HomeAssistant, fake_lan: FakeLan) -> None:
    first = mock_purifier_entry()
    second = mock_purifier_entry(
        unique_id="bfseconddevice0000000",
        data={**first.data, "device_id": "bfseconddevice0000000", CONF_HOST: "192.168.1.41"},
    )
    for item in (first, second):
        item.add_to_hass(hass)
        assert await hass.config_entries.async_setup(item.entry_id)
    await hass.async_block_till_done()
    assert len([i for i in FakeListener.instances if i.started]) == 1
    assert len(hass.states.async_entity_ids(FAN_DOMAIN)) == 2

    assert await hass.config_entries.async_unload(first.entry_id)
    assert len([i for i in FakeListener.instances if i.started]) == 1
    assert await hass.config_entries.async_unload(second.entry_id)
    assert not any(i.started for i in FakeListener.instances)


async def test_diagnostics_redact_key_and_address(
    hass: HomeAssistant, hass_client: ClientSessionGenerator, entry, fake_lan: FakeLan
) -> None:
    assert await async_setup_component(hass, "diagnostics", {})
    diagnostics = await get_diagnostics_for_config_entry(hass, hass_client, entry)
    assert diagnostics["entry"]["local_key"] == "**REDACTED**"
    assert diagnostics["entry"]["host"] == "**REDACTED**"
    assert diagnostics["dps"]["105"] == 129600
    assert diagnostics["decoded"]["filter_life_percent"] == 75
    assert diagnostics["connection"]["protocol_version"] == "3.3"
    assert LOCAL_KEY not in str(diagnostics)
