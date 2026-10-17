"""Shared fixtures for the Birdie integration tests."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import patch

import pytest

# Import the patched modules up front so ``patch`` can resolve them.
import custom_components.birdie.cloud.entry
import custom_components.birdie.config_flow  # noqa: F401

from .helpers import FakeBridgeClient


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> Iterator[None]:
    """Allow Home Assistant to load custom_components/birdie in every test."""
    yield


@pytest.fixture
def fake_bridge() -> Iterator[FakeBridgeClient]:
    """Replace the bridge client used by the config flow and entry setup."""
    fake = FakeBridgeClient()

    def factory(*args: object, **kwargs: object) -> FakeBridgeClient:
        fake.created_with.append(args)
        return fake

    with (
        patch("custom_components.birdie.config_flow.BirdieBridgeClient", factory),
        patch("custom_components.birdie.cloud.entry.BirdieBridgeClient", factory),
    ):
        yield fake
