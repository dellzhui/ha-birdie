"""Shared fixtures."""

from __future__ import annotations

from collections.abc import AsyncIterator

import aiohttp
import pytest
from aiohttp.test_utils import TestServer

from birdie_bridge.tuya import TuyaCloudClient, TuyaCredentials

from .fake_tuya import ACCESS_ID, ACCESS_SECRET, FakeTuyaCloud


class FakeClock:
    """Controllable wall clock (seconds)."""

    def __init__(self, start: float = 1_700_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def cloud() -> FakeTuyaCloud:
    fake = FakeTuyaCloud()
    fake.add_pedestal()
    return fake


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
async def server(cloud: FakeTuyaCloud) -> AsyncIterator[TestServer]:
    srv = TestServer(cloud.app())
    await srv.start_server()
    yield srv
    await srv.close()


@pytest.fixture
async def session() -> AsyncIterator[aiohttp.ClientSession]:
    async with aiohttp.ClientSession() as sess:
        yield sess


@pytest.fixture
def client(server: TestServer, session: aiohttp.ClientSession, clock: FakeClock) -> TuyaCloudClient:
    endpoint = str(server.make_url("")).rstrip("/")
    creds = TuyaCredentials(ACCESS_ID, ACCESS_SECRET, endpoint)
    return TuyaCloudClient(session, creds, clock=clock)
