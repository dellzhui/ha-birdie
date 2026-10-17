"""Tests for the Birdie Cloud Bridge HTTP client against a local fake bridge."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from custom_components.birdie.cloud.bridge import (
    BirdieBridgeClient,
    BridgeApiError,
    BridgeAuthError,
    BridgeCommandRejectedError,
    BridgeConnectionError,
    BridgeDeviceNotFoundError,
    BridgeDeviceOfflineError,
    BridgeRateLimitError,
    BridgeUnavailableError,
    StaticTokenProvider,
)

from ..helpers import ACCOUNT_ID, DEVICE_ID, TOKEN, load_fixture, pedestal_status

# These tests run a real local HTTP server; Home Assistant's test plugin blocks
# sockets by default.
pytestmark = pytest.mark.usefixtures("socket_enabled")


class FakeBridgeServer:
    """Minimal fake of the bridge HTTP API (docs/bridge-api.md)."""

    def __init__(self) -> None:
        self.valid_tokens = {TOKEN}
        self.requests: list[tuple[str, str, dict[str, str]]] = []
        self.commands: list[dict[str, Any]] = []
        self.force: dict[str, web.Response] = {}

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", self.handle)
        return app

    @staticmethod
    def error(status: int, code: str, **headers: str) -> web.Response:
        body = {"error": {"code": code, "message": code, "request_id": "req-1"}}
        return web.json_response(body, status=status, headers=headers)

    async def handle(self, request: web.Request) -> web.Response:
        self.requests.append((request.method, request.path_qs, dict(request.headers)))
        if request.path in self.force:
            return self.force[request.path]
        auth = request.headers.get("Authorization", "")
        if auth.removeprefix("Bearer ") not in self.valid_tokens:
            return self.error(401, "unauthorized")
        device = {
            "id": DEVICE_ID,
            "name": "Living Room Purifier",
            "model": "pedestal",
            "model_name": "Birdie Pedestal",
            "product_id": "synthpid00000001",
            "online": True,
        }
        path = request.path
        if path == "/v1/account":
            return web.json_response({"id": ACCOUNT_ID, "display_name": "jane@example.com"})
        if path == "/v1/devices":
            if request.query.get("include") == "status":
                device = device | {
                    "status": pedestal_status(),
                    "fetched_at": "2026-10-17T12:00:00Z",
                }
            return web.json_response({"devices": [device]})
        if path == f"/v1/devices/{DEVICE_ID}/specification":
            return web.json_response(load_fixture("pedestal_specification.json"))
        if path == f"/v1/devices/{DEVICE_ID}/commands" and request.method == "POST":
            self.commands.append(await request.json())
            return web.json_response({"accepted": True}, status=202)
        if path.startswith("/v1/devices/"):
            return self.error(404, "device_not_found")
        return self.error(400, "invalid_request")


@pytest.fixture
def bridge() -> FakeBridgeServer:
    return FakeBridgeServer()


@pytest.fixture
async def client(bridge: FakeBridgeServer) -> AsyncIterator[BirdieBridgeClient]:
    server = TestServer(bridge.app())
    await server.start_server()
    async with aiohttp.ClientSession() as session:
        yield BirdieBridgeClient(session, str(server.make_url("/")), StaticTokenProvider(TOKEN))
    await server.close()


async def test_account_and_devices(client: BirdieBridgeClient, bridge: FakeBridgeServer) -> None:
    account = await client.async_get_account()
    assert account.id == ACCOUNT_ID
    assert account.display_name == "jane@example.com"

    devices = await client.async_list_devices()
    assert len(devices) == 1
    device = devices[0]
    assert device.id == DEVICE_ID
    assert device.model == "pedestal"
    assert device.online is True
    assert device.status["pm25"] == 12
    assert bridge.requests[-1][1] == "/v1/devices?include=status"
    assert bridge.requests[-1][2]["Authorization"] == f"Bearer {TOKEN}"


async def test_list_without_status(client: BirdieBridgeClient, bridge: FakeBridgeServer) -> None:
    devices = await client.async_list_devices(include_status=False)
    assert devices[0].status == {}
    assert bridge.requests[-1][1] == "/v1/devices"


async def test_specification_and_commands(
    client: BirdieBridgeClient, bridge: FakeBridgeServer
) -> None:
    spec = await client.async_get_specification(DEVICE_ID)
    assert spec["category"] == "kj"
    await client.async_send_commands(DEVICE_ID, [{"code": "switch", "value": False}])
    assert bridge.commands == [{"commands": [{"code": "switch", "value": False}]}]


async def test_device_id_is_path_quoted(
    client: BirdieBridgeClient, bridge: FakeBridgeServer
) -> None:
    with pytest.raises(BridgeDeviceNotFoundError):
        await client.async_get_specification("../account")
    assert bridge.requests[-1][1] == "/v1/devices/..%2Faccount/specification"


async def test_unauthorized(client: BirdieBridgeClient, bridge: FakeBridgeServer) -> None:
    bridge.valid_tokens.clear()
    with pytest.raises(BridgeAuthError) as info:
        await client.async_get_account()
    assert info.value.status == 401
    assert TOKEN not in str(info.value)


async def test_token_refresh_retry(bridge: FakeBridgeServer) -> None:
    class RotatingTokens:
        def __init__(self) -> None:
            self.token = "expired"
            self.refreshes = 0

        async def async_get_access_token(self) -> str:
            return self.token

        async def async_handle_unauthorized(self) -> bool:
            self.refreshes += 1
            self.token = TOKEN
            return True

    tokens = RotatingTokens()
    server = TestServer(bridge.app())
    await server.start_server()
    try:
        async with aiohttp.ClientSession() as session:
            client = BirdieBridgeClient(session, str(server.make_url("/")), tokens)
            account = await client.async_get_account()
    finally:
        await server.close()
    assert account.id == ACCOUNT_ID
    assert tokens.refreshes == 1


@pytest.mark.parametrize(
    ("status", "code", "expected"),
    [
        (403, "account_disabled", BridgeAuthError),
        (404, "device_not_found", BridgeDeviceNotFoundError),
        (409, "device_offline", BridgeDeviceOfflineError),
        (422, "command_rejected", BridgeCommandRejectedError),
        (429, "rate_limited", BridgeRateLimitError),
        (502, "upstream_error", BridgeUnavailableError),
        (503, "upstream_unavailable", BridgeUnavailableError),
        (500, "internal_error", BridgeUnavailableError),
        (400, "invalid_request", BridgeApiError),
        (418, "something_new", BridgeApiError),
    ],
)
async def test_error_mapping(
    client: BirdieBridgeClient,
    bridge: FakeBridgeServer,
    status: int,
    code: str,
    expected: type[BridgeApiError],
) -> None:
    bridge.force["/v1/devices"] = FakeBridgeServer.error(status, code, **{"Retry-After": "30"})
    with pytest.raises(expected) as info:
        await client.async_list_devices()
    assert type(info.value) is expected
    assert info.value.code == code
    assert info.value.request_id == "req-1"
    assert info.value.retry_after == 30.0


async def test_error_without_json_body(
    client: BirdieBridgeClient, bridge: FakeBridgeServer
) -> None:
    bridge.force["/v1/devices"] = web.Response(status=503, text="<html>gateway</html>")
    with pytest.raises(BridgeUnavailableError) as info:
        await client.async_list_devices()
    assert info.value.code == "http_503"


async def test_invalid_json_success(client: BirdieBridgeClient, bridge: FakeBridgeServer) -> None:
    bridge.force["/v1/account"] = web.Response(status=200, text="not json")
    with pytest.raises(BridgeConnectionError):
        await client.async_get_account()


async def test_malformed_device_list(client: BirdieBridgeClient, bridge: FakeBridgeServer) -> None:
    bridge.force["/v1/devices"] = web.json_response({"items": []})
    with pytest.raises(BridgeConnectionError):
        await client.async_list_devices()


async def test_connection_error() -> None:
    async with aiohttp.ClientSession() as session:
        client = BirdieBridgeClient(
            session, "http://127.0.0.1:9", StaticTokenProvider(TOKEN), timeout=2
        )
        with pytest.raises(BridgeConnectionError):
            await client.async_get_account()


def test_static_token_provider_repr_hides_token() -> None:
    assert TOKEN not in repr(StaticTokenProvider(TOKEN))
