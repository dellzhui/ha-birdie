"""Bridge business logic, independent of the HTTP framework.

Every operation is scoped to one ``Principal``: a device is visible only if
Tuya lists it among that user's devices and its product is a known Birdie
model. Results are shaped as described in docs/bridge-api.md and contain no
Tuya identifiers or data point codes: devices get opaque ids and their state
and commands are translated by the device's ``Model``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from .auth import Principal
from .model import CommandRejected, Model, WritableFunctions
from .products import ProductCatalog
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


def device_id_for(tuya_device_id: str) -> str:
    """Opaque, stable public device id; the Tuya device id is never returned."""
    return hashlib.sha256(f"birdie-device:{tuya_device_id}".encode()).hexdigest()[:24]


@dataclass(frozen=True)
class _Device:
    tuya_id: str
    model: Model
    record: Mapping[str, Any]  # raw Tuya record; never returned as is

    @property
    def status(self) -> dict[str, Any]:
        return {
            item["code"]: item.get("value")
            for item in self.record.get("status") or []
            if isinstance(item, dict) and isinstance(item.get("code"), str)
        }


@dataclass(frozen=True)
class _UserDevices:
    fetched_at: float  # monotonic, for cache expiry
    fetched_at_iso: str
    devices: Mapping[str, _Device]  # public id -> device


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
        self._specs: dict[str, tuple[float, WritableFunctions]] = {}

    # ---------------------------------------------------------- public API

    async def get_account(self, principal: Principal) -> dict[str, Any]:
        return {"id": principal.account_id, "display_name": principal.display_name}

    async def list_devices(self, principal: Principal, *, include_state: bool) -> dict[str, Any]:
        snapshot = await self._user_devices(principal)
        return {
            "devices": [
                _device_json(device_id, device, snapshot, include_state)
                for device_id, device in snapshot.devices.items()
            ]
        }

    async def get_device(self, principal: Principal, device_id: str) -> dict[str, Any]:
        snapshot = await self._user_devices(principal)
        device = self._owned(snapshot, device_id)
        return _device_json(device_id, device, snapshot, include_state=True)

    async def send_commands(
        self, principal: Principal, device_id: str, body: Any
    ) -> dict[str, Any]:
        commands = _parse_commands(body)
        device = self._owned(await self._user_devices(principal), device_id)
        functions = await self._writable_functions(device.tuya_id)
        try:
            tuya_commands = device.model.translate_commands(commands, functions)
        except CommandRejected as err:
            raise BridgeHttpError(422, "command_rejected", str(err)) from None
        try:
            await self._tuya.async_send_commands(device.tuya_id, tuya_commands)
        except TuyaError as err:
            raise map_tuya_error(err) from err
        self._users.pop(principal.tuya_uid, None)  # next read shows fresh state
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
            devices: dict[str, _Device] = {}
            for record in records:
                model = self._catalog.model_for(record.get("product_id"))
                tuya_id = record.get("id")
                if model and isinstance(tuya_id, str):
                    devices[device_id_for(tuya_id)] = _Device(tuya_id, model, record)
            snapshot = _UserDevices(
                fetched_at=self._clock(),
                fetched_at_iso=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                devices=devices,
            )
            self._users[uid] = snapshot
            return snapshot

    @staticmethod
    def _owned(snapshot: _UserDevices, device_id: str) -> _Device:
        try:
            return snapshot.devices[device_id]
        except KeyError:
            raise device_not_found() from None

    async def _writable_functions(self, tuya_id: str) -> WritableFunctions:
        """Writable DPs of a device and their decoded ``values``, cached."""
        cached = self._specs.get(tuya_id)
        if cached and self._clock() - cached[0] < SPEC_CACHE_SECONDS:
            return cached[1]
        try:
            raw = await self._tuya.async_get_device_specification(tuya_id)
        except TuyaError as err:
            raise map_tuya_error(err) from err
        functions = {
            item["code"]: _decode_values(item.get("values"))
            for item in raw.get("functions") or []
            if isinstance(item, dict) and isinstance(item.get("code"), str)
        }
        self._specs[tuya_id] = (self._clock(), functions)
        return functions


def _device_json(
    device_id: str, device: _Device, snapshot: _UserDevices, include_state: bool
) -> dict[str, Any]:
    # Built field by field: never pass Tuya ids, local_key, ip, lat/lon or uid through.
    raw = device.record
    status = device.status
    result: dict[str, Any] = {
        "id": device_id,
        "name": raw.get("name") or device.model.name,
        "model": device.model.key,
        "model_name": device.model.name,
        "online": bool(raw.get("online", raw.get("is_online"))),
        "hardware_version": device.model.hardware_version(status),
    }
    if include_state:
        result["state"] = device.model.decode_state(status)
        result["fetched_at"] = snapshot.fetched_at_iso
    return result


def _decode_values(values: Any) -> dict[str, Any]:
    """Decode Tuya's JSON-in-a-string ``values``."""
    if isinstance(values, str):
        try:
            values = json.loads(values) if values else {}
        except json.JSONDecodeError:
            values = {}
    return values if isinstance(values, dict) else {}


def _parse_commands(body: Any) -> list[dict[str, Any]]:
    commands = body.get("commands") if isinstance(body, dict) else None
    if not isinstance(commands, list) or not 1 <= len(commands) <= MAX_COMMANDS:
        raise BridgeHttpError(
            400, "invalid_request", f"'commands' must be a list of 1 to {MAX_COMMANDS} items"
        )
    parsed = []
    for command in commands:
        if not isinstance(command, dict) or not isinstance(command.get("name"), str):
            raise BridgeHttpError(400, "invalid_request", "Each command needs a string 'name'")
        params = command.get("params", {})
        if not isinstance(params, dict):
            raise BridgeHttpError(400, "invalid_request", "'params' must be an object")
        parsed.append({"name": command["name"], "params": params})
    return parsed
