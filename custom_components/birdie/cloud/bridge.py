"""Client for the Birdie Cloud Bridge HTTP API (docs/bridge-api.md).

The bridge is the only cloud endpoint this integration talks to. It holds the
Tuya credentials server-side; this module never sees them. No Home Assistant
imports, so it can be unit-tested and reused on its own.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import quote

import aiohttp

_LOGGER = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 15.0
API_PREFIX = "/v1"


# ---------------------------------------------------------------- errors


class BridgeError(Exception):
    """Base class for bridge client errors."""


class BridgeConnectionError(BridgeError):
    """Network failure, timeout or a response that is not valid bridge JSON."""


class BridgeApiError(BridgeError):
    """The bridge returned an error response."""

    def __init__(
        self,
        status: int,
        code: str,
        message: str = "",
        request_id: str | None = None,
        retry_after: float | None = None,
    ) -> None:
        self.status = status
        self.code = code
        self.message = message
        self.request_id = request_id
        self.retry_after = retry_after
        detail = f"Bridge error {status} {code}"
        if message:
            detail += f": {message}"
        if request_id:
            detail += f" (request_id={request_id})"
        super().__init__(detail)


class BridgeAuthError(BridgeApiError):
    """The access token was rejected or the account may not use the bridge."""


class BridgeDeviceNotFoundError(BridgeApiError):
    """The device does not exist or does not belong to the account."""


class BridgeDeviceOfflineError(BridgeApiError):
    """A command was sent to an offline device."""


class BridgeCommandRejectedError(BridgeApiError):
    """The command was not allowed or rejected by the device."""


class BridgeRateLimitError(BridgeApiError):
    """Too many requests; retry after ``retry_after`` seconds."""


class BridgeUnavailableError(BridgeApiError):
    """The bridge or Tuya Cloud is temporarily unavailable."""


_ERRORS_BY_CODE: dict[str, type[BridgeApiError]] = {
    "unauthorized": BridgeAuthError,
    "account_disabled": BridgeAuthError,
    "device_not_found": BridgeDeviceNotFoundError,
    "device_offline": BridgeDeviceOfflineError,
    "command_rejected": BridgeCommandRejectedError,
    "rate_limited": BridgeRateLimitError,
    "upstream_error": BridgeUnavailableError,
    "upstream_unavailable": BridgeUnavailableError,
    "internal_error": BridgeUnavailableError,
}

_ERRORS_BY_STATUS: dict[int, type[BridgeApiError]] = {
    401: BridgeAuthError,
    403: BridgeAuthError,
    404: BridgeDeviceNotFoundError,
    409: BridgeDeviceOfflineError,
    422: BridgeCommandRejectedError,
    429: BridgeRateLimitError,
}


def error_from_response(status: int, body: Any, retry_after: float | None) -> BridgeApiError:
    """Build the most specific error from an error response."""
    error = body.get("error") if isinstance(body, dict) else None
    error = error if isinstance(error, dict) else {}
    code = str(error.get("code") or f"http_{status}")
    cls = _ERRORS_BY_CODE.get(code) or _ERRORS_BY_STATUS.get(status)
    if cls is None:
        cls = BridgeUnavailableError if status >= 500 else BridgeApiError
    return cls(
        status,
        code,
        str(error.get("message") or ""),
        error.get("request_id"),
        retry_after,
    )


# ------------------------------------------------------------------ auth


class TokenProvider(Protocol):
    """Supplies bearer tokens for bridge requests."""

    async def async_get_access_token(self) -> str:
        """Return a currently valid access token."""

    async def async_handle_unauthorized(self) -> bool:
        """React to a 401; return True if retrying with a new token may succeed."""


class StaticTokenProvider:
    """Fixed token, used for development tokens until sign-in is finalised."""

    def __init__(self, token: str) -> None:
        self._token = token

    def __repr__(self) -> str:
        return "StaticTokenProvider(<redacted>)"

    async def async_get_access_token(self) -> str:
        return self._token

    async def async_handle_unauthorized(self) -> bool:
        return False


# ---------------------------------------------------------------- models


@dataclass(frozen=True)
class BridgeAccount:
    """The signed-in Birdie account."""

    id: str
    display_name: str | None = None

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> BridgeAccount:
        try:
            account_id = str(data["id"])
        except KeyError as err:
            raise BridgeConnectionError("Malformed account response") from err
        name = data.get("display_name")
        return cls(id=account_id, display_name=str(name) if name else None)


@dataclass(frozen=True)
class BridgeDevice:
    """A device as returned by the bridge, optionally with its status."""

    id: str
    name: str
    model: str
    model_name: str
    product_id: str | None = None
    online: bool = False
    status: Mapping[str, Any] = field(default_factory=dict)
    fetched_at: str | None = None

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> BridgeDevice:
        try:
            device_id = str(data["id"])
            model = str(data["model"])
        except KeyError as err:
            raise BridgeConnectionError("Malformed device in bridge response") from err
        status = data.get("status")
        return cls(
            id=device_id,
            name=str(data.get("name") or data.get("model_name") or device_id),
            model=model,
            model_name=str(data.get("model_name") or model),
            product_id=data.get("product_id"),
            online=bool(data.get("online")),
            status=dict(status) if isinstance(status, dict) else {},
            fetched_at=data.get("fetched_at"),
        )


# ---------------------------------------------------------------- client


class BirdieBridgeClient:
    """Async client for one Birdie account on the bridge."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        base_url: str,
        token_provider: TokenProvider,
        *,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._tokens = token_provider
        self._timeout = aiohttp.ClientTimeout(total=timeout)

    async def async_get_account(self) -> BridgeAccount:
        return BridgeAccount.from_json(await self._request("GET", "/account"))

    async def async_list_devices(self, *, include_status: bool = True) -> list[BridgeDevice]:
        params = {"include": "status"} if include_status else None
        data = await self._request("GET", "/devices", params=params)
        devices = data.get("devices") if isinstance(data, dict) else None
        if not isinstance(devices, list):
            raise BridgeConnectionError("Malformed device list response")
        return [BridgeDevice.from_json(item) for item in devices if isinstance(item, dict)]

    async def async_get_device(self, device_id: str) -> BridgeDevice:
        data = await self._request("GET", f"/devices/{_segment(device_id)}")
        return BridgeDevice.from_json(data)

    async def async_get_specification(self, device_id: str) -> dict[str, Any]:
        data = await self._request("GET", f"/devices/{_segment(device_id)}/specification")
        return dict(data)

    async def async_send_commands(self, device_id: str, commands: list[dict[str, Any]]) -> None:
        await self._request(
            "POST",
            f"/devices/{_segment(device_id)}/commands",
            body={"commands": commands},
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        body: Any = None,
    ) -> Any:
        try:
            return await self._send(method, path, params, body)
        except BridgeAuthError as err:
            if err.code != "unauthorized" or not await self._tokens.async_handle_unauthorized():
                raise
            _LOGGER.debug("Retrying %s %s with a refreshed token", method, path)
            return await self._send(method, path, params, body)

    async def _send(
        self,
        method: str,
        path: str,
        params: Mapping[str, str] | None,
        body: Any,
    ) -> Any:
        token = await self._tokens.async_get_access_token()
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        url = f"{self._base_url}{API_PREFIX}{path}"
        try:
            async with self._session.request(
                method,
                url,
                params=params,
                json=body,
                headers=headers,
                timeout=self._timeout,
            ) as resp:
                text = await resp.text()
                data = _parse_json(text)
                if resp.status >= 400:
                    raise error_from_response(
                        resp.status, data, _retry_after(resp.headers.get("Retry-After"))
                    )
        except TimeoutError as err:
            raise BridgeConnectionError(f"Timeout calling {method} {path}") from err
        except aiohttp.ClientError as err:
            raise BridgeConnectionError(f"Error calling {method} {path}: {err}") from err
        if data is None:
            raise BridgeConnectionError(f"Invalid JSON from {method} {path}")
        return data


def _segment(value: str) -> str:
    """Quote a value for use as a single path segment."""
    if not value:
        raise ValueError("Empty path segment")
    return quote(value, safe="")


def _parse_json(text: str) -> Any:
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None
