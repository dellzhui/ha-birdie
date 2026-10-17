"""Tests for the developer CLI helpers."""

from __future__ import annotations

import pytest

from birdie_bridge.devtool import credentials_from_env, redact


def test_redact_nested() -> None:
    data = {"local_key": "k", "nested": [{"ip": "1.2.3.4", "name": "x"}], "id": "d"}
    assert redact(data) == {
        "local_key": "**REDACTED**",
        "nested": [{"ip": "**REDACTED**", "name": "x"}],
        "id": "d",
    }


def test_credentials_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TUYA_ACCESS_ID", "id")
    monkeypatch.setenv("TUYA_ACCESS_SECRET", "secret")
    monkeypatch.setenv("TUYA_REGION", "us")
    monkeypatch.delenv("TUYA_ENDPOINT", raising=False)
    creds = credentials_from_env()
    assert creds.endpoint == "https://openapi.tuyaus.com"
    assert "secret" not in repr(creds)


def test_credentials_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("TUYA_ACCESS_ID", "TUYA_ACCESS_SECRET", "TUYA_REGION", "TUYA_ENDPOINT"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(SystemExit):
        credentials_from_env()
