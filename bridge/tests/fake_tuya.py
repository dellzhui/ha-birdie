"""In-process fake of the Tuya Cloud OpenAPI for tests.

The fake verifies every request signature with an implementation written
independently of ``birdie_bridge.tuya.signing`` so that signing bugs are caught
end to end. Behaviour is modelled on the documented API; it is not a
substitute for testing against the real cloud.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aiohttp import web

FIXTURES = Path(__file__).parent / "fixtures"

ACCESS_ID = "testaccessid0001"
ACCESS_SECRET = "testaccesssecret00000000000000001"


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


def _expected_sign(request: web.Request, body: bytes, token: str) -> str:
    query = "&".join(f"{k}={request.query[k]}" for k in sorted(request.query))
    url = request.path + (f"?{query}" if query else "")
    content_hash = hashlib.sha256(body).hexdigest()
    to_sign = f"{request.method}\n{content_hash}\n\n{url}"
    message = (
        request.headers.get("client_id", "")
        + token
        + request.headers.get("t", "")
        + request.headers.get("nonce", "")
        + to_sign
    )
    return hmac.new(ACCESS_SECRET.encode(), message.encode(), hashlib.sha256).hexdigest().upper()


@dataclass
class FakeDevice:
    details: dict[str, Any]
    status: list[dict[str, Any]]
    spec: dict[str, Any]

    @property
    def online(self) -> bool:
        return bool(self.details.get("is_online"))


@dataclass
class FakeTuyaCloud:
    expire_time: int = 7200
    devices: dict[str, FakeDevice] = field(default_factory=dict)
    valid_tokens: set[str] = field(default_factory=set)
    refresh_tokens: dict[str, str] = field(default_factory=dict)
    token_requests: int = 0
    refresh_requests: int = 0
    commands: list[tuple[str, list[dict[str, Any]]]] = field(default_factory=list)
    request_log: list[str] = field(default_factory=list)
    force_error: dict[str, tuple[int, str]] = field(default_factory=dict)

    def add_pedestal(self, device_id: str = "bfsynthetic0000001", online: bool = True) -> None:
        details = load_fixture("pedestal_device.json")
        details["id"] = device_id
        details["is_online"] = online
        self.devices[device_id] = FakeDevice(
            details=details,
            status=load_fixture("pedestal_status.json"),
            spec=load_fixture("pedestal_specification.json"),
        )

    def expire_all_tokens(self) -> None:
        self.valid_tokens.clear()

    # ----------------------------------------------------------- web app

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", self._handle)
        return app

    @staticmethod
    def _ok(result: Any) -> web.Response:
        return web.json_response({"success": True, "result": result, "t": 0, "tid": "tid-ok"})

    @staticmethod
    def _fail(code: int, msg: str) -> web.Response:
        return web.json_response(
            {"success": False, "code": code, "msg": msg, "t": 0, "tid": "tid-fail"}
        )

    def _issue_token(self) -> dict[str, Any]:
        access, refresh = secrets.token_hex(16), secrets.token_hex(16)
        self.valid_tokens.add(access)
        self.refresh_tokens[refresh] = access
        return {
            "access_token": access,
            "refresh_token": refresh,
            "expire_time": self.expire_time,
            "uid": "synthprojectuid",
        }

    async def _handle(self, request: web.Request) -> web.Response:
        body = await request.read()
        path = request.path
        self.request_log.append(f"{request.method} {path}")
        if request.headers.get("client_id") != ACCESS_ID:
            return self._fail(1005, "clientId is invalid")
        if request.headers.get("sign_method") != "HMAC-SHA256":
            return self._fail(1004, "sign invalid")

        is_token_api = path.startswith("/v1.0/token")
        token = "" if is_token_api else request.headers.get("access_token", "")
        if request.headers.get("sign") != _expected_sign(request, body, token):
            return self._fail(1004, "sign invalid")

        if path in self.force_error:
            code, msg = self.force_error[path]
            return self._fail(code, msg)

        if is_token_api:
            return self._handle_token(request)
        if token not in self.valid_tokens:
            return self._fail(1010, "token invalid")
        return self._handle_device(request, body)

    def _handle_token(self, request: web.Request) -> web.Response:
        parts = request.path.split("/")
        if request.path == "/v1.0/token" and request.query.get("grant_type") == "1":
            self.token_requests += 1
            return self._ok(self._issue_token())
        if len(parts) == 4:
            self.refresh_requests += 1
            old_access = self.refresh_tokens.pop(parts[3], None)
            if old_access is None:
                return self._fail(1010, "token invalid")
            self.valid_tokens.discard(old_access)
            return self._ok(self._issue_token())
        return self._fail(1108, "uri path invalid")

    def _handle_device(self, request: web.Request, body: bytes) -> web.Response:
        parts = request.path.strip("/").split("/")
        if request.path == "/v2.0/cloud/thing/device":
            return self._list_devices(request)
        if request.path == "/v1.0/iot-03/devices/status":
            ids = request.query.get("device_ids", "").split(",")
            return self._ok(
                [
                    {"id": i, "status": copy.deepcopy(self.devices[i].status)}
                    for i in ids
                    if i in self.devices
                ]
            )
        # /v2.0/cloud/thing/{id}
        if parts[:3] == ["v2.0", "cloud", "thing"] and len(parts) == 4:
            device = self.devices.get(parts[3])
            if device is None:
                return self._fail(1106, "permission deny")
            return self._ok(copy.deepcopy(device.details))
        # /v1.0/iot-03/devices/{id}/{action}
        if parts[:3] == ["v1.0", "iot-03", "devices"] and len(parts) == 5:
            device = self.devices.get(parts[3])
            if device is None:
                return self._fail(1106, "permission deny")
            action = parts[4]
            if action == "status" and request.method == "GET":
                return self._ok(copy.deepcopy(device.status))
            if action == "specification" and request.method == "GET":
                return self._ok(copy.deepcopy(device.spec))
            if action == "commands" and request.method == "POST":
                return self._apply_commands(parts[3], device, json.loads(body))
        return self._fail(1108, "uri path invalid")

    def _list_devices(self, request: web.Request) -> web.Response:
        """Paginated project device list with camelCase keys, like the real API."""
        page_size = int(request.query.get("page_size", "20"))
        wanted = set(filter(None, request.query.get("product_ids", "").split(",")))
        ids = sorted(self.devices)
        if "last_id" in request.query:
            ids = [i for i in ids if i > request.query["last_id"]]
        page = []
        for device_id in ids:
            d = self.devices[device_id].details
            if wanted and d["product_id"] not in wanted:
                continue
            page.append(
                {
                    "id": device_id,
                    "name": d["name"],
                    "customName": d.get("custom_name"),
                    "productId": d["product_id"],
                    "productName": d["product_name"],
                    "category": d["category"],
                    "isOnline": d["is_online"],
                    "localKey": d["local_key"],
                }
            )
            if len(page) == page_size:
                break
        return self._ok(page)

    def _apply_commands(
        self, device_id: str, device: FakeDevice, payload: dict[str, Any]
    ) -> web.Response:
        if not device.online:
            return self._fail(2001, "device is offline")
        commands = payload.get("commands") or []
        writable = {f["code"] for f in device.spec["functions"]}
        for command in commands:
            if command.get("code") not in writable:
                return self._fail(2008, "command or value not support")
        self.commands.append((device_id, commands))
        for command in commands:
            for item in device.status:
                if item["code"] == command["code"]:
                    item["value"] = command["value"]
            if command["code"] == "reset_filter":
                for item in device.status:
                    if item["code"] == "filter_used_minutes":
                        item["value"] = 0
        return self._ok(True)
