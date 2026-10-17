"""Tests for the request signing primitives."""

from __future__ import annotations

import hashlib
import hmac

from birdie_bridge.tuya.signing import calculate_sign, canonical_url, sha256_hex, string_to_sign

EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def test_empty_body_hash() -> None:
    assert sha256_hex(b"") == EMPTY_SHA256


def test_canonical_url_sorts_params() -> None:
    assert canonical_url("/v1.0/token", {"grant_type": 1}) == "/v1.0/token?grant_type=1"
    assert canonical_url("/p", {"b": "2", "a": "1"}) == "/p?a=1&b=2"
    assert canonical_url("/p", None) == "/p"


def test_string_to_sign_layout() -> None:
    value = string_to_sign("get", "/v1.0/token", {"grant_type": 1})
    assert value == f"GET\n{EMPTY_SHA256}\n\n/v1.0/token?grant_type=1"


def test_string_to_sign_with_body_and_signed_headers() -> None:
    body = b'{"commands":[]}'
    value = string_to_sign("POST", "/x", body=body, signed_headers={"area_id": "29a"})
    assert value == f"POST\n{hashlib.sha256(body).hexdigest()}\narea_id:29a\n\n/x"


def test_calculate_sign_token_vs_business() -> None:
    sts = string_to_sign("GET", "/v1.0/token", {"grant_type": 1})
    token_sign = calculate_sign(
        client_id="cid", secret="sec", t="1588925778000", string_to_sign_value=sts
    )
    expected = hmac.new(b"sec", f"cid1588925778000{sts}".encode(), hashlib.sha256)
    assert token_sign == expected.hexdigest().upper()

    business = calculate_sign(
        client_id="cid",
        secret="sec",
        t="1",
        nonce="n",
        access_token="tok",
        string_to_sign_value=sts,
    )
    expected = hmac.new(b"sec", f"cidtok1n{sts}".encode(), hashlib.sha256)
    assert business == expected.hexdigest().upper()
    assert business == business.upper()


# Official worked example from
# https://developer.tuya.com/en/docs/iot/new-singnature?id=Kbw0q34cs2e5g
DOC_CLIENT_ID = "1KAD46OrT9HafiKdsXeg"
DOC_SECRET = "4OHBOnWOqaEC1mWXOpVL3yV50s0qGSRC"
DOC_T = "1588925778000"
DOC_NONCE = "5138cc3a9033d69856923fd07b491173"
DOC_HEADERS = {"area_id": "29a33e8796834b1efa6", "call_id": "8afdb70ab2ed11eb85290242ac130003"}


def test_official_vector_token_api() -> None:
    sts = string_to_sign("GET", "/v1.0/token", {"grant_type": 1}, signed_headers=DOC_HEADERS)
    sign = calculate_sign(
        client_id=DOC_CLIENT_ID,
        secret=DOC_SECRET,
        t=DOC_T,
        nonce=DOC_NONCE,
        string_to_sign_value=sts,
    )
    assert sign == "9E48A3E93B302EEECC803C7241985D0A34EB944F40FB573C7B5C2A82158AF13E"


def test_official_vector_business_api() -> None:
    sts = string_to_sign(
        "GET",
        "/v2.0/apps/schema/users",
        {"page_size": 50, "page_no": 1},
        signed_headers=DOC_HEADERS,
    )
    sign = calculate_sign(
        client_id=DOC_CLIENT_ID,
        secret=DOC_SECRET,
        t=DOC_T,
        nonce=DOC_NONCE,
        access_token="3f4eda2bdec17232f67c0b188af3eec1",
        string_to_sign_value=sts,
    )
    assert sign == "AE4481C692AA80B25F3A7E12C3A5FD9BBF6251539DD78E565A1A72A508A88784"
