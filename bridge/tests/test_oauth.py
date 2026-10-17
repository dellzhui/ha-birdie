"""OAuth 2.0 + PKCE sign-in: browser flow, token endpoint and /v1 access."""

from __future__ import annotations

import re
import secrets
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from aiohttp.test_utils import TestClient, TestServer

from birdie_bridge.app import create_app
from birdie_bridge.auth import account_id_for
from birdie_bridge.oauth import (
    DevIdentityProvider,
    IdentityUnavailableError,
    MemoryOAuthStore,
    OAuthServer,
    parse_dev_users,
)
from birdie_bridge.oauth.routes import client_ip_resolver
from birdie_bridge.oauth.server import s256
from birdie_bridge.products import ProductCatalog
from birdie_bridge.service import BridgeService
from birdie_bridge.tuya import TuyaCloudClient

from .conftest import FakeClock

REDIRECT = "https://my.home-assistant.io/redirect/oauth"
CLIENT_ID = "home-assistant"
ALICE = "alice@example.com"
PASSWORD = "correct horse"
ALICE_UID = "synthuser0001"


class Env:
    def __init__(self, http: TestClient, identity: DevIdentityProvider, clock: FakeClock):
        self.http = http
        self.identity = identity
        self.clock = clock
        self.verifier = secrets.token_urlsafe(64)

    def authorize_params(self, **overrides: str) -> dict[str, str]:
        params = {
            "response_type": "code",
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT,
            "state": "ha-signed-state",
            "code_challenge": s256(self.verifier),
            "code_challenge_method": "S256",
        }
        params.update(overrides)
        return {key: value for key, value in params.items() if value != ""}

    async def open_page(self, **overrides: str) -> str:
        resp = await self.http.get(
            "/oauth/authorize?" + urlencode(self.authorize_params(**overrides)),
            allow_redirects=False,
        )
        assert resp.status == 200, await resp.text()
        match = re.search(r'data-request-id="([^"]+)"', await resp.text())
        assert match
        return match.group(1)

    async def sign_in(self, request_id: str, email: str = ALICE, password: str = PASSWORD) -> Any:
        return await self.http.post(
            "/oauth/session",
            json={"request_id": request_id, "credential": {"email": email, "password": password}},
        )

    async def consent(self, request_id: str, allow: bool = True) -> dict[str, list[str]]:
        resp = await self.http.post(
            "/oauth/consent", json={"request_id": request_id, "allow": allow}
        )
        assert resp.status == 200, await resp.text()
        url = (await resp.json())["redirect"]
        assert url.startswith(REDIRECT + "?")
        return parse_qs(urlsplit(url).query)

    async def code(self) -> str:
        request_id = await self.open_page()
        assert (await self.sign_in(request_id)).status == 200
        query = await self.consent(request_id)
        assert query["state"] == ["ha-signed-state"]
        return query["code"][0]

    async def token(self, **form: str) -> Any:
        return await self.http.post("/oauth/token", data={"client_id": CLIENT_ID, **form})

    async def exchange(self, code: str, /, **overrides: str) -> Any:
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT,
            "code_verifier": self.verifier,
        }
        form.update(overrides)
        return await self.token(**form)

    async def tokens(self) -> dict[str, Any]:
        resp = await self.exchange(await self.code())
        assert resp.status == 200, await resp.text()
        return dict(await resp.json())

    async def refresh(self, refresh_token: str) -> Any:
        return await self.token(grant_type="refresh_token", refresh_token=refresh_token)

    async def api(self, access_token: str, path: str = "/v1/account") -> Any:
        return await self.http.get(path, headers={"Authorization": f"Bearer {access_token}"})


class FlakyIdentity(DevIdentityProvider):
    down = False

    async def refresh(self, user_id: str, signed_in_at: float) -> str:
        if self.down:
            raise IdentityUnavailableError
        return await super().refresh(user_id, signed_in_at)


