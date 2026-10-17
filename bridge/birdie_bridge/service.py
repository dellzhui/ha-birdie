"""Bridge business logic, independent of the HTTP framework.

Every operation is scoped to one ``Principal``: a device is visible only if
Tuya lists it among that user's devices and its product is a known Birdie
model. Results are shaped as described in docs/bridge-api.md.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from .auth import Principal
from .products import Model, ProductCatalog
from .tuya import (
    TuyaApiError,
    TuyaAuthError,
    TuyaCommandError,
    TuyaConnectionError,
    TuyaDeviceOfflineError,
    TuyaError,
    TuyaPlanError,
    TuyaRateLimitError,
)

_LOGGER = logging.getLogger(__name__)

STATUS_CACHE_SECONDS = 10.0
SPEC_CACHE_SECONDS = 3600.0
MAX_COMMANDS = 5
RATE_LIMIT_RETRY_AFTER = 5


class TuyaBackend(Protocol):
    """The subset of ``TuyaCloudClient`` the bridge uses."""

    async def async_get_user_devices(self, uid: str) -> list[dict[str, Any]]: ...

    async def async_get_device_specification(self, device_id: str) -> dict[str, Any]: ...

    async def async_send_commands(self, device_id: str, commands: list[dict[str, Any]]) -> bool: ...


class BridgeHttpError(Exception):
    """An error to return to the client with the documented envelope."""

    def __init__(
        self, status: int, code: str, message: str, retry_after: int | None = None
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.retry_after = retry_after


def device_not_found() -> BridgeHttpError:
    return BridgeHttpError(404, "device_not_found", "Device not found")


def map_tuya_error(err: TuyaError) -> BridgeHttpError:
    """Translate a Tuya client error into the bridge's public error."""
    if isinstance(err, TuyaDeviceOfflineError):
        return BridgeHttpError(409, "device_offline", "The device is not connected to the cloud")
    if isinstance(err, TuyaCommandError):
        return BridgeHttpError(422, "command_rejected", "The device rejected the command")
    if isinstance(err, TuyaRateLimitError):
        return BridgeHttpError(
            429, "rate_limited", "Upstream rate limit", retry_after=RATE_LIMIT_RETRY_AFTER
        )
    if isinstance(err, TuyaPlanError | TuyaAuthError):
        # Operator problem (credentials, plan, quota); never expose details.
        _LOGGER.error("Tuya project problem, operator action needed: %s", err)
        return BridgeHttpError(
            503, "upstream_unavailable", "Cloud service unavailable", retry_after=300
        )
    if isinstance(err, TuyaConnectionError):
        return BridgeHttpError(503, "upstream_unavailable", "Cloud service unreachable", 30)
    if isinstance(err, TuyaApiError):
        _LOGGER.warning("Unexpected Tuya error: %s", err)
    return BridgeHttpError(502, "upstream_error", "Unexpected cloud error")


@dataclass(frozen=True)
class _UserDevices:
    fetched_at: float  # monotonic, for cache expiry
    fetched_at_iso: str
    devices: Mapping[str, tuple[Model, Mapping[str, Any]]]  # id -> (model, raw tuya record)


