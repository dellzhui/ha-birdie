"""OAuth 2.0 sign-in for Home Assistant (docs/sign-in.md)."""

from .identity import (
    AccountRevokedError,
    DevIdentityProvider,
    Identity,
    IdentityProvider,
    IdentityUnavailableError,
    SignInError,
    parse_dev_users,
)
from .routes import setup_oauth_routes
from .server import HOME_ASSISTANT, OAuthClient, OAuthServer
from .store import MemoryOAuthStore, OAuthStore

__all__ = [
    "HOME_ASSISTANT",
    "AccountRevokedError",
    "DevIdentityProvider",
    "Identity",
    "IdentityProvider",
    "IdentityUnavailableError",
    "MemoryOAuthStore",
    "OAuthClient",
    "OAuthServer",
    "OAuthStore",
    "SignInError",
    "parse_dev_users",
    "setup_oauth_routes",
]
