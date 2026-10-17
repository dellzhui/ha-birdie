"""Tests for TuyaCloudClient against the in-process fake cloud."""

from __future__ import annotations

import logging

import aiohttp
import pytest

from birdie_bridge.tuya import (
    TuyaAuthError,
    TuyaCloudClient,
    TuyaCommandError,
    TuyaConnectionError,
    TuyaCredentials,
    TuyaDeviceOfflineError,
    TuyaPlanError,
    TuyaRateLimitError,
    resolve_endpoint,
)
from birdie_bridge.tuya.errors import TuyaApiError

from .conftest import FakeClock
from .fake_tuya import ACCESS_ID, ACCESS_SECRET, FakeTuyaCloud

DEVICE_ID = "bfsynthetic0000001"


async def test_token_is_fetched_once_and_reused(
    client: TuyaCloudClient, cloud: FakeTuyaCloud
) -> None:
    await client.async_get_device_status(DEVICE_ID)
    await client.async_get_device_status(DEVICE_ID)
    assert cloud.token_requests == 1


async def test_token_refreshed_before_expiry(
    client: TuyaCloudClient, cloud: FakeTuyaCloud, clock: FakeClock
) -> None:
    await client.async_get_device_status(DEVICE_ID)
    clock.advance(cloud.expire_time - 60)  # inside the refresh margin
    await client.async_get_device_status(DEVICE_ID)
    assert cloud.token_requests == 1
    assert cloud.refresh_requests == 1


async def test_failed_refresh_falls_back_to_new_token(
    client: TuyaCloudClient, cloud: FakeTuyaCloud, clock: FakeClock
) -> None:
    await client.async_get_device_status(DEVICE_ID)
    cloud.refresh_tokens.clear()
    clock.advance(cloud.expire_time)
    await client.async_get_device_status(DEVICE_ID)
    assert cloud.refresh_requests == 1
    assert cloud.token_requests == 2


async def test_server_side_token_invalidation_retries_once(
    client: TuyaCloudClient, cloud: FakeTuyaCloud
) -> None:
    await client.async_get_device_status(DEVICE_ID)
    cloud.expire_all_tokens()
    status = await client.async_get_device_status(DEVICE_ID)
    assert status
    assert cloud.token_requests == 2


async def test_concurrent_requests_share_one_token_fetch(
    client: TuyaCloudClient, cloud: FakeTuyaCloud
) -> None:
    import asyncio

    await asyncio.gather(*(client.async_get_device_status(DEVICE_ID) for _ in range(5)))
    assert cloud.token_requests == 1


async def test_send_commands_signed_body(client: TuyaCloudClient, cloud: FakeTuyaCloud) -> None:
    ok = await client.async_send_commands(DEVICE_ID, [{"code": "switch", "value": False}])
    assert ok is True
    assert cloud.commands == [(DEVICE_ID, [{"code": "switch", "value": False}])]


async def test_bad_secret_raises_auth_error(server, session: aiohttp.ClientSession) -> None:
    endpoint = str(server.make_url("")).rstrip("/")
    bad = TuyaCloudClient(session, TuyaCredentials(ACCESS_ID, "wrong-secret", endpoint))
    with pytest.raises(TuyaAuthError) as info:
        await bad.async_ensure_token()
    assert info.value.code == 1004


async def test_unknown_device_permission_denied(client: TuyaCloudClient) -> None:
    with pytest.raises(TuyaAuthError):
        await client.async_get_device("bfunknown")


async def test_offline_device_command(client: TuyaCloudClient, cloud: FakeTuyaCloud) -> None:
    cloud.add_pedestal("bfoffline", online=False)
    with pytest.raises(TuyaDeviceOfflineError):
        await client.async_send_commands("bfoffline", [{"code": "switch", "value": True}])


async def test_rate_limit_and_unknown_errors(client: TuyaCloudClient, cloud: FakeTuyaCloud) -> None:
    path = f"/v1.0/iot-03/devices/{DEVICE_ID}/status"
    cloud.force_error[path] = (1199, "your requests are too frequent")
    with pytest.raises(TuyaRateLimitError):
        await client.async_get_device_status(DEVICE_ID)
    cloud.force_error[path] = (99999, "something new")
    with pytest.raises(TuyaApiError) as info:
        await client.async_get_device_status(DEVICE_ID)
    assert type(info.value) is TuyaApiError
    assert info.value.tid == "tid-fail"


async def test_connection_error(session: aiohttp.ClientSession) -> None:
    creds = TuyaCredentials(ACCESS_ID, ACCESS_SECRET, "http://127.0.0.1:9")
    client = TuyaCloudClient(session, creds, timeout=2)
    with pytest.raises(TuyaConnectionError):
        await client.async_ensure_token()


@pytest.mark.parametrize("bad_id", ["../token", "a/b", "", "x" * 65, "id?x=1"])
async def test_rejects_path_injection(client: TuyaCloudClient, bad_id: str) -> None:
    with pytest.raises(ValueError):
        await client.async_get_device_status(bad_id)


async def test_secrets_never_logged(
    client: TuyaCloudClient, cloud: FakeTuyaCloud, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    await client.async_get_device(DEVICE_ID)
    cloud.expire_all_tokens()
    await client.async_get_device_status(DEVICE_ID)
    token = client.token
    assert token is not None
    text = caplog.text + repr(token) + repr(client._credentials)
    assert ACCESS_SECRET not in text
    assert token.access_token not in text
    assert token.refresh_token not in text


def test_resolve_endpoint() -> None:
    assert resolve_endpoint("eu") == "https://openapi.tuyaeu.com"
    assert resolve_endpoint("https://example.test/") == "https://example.test"
    with pytest.raises(ValueError):
        resolve_endpoint("mars")


async def test_plan_quota_error(client: TuyaCloudClient, cloud: FakeTuyaCloud) -> None:
    cloud.force_error["/v1.0/token"] = (28841004, "trial edition quota exhausted")
    with pytest.raises(TuyaPlanError):
        await client.async_ensure_token()


async def test_command_error(client: TuyaCloudClient) -> None:
    with pytest.raises(TuyaCommandError):
        await client.async_send_commands(DEVICE_ID, [{"code": "pm25", "value": 1}])


async def test_list_project_devices_paginates(
    client: TuyaCloudClient, cloud: FakeTuyaCloud
) -> None:
    for n in range(4):
        cloud.add_pedestal(f"bfextra{n}")
    devices = await client.async_list_project_devices(page_size=2)
    assert [d["id"] for d in devices] == sorted(cloud.devices)
    assert sum(r.endswith("/v2.0/cloud/thing/device") for r in cloud.request_log) == 3
    filtered = await client.async_list_project_devices(["otherpid"])
    assert filtered == []


async def test_batch_status(client: TuyaCloudClient, cloud: FakeTuyaCloud) -> None:
    cloud.add_pedestal("bfsecond")
    result = await client.async_get_devices_status([DEVICE_ID, "bfsecond"])
    assert {item["id"] for item in result} == {DEVICE_ID, "bfsecond"}
    with pytest.raises(ValueError):
        await client.async_get_devices_status([])
    with pytest.raises(ValueError):
        await client.async_get_devices_status([f"d{n}" for n in range(21)])
