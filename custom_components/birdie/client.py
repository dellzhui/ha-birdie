"""BLE client for Birdie devices."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import logging
from typing import Any

from bleak import BleakClient
from bleak.backends.client import BaseBleakClient
from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from bleak_retry_connector import (
    close_stale_connections_by_address,
    establish_connection,
    get_device,
)

from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant

from .ble import bytes_to_hex, parse_characteristic_values, parse_known_characteristic
from .const import (
    BIRDIE_STATE_CHARACTERISTIC_UUID,
    BIRDIE_GATT_SERVICE_UUIDS,
    CONNECT_TIMEOUT,
    COOL_DOWN_PERIOD_CHARACTERISTIC_UUID,
    IAQ_CO2_THRESHOLD_CHARACTERISTIC_UUID,
    KNOWN_CHARACTERISTICS,
    NOTIFY_CHARACTERISTIC_UUIDS,
    READ_CHARACTERISTIC_UUIDS,
    READ_TIMEOUT,
    WRITE_TIMEOUT,
)

_LOGGER = logging.getLogger(__name__)

try:
    from bleak.backends.bluezdbus.client import BleakClientBlueZDBus
except ImportError:
    BleakClientBlueZDBus = None


if BleakClientBlueZDBus is not None:

    class BirdieBlueZClient(BleakClientBlueZDBus):
        """BlueZ client wrapper compatible with bleak-retry-connector."""

        async def connect(
            self, *args: Any, pair: bool = False, **kwargs: Any
        ) -> None:
            """Connect with the pair argument expected by bleak 2.x BlueZ."""
            await super().connect(pair=pair, **kwargs)

        async def start_notify(
            self, char_specifier: Any, callback: Any, **kwargs: Any
        ) -> None:
            """Start notify with the bluez argument expected by bleak 2.x BlueZ."""
            kwargs.setdefault("bluez", {})
            await super().start_notify(char_specifier, callback, **kwargs)

else:
    BirdieBlueZClient = None


DataCallback = Callable[[dict[str, Any]], None]
DisconnectCallback = Callable[[], None]


class BirdieBleError(Exception):
    """Base Birdie BLE error."""


class BirdieBleClient:
    """Manage a BLE connection to a Birdie device."""

    def __init__(
        self,
        hass: HomeAssistant,
        address: str,
        data_callback: DataCallback,
        disconnect_callback: DisconnectCallback,
    ) -> None:
        """Initialize the client."""
        self._hass = hass
        self.address = address
        self._data_callback = data_callback
        self._disconnect_callback = disconnect_callback
        self._client: BleakClient | None = None
        self._services: Any | None = None
        self._notify_characteristics: list[Any] = []
        self._expected_disconnect = False

    @property
    def is_connected(self) -> bool:
        """Return true if the BLE client is connected."""
        return bool(self._client and self._client.is_connected)

    async def async_connect(self) -> bool:
        """Connect to the BLE device."""
        if self.is_connected:
            return True
        if self._client is not None:
            self._client = None
            self._services = None
            self._notify_characteristics.clear()

        await close_stale_connections_by_address(self.address)

        ble_device, client_class, client_kwargs = await self._async_ble_device()
        if ble_device is None:
            return False

        self._expected_disconnect = False

        try:
            client = await establish_connection(
                client_class,
                ble_device,
                self.address,
                disconnected_callback=self._handle_disconnect,
                max_attempts=3,
                timeout=CONNECT_TIMEOUT,
                use_services_cache=False,
                **client_kwargs,
            )
        except (BleakError, TimeoutError, asyncio.TimeoutError, OSError):
            return False

        if not client.is_connected:
            return False

        self._client = client
        try:
            self._services = await self._async_get_services(client)
        except (
            BirdieBleError,
            BleakError,
            TimeoutError,
            asyncio.TimeoutError,
            OSError,
        ):
            await self.async_disconnect()
            return False

        _LOGGER.info("Connected to Birdie %s", self.address)
        return True

    async def _async_ble_device(
        self,
    ) -> tuple[BLEDevice | None, type[BaseBleakClient], dict[str, Any]]:
        """Return the BLE device and client class to use for this connection."""
        connectable_scanners = bluetooth.async_scanner_count(
            self._hass, connectable=True
        )
        ha_ble_device = bluetooth.async_ble_device_from_address(
            self._hass, self.address, connectable=True
        )

        if connectable_scanners and ha_ble_device is not None:
            return (
                ha_ble_device,
                BleakClient,
                {"services": BIRDIE_GATT_SERVICE_UUIDS},
            )

        bluez_device = await get_device(self.address)
        if bluez_device is not None and BirdieBlueZClient is not None:
            return (
                bluez_device,
                BirdieBlueZClient,
                {"bluez": {}, "services": BIRDIE_GATT_SERVICE_UUIDS},
            )

        if ha_ble_device is not None:
            return (
                ha_ble_device,
                BleakClient,
                {"services": BIRDIE_GATT_SERVICE_UUIDS},
            )

        return (
            bluez_device,
            BleakClient,
            {"services": BIRDIE_GATT_SERVICE_UUIDS},
        )

    async def async_read_initial(self) -> dict[str, Any]:
        """Read all known readable characteristics."""
        values: dict[str, Any] = {}
        for uuid in READ_CHARACTERISTIC_UUIDS:
            try:
                data = await self._async_read_uuid(uuid)
            except BirdieBleError:
                continue
            try:
                values.update(parse_characteristic_values(uuid, data))
                if uuid == BIRDIE_STATE_CHARACTERISTIC_UUID:
                    _LOGGER.debug(
                        "Initial Birdie state read from %s: raw=[%s] parsed=%s",
                        self.address,
                        bytes_to_hex(data),
                        parse_known_characteristic(uuid, data),
                    )
            except ValueError as err:
                _LOGGER.warning(
                    "Unable to parse %s from %s: raw=[%s] error=%s",
                    KNOWN_CHARACTERISTICS.get(uuid, uuid),
                    self.address,
                    bytes_to_hex(data),
                    err,
                )
        return values

    async def async_start_notify(self) -> None:
        """Subscribe to Birdie notification characteristics."""
        client = self._require_client()
        services = self._require_services()

        for uuid in NOTIFY_CHARACTERISTIC_UUIDS:
            characteristic = services.get_characteristic(uuid)
            if characteristic is None:
                continue
            if "notify" not in characteristic.properties:
                continue

            def _callback(
                *args: Any,
                characteristic_uuid: str = uuid,
            ) -> None:
                data = args[-1]
                self._handle_notify(characteristic_uuid, data)

            try:
                await asyncio.wait_for(
                    client.start_notify(characteristic, _callback),
                    timeout=WRITE_TIMEOUT,
                )
            except (BleakError, TimeoutError, asyncio.TimeoutError, OSError) as err:
                _LOGGER.warning(
                    "Unable to subscribe %s on %s: %s",
                    KNOWN_CHARACTERISTICS.get(uuid, uuid),
                    self.address,
                    err,
                )
                continue
            self._notify_characteristics.append(characteristic)
            if uuid == BIRDIE_STATE_CHARACTERISTIC_UUID:
                _LOGGER.debug(
                    "Subscribed to Birdie state notifications on %s",
                    self.address,
                )

    async def async_disconnect(self) -> None:
        """Disconnect and clean up notifications."""
        self._expected_disconnect = True
        client = self._client
        if client is None:
            self._services = None
            self._notify_characteristics.clear()
            return

        for characteristic in list(self._notify_characteristics):
            try:
                await client.stop_notify(characteristic)
            except (BleakError, TimeoutError, asyncio.TimeoutError, OSError):
                pass

        if client.is_connected:
            try:
                await client.disconnect()
            except (BleakError, TimeoutError, asyncio.TimeoutError, OSError):
                pass

        self._client = None
        self._services = None
        self._notify_characteristics.clear()

    async def async_write_co2_threshold(self, value: int) -> int:
        """Write CO2 threshold and return the value read back from the device."""
        payload = int(value).to_bytes(2, "little", signed=False)
        data = await self._async_write_then_read(
            IAQ_CO2_THRESHOLD_CHARACTERISTIC_UUID, payload
        )
        parsed = parse_characteristic_values(IAQ_CO2_THRESHOLD_CHARACTERISTIC_UUID, data)
        return int(parsed["co2_threshold_ppm"])

    async def async_write_cool_down(self, value: int) -> int:
        """Write cool down minutes and return the value read back from the device."""
        payload = int(value).to_bytes(1, "little", signed=False)
        data = await self._async_write_then_read(
            COOL_DOWN_PERIOD_CHARACTERISTIC_UUID, payload
        )
        parsed = parse_characteristic_values(COOL_DOWN_PERIOD_CHARACTERISTIC_UUID, data)
        return int(parsed["cool_down_minutes"])

    async def _async_write_then_read(self, uuid: str, payload: bytes) -> bytes:
        """Write a characteristic, then read it back."""
        client = self._require_client()
        characteristic = self._get_characteristic(uuid)
        try:
            await asyncio.wait_for(
                client.write_gatt_char(characteristic, payload, response=True),
                timeout=WRITE_TIMEOUT,
            )
            return await self._async_read_uuid(uuid)
        except (BleakError, TimeoutError, asyncio.TimeoutError, OSError) as err:
            raise BirdieBleError(
                f"failed to write {KNOWN_CHARACTERISTICS.get(uuid, uuid)}: {err}"
            ) from err

    async def _async_read_uuid(self, uuid: str) -> bytes:
        """Read a characteristic by UUID."""
        client = self._require_client()
        characteristic = self._get_characteristic(uuid)
        if "read" not in characteristic.properties:
            raise BirdieBleError(f"{uuid} is not readable")
        try:
            return bytes(
                await asyncio.wait_for(
                    client.read_gatt_char(characteristic), timeout=READ_TIMEOUT
                )
            )
        except (BleakError, TimeoutError, asyncio.TimeoutError, OSError) as err:
            raise BirdieBleError(
                f"failed to read {KNOWN_CHARACTERISTICS.get(uuid, uuid)}: {err}"
            ) from err

    async def _async_get_services(self, client: BleakClient) -> Any:
        """Return services across Bleak versions."""
        services = getattr(client, "services", None)
        if self._services_have_known_characteristics(services):
            return services
        get_services = getattr(client, "get_services", None)
        if get_services is not None:
            services = await get_services()
            if self._services_have_known_characteristics(services):
                return services

        raise BirdieBleError("GATT services do not include any Birdie characteristics")

    def _services_have_known_characteristics(self, services: Any | None) -> bool:
        """Return true if service discovery exposed at least one known characteristic."""
        if services is None or not hasattr(services, "get_characteristic"):
            return False
        return any(
            services.get_characteristic(uuid) is not None
            for uuid in set(READ_CHARACTERISTIC_UUIDS + NOTIFY_CHARACTERISTIC_UUIDS)
        )

    def _get_characteristic(self, uuid: str) -> Any:
        """Return a characteristic by UUID or raise."""
        services = self._require_services()
        characteristic = services.get_characteristic(uuid)
        if characteristic is None:
            raise BirdieBleError(f"{KNOWN_CHARACTERISTICS.get(uuid, uuid)} is missing")
        return characteristic

    def _require_client(self) -> BleakClient:
        """Return the connected client or raise."""
        if self._client is None or not self._client.is_connected:
            raise BirdieBleError(f"Birdie {self.address} is not connected")
        return self._client

    def _require_services(self) -> Any:
        """Return cached services or raise."""
        if self._services is None:
            raise BirdieBleError(f"Birdie {self.address} services are not available")
        return self._services

    def _handle_notify(self, uuid: str, data: bytearray) -> None:
        """Handle a BLE notification."""
        try:
            values = parse_characteristic_values(uuid, data)
        except ValueError as err:
            _LOGGER.warning(
                "Unable to parse notify %s from %s: raw=[%s] error=%s",
                KNOWN_CHARACTERISTICS.get(uuid, uuid),
                self.address,
                bytes_to_hex(data),
                err,
            )
            return
        if values:
            if uuid == BIRDIE_STATE_CHARACTERISTIC_UUID:
                _LOGGER.debug(
                    "Birdie state notify from %s: raw=[%s] parsed=%s",
                    self.address,
                    bytes_to_hex(data),
                    parse_known_characteristic(uuid, data),
                )
            self._hass.loop.call_soon_threadsafe(self._data_callback, values)

    def _handle_disconnect(self, client: BleakClient | None = None) -> None:
        """Handle BLE disconnect."""
        self._client = None
        self._services = None
        self._notify_characteristics.clear()
        if self._expected_disconnect:
            return
        _LOGGER.warning("Birdie %s disconnected", self.address)
        self._hass.loop.call_soon_threadsafe(self._disconnect_callback)
