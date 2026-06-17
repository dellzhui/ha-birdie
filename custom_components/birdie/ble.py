"""BLE helpers and parsers for Birdie devices."""

from __future__ import annotations

from .const import (
    BATTERY_LEVEL_CHARACTERISTIC_UUID,
    BATTERY_LEVEL_PERCENT,
    BIRDIE_NAME_PREFIXES,
    BIRDIE_STATE_CHARACTERISTIC_UUID,
    BIRDIE_STATE_DOWN,
    BIRDIE_STATE_NAMES,
    BIRDIE_STATE_VALUES,
    CO2_CHARACTERISTIC_UUID,
    COOL_DOWN_PERIOD_CHARACTERISTIC_UUID,
    ENVIRONMENTAL_SERVICE_UUID,
    FIRMWARE_REVISION_CHARACTERISTIC_UUID,
    HARDWARE_VERSION_CHARACTERISTIC_UUID,
    HUMIDITY_CHARACTERISTIC_UUID,
    IAQ_CO2_THRESHOLD_CHARACTERISTIC_UUID,
    TEMPERATURE_CHARACTERISTIC_UUID,
    UNKNOWN_STATE,
)


def _ensure_length(data: bytes | bytearray | memoryview, expected: int) -> bytes:
    """Return bytes if the payload length matches the expected length."""
    payload = bytes(data)
    if len(payload) != expected:
        raise ValueError(f"expected {expected} byte(s), got {len(payload)}")
    return payload


def uint8(data: bytes | bytearray | memoryview) -> int:
    """Parse a one-byte unsigned integer."""
    return _ensure_length(data, 1)[0]


def uint16_le(data: bytes | bytearray | memoryview) -> int:
    """Parse a two-byte little-endian unsigned integer."""
    return int.from_bytes(_ensure_length(data, 2), "little", signed=False)


def int16_le(data: bytes | bytearray | memoryview) -> int:
    """Parse a two-byte little-endian signed integer."""
    return int.from_bytes(_ensure_length(data, 2), "little", signed=True)


def uint32_le(data: bytes | bytearray | memoryview) -> int:
    """Parse a four-byte little-endian unsigned integer."""
    return int.from_bytes(_ensure_length(data, 4), "little", signed=False)


def int32_le(data: bytes | bytearray | memoryview) -> int:
    """Parse a four-byte little-endian signed integer."""
    return int.from_bytes(_ensure_length(data, 4), "little", signed=True)


def parse_version(data: bytes | bytearray | memoryview) -> str:
    """Parse a three-byte firmware or hardware version as major.minor.patch."""
    payload = _ensure_length(data, 3)
    return f"{payload[0]}.{payload[1]}.{payload[2]}"


def parse_co2_ppm(data: bytes | bytearray | memoryview) -> int:
    """Parse CO2 ppm."""
    return uint16_le(data)


def parse_temperature_celsius(data: bytes | bytearray | memoryview) -> float:
    """Parse temperature from either int16 or int32 millidegrees Celsius."""
    payload = bytes(data)
    if len(payload) == 2:
        return int16_le(payload) / 1000
    if len(payload) == 4:
        return int32_le(payload) / 1000
    raise ValueError(f"expected 2 or 4 byte(s), got {len(payload)}")


def parse_humidity_percent(data: bytes | bytearray | memoryview) -> float:
    """Parse relative humidity from either uint16 or uint32 milli-percent RH."""
    payload = bytes(data)
    if len(payload) == 2:
        return uint16_le(payload) / 1000
    if len(payload) == 4:
        return uint32_le(payload) / 1000
    raise ValueError(f"expected 2 or 4 byte(s), got {len(payload)}")


def parse_birdie_state(data: bytes | bytearray | memoryview) -> tuple[int, str, bool]:
    """Parse Birdie state and derive CO2 alarm state."""
    state = uint8(data)
    return state, BIRDIE_STATE_NAMES.get(state, f"UNKNOWN_{state}"), state == BIRDIE_STATE_DOWN


def parse_birdie_state_value(data: bytes | bytearray | memoryview) -> str:
    """Parse Birdie state as a Home Assistant state string."""
    return BIRDIE_STATE_VALUES.get(uint8(data), UNKNOWN_STATE)


def parse_battery_percent(data: bytes | bytearray | memoryview) -> int | None:
    """Parse the battery level enum as a percentage."""
    return BATTERY_LEVEL_PERCENT.get(uint8(data))


