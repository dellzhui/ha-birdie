"""HTTP-level tests of the bridge: bridge app -> TuyaCloudClient -> fake Tuya cloud."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from aiohttp.test_utils import TestClient, TestServer

from birdie_bridge.app import create_app
from birdie_bridge.auth import DevTokenAuthenticator, account_id_for
from birdie_bridge.products import ProductCatalog
from birdie_bridge.service import BridgeService
from birdie_bridge.tuya import TuyaCloudClient

from .conftest import FakeClock
from .fake_tuya import FakeTuyaCloud

ALICE = "dev-token-alice"
BOB = "dev-token-bob"
ALICE_UID = "synthuser0001"
BOB_UID = "synthuser0002"
DEVICE_ID = "bfsynthetic0000001"
USERS_PATH = f"/v1.0/users/{ALICE_UID}/devices"
SWITCH_OFF = {"code": "switch", "value": False}


def auth(token: str = ALICE) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def make_app(client: TuyaCloudClient, clock: FakeClock, **kwargs: Any):
    service = BridgeService(client, ProductCatalog({"synthpid00000001": "pedestal"}), clock=clock)
    authenticator = DevTokenAuthenticator({ALICE: ALICE_UID, BOB: BOB_UID})
    return create_app(service, authenticator, clock=clock, **kwargs)


@pytest.fixture
async def bridge(client: TuyaCloudClient, clock: FakeClock) -> AsyncIterator[TestClient]:
    async with TestClient(TestServer(make_app(client, clock))) as test_client:
        yield test_client


async def _error(resp: Any) -> dict[str, Any]:
    body = await resp.json()
    assert set(body) == {"error"}
    assert body["error"]["request_id"] == resp.headers["X-Request-ID"]
    return body["error"]


async def test_health_without_auth(bridge: TestClient) -> None:
    resp = await bridge.get("/v1/health")
    assert resp.status == 200
    assert await resp.json() == {"status": "ok"}
    assert resp.headers["X-Request-ID"]


@pytest.mark.parametrize(
    "headers", [{}, {"Authorization": "Bearer nope"}, {"Authorization": "Basic x"}]
)
async def test_unauthorized(bridge: TestClient, headers: dict[str, str]) -> None:
    resp = await bridge.get("/v1/devices", headers=headers)
    assert resp.status == 401
    assert (await _error(resp))["code"] == "unauthorized"


async def test_account(bridge: TestClient) -> None:
    resp = await bridge.get("/v1/account", headers=auth())
    assert resp.status == 200
    body = await resp.json()
    assert body == {"id": account_id_for(ALICE_UID), "display_name": None}
    assert ALICE_UID not in await resp.text()


async def test_list_devices_with_status(bridge: TestClient) -> None:
    resp = await bridge.get("/v1/devices?include=status", headers=auth())
    assert resp.status == 200
    text = await resp.text()
    for secret in ("SYNTHETIC-LOCAL-KEY", "203.0.113.10", ALICE_UID, "local_key"):
        assert secret not in text
    (device,) = (await resp.json())["devices"]
    assert device["id"] == DEVICE_ID
    assert device["model"] == "pedestal"
    assert device["model_name"] == "Birdie Pedestal"
    assert device["name"] == "Living Room Purifier"
    assert device["online"] is True
    assert device["status"]["pm25"] == 12
    assert device["status"]["mode"] == "auto"
    assert device["fetched_at"].endswith("Z")


async def test_list_devices_without_status(bridge: TestClient) -> None:
    resp = await bridge.get("/v1/devices", headers=auth())
    (device,) = (await resp.json())["devices"]
    assert "status" not in device


async def test_invalid_include(bridge: TestClient) -> None:
    resp = await bridge.get("/v1/devices?include=everything", headers=auth())
    assert resp.status == 400
    assert (await _error(resp))["code"] == "invalid_request"


async def test_unsupported_product_hidden(bridge: TestClient, cloud: FakeTuyaCloud) -> None:
    cloud.add_pedestal("bfotherproduct", product_id="unknownpid")
    resp = await bridge.get("/v1/devices", headers=auth())
    assert [d["id"] for d in (await resp.json())["devices"]] == [DEVICE_ID]
    resp = await bridge.get("/v1/devices/bfotherproduct", headers=auth())
    assert resp.status == 404


async def test_other_user_cannot_see_or_control(bridge: TestClient, cloud: FakeTuyaCloud) -> None:
    resp = await bridge.get("/v1/devices", headers=auth(BOB))
    assert (await resp.json())["devices"] == []
    for method, path, body in (
        ("GET", f"/v1/devices/{DEVICE_ID}", None),
        ("GET", f"/v1/devices/{DEVICE_ID}/specification", None),
        (
            "POST",
            f"/v1/devices/{DEVICE_ID}/commands",
            {"commands": [{"code": "switch", "value": False}]},
        ),
    ):
        resp = await bridge.request(method, path, json=body, headers=auth(BOB))
        assert resp.status == 404, path
        assert (await _error(resp))["code"] == "device_not_found"
    assert cloud.commands == []


async def test_get_device(bridge: TestClient) -> None:
    resp = await bridge.get(f"/v1/devices/{DEVICE_ID}", headers=auth())
    assert resp.status == 200
    body = await resp.json()
    assert body["status"]["filter_total_minutes"] == 129600


async def test_specification_values_decoded(bridge: TestClient) -> None:
    resp = await bridge.get(f"/v1/devices/{DEVICE_ID}/specification", headers=auth())
    assert resp.status == 200
    spec = await resp.json()
    mode = next(f for f in spec["functions"] if f["code"] == "mode")
    assert mode["values"]["range"][0] == "auto"
    assert spec["category"] == "kj"


async def test_command_accepted_and_cache_invalidated(
    bridge: TestClient, cloud: FakeTuyaCloud
) -> None:
    await bridge.get("/v1/devices?include=status", headers=auth())
    resp = await bridge.post(
        f"/v1/devices/{DEVICE_ID}/commands",
        json={"commands": [{"code": "switch", "value": False}, {"code": "mode", "value": "sleep"}]},
        headers=auth(),
    )
    assert resp.status == 202
    assert await resp.json() == {"accepted": True}
    assert cloud.commands == [
        (DEVICE_ID, [{"code": "switch", "value": False}, {"code": "mode", "value": "sleep"}])
    ]
    resp = await bridge.get("/v1/devices?include=status", headers=auth())
    status = (await resp.json())["devices"][0]["status"]
    assert status["switch"] is False
    assert status["mode"] == "sleep"


@pytest.mark.parametrize(
    ("commands", "status", "code"),
    [
        ([{"code": "pm25", "value": 1}], 422, "command_rejected"),
        ([{"code": "mode", "value": "turbo"}], 422, "command_rejected"),
        ([{"code": "switch", "value": "yes"}], 422, "command_rejected"),
        ([{"code": "reset_filter", "value": False}], 422, "command_rejected"),
        ([], 400, "invalid_request"),
        ([{"code": "switch", "value": True}] * 6, 400, "invalid_request"),
        ([{"code": "switch"}], 400, "invalid_request"),
        ("switch", 400, "invalid_request"),
    ],
)
async def test_command_validation(
    bridge: TestClient, cloud: FakeTuyaCloud, commands: Any, status: int, code: str
) -> None:
    resp = await bridge.post(
        f"/v1/devices/{DEVICE_ID}/commands", json={"commands": commands}, headers=auth()
    )
    assert resp.status == status
    assert (await _error(resp))["code"] == code
    assert cloud.commands == []


async def test_command_body_not_json(bridge: TestClient) -> None:
    resp = await bridge.post(f"/v1/devices/{DEVICE_ID}/commands", data=b"{not json", headers=auth())
    assert resp.status == 400


async def test_command_to_offline_device(bridge: TestClient, cloud: FakeTuyaCloud) -> None:
    cloud.add_pedestal("bfoffline", online=False)
    resp = await bridge.post(
        "/v1/devices/bfoffline/commands",
        json={"commands": [{"code": "switch", "value": True}]},
        headers=auth(),
    )
    assert resp.status == 409
    assert (await _error(resp))["code"] == "device_offline"


@pytest.mark.parametrize(
    ("tuya_code", "status", "code"),
    [
        (1199, 429, "rate_limited"),
        (28841004, 503, "upstream_unavailable"),
        (1106, 503, "upstream_unavailable"),
        (99999, 502, "upstream_error"),
    ],
)
async def test_tuya_errors_mapped(
    bridge: TestClient, cloud: FakeTuyaCloud, tuya_code: int, status: int, code: str
) -> None:
    cloud.force_error[USERS_PATH] = (tuya_code, "forced")
    resp = await bridge.get("/v1/devices", headers=auth())
    assert resp.status == status
    error = await _error(resp)
    assert error["code"] == code
    assert "forced" not in error["message"]  # upstream details are not exposed
    if status in (429, 503):
        assert int(resp.headers["Retry-After"]) > 0


async def test_status_cache(bridge: TestClient, cloud: FakeTuyaCloud, clock: FakeClock) -> None:
    def user_calls() -> int:
        return sum(entry == f"GET {USERS_PATH}" for entry in cloud.request_log)

    await bridge.get("/v1/devices?include=status", headers=auth())
    await bridge.get(f"/v1/devices/{DEVICE_ID}", headers=auth())
    assert user_calls() == 1
    clock.advance(11)
    await bridge.get("/v1/devices?include=status", headers=auth())
    assert user_calls() == 2


async def test_specification_cached(bridge: TestClient, cloud: FakeTuyaCloud) -> None:
    for _ in range(2):
        await bridge.get(f"/v1/devices/{DEVICE_ID}/specification", headers=auth())
        await bridge.post(
            f"/v1/devices/{DEVICE_ID}/commands",
            json={"commands": [{"code": "switch", "value": True}]},
            headers=auth(),
        )
    assert sum(entry.endswith("/specification") for entry in cloud.request_log) == 1


async def test_per_account_rate_limit(client: TuyaCloudClient, clock: FakeClock) -> None:
    async with TestClient(TestServer(make_app(client, clock, rate_limit=2))) as test_client:
        for _ in range(2):
            assert (await test_client.get("/v1/account", headers=auth())).status == 200
        resp = await test_client.get("/v1/account", headers=auth())
        assert resp.status == 429
        assert (await _error(resp))["code"] == "rate_limited"
        assert int(resp.headers["Retry-After"]) >= 1
        # Other accounts are not affected.
        assert (await test_client.get("/v1/account", headers=auth(BOB))).status == 200
        clock.advance(61)
        assert (await test_client.get("/v1/account", headers=auth())).status == 200


async def test_request_id_echo_and_unknown_endpoint(bridge: TestClient) -> None:
    resp = await bridge.get("/v1/nothing", headers={**auth(), "X-Request-ID": "abc-123"})
    assert resp.status == 404
    assert resp.headers["X-Request-ID"] == "abc-123"
    assert (await _error(resp))["code"] == "not_found"
    resp = await bridge.get("/v1/health", headers={"X-Request-ID": "bad id with spaces"})
    assert resp.headers["X-Request-ID"] != "bad id with spaces"