@pytest.fixture
async def env(client: TuyaCloudClient, clock: FakeClock) -> AsyncIterator[Env]:
    async with RunningEnv(client, clock) as value:
        yield value


class RunningEnv:
    """Bridge with OAuth in front of the fake Tuya cloud; async context manager."""

    def __init__(self, client: TuyaCloudClient, clock: FakeClock, **app_kwargs: Any) -> None:
        identity = FlakyIdentity({ALICE: (PASSWORD, ALICE_UID)}, clock=clock)
        oauth = OAuthServer(MemoryOAuthStore(clock=clock), identity, clock=clock)
        service = BridgeService(
            client, ProductCatalog({"synthpid00000001": "pedestal"}), clock=clock
        )
        app = create_app(service, oauth, clock=clock, oauth=oauth, **app_kwargs)
        self.http = TestClient(TestServer(app))
        self.env = Env(self.http, identity, clock)

    async def __aenter__(self) -> Env:
        await self.http.start_server()
        return self.env

    async def __aexit__(self, *exc: object) -> None:
        await self.http.close()


# ------------------------------------------------------------------ browser


async def test_full_flow_gives_access_to_own_devices(env: Env) -> None:
    tokens = await env.tokens()
    assert tokens["token_type"] == "Bearer"
    assert tokens["expires_in"] == 3600
    assert tokens["access_token"].startswith("bat_")
    assert tokens["refresh_token"].startswith("brt_")

    resp = await env.api(tokens["access_token"])
    assert resp.status == 200
    assert await resp.json() == {"id": account_id_for(f"dev:{ALICE}"), "display_name": ALICE}
    resp = await env.api(tokens["access_token"], "/v1/devices?include=state")
    assert len((await resp.json())["devices"]) == 1


async def test_sign_in_page_is_hardened(env: Env) -> None:
    resp = await env.http.get(
        "/oauth/authorize?" + urlencode(env.authorize_params()), allow_redirects=False
    )
    csp = resp.headers["Content-Security-Policy"]
    assert "frame-ancestors 'none'" in csp
    assert "script-src 'self'" in csp
    assert resp.headers["Referrer-Policy"] == "no-referrer"
    assert resp.headers["Cache-Control"] == "no-store"
    cookie = resp.headers["Set-Cookie"]
    assert "HttpOnly" in cookie
    assert "SameSite=Strict" in cookie
    assert "Path=/oauth" in cookie
    assert 'data-method="dev_password"' in await resp.text()

    js = await env.http.get("/oauth/static/signin.js")
    assert js.status == 200
    assert js.content_type == "text/javascript"
    assert (await env.http.get("/oauth/static/..%2Fserver.py")).status == 404


@pytest.mark.parametrize(
    "overrides",
    [
        {"client_id": "someone-else"},
        {"redirect_uri": "https://attacker.example/callback"},
        {"redirect_uri": "http://homeassistant.local:8123/auth/external/callback"},
    ],
)
async def test_untrusted_client_or_redirect_gets_no_redirect(
    env: Env, overrides: dict[str, str]
) -> None:
    resp = await env.http.get(
        "/oauth/authorize?" + urlencode(env.authorize_params(**overrides)),
        allow_redirects=False,
    )
    assert resp.status == 400
    assert "Location" not in resp.headers


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"response_type": "token"}, "unsupported_response_type"),
        ({"code_challenge_method": "plain"}, "invalid_request"),
        ({"code_challenge_method": ""}, "invalid_request"),
        ({"code_challenge": "short"}, "invalid_request"),
    ],
)
async def test_invalid_request_redirects_with_error(
    env: Env, overrides: dict[str, str], error: str
) -> None:
    resp = await env.http.get(
        "/oauth/authorize?" + urlencode(env.authorize_params(**overrides)),
        allow_redirects=False,
    )
    assert resp.status == 302
    query = parse_qs(urlsplit(resp.headers["Location"]).query)
    assert query["error"] == [error]
    assert query["state"] == ["ha-signed-state"]


