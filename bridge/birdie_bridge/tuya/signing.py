"""Tuya Cloud OpenAPI request signing (HMAC-SHA256).

Pure functions only, so the algorithm can be unit-tested against known vectors
and reused by any transport.

    stringToSign = METHOD + "\\n" + sha256_hex(body) + "\\n" + headers_block + "\\n" + url
    str          = client_id [+ access_token] + t + nonce + stringToSign
    sign         = upper(hex(hmac_sha256(secret, str)))

``url`` is the request path followed by the query parameters sorted by key
(``key=value`` joined with ``&``). Token endpoints are signed without an
access token; all other ("business") endpoints include it.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping

SIGN_METHOD = "HMAC-SHA256"


def sha256_hex(content: bytes) -> str:
    """Return the lowercase hex SHA-256 digest of the request body."""
    return hashlib.sha256(content).hexdigest()


def canonical_url(path: str, params: Mapping[str, object] | None = None) -> str:
    """Return the path plus sorted ``key=value`` query string used for signing."""
    if not params:
        return path
    query = "&".join(f"{key}={params[key]}" for key in sorted(params))
    return f"{path}?{query}"


def string_to_sign(
    method: str,
    path: str,
    params: Mapping[str, object] | None = None,
    body: bytes = b"",
    signed_headers: Mapping[str, str] | None = None,
) -> str:
    """Build the ``stringToSign`` block of the signature."""
    headers_block = ""
    if signed_headers:
        headers_block = "".join(f"{key}:{value}\n" for key, value in signed_headers.items())
    return "\n".join(
        (
            method.upper(),
            sha256_hex(body),
            headers_block,
            canonical_url(path, params),
        )
    )


def calculate_sign(
    *,
    client_id: str,
    secret: str,
    t: str,
    string_to_sign_value: str,
    access_token: str = "",
    nonce: str = "",
) -> str:
    """Return the uppercase hex HMAC-SHA256 signature."""
    message = f"{client_id}{access_token}{t}{nonce}{string_to_sign_value}"
    digest = hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256)
    return digest.hexdigest().upper()
