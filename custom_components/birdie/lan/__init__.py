"""Local (LAN) communication with Birdie Wi-Fi devices.

Thin asynchronous layer over the protocol library (MIT licensed), which
implements the local protocol 3.1-3.5 including its encryption. No Home
Assistant imports, so it can be tested and reused on its own.
"""

from .device import (
    PROTOCOL_AUTO,
    PROTOCOL_VERSIONS,
    DeviceConfig,
    LanDevice,
    Timings,
)
from .discovery import DiscoveredDevice, DiscoveryListener, async_discover
from .errors import DeviceUnreachableError, InvalidLocalKeyError, LanError
from .search import FoundDevice, addresses_to_search, async_find_device

__all__ = [
    "PROTOCOL_AUTO",
    "PROTOCOL_VERSIONS",
    "DeviceConfig",
    "DeviceUnreachableError",
    "DiscoveredDevice",
    "DiscoveryListener",
    "FoundDevice",
    "InvalidLocalKeyError",
    "LanDevice",
    "LanError",
    "Timings",
    "addresses_to_search",
    "async_discover",
    "async_find_device",
]
