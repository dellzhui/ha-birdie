"""Local (LAN) communication with Tuya-based Wi-Fi devices.

Thin asynchronous layer over tinytuya (MIT licensed), which implements the
Tuya LAN protocol 3.1-3.5 including its encryption. No Home Assistant
imports, so it can be tested and reused on its own.
"""

from .device import (
    PROTOCOL_AUTO,
    PROTOCOL_VERSIONS,
    DeviceConfig,
    Timings,
    TuyaLanDevice,
)
from .discovery import DiscoveredDevice, DiscoveryListener, async_discover
from .errors import DeviceUnreachableError, InvalidLocalKeyError, TuyaLanError

__all__ = [
    "PROTOCOL_AUTO",
    "PROTOCOL_VERSIONS",
    "DeviceConfig",
    "DeviceUnreachableError",
    "DiscoveredDevice",
    "DiscoveryListener",
    "InvalidLocalKeyError",
    "Timings",
    "TuyaLanDevice",
    "TuyaLanError",
    "async_discover",
]
