"""OAuth 2.0 authorization server: authorization code grant with PKCE.

Implements the protocol in docs/bridge-api.md section 2.1 and the token
handling in docs/sign-in.md section 5.4, independent of the HTTP framework.
The server also authenticates ``/v1`` access tokens (``Authenticator``).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import re
import secrets
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from ..auth import AuthenticationError, Principal, account_id_for
from .identity import (
    AccountRevokedError,
    Identity,
    IdentityProvider,
    IdentityUnavailableError,
    SignInError,
)
from .store import AccessTokenRecord, AuthCode, AuthRequest, Grant, OAuthStore

REQUEST_TTL = 600
CODE_TTL = 60
ACCESS_TOKEN_TTL = 3600
REFRESH_IDLE_TTL = 90 * 24 * 3600
ROTATION_GRACE = 120
MAX_SIGN_IN_AGE = 300
RETRY_AFTER = 30

MY_HOME_ASSISTANT_REDIRECT = "https://my.home-assistant.io/redirect/oauth"

_CHALLENGE = re.compile(r"^[A-Za-z0-9_-]{43}$")  # base64url SHA-256, no padding
_VERIFIER = re.compile(r"^[A-Za-z0-9._~-]{43,128}$")  # RFC 7636 section 4.1
_MAX_STATE = 4096


@dataclass(frozen=True)
class OAuthClient:
    client_id: str
    name: str
    redirect_uris: frozenset[str]


HOME_ASSISTANT = OAuthClient(
    "home-assistant", "Home Assistant", frozenset({MY_HOME_ASSISTANT_REDIRECT})
)


class AuthorizeError(Exception):
    """Invalid /oauth/authorize request.

    With ``redirect_uri`` the error is sent back to the client; without it the
    client or redirect URI is untrusted and an error page is shown instead.
    """

    def __init__(
        self,
        error: str,
        description: str,
        redirect_uri: str | None = None,
        state: str | None = None,
    ) -> None:
        super().__init__(description)
        self.error = error
        self.description = description
        self.redirect_uri = redirect_uri
        self.state = state

    def redirect_url(self) -> str | None:
        if self.redirect_uri is None:
            return None
        params = {"error": self.error, "error_description": self.description}
        if self.state:
            params["state"] = self.state
        return add_query(self.redirect_uri, params)


class RequestNotFoundError(Exception):
    """Unknown or expired authorization request, or a different browser."""


class TokenError(Exception):
    """An RFC 6749 token endpoint error."""

    def __init__(self, status: int, error: str, description: str) -> None:
        super().__init__(description)
        self.status = status
        self.error = error
        self.description = description


class UnavailableError(Exception):
    """A dependency is down; answer 503 (never 4xx, see docs/sign-in.md 5.5)."""

    def __init__(self, retry_after: int = RETRY_AFTER) -> None:
        super().__init__("Service temporarily unavailable")
        self.retry_after = retry_after


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def add_query(url: str, params: Mapping[str, str]) -> str:
    return f"{url}{'&' if '?' in url else '?'}{urlencode(params)}"


def _new_secret() -> str:
    return secrets.token_urlsafe(32)


def _invalid_grant(description: str) -> TokenError:
    return TokenError(400, "invalid_grant", description)


class OAuthServer:
    """Authorization server and access token authenticator."""

    def __init__(
        self,
        store: OAuthStore,
        identity: IdentityProvider,
        *,
        clients: Iterable[OAuthClient] = (HOME_ASSISTANT,),
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._store = store
        self._identity = identity
        self._clients = {client.client_id: client for client in clients}
        self._clock = clock
        # Serialises code exchange and refresh rotation; a shared store needs
        # transactions instead (see store.py).
        self._token_lock = asyncio.Lock()

    @property
    def sign_in_method(self) -> str:
        return self._identity.sign_in_method

    def client_name(self, client_id: str) -> str:
        return self._clients[client_id].name

    # ------------------------------------------------------ authorization

    async def start_authorization(self, params: Mapping[str, str]) -> tuple[AuthRequest, str]:
        """Validate an authorize request; return it and the browser binding secret."""
        client = self._clients.get(params.get("client_id", ""))
        redirect_uri = params.get("redirect_uri", "")
        if client is None or redirect_uri not in client.redirect_uris:
            raise AuthorizeError("invalid_request", "Unknown client or redirect URI")
        state = params.get("state", "")

        def fail(error: str, description: str) -> AuthorizeError:
            return AuthorizeError(error, description, redirect_uri, state[:_MAX_STATE])

        if params.get("response_type") != "code":
            raise fail("unsupported_response_type", "Only response_type=code is supported")
        if params.get("code_challenge_method") != "S256":
            raise fail("invalid_request", "PKCE with code_challenge_method=S256 is required")
        challenge = params.get("code_challenge", "")
        if not _CHALLENGE.match(challenge):
            raise fail("invalid_request", "Invalid code_challenge")
        if not state or len(state) > _MAX_STATE:
            raise fail("invalid_request", "Missing or oversized state")

        binding = _new_secret()
        request = AuthRequest(
            id=_new_secret(),
            client_id=client.client_id,
            redirect_uri=redirect_uri,
            state=state,
            code_challenge=challenge,
            browser_binding_hash=hash_secret(binding),
            expires_at=self._clock() + REQUEST_TTL,
        )
        await self._store.put_request(request)
        return request, binding

    async def sign_in(
        self, request_id: Any, binding: str | None, credential: Mapping[str, Any]
    ) -> Identity:
        """Verify the credential from the sign-in page for a pending request."""
        request = await self._pending(request_id, binding)
        try:
            identity = await self._identity.verify(credential)
        except IdentityUnavailableError as err:
            raise UnavailableError() from err
        if self._clock() - identity.signed_in_at > MAX_SIGN_IN_AGE:
            raise SignInError("Please sign in again.")
        request.identity = identity
        await self._store.put_request(request)
        return identity

    async def finish(self, request_id: Any, binding: str | None, allow: bool) -> str:
        """Record the user's consent decision; return the redirect URL for the browser."""
        request = await self._pending(request_id, binding)
        await self._store.delete_request(request.id)
        if not allow:
            return add_query(
                request.redirect_uri, {"error": "access_denied", "state": request.state}
            )
        identity = request.identity
        if identity is None:
            raise RequestNotFoundError("Not signed in")
        now = self._clock()
        grant = Grant(
            id=secrets.token_hex(16),
            client_id=request.client_id,
            account_id=account_id_for(identity.user_id),
            user_id=identity.user_id,
            tuya_uid=identity.tuya_uid,
            display_name=identity.email,
            signed_in_at=identity.signed_in_at,
            created_at=now,
            last_used_at=now,
        )
        code = f"bac_{_new_secret()}"
        await self._store.put_grant(grant)
        await self._store.put_code(
            AuthCode(
                code_hash=hash_secret(code),
                grant_id=grant.id,
                client_id=request.client_id,
                redirect_uri=request.redirect_uri,
                code_challenge=request.code_challenge,
                expires_at=now + CODE_TTL,
            )
        )
        return add_query(request.redirect_uri, {"code": code, "state": request.state})

    async def _pending(self, request_id: Any, binding: str | None) -> AuthRequest:
        request = await self._store.get_request(request_id) if isinstance(request_id, str) else None
        if (
            request is None
            or not binding
            or not hmac.compare_digest(request.browser_binding_hash, hash_secret(binding))
        ):
            raise RequestNotFoundError("Unknown or expired sign-in request")
        return request

    # ------------------------------------------------------------- tokens

    async def token(self, form: Mapping[str, str]) -> dict[str, Any]:
        """Handle POST /oauth/token."""
        client_id = form.get("client_id", "")
        if client_id not in self._clients:
            raise TokenError(401, "invalid_client", "Unknown client")
        grant_type = form.get("grant_type")
        async with self._token_lock:
            if grant_type == "authorization_code":
                return await self._exchange_code(form, client_id)
            if grant_type == "refresh_token":
                return await self._refresh(form, client_id)
        raise TokenError(400, "unsupported_grant_type", "Unsupported grant_type")

    async def _exchange_code(self, form: Mapping[str, str], client_id: str) -> dict[str, Any]:
        code = form.get("code", "")
        record = await self._store.get_code(hash_secret(code)) if code else None
        if record is None or record.client_id != client_id:
            raise _invalid_grant("Invalid authorization code")
        if record.used:
            # RFC 6749 4.1.2: a reused code revokes what it issued.
            await self._revoke_grant(record.grant_id)
            raise _invalid_grant("Authorization code already used")
        record.used = True
        await self._store.put_code(record)
        if record.expires_at <= self._clock():
            raise _invalid_grant("Authorization code expired")
        if form.get("redirect_uri") != record.redirect_uri:
            raise _invalid_grant("redirect_uri does not match")
        verifier = form.get("code_verifier", "")
        if not _VERIFIER.match(verifier) or not hmac.compare_digest(
            s256(verifier), record.code_challenge
        ):
            raise _invalid_grant("PKCE verification failed")
        grant = await self._store.get_grant(record.grant_id)
        if grant is None or grant.revoked:
            raise _invalid_grant("Grant revoked")
        return await self._issue(grant)

    async def _refresh(self, form: Mapping[str, str], client_id: str) -> dict[str, Any]:
        token = form.get("refresh_token", "")
        grant_id = _refresh_grant_id(token)
        grant = await self._store.get_grant(grant_id) if grant_id else None
        if grant is None or grant.revoked or grant.client_id != client_id:
            raise _invalid_grant("Invalid refresh token")
        now = self._clock()
        if now - grant.last_used_at > REFRESH_IDLE_TTL:
            await self._revoke_grant(grant.id)
            raise _invalid_grant("Refresh token expired")
        token_hash = hash_secret(token)
        if grant.refresh_hash and hmac.compare_digest(token_hash, grant.refresh_hash):
            keep_previous = False
        elif grant.previous_refresh_hash and hmac.compare_digest(
            token_hash, grant.previous_refresh_hash
        ):
            if now - grant.rotated_at > ROTATION_GRACE:
                await self._revoke_grant(grant.id)
                raise _invalid_grant("Refresh token reuse detected")
            # The client most likely lost the previous response; issue again.
            keep_previous = True
        else:
            raise _invalid_grant("Invalid refresh token")
        try:
            grant.tuya_uid = await self._identity.refresh(grant.user_id, grant.signed_in_at)
        except AccountRevokedError:
            await self._revoke_grant(grant.id)
            raise _invalid_grant("The Birdie account must sign in again") from None
        except IdentityUnavailableError as err:
            raise UnavailableError() from err
        return await self._issue(grant, keep_previous=keep_previous)

    async def _issue(self, grant: Grant, *, keep_previous: bool = False) -> dict[str, Any]:
        now = self._clock()
        access_token = f"bat_{_new_secret()}"
        refresh_token = f"brt_{grant.id}.{_new_secret()}"
        await self._store.put_access_token(
            AccessTokenRecord(hash_secret(access_token), grant.id, now + ACCESS_TOKEN_TTL)
        )
        if not keep_previous:
            grant.previous_refresh_hash = grant.refresh_hash
            grant.rotated_at = now
        grant.refresh_hash = hash_secret(refresh_token)
        grant.last_used_at = now
        await self._store.put_grant(grant)
        return {
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": ACCESS_TOKEN_TTL,
            "refresh_token": refresh_token,
        }

    # ------------------------------------------------------------ revoking

    async def revoke(self, form: Mapping[str, str]) -> None:
        """Handle POST /oauth/revoke (RFC 7009): end the grant behind a token."""
        token = form.get("token", "")
        grant_id: str | None = None
        if token.startswith("bat_"):
            record = await self._store.get_access_token(hash_secret(token))
            grant_id = record.grant_id if record else None
        elif (candidate := _refresh_grant_id(token)) is not None:
            grant = await self._store.get_grant(candidate)
            token_hash = hash_secret(token)
            if grant and token_hash in (grant.refresh_hash, grant.previous_refresh_hash):
                grant_id = grant.id
        if grant_id:
            async with self._token_lock:
                await self._revoke_grant(grant_id)

    async def _revoke_grant(self, grant_id: str) -> None:
        grant = await self._store.get_grant(grant_id)
        if grant and not grant.revoked:
            grant.revoked = True
            await self._store.put_grant(grant)

    # ------------------------------------------------------ authentication

    async def authenticate(self, token: str) -> Principal:
        """Resolve a /v1 bearer access token."""
        record = (
            await self._store.get_access_token(hash_secret(token))
            if token.startswith("bat_")
            else None
        )
        grant = await self._store.get_grant(record.grant_id) if record else None
        if grant is None or grant.revoked:
            raise AuthenticationError("Unknown or expired token")
        return Principal(
            account_id=grant.account_id,
            tuya_uid=grant.tuya_uid,
            display_name=grant.display_name,
        )


def _refresh_grant_id(token: str) -> str | None:
    if not token.startswith("brt_"):
        return None
    grant_id, sep, secret = token.removeprefix("brt_").partition(".")
    return grant_id if sep and grant_id and secret else None
