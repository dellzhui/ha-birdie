"""Contract test: the Home Assistant bridge client against the real bridge app.

Home Assistant client -> bridge HTTP app -> TuyaCloudClient -> fake Tuya cloud.
Skipped when the integration sources are not next to the bridge (e.g. after
the bridge moves to its own repository).
"""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from pathlib import Path

import aiohttp
import pytest
from aiohttp.test_utils import TestServer

from birdie_bridge.app import create_app
from birdie_bridge.auth import DevTokenAuthenticator, account_id_for
from birdie_bridge.products import ProductCatalog
from birdie_bridge.service import BridgeService, device_id_for
from birdie_bridge.tuya import TuyaCloudClient

from .conftest import FakeClock
from .fake_tuya import FakeTuyaCloud

REPO_ROOT = Path(__file__).resolve().parents[2]
if (REPO_ROOT / "custom_components" / "birdie").is_dir():
    sys.path.insert(0, str(REPO_ROOT))
ha_bridge = pytest.importorskip("custom_components.birdie.cloud.bridge")
pedestal = pytest.importorskip("custom_components.birdie.cloud.pedestal")

TOKEN = "dev-token-contract"
UID = "synthuser0001"
TUYA_ID = "bfsynthetic0000001"
DEVICE_ID = device_id_for(TUYA_ID)


@pytest.fixture
async def ha_client(
    client: TuyaCloudClient, clock: FakeClock
) -> AsyncIterator[ha_bridge.BirdieBridgeClient]:
    service = BridgeService(client, ProductCatalog({"synthpid00000001": "pedestal"}), clock=clock)
    app = create_app(service, DevTokenAuthenticator({TOKEN: UID}), clock=clock)
    server = TestServer(app)
    await server.start_server()
    async with aiohttp.ClientSession() as session:
        yield ha_bridge.BirdieBridgeClient(
            session, str(server.make_url("/")), ha_bridge.StaticTokenProvider(TOKEN)
        )
    await server.close()


async def test_full_round_trip(
    ha_client: ha_bridge.BirdieBridgeClient, cloud: FakeTuyaCloud, clock: FakeClock
) -> None:
    account = await ha_client.async_get_account()
    assert account.id == account_id_for(UID)

    (device,) = await ha_client.async_list_devices()
    assert device.id == DEVICE_ID
    assert device.model == pedestal.MODEL_PEDESTAL
    assert device.hardware_version == "1.0.0"
    state = pedestal.PedestalState.from_api(device.state)
    assert state.pm25 == 12
    assert state.filter_life_percent == 75
    assert state.fault == "none"

    commands = [pedestal.set_power_command(True), pedestal.set_fan_speed_command(3)]
    await ha_client.async_send_commands(DEVICE_ID, commands)
    assert cloud.commands[-1] == (
        TUYA_ID,
        [{"code": "switch", "value": True}, {"code": "mode", "value": "manual3"}],
    )

    (device,) = await ha_client.async_list_devices()
    state = pedestal.PedestalState.from_api(device.state)
    assert (state.mode, state.fan_speed) == ("manual", 3)
    assert pedestal.apply_commands(state, commands) == state  # optimistic matches confirmed

    for command in (pedestal.set_mode_command("sleep"), pedestal.reset_filter_command()):
        await ha_client.async_send_commands(DEVICE_ID, [command])
    assert cloud.commands[-2:] == [
        (TUYA_ID, [{"code": "mode", "value": "sleep"}]),
        (TUYA_ID, [{"code": "reset_filter", "value": True}]),
    ]


async def test_errors_cross_the_contract(
    ha_client: ha_bridge.BirdieBridgeClient, cloud: FakeTuyaCloud, clock: FakeClock
) -> None:
    cloud.add_pedestal("bfoffline", online=False)
    with pytest.raises(ha_bridge.BridgeDeviceOfflineError):
        await ha_client.async_send_commands(
            device_id_for("bfoffline"), [pedestal.set_power_command(True)]
        )
    with pytest.raises(ha_bridge.BridgeDeviceNotFoundError):
        await ha_client.async_get_device(TUYA_ID)  # Tuya ids are not accepted
    with pytest.raises(ha_bridge.BridgeCommandRejectedError):
        await ha_client.async_send_commands(
            DEVICE_ID, [{"name": "set_fan_speed", "params": {"speed": 9}}]
        )
    cloud.force_error[f"/v1.0/users/{UID}/devices"] = (1199, "too frequent")
    clock.advance(11)  # let the bridge's status cache expire
    with pytest.raises(ha_bridge.BridgeRateLimitError) as info:
        await ha_client.async_list_devices()
    assert info.value.retry_after and info.value.retry_after > 0


async def test_bad_token_is_auth_error(client: TuyaCloudClient, clock: FakeClock) -> None:
    service = BridgeService(client, ProductCatalog({"synthpid00000001": "pedestal"}), clock=clock)
    server = TestServer(create_app(service, DevTokenAuthenticator({TOKEN: UID}), clock=clock))
    await server.start_server()
    try:
        async with aiohttp.ClientSession() as session:
            bad = ha_bridge.BirdieBridgeClient(
                session, str(server.make_url("/")), ha_bridge.StaticTokenProvider("wrong")
            )
            with pytest.raises(ha_bridge.BridgeAuthError):
                await bad.async_list_devices()
    finally:
        await server.close()
