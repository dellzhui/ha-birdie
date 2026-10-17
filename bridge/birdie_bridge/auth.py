"""Authentication of bridge callers.

An ``Authenticator`` turns a bearer token into a ``Principal``: the Birdie
account and the Tuya user whose devices it may access. The production
authenticator depends on how the Birdie app signs users in (Firebase Auth or
Tuya's user pool) and is not implemented yet; see docs/bridge-api.md 2.1.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol


class AuthenticationError(Exception):
    """The token is missing, malformed, expired or unknown."""


@dataclass(frozen=True)
class Principal:
    """An authenticated Birdie account."""

    account_id: str
    tuya_uid: str
    display_name: str | None = None

    def __repr__(self) -> str:
        return f"Principal(account_id={self.account_id!r})"


class Authenticator(Protocol):
    async def authenticate(self, token: str) -> Principal:
        """Return the principal for ``token`` or raise ``AuthenticationError``."""


def account_id_for(tuya_uid: str) -> str:
    """Opaque, stable account id that does not reveal the Tuya UID."""
    return hashlib.sha256(f"birdie-account:{tuya_uid}".encode()).hexdigest()[:24]


class DevTokenAuthenticator:
    """Static development tokens mapped to Tuya UIDs.

    Only for local development and remote debugging; the server refuses to
    enable it unless explicitly running in development mode.
    """

    def __init__(self, tokens: Mapping[str, str]) -> None:
        if not tokens:
            raise ValueError("At least one development token is required")
        self._tokens = dict(tokens)

    def __repr__(self) -> str:
        return f"DevTokenAuthenticator(<{len(self._tokens)} tokens>)"

    async def authenticate(self, token: str) -> Principal:
        for known, uid in self._tokens.items():
            if hmac.compare_digest(known.encode(), token.encode()):
                return Principal(account_id=account_id_for(uid), tuya_uid=uid)
        raise AuthenticationError("Unknown token")


def parse_dev_tokens(value: str) -> dict[str, str]:
    """Parse ``token=tuya_uid,token2=tuya_uid2`` (BRIDGE_DEV_TOKENS)."""
    result: dict[str, str] = {}
    for item in filter(None, (part.strip() for part in value.split(","))):
        token, sep, uid = item.partition("=")
        if not sep or not token.strip() or not uid.strip():
            raise ValueError("Invalid BRIDGE_DEV_TOKENS entry; expected token=tuya_uid")
        result[token.strip()] = uid.strip()
    return result
