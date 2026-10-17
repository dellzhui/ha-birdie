"""Identity providers: who is signing in on the Birdie sign-in page.

The sign-in page collects a *credential* (for Firebase: an ID token; for the
development provider: e-mail and password) and the bridge asks the provider
to verify it. The provider also resolves the user's Tuya UID and, on every
token refresh, confirms that the user may still use the bridge
(docs/sign-in.md, sections 5.2-5.4).
"""

from __future__ import annotations

import hmac
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol


class SignInError(Exception):
    """The credential was rejected; shown to the user on the sign-in page."""


class AccountRevokedError(Exception):
    """The user was deleted, disabled or signed out everywhere since signing in."""


class IdentityUnavailableError(Exception):
    """The identity provider could not be reached; the caller should retry."""


@dataclass(frozen=True)
class Identity:
    """A verified sign-in."""

    user_id: str  # identity provider's stable user id (e.g. Firebase UID)
    tuya_uid: str
    email: str | None
    signed_in_at: float  # epoch seconds when the user authenticated

    def __repr__(self) -> str:
        return f"Identity(user_id={self.user_id!r})"


class IdentityProvider(Protocol):
    """Verifies sign-ins and re-checks users on refresh."""

    # Which sign-in form the page shows ("dev_password" for the development provider).
    sign_in_method: str

    async def verify(self, credential: Mapping[str, Any]) -> Identity:
        """Return the identity or raise ``SignInError`` / ``IdentityUnavailableError``."""

    async def refresh(self, user_id: str, signed_in_at: float) -> str:
        """Return the user's current Tuya UID.

        Raise ``AccountRevokedError`` if the grant must end, or
        ``IdentityUnavailableError`` if this cannot be decided now.
        """


@dataclass
class DevUser:
    password: str
    tuya_uid: str
    disabled: bool = False
    tokens_valid_after: float = 0.0


class DevIdentityProvider:
    """E-mail and password users from configuration; development only.

    Stands in for Birdie's identity provider so the whole OAuth flow can be
    exercised before Birdie's Firebase project is available.
    """

    sign_in_method = "dev_password"

    def __init__(
        self, users: Mapping[str, tuple[str, str]], *, clock: Callable[[], float] = time.time
    ) -> None:
        if not users:
            raise ValueError("At least one development user is required")
        self._users = {
            email.lower(): DevUser(password, uid) for email, (password, uid) in users.items()
        }
        self._clock = clock

    def __repr__(self) -> str:
        return f"DevIdentityProvider(<{len(self._users)} users>)"

    async def verify(self, credential: Mapping[str, Any]) -> Identity:
        email = credential.get("email")
        password = credential.get("password")
        if not isinstance(email, str) or not isinstance(password, str):
            raise SignInError("Enter your e-mail address and password.")
        user = self._users.get(email.strip().lower())
        if (
            user is None
            or not hmac.compare_digest(user.password.encode(), password.encode())
            or user.disabled
        ):
            raise SignInError("Wrong e-mail address or password.")
        return Identity(
            user_id=f"dev:{email.strip().lower()}",
            tuya_uid=user.tuya_uid,
            email=email.strip().lower(),
            signed_in_at=self._clock(),
        )

    async def refresh(self, user_id: str, signed_in_at: float) -> str:
        user = self._users.get(user_id.removeprefix("dev:"))
        if user is None or user.disabled or user.tokens_valid_after > signed_in_at:
            raise AccountRevokedError(user_id)
        return user.tuya_uid

    # Test and demo helpers mirroring Firebase account changes.

    def disable(self, email: str) -> None:
        self._users[email.lower()].disabled = True

    def sign_out_everywhere(self, email: str) -> None:
        """Like a password change: tokens from earlier sign-ins become invalid."""
        self._users[email.lower()].tokens_valid_after = self._clock()


def parse_dev_users(value: str) -> dict[str, tuple[str, str]]:
    """Parse ``email:password=tuya_uid[,...]`` (BRIDGE_DEV_USERS)."""
    result: dict[str, tuple[str, str]] = {}
    for item in filter(None, (part.strip() for part in value.split(","))):
        credentials, sep, uid = item.rpartition("=")
        email, colon, password = credentials.partition(":")
        if not sep or not colon or not email.strip() or not password or not uid.strip():
            raise ValueError("Invalid BRIDGE_DEV_USERS entry; expected email:password=tuya_uid")
        result[email.strip().lower()] = (password, uid.strip())
    return result
