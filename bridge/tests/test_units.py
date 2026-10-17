"""Unit tests for bridge configuration helpers, auth and products."""

from __future__ import annotations

import pytest

from birdie_bridge.__main__ import build_auth, build_catalog
from birdie_bridge.auth import (
    AuthenticationError,
    DevTokenAuthenticator,
    account_id_for,
    parse_dev_tokens,
)
from birdie_bridge.pedestal import PEDESTAL
from birdie_bridge.products import ProductCatalog, parse_product_map


def test_parse_dev_tokens() -> None:
    assert parse_dev_tokens(" a=uid1, b=uid2 ,") == {"a": "uid1", "b": "uid2"}
    with pytest.raises(ValueError) as info:
        parse_dev_tokens("secret-token-without-uid")
    assert "secret-token" not in str(info.value)


async def test_dev_authenticator() -> None:
    auth = DevTokenAuthenticator({"s3cr3t-dev": "uid1"})
    principal = await auth.authenticate("s3cr3t-dev")
    assert principal.tuya_uid == "uid1"
    assert principal.account_id == account_id_for("uid1")
    assert "uid1" not in repr(principal)
    assert "s3cr3t-dev" not in repr(auth)
    with pytest.raises(AuthenticationError):
        await auth.authenticate("other")


def test_account_id_is_stable_and_opaque() -> None:
    assert account_id_for("uid1") == account_id_for("uid1")
    assert account_id_for("uid1") != account_id_for("uid2")
    assert "uid1" not in account_id_for("uid1")


def test_product_catalog() -> None:
    catalog = ProductCatalog()
    assert catalog.model_for("a2zf7q0zxcutn5dy") is PEDESTAL
    assert catalog.model_for("other") is None
    assert catalog.model_for(None) is None
    with pytest.raises(ValueError):
        ProductCatalog({"pid": "unknown_model"})


def test_parse_product_map() -> None:
    assert parse_product_map("p1=pedestal, p2=pedestal") == {"p1": "pedestal", "p2": "pedestal"}
    assert parse_product_map("") == {}
    with pytest.raises(ValueError):
        parse_product_map("p1")


async def test_auth_selection() -> None:
    dev = build_auth({"BRIDGE_ENV": "development", "BRIDGE_DEV_TOKENS": "t=u"})
    assert dev.oauth is None
    assert (await dev.authenticator.authenticate("t")).tuya_uid == "u"
    with_users = build_auth(
        {"BRIDGE_ENV": "development", "BRIDGE_DEV_USERS": "a@example.com:pw=u1"}
    )
    assert with_users.oauth is not None
    with pytest.raises(SystemExit):
        build_auth({"BRIDGE_ENV": "development"})
    with pytest.raises(SystemExit):
        build_auth({"BRIDGE_DEV_TOKENS": "t=u"})  # production refuses dev tokens
    with pytest.raises(SystemExit):
        build_auth({"BRIDGE_DEV_USERS": "a@example.com:pw=u1"})
    with pytest.raises(SystemExit):
        build_auth({})  # no production identity provider yet


def test_build_catalog_with_extra_products() -> None:
    catalog = build_catalog({"BRIDGE_EXTRA_PRODUCTS": "clonepid=pedestal"})
    assert catalog.model_for("clonepid") is PEDESTAL
    assert catalog.model_for("a2zf7q0zxcutn5dy") is PEDESTAL
