"""Shared test helpers for the Birdie integration (synthetic data only)."""

from __future__ import annotations

import asyncio
import copy
from collections.abc import Callable
from typing import Any, ClassVar

from homeassistant.const import CONF_HOST

from custom_components.birdie.const import (
    CONF_DETECTED_VERSION,
    CONF_DEVICE_ID,
    CONF_ENTRY_TYPE,
    CONF_LOCAL_KEY,
    CONF_PRODUCT_KEY,
    CONF_PROTOCOL_VERSION,
    DOMAIN,
    ENTRY_TYPE_PURIFIER,
)
from custom_components.birdie.tuya_lan import (
    DeviceConfig,
    DeviceUnreachableError,
    DiscoveredDevice,
    TuyaLanError,
)

DEVICE_ID = "bf3a9c71d2e04b58f6qz1x"
LOCAL_KEY = "0123456789abcdef"
NEW_KEY = "fedcba9876543210"
HOST = "192.168.1.40"
PRODUCT_KEY = "a2zf7q0zxcutn5dy"
TITLE = "Birdie Air Purifier"
FAN = "fan.birdie_air_purifier"

# Synthetic DP values shaped like a real purifier's report (same DPs and
# types; DP 108 is never reported).
PURIFIER_DPS: dict[str, Any] = {
    "1": True,
    "2": 12,
    "3": "auto",
    "21": "good",
    "105": 129600,
    "106": 32400,
    "107": False,
    "109": True,
    "110": "1.2",
    "111": "normal",
}


def purifier_entry_data(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        CONF_ENTRY_TYPE: ENTRY_TYPE_PURIFIER,
        CONF_DEVICE_ID: DEVICE_ID,
        CONF_HOST: HOST,
        CONF_LOCAL_KEY: LOCAL_KEY,
        CONF_PROTOCOL_VERSION: "auto",
        CONF_DETECTED_VERSION: "3.3",
        CONF_PRODUCT_KEY: PRODUCT_KEY,
    }
    data.update(overrides)
    return data


def mock_purifier_entry(**kwargs: Any) -> Any:
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    kwargs.setdefault("data", purifier_entry_data())
    kwargs.setdefault("unique_id", DEVICE_ID)
    return MockConfigEntry(domain=DOMAIN, title=TITLE, **kwargs)


def discovered(**overrides: Any) -> DiscoveredDevice:
    values: dict[str, Any] = {
        "device_id": DEVICE_ID,
        "host": HOST,
        "protocol_version": "3.3",
        "product_key": PRODUCT_KEY,
        "seen_at": 0.0,
    }
    values.update(overrides)
    return DiscoveredDevice(**values)


class FakeLan:
    """Controls the in-memory devices that replace ``TuyaLanDevice``."""

    def __init__(self) -> None:
        self.dps: dict[str, Any] = copy.deepcopy(PURIFIER_DPS)
        self.version = "3.3"
        self.accepted_key = LOCAL_KEY
        # Set to an exception instance to make connecting / commands fail.
        self.connect_error: TuyaLanError | None = None
        self.command_error: TuyaLanError | None = None
        self.devices: list[FakeLanDevice] = []
        self.sent: list[dict[str, Any]] = []

    @property
    def device(self) -> FakeLanDevice:
        """The most recently created device that is still open."""
        open_devices = [device for device in self.devices if not device.closed]
        return open_devices[-1]

    def factory(self, config: DeviceConfig, **kwargs: Any) -> FakeLanDevice:
        device = FakeLanDevice(self, config, **kwargs)
        self.devices.append(device)
        return device


class FakeLanDevice:
    """In-memory stand-in for ``TuyaLanDevice`` (same public API)."""

    def __init__(
        self,
        lan: FakeLan,
        config: DeviceConfig,
        *,
        on_status: Callable[[dict[str, Any]], None] | None = None,
        on_availability: Callable[[bool, TuyaLanError | None], None] | None = None,
        on_error: Callable[[TuyaLanError], None] | None = None,
        version_hint: str | None = None,
        **_: Any,
    ) -> None:
        self._lan = lan
        self.config = config
        self._on_status = on_status
        self._on_availability = on_availability
        self._on_error = on_error
        self.version_hint = version_hint
        self.protocol_version: str | None = None
        self.available = False
        self.started = False
        self.closed = False
        self.last_error: TuyaLanError | None = None
        self.dps: dict[str, Any] = {}

    def _check(self) -> None:
        if self._lan.connect_error is not None:
            raise self._lan.connect_error
        if self.config.local_key != self._lan.accepted_key:
            from custom_components.birdie.tuya_lan import InvalidLocalKeyError

            raise InvalidLocalKeyError("The device did not accept the local key")

    async def async_connect(self) -> dict[str, Any]:
        await asyncio.sleep(0)
        self._check()
        self.protocol_version = self._lan.version
        self.dps = dict(self._lan.dps)
        self.available = True
        return dict(self.dps)

    def start(self) -> None:
        self.started = True

    async def async_close(self) -> None:
        self.closed = True

    async def async_refresh(self) -> dict[str, Any]:
        if not self.available:
            raise DeviceUnreachableError("not connected")
        self.dps = dict(self._lan.dps)
        return dict(self.dps)

    async def async_set_dps(self, dps: dict[str, Any]) -> None:
        if self._lan.command_error is not None:
            raise self._lan.command_error
        if not self.available:
            raise DeviceUnreachableError("not connected")
        self._lan.sent.append(dict(dps))

    async def async_reconfigure(self, **changes: Any) -> None:
        from dataclasses import replace

        self.config = replace(self.config, **{k: v for k, v in changes.items() if v is not None})

    # --- simulate the background loop

    def push(self, dps: dict[str, Any]) -> None:
        """The device reported changed DPs."""
        self._lan.dps.update(dps)
        self.dps.update(dps)
        assert self._on_status is not None
        self._on_status(dict(self.dps))

    def go_offline(self, error: TuyaLanError | None = None) -> None:
        self.available = False
        self.last_error = error or DeviceUnreachableError("The device stopped answering")
        assert self._on_availability is not None
        self._on_availability(False, self.last_error)

    def fail_reconnect(self, error: TuyaLanError) -> None:
        self.last_error = error
        assert self._on_error is not None
        self._on_error(error)

    def come_back(self) -> None:
        self.available = True
        self.dps = dict(self._lan.dps)
        assert self._on_availability is not None and self._on_status is not None
        self._on_availability(True, None)
        self._on_status(dict(self.dps))


class FakeListener:
    """Stand-in for the UDP discovery listener (no sockets)."""

    instances: ClassVar[list[FakeListener]] = []

    def __init__(self, devices: dict[str, DiscoveredDevice] | None = None) -> None:
        self.devices: dict[str, DiscoveredDevice] = dict(devices or {})
        self._callbacks: list[Callable[[DiscoveredDevice], None]] = []
        self.started = False
        FakeListener.instances.append(self)

    @property
    def listening(self) -> bool:
        return self.started

    def add_callback(self, callback: Callable[[DiscoveredDevice], None]) -> Callable[[], None]:
        self._callbacks.append(callback)
        return lambda: self._callbacks.remove(callback)

    def handle(self, device: DiscoveredDevice) -> None:
        self.devices[device.device_id] = device
        for callback in list(self._callbacks):
            callback(device)

    async def async_start(self) -> None:
        self.started = True

    async def async_stop(self) -> None:
        self.started = False
