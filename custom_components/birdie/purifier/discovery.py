"""Shared LAN discovery for Birdie air purifiers.

While at least one purifier is set up, one listener receives the devices'
UDP broadcasts. It follows configured purifiers to a new address and offers
new Birdie purifiers in the Home Assistant UI. The config flow uses the same
listener, or a short-lived one when none is running.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components import network
from homeassistant.config_entries import SOURCE_INTEGRATION_DISCOVERY
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import discovery_flow

from ..const import (
    CONF_DEVICE_ID,
    CONF_PRODUCT_KEY,
    CONF_PROTOCOL_VERSION,
    DOMAIN,
    is_purifier_entry_data,
)
from ..tuya_lan import DiscoveredDevice, DiscoveryListener, async_discover
from .coordinator import BirdiePurifierCoordinator
from .model import PRODUCT_KEYS

_LOGGER = logging.getLogger(__name__)

DATA_DISCOVERY = f"{DOMAIN}_lan_discovery"


def is_birdie_purifier(device: DiscoveredDevice) -> bool:
    return device.product_key in PRODUCT_KEYS


async def _async_create_listener(hass: HomeAssistant) -> DiscoveryListener:
    source_ip: str | None
    try:
        source_ip = await network.async_get_source_ip(hass, target_ip=network.IPV4_BROADCAST_ADDR)
    except Exception:  # the request broadcast is optional
        source_ip = None
    return DiscoveryListener(request_source_ip=source_ip)


class PurifierDiscovery:
    """Reference-counted listener shared by all purifier entries."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._users = 0
        self._listener: DiscoveryListener | None = None
        self._remove_callback: Any = None
        self._offered: set[str] = set()

    @property
    def listener(self) -> DiscoveryListener | None:
        return self._listener

    async def async_acquire(self) -> None:
        self._users += 1
        if self._listener is not None:
            return
        listener = await _async_create_listener(self._hass)
        self._listener = listener
        self._remove_callback = listener.add_callback(self._async_on_device)
        await listener.async_start()

    async def async_release(self) -> None:
        self._users = max(0, self._users - 1)
        if self._users or self._listener is None:
            return
        listener, self._listener = self._listener, None
        if self._remove_callback is not None:
            self._remove_callback()
            self._remove_callback = None
        await listener.async_stop()

    @callback
    def _async_on_device(self, device: DiscoveredDevice) -> None:
        for entry in self._hass.config_entries.async_entries(DOMAIN):
            if not is_purifier_entry_data(entry.data):
                continue
            if entry.data.get(CONF_DEVICE_ID) != device.device_id:
                continue
            coordinator = getattr(entry, "runtime_data", None)
            if device.host != entry.data.get(CONF_HOST) and isinstance(
                coordinator, BirdiePurifierCoordinator
            ):
                self._hass.async_create_task(coordinator.async_set_host(device.host))
            return
        if is_birdie_purifier(device) and device.device_id not in self._offered:
            self._offered.add(device.device_id)
            discovery_flow.async_create_flow(
                self._hass,
                DOMAIN,
                context={"source": SOURCE_INTEGRATION_DISCOVERY},
                data=discovery_data(device),
            )


def discovery_data(device: DiscoveredDevice) -> dict[str, Any]:
    """What the config flow needs from an announcement."""
    return {
        CONF_DEVICE_ID: device.device_id,
        CONF_HOST: device.host,
        CONF_PROTOCOL_VERSION: device.protocol_version,
        CONF_PRODUCT_KEY: device.product_key,
    }


def get_discovery(hass: HomeAssistant) -> PurifierDiscovery:
    discovery: PurifierDiscovery | None = hass.data.get(DATA_DISCOVERY)
    if discovery is None:
        discovery = hass.data[DATA_DISCOVERY] = PurifierDiscovery(hass)
    return discovery


async def async_scan(
    hass: HomeAssistant, seconds: float, *, device_id: str | None = None
) -> dict[str, DiscoveredDevice]:
    """Collect announcements, reusing the running listener if there is one."""
    shared = get_discovery(hass).listener
    if shared is not None:
        return await async_discover(seconds, device_id=device_id, listener=shared)
    listener = await _async_create_listener(hass)
    await listener.async_start()
    try:
        return await async_discover(seconds, device_id=device_id, listener=listener)
    finally:
        await listener.async_stop()
