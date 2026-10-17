"""aiohttp application exposing the Birdie Cloud Bridge API v1."""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any

from aiohttp import web

from .auth import AuthenticationError, Authenticator, Principal
from .service import BridgeHttpError, BridgeService

_LOGGER = logging.getLogger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

SERVICE_KEY = web.AppKey("service", BridgeService)
AUTH_KEY = web.AppKey("authenticator", Authenticator)

Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]


class RateLimiter:
    """Sliding-window request limit per key (account)."""

    def __init__(
        self, limit: int, window: float, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._limit = limit
        self._window = window
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}

    def check(self, key: str) -> int | None:
        """Record a hit; return seconds to wait if the limit is exceeded."""
        now = self._clock()
        hits = self._hits.setdefault(key, deque())
        while hits and now - hits[0] >= self._window:
            hits.popleft()
        if len(hits) >= self._limit:
            return max(1, int(self._window - (now - hits[0])) + 1)
        hits.append(now)
        return None


LIMITER_KEY = web.AppKey("rate_limiter", RateLimiter)


def _error_response(
    status: int, code: str, message: str, request_id: str, retry_after: int | None = None
) -> web.Response:
    headers = {"Retry-After": str(retry_after)} if retry_after is not None else None
    return web.json_response(
        {"error": {"code": code, "message": message, "request_id": request_id}},
        status=status,
        headers=headers,
    )


@web.middleware
async def _envelope_middleware(request: web.Request, handler: Handler) -> web.StreamResponse:
    """Attach a request id and turn every failure into the documented error body."""
    incoming = request.headers.get(REQUEST_ID_HEADER, "")
    request_id = incoming if _REQUEST_ID_PATTERN.match(incoming) else uuid.uuid4().hex[:16]
    request["request_id"] = request_id
    try:
        response = await handler(request)
    except BridgeHttpError as err:
        response = _error_response(err.status, err.code, err.message, request_id, err.retry_after)
    except web.HTTPNotFound:
        response = _error_response(404, "not_found", "Unknown endpoint", request_id)
    except web.HTTPMethodNotAllowed:
        response = _error_response(405, "method_not_allowed", "Method not allowed", request_id)
    except Exception:
        _LOGGER.exception("Unhandled error (request_id=%s)", request_id)
        response = _error_response(500, "internal_error", "Internal error", request_id)
    response.headers[REQUEST_ID_HEADER] = request_id
    return response


async def _principal(request: web.Request) -> Principal:
    """Authenticate the caller and apply the per-account rate limit."""
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise BridgeHttpError(401, "unauthorized", "Missing bearer token")
    try:
        principal = await request.app[AUTH_KEY].authenticate(token.strip())
    except AuthenticationError:
        raise BridgeHttpError(401, "unauthorized", "Invalid or expired token") from None
    retry_after = request.app[LIMITER_KEY].check(principal.account_id)
    if retry_after is not None:
        raise BridgeHttpError(429, "rate_limited", "Too many requests", retry_after)
    return principal


def _json(data: Any, status: int = 200) -> web.Response:
    return web.json_response(data, status=status)


async def _health(request: web.Request) -> web.Response:
    return _json({"status": "ok"})


async def _account(request: web.Request) -> web.Response:
    principal = await _principal(request)
    return _json(await request.app[SERVICE_KEY].get_account(principal))


async def _devices(request: web.Request) -> web.Response:
    principal = await _principal(request)
    include = request.query.get("include", "")
    if include not in ("", "state"):
        raise BridgeHttpError(400, "invalid_request", "include must be 'state'")
    service = request.app[SERVICE_KEY]
    return _json(await service.list_devices(principal, include_state=include == "state"))


async def _device(request: web.Request) -> web.Response:
    principal = await _principal(request)
    device_id = request.match_info["device_id"]
    return _json(await request.app[SERVICE_KEY].get_device(principal, device_id))


async def _commands(request: web.Request) -> web.Response:
    principal = await _principal(request)
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise BridgeHttpError(400, "invalid_request", "Body must be JSON") from None
    device_id = request.match_info["device_id"]
    result = await request.app[SERVICE_KEY].send_commands(principal, device_id, body)
    return _json(result, status=202)


def create_app(
    service: BridgeService,
    authenticator: Authenticator,
    *,
    rate_limit: int = 60,
    rate_window: float = 60.0,
    clock: Callable[[], float] = time.monotonic,
) -> web.Application:
    """Build the bridge web application."""
    app = web.Application(middlewares=[_envelope_middleware], client_max_size=64 * 1024)
    app[SERVICE_KEY] = service
    app[AUTH_KEY] = authenticator
    app[LIMITER_KEY] = RateLimiter(rate_limit, rate_window, clock)
    app.router.add_get("/v1/health", _health)
    app.router.add_get("/v1/account", _account)
    app.router.add_get("/v1/devices", _devices)
    app.router.add_get("/v1/devices/{device_id}", _device)
    app.router.add_post("/v1/devices/{device_id}/commands", _commands)
    return app