async def test_wrong_password_can_retry(env: Env) -> None:
    request_id = await env.open_page()
    resp = await env.sign_in(request_id, password="wrong")
    assert resp.status == 400
    assert (await resp.json())["error"] == "sign_in_failed"
    resp = await env.sign_in(request_id)
    assert resp.status == 200
    assert (await resp.json())["account"] == ALICE


async def test_request_bound_to_browser(env: Env) -> None:
    request_id = await env.open_page()
    env.http.session.cookie_jar.clear()
    resp = await env.sign_in(request_id)
    assert resp.status == 400
    assert (await resp.json())["error"] == "request_expired"


async def test_consent_requires_sign_in(env: Env) -> None:
    request_id = await env.open_page()
    resp = await env.http.post("/oauth/consent", json={"request_id": request_id, "allow": True})
    assert resp.status == 400


async def test_cancel_returns_access_denied(env: Env) -> None:
    request_id = await env.open_page()
    await env.sign_in(request_id)
    query = await env.consent(request_id, allow=False)
    assert query == {"error": ["access_denied"], "state": ["ha-signed-state"]}


async def test_request_expires(env: Env) -> None:
    request_id = await env.open_page()
    env.clock.advance(601)
    assert (await env.sign_in(request_id)).status == 400


# ------------------------------------------------------------ code exchange


@pytest.mark.parametrize(
    "overrides",
    [
        {"code_verifier": secrets.token_urlsafe(64)},
        {"code_verifier": "too-short"},
        {"redirect_uri": "https://attacker.example/callback"},
        {"code": "bac_unknown"},
    ],
)
async def test_code_exchange_rejected(env: Env, overrides: dict[str, str]) -> None:
    resp = await env.exchange(await env.code(), **overrides)
    assert resp.status == 400
    assert (await resp.json())["error"] == "invalid_grant"
    assert resp.headers["Cache-Control"] == "no-store"


async def test_code_expires(env: Env) -> None:
    code = await env.code()
    env.clock.advance(61)
    assert (await env.exchange(code)).status == 400


async def test_code_reuse_revokes_grant(env: Env) -> None:
    code = await env.code()
    tokens = await (await env.exchange(code)).json()
    resp = await env.exchange(code)
    assert (await resp.json())["error"] == "invalid_grant"
    assert (await env.api(tokens["access_token"])).status == 401
    assert (await env.refresh(tokens["refresh_token"])).status == 400


async def test_unknown_client_and_grant_type(env: Env) -> None:
    resp = await env.http.post(
        "/oauth/token", data={"client_id": "x", "grant_type": "authorization_code"}
    )
    assert resp.status == 401
    assert (await resp.json())["error"] == "invalid_client"
    resp = await env.token(grant_type="password")
    assert resp.status == 400
    assert (await resp.json())["error"] == "unsupported_grant_type"


# ------------------------------------------------------------------ refresh


async def test_refresh_rotates(env: Env) -> None:
    first = await env.tokens()
    resp = await env.refresh(first["refresh_token"])
    assert resp.status == 200
    second = await resp.json()
    assert second["refresh_token"] != first["refresh_token"]
    assert (await env.api(second["access_token"])).status == 200
    resp = await env.refresh(second["refresh_token"])
    assert resp.status == 200


async def test_previous_refresh_token_grace_then_reuse_detection(env: Env) -> None:
    first = await env.tokens()
    second = await (await env.refresh(first["refresh_token"])).json()
    # Lost response: the previous token still works shortly after rotation.
    resp = await env.refresh(first["refresh_token"])
    assert resp.status == 200
    third = await resp.json()
    assert (await env.refresh(second["refresh_token"])).status == 400  # superseded

    env.clock.advance(121)
    resp = await env.refresh(first["refresh_token"])
    assert (await resp.json())["error"] == "invalid_grant"
    # Reuse after the grace period revokes the whole grant.
    assert (await env.refresh(third["refresh_token"])).status == 400
    assert (await env.api(third["access_token"])).status == 401


