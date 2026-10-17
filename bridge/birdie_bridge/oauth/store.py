"""Storage for OAuth authorization requests, codes, grants and access tokens.

Only hashes of codes and tokens are stored (docs/sign-in.md, section 5.4).
``MemoryOAuthStore`` serves development and tests with a single process. A
deployment with several instances needs a shared store (Firestore is planned)
implementing the same protocol; grant updates during refresh token rotation
must then run in a transaction.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from .identity import Identity


@dataclass
class AuthRequest:
    """A pending /oauth/authorize request, between the page load and consent."""

    id: str
    client_id: str
    redirect_uri: str
    state: str
    code_challenge: str
    browser_binding_hash: str  # hash of the cookie that ties the request to one browser
    expires_at: float
    identity: Identity | None = None  # set once the user has signed in


@dataclass
class AuthCode:
    code_hash: str
    grant_id: str
    client_id: str
    redirect_uri: str
    code_challenge: str
    expires_at: float
    used: bool = False


@dataclass
class Grant:
    """Everything issued from one sign-in; revoking it ends all its tokens."""

    id: str
    client_id: str
    account_id: str
    user_id: str
    tuya_uid: str
    display_name: str | None
    signed_in_at: float
    created_at: float
    last_used_at: float
    refresh_hash: str | None = None
    previous_refresh_hash: str | None = None
    rotated_at: float = 0.0
    revoked: bool = False


@dataclass
class AccessTokenRecord:
    token_hash: str
    grant_id: str
    expires_at: float


class OAuthStore(Protocol):
    async def put_request(self, request: AuthRequest) -> None: ...

    async def get_request(self, request_id: str) -> AuthRequest | None: ...

    async def delete_request(self, request_id: str) -> None: ...

    async def put_code(self, code: AuthCode) -> None: ...

    async def get_code(self, code_hash: str) -> AuthCode | None: ...

    async def put_grant(self, grant: Grant) -> None: ...

    async def get_grant(self, grant_id: str) -> Grant | None: ...

    async def put_access_token(self, token: AccessTokenRecord) -> None: ...

    async def get_access_token(self, token_hash: str) -> AccessTokenRecord | None: ...


@dataclass
class MemoryOAuthStore:
    """In-process store; expired entries are dropped on access."""

    clock: Callable[[], float] = time.time
    requests: dict[str, AuthRequest] = field(default_factory=dict)
    codes: dict[str, AuthCode] = field(default_factory=dict)
    grants: dict[str, Grant] = field(default_factory=dict)
    access_tokens: dict[str, AccessTokenRecord] = field(default_factory=dict)

    async def put_request(self, request: AuthRequest) -> None:
        self._purge()
        self.requests[request.id] = request

    async def get_request(self, request_id: str) -> AuthRequest | None:
        request = self.requests.get(request_id)
        return request if request and request.expires_at > self.clock() else None

    async def delete_request(self, request_id: str) -> None:
        self.requests.pop(request_id, None)

    async def put_code(self, code: AuthCode) -> None:
        self.codes[code.code_hash] = code

    async def get_code(self, code_hash: str) -> AuthCode | None:
        # Expired codes are kept until purged so that reuse can still be detected.
        return self.codes.get(code_hash)

    async def put_grant(self, grant: Grant) -> None:
        self.grants[grant.id] = grant

    async def get_grant(self, grant_id: str) -> Grant | None:
        return self.grants.get(grant_id)

    async def put_access_token(self, token: AccessTokenRecord) -> None:
        self.access_tokens[token.token_hash] = token

    async def get_access_token(self, token_hash: str) -> AccessTokenRecord | None:
        token = self.access_tokens.get(token_hash)
        return token if token and token.expires_at > self.clock() else None

    def _purge(self) -> None:
        now = self.clock()
        for key in [k for k, v in self.requests.items() if v.expires_at <= now]:
            del self.requests[key]
        for key in [k for k, v in self.codes.items() if v.expires_at + 3600 <= now]:
            del self.codes[key]
        for key in [k for k, v in self.access_tokens.items() if v.expires_at <= now]:
            del self.access_tokens[key]
