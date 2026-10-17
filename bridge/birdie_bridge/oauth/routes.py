"""aiohttp routes for the OAuth endpoints and the Birdie sign-in page."""

from __future__ import annotations

import html
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aiohttp import web

from .identity import SignInError
from .server import (
    REQUEST_TTL,
    AuthorizeError,
    OAuthServer,
    RequestNotFoundError,
    TokenError,
    UnavailableError,
)

STATIC_DIR = Path(__file__).parent / "static"
STATIC_FILES = {
    "signin.js": "text/javascript",
    "signin.css": "text/css",
}
COOKIE_PREFIX = "birdie_signin_"


@dataclass(frozen=True)
class OAuthContext:
    server: OAuthServer
    check_rate: Callable[[str], int | None]  # key -> seconds to wait, or None
    client_ip: Callable[[web.Request], str]


OAUTH_KEY = web.AppKey("oauth", OAuthContext)

PAGE_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}
NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}

_SIGN_IN_TEMPLATE = (STATIC_DIR / "signin.html").read_text(encoding="utf-8")
_ERROR_TEMPLATE = (STATIC_DIR / "error.html").read_text(encoding="utf-8")


def _is_https(request: web.Request) -> bool:
    return request.secure or request.headers.get("X-Forwarded-Proto", "") == "https"


def _page(request: web.Request, body: str, status: int = 200) -> web.Response:
    headers = dict(PAGE_HEADERS)
    if _is_https(request):
        headers["Strict-Transport-Security"] = "max-age=31536000"
    return web.Response(text=body, status=status, content_type="text/html", headers=headers)


def _render(template: str, values: Mapping[str, str]) -> str:
    for key, value in values.items():
        template = template.replace("{{" + key + "}}", html.escape(value, quote=True))
    return template


def _error_page(request: web.Request, message: str, status: int = 400) -> web.Response:
    return _page(request, _render(_ERROR_TEMPLATE, {"message": message}), status)


def _rate_limited(request: web.Request) -> int | None:
    context = request.app[OAUTH_KEY]
    return context.check_rate(f"ip:{context.client_ip(request)}")


def _json(data: Any, status: int = 200, headers: Mapping[str, str] | None = None) -> web.Response:
    return web.json_response(data, status=status, headers={**NO_STORE, **(headers or {})})


async def _json_body(request: web.Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        body = None
    return body if isinstance(body, dict) else {}


# ------------------------------------------------------------------ handlers


async def _authorize(request: web.Request) -> web.Response:
    if _rate_limited(request) is not None:
        return _error_page(request, "Too many attempts. Please wait a minute.", 429)
    server = request.app[OAUTH_KEY].server
    try:
        auth_request, binding = await server.start_authorization(dict(request.query))
    except AuthorizeError as err:
        if (url := err.redirect_url()) is not None:
            return web.Response(status=302, headers={"Location": url, **NO_STORE})
        return _error_page(request, "This sign-in link is not valid.")
    response = _page(
        request,
        _render(
            _SIGN_IN_TEMPLATE,
            {
                "request_id": auth_request.id,
                "client_name": server.client_name(auth_request.client_id),
                "method": server.sign_in_method,
            },
        ),
    )
    response.set_cookie(
        COOKIE_PREFIX + auth_request.id[:16],
        binding,
        max_age=REQUEST_TTL,
        path="/oauth",
        secure=_is_https(request),
        httponly=True,
        samesite="Strict",
    )
    return response


def _binding(request: web.Request, request_id: Any) -> str | None:
    if not isinstance(request_id, str):
        return None
    return request.cookies.get(COOKIE_PREFIX + request_id[:16])


_EXPIRED = "This sign-in has expired. Start again from Home Assistant."


async def _session(request: web.Request) -> web.Response:
    if (retry := _rate_limited(request)) is not None:
        return _json(
            {"error": "rate_limited", "message": "Too many attempts."},
            429,
            {"Retry-After": str(retry)},
        )
    body = await _json_body(request)
    request_id = body.get("request_id")
    credential = body.get("credential")
    try:
        identity = await request.app[OAUTH_KEY].server.sign_in(
            request_id,
            _binding(request, request_id),
            credential if isinstance(credential, dict) else {},
        )
    except SignInError as err:
        return _json({"error": "sign_in_failed", "message": str(err)}, 400)
    except RequestNotFoundError:
        return _json({"error": "request_expired", "message": _EXPIRED}, 400)
    except UnavailableError as err:
        return _json(
            {"error": "unavailable", "message": "Sign-in is temporarily unavailable."},
            503,
            {"Retry-After": str(err.retry_after)},
        )
    return _json({"next": "consent", "account": identity.email})


async def _consent(request: web.Request) -> web.Response:
    body = await _json_body(request)
    request_id = body.get("request_id")
    try:
        url = await request.app[OAUTH_KEY].server.finish(
            request_id, _binding(request, request_id), body.get("allow") is True
        )
    except RequestNotFoundError:
        return _json({"error": "request_expired", "message": _EXPIRED}, 400)
    response = _json({"redirect": url})
    if isinstance(request_id, str):
        response.del_cookie(COOKIE_PREFIX + request_id[:16], path="/oauth")
    return response


async def _token(request: web.Request) -> web.Response:
    if (retry := _rate_limited(request)) is not None:
        return _json({"error": "slow_down"}, 429, {"Retry-After": str(retry)})
    form = {key: str(value) for key, value in (await request.post()).items()}
    try:
        return _json(await request.app[OAUTH_KEY].server.token(form))
    except TokenError as err:
        return _json({"error": err.error, "error_description": err.description}, err.status)
    except UnavailableError as err:
        return _json(
            {"error": "temporarily_unavailable"}, 503, {"Retry-After": str(err.retry_after)}
        )


async def _revoke(request: web.Request) -> web.Response:
    form = {key: str(value) for key, value in (await request.post()).items()}
    await request.app[OAUTH_KEY].server.revoke(form)
    return web.Response(status=200, headers=NO_STORE)


async def _static(request: web.Request) -> web.Response:
    name = request.match_info["name"]
    content_type = STATIC_FILES.get(name)
    if content_type is None:
        raise web.HTTPNotFound()
    return web.Response(
        body=(STATIC_DIR / name).read_bytes(),
        content_type=content_type,
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "max-age=300"},
    )


# --------------------------------------------------------------------- setup


def client_ip_resolver(trusted_proxy_hops: int) -> Callable[[web.Request], str]:
    """Client IP for rate limiting; trusts that many proxies in X-Forwarded-For."""

    def resolve(request: web.Request) -> str:
        if trusted_proxy_hops > 0:
            hops = [part.strip() for part in request.headers.get("X-Forwarded-For", "").split(",")]
            hops = [hop for hop in hops if hop]
            if len(hops) >= trusted_proxy_hops:
                return hops[-trusted_proxy_hops]
        return request.remote or "unknown"

    return resolve


def setup_oauth_routes(
    app: web.Application,
    server: OAuthServer,
    check_rate: Callable[[str], int | None],
    *,
    trusted_proxy_hops: int = 0,
) -> None:
    app[OAUTH_KEY] = OAuthContext(server, check_rate, client_ip_resolver(trusted_proxy_hops))
    app.router.add_get("/oauth/authorize", _authorize)
    app.router.add_post("/oauth/session", _session)
    app.router.add_post("/oauth/consent", _consent)
    app.router.add_post("/oauth/token", _token)
    app.router.add_post("/oauth/revoke", _revoke)
    app.router.add_get("/oauth/static/{name}", _static)
