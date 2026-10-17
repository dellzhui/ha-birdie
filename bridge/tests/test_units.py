"""Unit tests for bridge configuration helpers, auth and products."""

from __future__ import annotations

import pytest

from birdie_bridge.__main__ import build_authenticator, build_catalog
from birdie_bridge.auth import (
    AuthenticationError,
    DevTokenAuthenticator,
    account_id_for,
    parse_dev_tokens,
)
from birdie_bridge.products import PEDESTAL, ProductCatalog, parse_product_map


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


def test_pedestal_command_rules() -> None:
    rules = PEDESTAL.commands
    assert rules["switch"].check(True, {}) is None
    assert rules["switch"].check(1, {}) is not None
    assert rules["mode"].check("auto", {"range": ["auto"]}) is None
    assert rules["mode"].check("turbo", {"range": ["auto"]}) is not None
    assert rules["reset_filter"].check(True, {}) is None
    assert rules["reset_filter"].check(False, {}) is not None


def test_authenticator_selection() -> None:
    dev = build_authenticator({"BRIDGE_ENV": "development", "BRIDGE_DEV_TOKENS": "t=u"})
    assert isinstance(dev, DevTokenAuthenticator)
    with pytest.raises(SystemExit):
        build_authenticator({"BRIDGE_ENV": "development"})
    with pytest.raises(SystemExit):
        build_authenticator({"BRIDGE_DEV_TOKENS": "t=u"})  # production refuses dev tokens
    with pytest.raises(SystemExit):
        build_authenticator({})  # no production authenticator yet


def test_build_catalog_with_extra_products() -> None:
    catalog = build_catalog({"BRIDGE_EXTRA_PRODUCTS": "clonepid=pedestal"})
    assert catalog.model_for("clonepid") is PEDESTAL
    assert catalog.model_for("a2zf7q0zxcutn5dy") is PEDESTAL