def parse_co2_threshold_ppm(data: bytes | bytearray | memoryview) -> int:
    """Parse the configured CO2 alarm threshold."""
    return uint16_le(data)


def parse_cool_down_minutes(data: bytes | bytearray | memoryview) -> int:
    """Parse the configured cool down period in minutes."""
    return uint8(data)


def bytes_to_hex(data: bytes | bytearray | memoryview) -> str:
    """Format bytes for logs."""
    return bytes(data).hex(" ")


def is_birdie_name(name: str | None) -> bool:
    """Return true when a BLE local name looks like a Birdie device."""
    return bool(name) and any(name.startswith(prefix) for prefix in BIRDIE_NAME_PREFIXES)


def is_birdie_advertisement(
    name: str | None, service_uuids: list[str] | tuple[str, ...] | set[str] | None
) -> bool:
    """Match Birdie advertisements, preferring the Environmental Service UUID."""
    normalized_uuids = {uuid.lower() for uuid in service_uuids or ()}
    if ENVIRONMENTAL_SERVICE_UUID in normalized_uuids:
        return True
    return is_birdie_name(name)


def normalize_address(address: str) -> str:
    """Normalize a BLE address for stable config entry identifiers."""
    return address.strip().upper()


def parse_characteristic_values(
    characteristic_uuid: str, data: bytes | bytearray | memoryview
) -> dict[str, int | float | str | None]:
    """Parse a characteristic payload into BirdieData field changes."""
    uuid = characteristic_uuid.lower()
    if uuid == CO2_CHARACTERISTIC_UUID:
        return {"co2_ppm": parse_co2_ppm(data)}
    if uuid == TEMPERATURE_CHARACTERISTIC_UUID:
        return {"temperature_celsius": parse_temperature_celsius(data)}
    if uuid == HUMIDITY_CHARACTERISTIC_UUID:
        return {"humidity_percent": parse_humidity_percent(data)}
    if uuid == BIRDIE_STATE_CHARACTERISTIC_UUID:
        state, _name, _alarm = parse_birdie_state(data)
        return {"birdie_state": state}
    if uuid == BATTERY_LEVEL_CHARACTERISTIC_UUID:
        return {"battery_percent": parse_battery_percent(data)}
    if uuid == IAQ_CO2_THRESHOLD_CHARACTERISTIC_UUID:
        return {"co2_threshold_ppm": parse_co2_threshold_ppm(data)}
    if uuid == COOL_DOWN_PERIOD_CHARACTERISTIC_UUID:
        return {"cool_down_minutes": parse_cool_down_minutes(data)}
    if uuid == FIRMWARE_REVISION_CHARACTERISTIC_UUID:
        return {"firmware_version": parse_version(data)}
    if uuid == HARDWARE_VERSION_CHARACTERISTIC_UUID:
        return {"hardware_version": parse_version(data)}
    return {}


def parse_known_characteristic(
    characteristic_uuid: str, data: bytes | bytearray | memoryview
) -> str:
    """Parse a known characteristic payload into a human-readable value."""
    uuid = characteristic_uuid.lower()
    if uuid == CO2_CHARACTERISTIC_UUID:
        return f"{parse_co2_ppm(data)} ppm"
    if uuid == TEMPERATURE_CHARACTERISTIC_UUID:
        return f"{parse_temperature_celsius(data):.3f} deg C"
    if uuid == HUMIDITY_CHARACTERISTIC_UUID:
        return f"{parse_humidity_percent(data):.3f} %RH"
    if uuid == BIRDIE_STATE_CHARACTERISTIC_UUID:
        state, name, alarm = parse_birdie_state(data)
        return f"{name} ({state}), CO2 alarm {'on' if alarm else 'off'}"
    if uuid == BATTERY_LEVEL_CHARACTERISTIC_UUID:
        raw = uint8(data)
        percent = parse_battery_percent(data)
        if percent is None:
            return f"unknown enum {raw}"
        return f"{percent}% (enum {raw})"
    if uuid == IAQ_CO2_THRESHOLD_CHARACTERISTIC_UUID:
        return f"{parse_co2_threshold_ppm(data)} ppm"
    if uuid == COOL_DOWN_PERIOD_CHARACTERISTIC_UUID:
        return f"{parse_cool_down_minutes(data)} min"
    if uuid in (FIRMWARE_REVISION_CHARACTERISTIC_UUID, HARDWARE_VERSION_CHARACTERISTIC_UUID):
        return parse_version(data)
    return "unparsed"
