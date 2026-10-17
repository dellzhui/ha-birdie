"""Async client for the Tuya Cloud OpenAPI.

Handles request signing, access-token lifecycle (fetch, proactive refresh,
one transparent retry on token errors) and error mapping. It is deliberately
independent of Home Assistant so it can run in an integration, a dev tool or
a server-side component that holds the project credentials.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import aiohttp

from .errors import TuyaConnectionError, TuyaTokenError, api_error_from_response
from .signing import SIGN_METHOD, calculate_sign, string_to_sign

_LOGGER = logging.getLogger(__name__)

TOKEN_PATH = "/v1.0/token"
DEVICE_LIST_PATH = "/v2.0/cloud/thing/device"
MAX_BATCH_DEVICES = 20
# Refresh this many seconds before the token actually expires.
TOKEN_REFRESH_MARGIN = 120.0
DEFAULT_TIMEOUT = 15.0

_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@dataclass(frozen=True)
class TuyaCredentials:
    """Cloud project credentials. The secret is excluded from ``repr``."""

    access_id: str
    access_secret: str = field(repr=False)
    endpoint: str


@dataclass(frozen=True)
class TokenInfo:
    """An OpenAPI access token. Token values are excluded from ``repr``."""

    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    expires_at: float
    uid: str | None = None

    def expires_soon(self, now: float, margin: float = TOKEN_REFRESH_MARGIN) -> bool:
        return now >= self.expires_at - margin


def _check_id(value: str, what: str) -> str:
    """Reject identifiers that could alter the request path."""
    if not _ID_PATTERN.match(value):
        raise ValueError(f"Invalid {what}")
    return value


class TuyaCloudClient:
    """Minimal, typed wrapper around the Tuya Cloud OpenAPI."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        credentials: TuyaCredentials,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._session = session
        self._credentials = credentials
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._clock = clock
        self._token: TokenInfo | None = None
        self._token_lock = asyncio.Lock()

    @property
    def token(self) -> TokenInfo | None:
        return self._token

    # ------------------------------------------------------------------ token

    async def async_ensure_token(self) -> TokenInfo:
        """Return a valid token, fetching or refreshing it when needed."""
        async with self._token_lock:
            token = self._token
            now = self._clock()
            if token is not None and not token.expires_soon(now):
                return token
            if token is not None:
                try:
                    self._token = await self._refresh_token(token)
                    return self._token
                except TuyaTokenError:
                    _LOGGER.debug("Token refresh rejected; requesting a new token")
            self._token = await self._fetch_token()
            return self._token

    def invalidate_token(self) -> None:
        self._token = None

    async def _fetch_token(self) -> TokenInfo:
        result = await self._send("GET", TOKEN_PATH, params={"grant_type": 1}, token=None)
        return self._parse_token(result)

    async def _refresh_token(self, token: TokenInfo) -> TokenInfo:
        path = f"{TOKEN_PATH}/{token.refresh_token}"
        result = await self._send("GET", path, token=None)
        return self._parse_token(result)

    def _parse_token(self, result: Any) -> TokenInfo:
        try:
            return TokenInfo(
                access_token=str(result["access_token"]),
                refresh_token=str(result["refresh_token"]),
                expires_at=self._clock() + float(result["expire_time"]),
                uid=result.get("uid"),
            )
        except (KeyError, TypeError, ValueError) as err:
            raise TuyaConnectionError("Malformed token response") from err

    # ---------------------------------------------------------------- request

    async def async_request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        body: Any = None,
    ) -> Any:
        """Call a business API and return its ``result`` payload.

        A token error triggers a single retry with a freshly fetched token.
        """
        token = await self.async_ensure_token()
        try:
            return await self._send(method, path, params=params, body=body, token=token)
        except TuyaTokenError:
            _LOGGER.debug("Token rejected by %s %s; retrying with a new token", method, path)
            async with self._token_lock:
                if self._token is token:
                    self._token = None
            token = await self.async_ensure_token()
            return await self._send(method, path, params=params, body=body, token=token)

    async def _send(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        body: Any = None,
        token: TokenInfo | None,
    ) -> Any:
        creds = self._credentials
        payload = b"" if body is None else json.dumps(body, separators=(",", ":")).encode()
        t = str(int(self._clock() * 1000))
        nonce = uuid.uuid4().hex
        access_token = token.access_token if token else ""
        sign = calculate_sign(
            client_id=creds.access_id,
            secret=creds.access_secret,
            t=t,
            nonce=nonce,
            access_token=access_token,
            string_to_sign_value=string_to_sign(method, path, params, payload),
        )
        headers = {
            "client_id": creds.access_id,
            "sign": sign,
            "sign_method": SIGN_METHOD,
            "t": t,
            "nonce": nonce,
            "lang": "en",
        }
        if access_token:
            headers["access_token"] = access_token
        if payload:
            headers["Content-Type"] = "application/json"

        query = {key: str(value) for key, value in params.items()} if params else None
        try:
            async with self._session.request(
                method,
                f"{creds.endpoint}{path}",
                params=query,
                data=payload or None,
                headers=headers,
                timeout=self._timeout,
            ) as resp:
                if resp.status >= 400:
                    raise TuyaConnectionError(f"HTTP {resp.status} from {method} {path}")
                data = await resp.json(content_type=None)
        except TimeoutError as err:
            raise TuyaConnectionError(f"Timeout calling {method} {path}") from err
        except (aiohttp.ClientError, json.JSONDecodeError) as err:
            raise TuyaConnectionError(f"Error calling {method} {path}: {err}") from err

        if not isinstance(data, dict):
            raise TuyaConnectionError(f"Unexpected response from {method} {path}")
        if not data.get("success"):
            error = api_error_from_response(
                _as_int(data.get("code")), str(data.get("msg", "")), data.get("tid")
            )
            _LOGGER.debug("%s %s failed: %s", method, path, error)
            raise error
        return data.get("result")

    # ------------------------------------------------------------ device API

    async def async_get_device(self, device_id: str) -> dict[str, Any]:
        """Device details (``GET /v2.0/cloud/thing/{id}``): name, product_id, is_online...

        The raw result contains sensitive fields such as ``local_key``;
        callers must not log or persist it verbatim.
        """
        _check_id(device_id, "device id")
        result = await self.async_request("GET", f"/v2.0/cloud/thing/{device_id}")
        return dict(result or {})

    async def async_get_device_status(self, device_id: str) -> list[dict[str, Any]]:
        """Latest reported data points as ``[{"code": ..., "value": ...}]``."""
        _check_id(device_id, "device id")
        result = await self.async_request("GET", f"/v1.0/iot-03/devices/{device_id}/status")
        return list(result or [])

    async def async_get_devices_status(self, device_ids: list[str]) -> list[dict[str, Any]]:
        """Status of up to 20 devices in one call: ``[{"id": ..., "status": [...]}]``."""
        if not 0 < len(device_ids) <= MAX_BATCH_DEVICES:
            raise ValueError(f"Between 1 and {MAX_BATCH_DEVICES} device ids are required")
        ids = ",".join(_check_id(device_id, "device id") for device_id in device_ids)
        result = await self.async_request(
            "GET", "/v1.0/iot-03/devices/status", params={"device_ids": ids}
        )
        return list(result or [])

    async def async_get_device_specification(self, device_id: str) -> dict[str, Any]:
        """Instruction set and status set with data point types and ranges."""
        _check_id(device_id, "device id")
        result = await self.async_request("GET", f"/v1.0/iot-03/devices/{device_id}/specification")
        return dict(result or {})

    async def async_send_commands(self, device_id: str, commands: list[dict[str, Any]]) -> bool:
        """Send standard-instruction commands to a device."""
        _check_id(device_id, "device id")
        result = await self.async_request(
            "POST",
            f"/v1.0/iot-03/devices/{device_id}/commands",
            body={"commands": commands},
        )
        return bool(result)

    async def async_list_project_devices(
        self, product_ids: list[str] | None = None, *, page_size: int = MAX_BATCH_DEVICES
    ) -> list[dict[str, Any]]:
        """All devices visible to the cloud project (``GET /v2.0/cloud/thing/device``).

        Note: this endpoint returns camelCase keys (``productId``, ``isOnline``).
        """
        params: dict[str, Any] = {"page_size": page_size}
        if product_ids:
            params["product_ids"] = ",".join(_check_id(p, "product id") for p in product_ids)
        devices: list[dict[str, Any]] = []
        while True:
            page = list(await self.async_request("GET", DEVICE_LIST_PATH, params=params) or [])
            devices.extend(page)
            last_id = page[-1].get("id") if page else None
            if len(page) < page_size or not last_id or last_id == params.get("last_id"):
                return devices
            params["last_id"] = last_id

    async def async_get_user_devices(self, uid: str) -> list[dict[str, Any]]:
        """Devices bound to an app user (legacy API; user must be linked to the project)."""
        _check_id(uid, "user id")
        result = await self.async_request("GET", f"/v1.0/users/{uid}/devices")
        return list(result or [])


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