class BridgeService:
    """Implements the bridge API operations on top of Tuya Cloud."""

    def __init__(
        self,
        tuya: TuyaBackend,
        catalog: ProductCatalog,
        *,
        clock: Callable[[], float] = time.monotonic,
        status_ttl: float = STATUS_CACHE_SECONDS,
    ) -> None:
        self._tuya = tuya
        self._catalog = catalog
        self._clock = clock
        self._status_ttl = status_ttl
        self._users: dict[str, _UserDevices] = {}
        self._user_locks: dict[str, asyncio.Lock] = {}
        self._specs: dict[str, tuple[float, dict[str, Any]]] = {}

    # ---------------------------------------------------------- public API

    async def get_account(self, principal: Principal) -> dict[str, Any]:
        return {"id": principal.account_id, "display_name": principal.display_name}

    async def list_devices(self, principal: Principal, *, include_status: bool) -> dict[str, Any]:
        snapshot = await self._user_devices(principal)
        return {
            "devices": [
                self._device_json(device_id, model, raw, snapshot, include_status)
                for device_id, (model, raw) in snapshot.devices.items()
            ]
        }

    async def get_device(self, principal: Principal, device_id: str) -> dict[str, Any]:
        snapshot = await self._user_devices(principal)
        model, raw = self._owned(snapshot, device_id)
        return self._device_json(device_id, model, raw, snapshot, include_status=True)

    async def get_specification(self, principal: Principal, device_id: str) -> dict[str, Any]:
        self._owned(await self._user_devices(principal), device_id)
        return await self._specification(device_id)

    async def send_commands(
        self, principal: Principal, device_id: str, body: Any
    ) -> dict[str, Any]:
        commands = _parse_commands(body)
        model, _raw = self._owned(await self._user_devices(principal), device_id)
        spec = await self._specification(device_id)
        spec_values = {item["code"]: item.get("values") or {} for item in spec["functions"]}
        for command in commands:
            rule = model.commands.get(command["code"])
            if rule is None or command["code"] not in spec_values:
                raise BridgeHttpError(
                    422, "command_rejected", f"Command {command['code']!r} is not allowed"
                )
            problem = rule.check(command["value"], spec_values[command["code"]])
            if problem:
                raise BridgeHttpError(422, "command_rejected", f"{command['code']} {problem}")
        try:
            await self._tuya.async_send_commands(device_id, commands)
        except TuyaError as err:
            raise map_tuya_error(err) from err
        self._users.pop(principal.tuya_uid, None)  # next read shows fresh status
        return {"accepted": True}

    # ------------------------------------------------------------ internals

    async def _user_devices(self, principal: Principal) -> _UserDevices:
        uid = principal.tuya_uid
        lock = self._user_locks.setdefault(uid, asyncio.Lock())
        async with lock:
            cached = self._users.get(uid)
            if cached and self._clock() - cached.fetched_at < self._status_ttl:
                return cached
            try:
                records = await self._tuya.async_get_user_devices(uid)
            except TuyaError as err:
                raise map_tuya_error(err) from err
            devices: dict[str, tuple[Model, Mapping[str, Any]]] = {}
            for record in records:
                model = self._catalog.model_for(record.get("product_id"))
                device_id = record.get("id")
                if model and isinstance(device_id, str):
                    devices[device_id] = (model, record)
            snapshot = _UserDevices(
                fetched_at=self._clock(),
                fetched_at_iso=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                devices=devices,
            )
            self._users[uid] = snapshot
            return snapshot

    @staticmethod
    def _owned(snapshot: _UserDevices, device_id: str) -> tuple[Model, Mapping[str, Any]]:
        try:
            return snapshot.devices[device_id]
        except KeyError:
            raise device_not_found() from None

    async def _specification(self, device_id: str) -> dict[str, Any]:
        cached = self._specs.get(device_id)
        if cached and self._clock() - cached[0] < SPEC_CACHE_SECONDS:
            return cached[1]
        try:
            raw = await self._tuya.async_get_device_specification(device_id)
        except TuyaError as err:
            raise map_tuya_error(err) from err
        spec = {
            "category": raw.get("category"),
            "functions": [_spec_item(item) for item in raw.get("functions") or []],
            "status": [_spec_item(item) for item in raw.get("status") or []],
        }
        self._specs[device_id] = (self._clock(), spec)
        return spec

    @staticmethod
    def _device_json(
        device_id: str,
        model: Model,
        raw: Mapping[str, Any],
        snapshot: _UserDevices,
        include_status: bool,
    ) -> dict[str, Any]:
        # Whitelist fields: never pass local_key, ip, lat/lon or uid through.
        result: dict[str, Any] = {
            "id": device_id,
            "name": raw.get("name") or model.name,
            "model": model.key,
            "model_name": model.name,
            "product_id": raw.get("product_id"),
            "online": bool(raw.get("online", raw.get("is_online"))),
        }
        if include_status:
            result["status"] = {
                item["code"]: item.get("value")
                for item in raw.get("status") or []
                if isinstance(item, dict) and isinstance(item.get("code"), str)
            }
            result["fetched_at"] = snapshot.fetched_at_iso
        return result


def _spec_item(item: Mapping[str, Any]) -> dict[str, Any]:
    """Decode Tuya's JSON-in-a-string ``values`` into an object."""
    values: Any = item.get("values")
    if isinstance(values, str):
        try:
            values = json.loads(values) if values else {}
        except json.JSONDecodeError:
            values = {}
    return {
        "code": item.get("code"),
        "type": item.get("type"),
        "values": values if isinstance(values, dict) else {},
    }


def _parse_commands(body: Any) -> list[dict[str, Any]]:
    commands = body.get("commands") if isinstance(body, dict) else None
    if not isinstance(commands, list) or not 1 <= len(commands) <= MAX_COMMANDS:
        raise BridgeHttpError(
            400, "invalid_request", f"'commands' must be a list of 1 to {MAX_COMMANDS} items"
        )
    parsed = []
    for command in commands:
        if not isinstance(command, dict) or not isinstance(command.get("code"), str):
            raise BridgeHttpError(400, "invalid_request", "Each command needs a string 'code'")
        if "value" not in command:
            raise BridgeHttpError(400, "invalid_request", "Each command needs a 'value'")
        parsed.append({"code": command["code"], "value": command["value"]})
    return parsed