async def test_password_change_and_disable_end_the_grant(env: Env) -> None:
    tokens = await env.tokens()
    env.clock.advance(10)
    env.identity.sign_out_everywhere(ALICE)
    resp = await env.refresh(tokens["refresh_token"])
    assert resp.status == 400
    assert (await resp.json())["error"] == "invalid_grant"
    assert (await env.api(tokens["access_token"])).status == 401

    env.clock.advance(10)
    tokens = await env.tokens()  # signing in again works
    env.identity.disable(ALICE)
    assert (await env.refresh(tokens["refresh_token"])).status == 400
    request_id = await env.open_page()
    assert (await env.sign_in(request_id)).status == 400


async def test_identity_outage_is_transient(env: Env) -> None:
    tokens = await env.tokens()
    env.identity.down = True  # type: ignore[attr-defined]
    resp = await env.refresh(tokens["refresh_token"])
    assert resp.status == 503  # never 4xx: Home Assistant would ask to sign in again
    assert int(resp.headers["Retry-After"]) > 0
    env.identity.down = False  # type: ignore[attr-defined]
    assert (await env.refresh(tokens["refresh_token"])).status == 200


async def test_refresh_idle_expiry(env: Env) -> None:
    tokens = await env.tokens()
    env.clock.advance(91 * 24 * 3600)
    assert (await env.refresh(tokens["refresh_token"])).status == 400


async def test_access_token_expires(env: Env) -> None:
    tokens = await env.tokens()
    env.clock.advance(3601)
    resp = await env.api(tokens["access_token"])
    assert resp.status == 401
    assert (await resp.json())["error"]["code"] == "unauthorized"


@pytest.mark.parametrize("which", ["refresh_token", "access_token"])
async def test_revoke(env: Env, which: str) -> None:
    tokens = await env.tokens()
    resp = await env.http.post(
        "/oauth/revoke", data={"token": tokens[which], "client_id": CLIENT_ID}
    )
    assert resp.status == 200
    assert (await env.api(tokens["access_token"])).status == 401
    assert (await env.refresh(tokens["refresh_token"])).status == 400


async def test_revoke_unknown_token_is_ok(env: Env) -> None:
    for token in ("", "brt_nothing.here", "bat_nope", "random"):
        resp = await env.http.post("/oauth/revoke", data={"token": token, "client_id": CLIENT_ID})
        assert resp.status == 200


# ------------------------------------------------------------------ limits


async def test_oauth_rate_limit_per_ip(client: TuyaCloudClient, clock: FakeClock) -> None:
    async with RunningEnv(client, clock, oauth_rate_limit=2) as env:
        for _ in range(2):
            await env.open_page()
        resp = await env.http.get(
            "/oauth/authorize?" + urlencode(env.authorize_params()), allow_redirects=False
        )
        assert resp.status == 429
        resp = await env.token(grant_type="refresh_token", refresh_token="brt_x.y")
        assert resp.status == 429


def test_client_ip_resolver() -> None:
    class Request:
        def __init__(self, xff: str) -> None:
            self.headers = {"X-Forwarded-For": xff}
            self.remote = "10.0.0.1"

    direct = client_ip_resolver(0)
    behind_one = client_ip_resolver(1)
    assert direct(Request("1.2.3.4")) == "10.0.0.1"  # type: ignore[arg-type]
    assert behind_one(Request("spoofed, 1.2.3.4")) == "1.2.3.4"  # type: ignore[arg-type]
    assert behind_one(Request("")) == "10.0.0.1"  # type: ignore[arg-type]


def test_parse_dev_users() -> None:
    assert parse_dev_users("A@Example.com:pa:ss=uid1, b@x.io:pw=uid2") == {
        "a@example.com": ("pa:ss", "uid1"),
        "b@x.io": ("pw", "uid2"),
    }
    for bad in ("a@example.com=uid", "a@example.com:pw", ":pw=uid"):
        with pytest.raises(ValueError):
            parse_dev_users(bad)
