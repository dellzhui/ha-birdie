"""Asynchronous client for one Tuya Wi-Fi device on the local network.

tinytuya's ``Device`` does blocking socket I/O. Each ``TuyaLanDevice`` owns a
single worker thread that performs every tinytuya call, so calls are
serialised without locks and never block the event loop. A background loop
keeps a persistent connection open, sends heartbeats, receives the status
reports the device pushes, polls the full status now and then, and
reconnects with backoff when the connection drops.

Data points (DPs) are addressed by their numeric id as a string (``"1"``),
the way the LAN protocol reports them.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import socket
import time
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from typing import Any

import tinytuya

from .errors import DeviceUnreachableError, InvalidLocalKeyError, TuyaLanError
from .redact import register_secret, unregister_secret

_LOGGER = logging.getLogger(__name__)

DEFAULT_PORT = 6668
PROTOCOL_AUTO = "auto"
PROTOCOL_VERSIONS: tuple[str, ...] = ("3.1", "3.2", "3.3", "3.4", "3.5")
# Order tried when the version is unknown. 3.2 is only used when configured:
# tinytuya probes 3.2 devices with many requests.
AUTO_PROTOCOL_ORDER: tuple[str, ...] = ("3.3", "3.5", "3.4", "3.1")
LOCAL_KEY_LENGTH = 16

# tinytuya error codes (tinytuya.core.error_helper).
_ERR_CONNECT = "901"
_ERR_OFFLINE = "905"
_ERR_TIMEOUT = "902"
_ERR_STATE = "906"
_ERR_KEY_OR_VER = "914"
_ERR_JSON = "900"
_ERR_PAYLOAD = "904"
_UNREACHABLE_ERRORS = frozenset({_ERR_CONNECT, _ERR_OFFLINE})
_KEY_ERRORS = frozenset({_ERR_KEY_OR_VER, _ERR_PAYLOAD, _ERR_JSON})
_CONNECTION_LOST_ERRORS = frozenset(
    {_ERR_CONNECT, _ERR_OFFLINE, _ERR_TIMEOUT, _ERR_STATE, _ERR_KEY_OR_VER}
)

StatusCallback = Callable[[dict[str, Any]], None]
AvailabilityCallback = Callable[[bool, TuyaLanError | None], None]
ErrorCallback = Callable[[TuyaLanError], None]


@dataclass(frozen=True)
class DeviceConfig:
    """How to reach one device. ``protocol_version`` is a version or ``"auto"``."""

    device_id: str
    host: str
    local_key: str = field(repr=False)
    protocol_version: str = PROTOCOL_AUTO
    port: int = DEFAULT_PORT

    def __post_init__(self) -> None:
        if not self.device_id or not self.host:
            raise ValueError("device_id and host are required")
        if self.protocol_version != PROTOCOL_AUTO and self.protocol_version not in (
            PROTOCOL_VERSIONS
        ):
            raise ValueError(f"Unsupported protocol version {self.protocol_version!r}")


@dataclass(frozen=True)
class Timings:
    """Timeouts and intervals in seconds; tests shorten them."""

    connect_timeout: float = 5.0
    # Upper bound for how long a command waits behind a pending receive.
    receive_timeout: float = 0.5
    heartbeat_interval: float = 10.0
    # The device pushes changes; a full status read catches anything missed.
    status_interval: float = 60.0
    # No frame at all (heartbeat replies included) for this long: reconnect.
    dead_after: float = 35.0
    refresh_after_command: float = 3.0
    reconnect_min: float = 5.0
    reconnect_max: float = 60.0


class _ConnectionLost(DeviceUnreachableError):
    """The persistent connection broke; the loop reconnects."""


def _error_code(result: Any) -> str | None:
    if isinstance(result, dict) and "Err" in result:
        return str(result["Err"])
    return None


def _dps_of(result: Any) -> dict[str, Any] | None:
    if isinstance(result, dict) and "Err" not in result:
        dps = result.get("dps")
        if isinstance(dps, dict):
            return {str(key): value for key, value in dps.items()}
    return None


def _open_error(code: str | None, version: str) -> TuyaLanError:
    if code in _UNREACHABLE_ERRORS:
        return DeviceUnreachableError("Cannot connect to the device")
    if code in _KEY_ERRORS:
        return InvalidLocalKeyError("The device did not accept the local key")
    return DeviceUnreachableError(f"No valid answer from the device (protocol {version})")


class TuyaLanDevice:
    """One device: connect, read status, send DPs, stay connected."""

    def __init__(
        self,
        config: DeviceConfig,
        *,
        on_status: StatusCallback | None = None,
        on_availability: AvailabilityCallback | None = None,
        on_error: ErrorCallback | None = None,
        timings: Timings | None = None,
        version_hint: str | None = None,
    ) -> None:
        self._config = config
        self._on_status = on_status
        self._on_availability = on_availability
        self._on_error = on_error
        self._t = timings or Timings()
        self._version_hint = version_hint
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="birdie_tuya_lan")
        self._api: tinytuya.Device | None = None
        self._version: str | None = None
        self._dps: dict[str, Any] = {}
        self._task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._closed = False
        self._last_rx = 0.0
        self._last_heartbeat = 0.0
        self._last_status = 0.0
        self._refresh_at: float | None = None
        self._available = False
        self.last_error: TuyaLanError | None = None
        register_secret(config.local_key)

    def __repr__(self) -> str:
        return f"TuyaLanDevice({self._config!r})"

    # ---------------------------------------------------------- properties

    @property
    def config(self) -> DeviceConfig:
        return self._config

    @property
    def protocol_version(self) -> str | None:
        """The protocol version of the current or last working connection."""
        return self._version

    @property
    def available(self) -> bool:
        return self._available

    @property
    def dps(self) -> dict[str, Any]:
        """Last known value of every DP the device reported."""
        return dict(self._dps)

    # ----------------------------------------------------------- public API

    async def async_connect(self) -> dict[str, Any]:
        """Connect and read the full status; raise ``TuyaLanError`` on failure."""
        version, dps = await self._call(self._w_open)
        self._handle_connected(version, dps)
        return dict(self._dps)

    def start(self) -> None:
        """Start the background loop that keeps the device connected."""
        if self._task is None and not self._closed:
            self._task = asyncio.get_running_loop().create_task(
                self._run(), name=f"birdie_tuya_lan_{self._config.device_id[-6:]}"
            )

    async def async_close(self) -> None:
        """Stop the loop, close the connection and the worker thread."""
        if self._closed:
            return
        self._closed = True
        self._wake.set()
        self._interrupt_socket()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(self._executor, self._w_drop)
        await loop.run_in_executor(None, self._executor.shutdown)
        unregister_secret(self._config.local_key)

    async def async_refresh(self) -> dict[str, Any]:
        """Read the full status now."""
        self._require_connection()
        try:
            dps = await self._call(self._w_status)
        except _ConnectionLost as err:
            await self._async_lost(err)
            raise DeviceUnreachableError(str(err)) from err
        self._handle_dps(dps, full=True)
        return dict(self._dps)

    async def async_set_dps(self, dps: Mapping[str, Any]) -> None:
        """Send DP values. The device confirms with a status push."""
        if not dps:
            return
        self._require_connection()
        try:
            await self._call(self._w_set, {str(key): value for key, value in dps.items()})
        except _ConnectionLost as err:
            await self._async_lost(err)
            raise DeviceUnreachableError(str(err)) from err
        # Read back in case the device does not push the change.
        self._refresh_at = time.monotonic() + self._t.refresh_after_command

    async def async_reconfigure(
        self,
        *,
        host: str | None = None,
        local_key: str | None = None,
        protocol_version: str | None = None,
        port: int | None = None,
    ) -> None:
        """Use a new address, key or version; reconnects right away."""
        changes: dict[str, Any] = {}
        if host is not None:
            changes["host"] = host
        if port is not None:
            changes["port"] = port
        if local_key is not None:
            changes["local_key"] = local_key
        if protocol_version is not None:
            changes["protocol_version"] = protocol_version
        config = replace(self._config, **changes)
        if config == self._config:
            return
        old_key = self._config.local_key
        register_secret(config.local_key)
        self._config = config
        unregister_secret(old_key)
        self._interrupt_socket()
        await self._call(self._w_drop)
        if self._available:
            self._set_available(False, DeviceUnreachableError("Reconnecting with new settings"))
        self._wake.set()

    # ------------------------------------------------------------ the loop

    async def _run(self) -> None:
        backoff = self._t.reconnect_min
        while not self._closed:
            if self._api is None:
                try:
                    version, dps = await self._call(self._w_open)
                except TuyaLanError as err:
                    self.last_error = err
                    self._set_available(False, err)
                    if self._on_error is not None:
                        self._on_error(err)
                    _LOGGER.debug(
                        "Device %s not connected (%s); retrying in %.0fs",
                        self._config.device_id,
                        err,
                        backoff,
                    )
                    await self._sleep(backoff)
                    backoff = min(backoff * 2, self._t.reconnect_max)
                    continue
                backoff = self._t.reconnect_min
                self._handle_connected(version, dps)
                continue
            try:
                await self._step()
            except _ConnectionLost as err:
                await self._async_lost(err)
                await self._sleep(1.0)

    async def _step(self) -> None:
        now = time.monotonic()
        if now - self._last_rx > self._t.dead_after:
            raise _ConnectionLost("The device stopped answering")
        if now - self._last_status >= self._t.status_interval or (
            self._refresh_at is not None and now >= self._refresh_at
        ):
            self._refresh_at = None
            self._handle_dps(await self._call(self._w_status), full=True)
            return
        if now - self._last_heartbeat >= self._t.heartbeat_interval:
            await self._call(self._w_heartbeat)
        dps = await self._call(self._w_receive)
        if dps:
            self._handle_dps(dps, full=False)

    async def _sleep(self, seconds: float) -> None:
        self._wake.clear()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._wake.wait(), seconds)

    async def _async_lost(self, err: TuyaLanError) -> None:
        _LOGGER.debug("Connection to device %s lost: %s", self._config.device_id, err)
        self.last_error = err
        await self._call(self._w_drop)
        self._set_available(False, err)

    def _handle_connected(self, version: str, dps: dict[str, Any]) -> None:
        if version != self._version:
            _LOGGER.debug("Device %s uses protocol %s", self._config.device_id, version)
        self._version = version
        self.last_error = None
        now = time.monotonic()
        self._last_rx = self._last_heartbeat = now
        self._set_available(True, None)
        self._handle_dps(dps, full=True)

    def _handle_dps(self, dps: dict[str, Any], *, full: bool) -> None:
        now = time.monotonic()
        self._last_rx = now
        if full:
            self._last_status = now
            self._refresh_at = None
        changed = {key: value for key, value in dps.items() if self._dps.get(key) != value}
        new_keys = set(dps) - set(self._dps)
        self._dps.update(dps)
        if (changed or new_keys or full) and self._on_status is not None:
            self._on_status(dict(self._dps))

    def _set_available(self, available: bool, error: TuyaLanError | None) -> None:
        if available == self._available:
            return
        self._available = available
        if self._on_availability is not None:
            self._on_availability(available, error)

    def _require_connection(self) -> None:
        if self._closed:
            raise DeviceUnreachableError("The device connection is closed")
        if self._api is None:
            raise DeviceUnreachableError("The device is not connected")

    def _interrupt_socket(self) -> None:
        """Wake a worker blocked in recv() so shutdown is quick."""
        api = self._api
        sock = getattr(api, "socket", None) if api is not None else None
        if sock is not None:
            with contextlib.suppress(OSError):
                sock.shutdown(socket.SHUT_RDWR)

    async def _call(self, func: Callable[..., Any], *args: Any) -> Any:
        return await asyncio.get_running_loop().run_in_executor(self._executor, func, *args)

    # ------------------------------------------- worker thread (blocking)

    def _new_api(self, version: str) -> tinytuya.Device:
        config = self._config
        api = tinytuya.Device(
            config.device_id,
            address=config.host,
            local_key=config.local_key,
            version=float(version),
            persist=True,
            connection_timeout=self._t.connect_timeout,
            connection_retry_limit=1,
            connection_retry_delay=0,
            port=config.port,
        )
        # Only 3.2 devices need tinytuya's "device22" variant; letting it
        # auto-switch can leave other devices unresponsive.
        api.disabledetect = version != "3.2"
        return api

    def _versions_to_try(self) -> list[str]:
        configured = self._config.protocol_version
        if configured != PROTOCOL_AUTO:
            return [configured]
        order = list(AUTO_PROTOCOL_ORDER)
        for preferred in (self._version_hint, self._version):
            if preferred in PROTOCOL_VERSIONS:
                with contextlib.suppress(ValueError):
                    order.remove(preferred)
                order.insert(0, preferred)
        return order

    def _w_open(self) -> tuple[str, dict[str, Any]]:
        self._w_drop()
        errors: list[TuyaLanError] = []
        for version in self._versions_to_try():
            api = self._new_api(version)
            result = api.status()
            dps = _dps_of(result)
            if dps is not None and api.socket is not None:
                self._api = api
                return version, dps
            api.close()
            code = _error_code(result)
            error = _open_error(code, version)
            _LOGGER.debug(
                "Device %s: protocol %s failed (%s)", self._config.device_id, version, code
            )
            if code in _UNREACHABLE_ERRORS:
                # Nothing listens at the address; other versions will not help.
                raise error
            errors.append(error)
        for error in errors:
            if isinstance(error, InvalidLocalKeyError):
                raise error
        raise errors[-1] if errors else DeviceUnreachableError("No protocol version to try")

    def _w_drop(self) -> None:
        api, self._api = self._api, None
        if api is not None:
            with contextlib.suppress(Exception):
                api.close()

    def _w_api(self) -> tinytuya.Device:
        api = self._api
        if api is None or api.socket is None:
            raise _ConnectionLost("Not connected")
        return api

    def _w_status(self) -> dict[str, Any]:
        api = self._w_api()
        result = api.status()
        dps = _dps_of(result)
        if dps is None or api.socket is None:
            raise _ConnectionLost(f"Status request failed ({_error_code(result) or 'no data'})")
        return dps

    def _w_heartbeat(self) -> None:
        api = self._w_api()
        result = api.heartbeat(nowait=True)
        self._last_heartbeat = time.monotonic()
        if _error_code(result) is not None or api.socket is None:
            raise _ConnectionLost("Heartbeat failed")

    def _w_receive(self) -> dict[str, Any] | None:
        api = self._w_api()
        api.set_socketTimeout(self._t.receive_timeout)
        try:
            result = api.receive()
        finally:
            if api.socket is not None:
                api.set_socketTimeout(self._t.connect_timeout)
            else:
                api.connection_timeout = self._t.connect_timeout
        if getattr(api, "raw_recv", None):
            self._last_rx = time.monotonic()
        code = _error_code(result)
        if api.socket is None or code in _CONNECTION_LOST_ERRORS:
            raise _ConnectionLost(f"Connection closed ({code or 'no data'})")
        return _dps_of(result)

    def _w_set(self, dps: dict[str, Any]) -> None:
        api = self._w_api()
        result = api.set_multiple_values(dps, nowait=True)
        if _error_code(result) is not None or api.socket is None:
            raise _ConnectionLost(f"Sending the command failed ({_error_code(result)})")
