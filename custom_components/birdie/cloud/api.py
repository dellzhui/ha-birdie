"""Transport-agnostic Pedestal API.

``PedestalApi`` talks to any backend implementing ``CloudTransport``. Today
that is ``TuyaCloudClient`` (direct Tuya OpenAPI, holds project credentials).
A credential-less transport (e.g. a Birdie-operated relay) can be added later
without touching the device model or the Home Assistant entities.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Protocol

from .pedestal import (
    DeviceSpecification,
    PedestalState,
    mode_command,
    power_command,
    reset_filter_command,
)


class CloudTransport(Protocol):
    """Subset of cloud operations the Pedestal integration relies on."""

    async def async_get_device(self, device_id: str) -> dict[str, Any]: ...

    async def async_get_device_status(self, device_id: str) -> list[dict[str, Any]]: ...

    async def async_get_device_specification(self, device_id: str) -> dict[str, Any]: ...

    async def async_send_commands(self, device_id: str, commands: list[dict[str, Any]]) -> bool: ...


@dataclass(frozen=True)
class DeviceInfo:
    """Static/slow-changing device metadata. Excludes secrets such as ``local_key``."""

    device_id: str
    name: str | None
    product_id: str | None
    product_name: str | None
    category: str | None
    online: bool

    @classmethod
    def from_api(cls, device_id: str, result: dict[str, Any]) -> DeviceInfo:
        """Accept v2.0 details (snake_case), project list (camelCase) or legacy records."""

        def pick(*keys: str) -> Any:
            return next((result[k] for k in keys if result.get(k) is not None), None)

        return cls(
            device_id=device_id,
            name=pick("custom_name", "customName", "name"),
            product_id=pick("product_id", "productId"),
            product_name=pick("product_name", "productName"),
            category=pick("category"),
            online=bool(pick("is_online", "isOnline", "online")),
        )


@dataclass(frozen=True)
class PedestalSnapshot:
    info: DeviceInfo
    state: PedestalState


class PedestalApi:
    """High-level operations for one or more Pedestal devices."""

    def __init__(self, transport: CloudTransport) -> None:
        self._transport = transport
        self._specs: dict[str, DeviceSpecification] = {}

    async def async_get_specification(
        self, device_id: str, *, refresh: bool = False
    ) -> DeviceSpecification:
        """Return the device's DP specification (cached after the first call)."""
        if refresh or device_id not in self._specs:
            result = await self._transport.async_get_device_specification(device_id)
            self._specs[device_id] = DeviceSpecification.from_api(result)
        return self._specs[device_id]

    async def async_get_snapshot(self, device_id: str) -> PedestalSnapshot:
        details, status = await asyncio.gather(
            self._transport.async_get_device(device_id),
            self._transport.async_get_device_status(device_id),
        )
        return PedestalSnapshot(
            info=DeviceInfo.from_api(device_id, details),
            state=PedestalState.from_status(status),
        )

    async def async_set_power(self, device_id: str, on: bool) -> None:
        spec = await self.async_get_specification(device_id)
        await self._transport.async_send_commands(device_id, [power_command(on, spec)])

    async def async_set_mode(self, device_id: str, mode: str) -> None:
        spec = await self.async_get_specification(device_id)
        await self._transport.async_send_commands(device_id, [mode_command(mode, spec)])

    async def async_reset_filter(self, device_id: str) -> None:
        spec = await self.async_get_specification(device_id)
        await self._transport.async_send_commands(device_id, [reset_filter_command(spec)])
