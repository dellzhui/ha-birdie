"""Birdie air purifier: Tuya data points <-> Birdie state and commands.

This is the only place that knows the purifier's data points (DPs). DP ids,
codes, types and ranges come from the product definition in Birdie's Tuya
Developer console (product "Birdie Pedestal", PID a2zf7q0zxcutn5dy).

Observed on a real purifier over the LAN (protocol 3.4, firmware HW1.0.0,
2026-10-26 probe; docs/remote-testing.md): DPs 1, 2, 3, 21, 105, 106, 107,
109, 110 and 111 are reported, 108 is not; writes to 1 and 3 work, including
every mode; changes made in the Birdie app are pushed within a second. The
remaining assumptions are marked below. No I/O.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from typing import Any

# Tuya product ids of supported purifiers (the "productKey" in LAN broadcasts).
PRODUCT_KEYS: frozenset[str] = frozenset({"a2zf7q0zxcutn5dy"})

# The customer has called the product both "Pedestal" and "Podium"; the
# user-facing name stays neutral until Birdie confirms it.
MODEL_NAME = "Air Purifier"

# DP ids (rw = send and report, ro = report only, per the console).
DP_SWITCH = "1"  # switch, bool, rw
DP_PM25 = "2"  # pm25, value 0-500 ug/m3, ro
DP_MODE = "3"  # mode, enum auto/sleep/manual1-4, rw
DP_AIR_QUALITY = "21"  # air_quality, enum, ro
DP_FILTER_TOTAL_MINUTES = "105"  # filter_total_minutes, value 0-129600 min, ro
DP_FILTER_USED_MINUTES = "106"  # filter_used_minutes, value 0-129600 min, ro
DP_FILTER_STATUS = "107"  # filter_status, bool, ro
DP_RESET_FILTER = "108"  # reset_filter, bool, rw
DP_FILTER_COVER = "109"  # filter_cover, bool, ro
DP_HW_VERSION = "110"  # hw_version, string, ro
DP_DEVICE_ERROR = "111"  # device_error, enum normal/fan/pm25/ocp, ro

# The device's ``mode`` DP mixes modes and speeds (manual1..4). Home
# Assistant separates them into presets and a speed.
MODE_AUTO = "auto"
MODE_SLEEP = "sleep"
MODE_MANUAL = "manual"
PRESET_MODES: tuple[str, ...] = (MODE_AUTO, MODE_SLEEP)
FAN_SPEED_MIN = 1
FAN_SPEED_MAX = 4

AIR_QUALITY_LEVELS: tuple[str, ...] = ("good", "moderate", "poor", "very_poor")

# ``device_error`` value -> fault. "ocp" is presumably over-current protection.
_FAULTS_BY_DP: Mapping[str, str] = {
    "normal": "none",
    "fan": "fan",
    "pm25": "pm25_sensor",
    "ocp": "overcurrent",
}
FAULTS: tuple[str, ...] = tuple(_FAULTS_BY_DP.values())
FAULT_NONE = "none"
FAULT_PM25_SENSOR = "pm25_sensor"

# The console only says "Bool" for these.
# ASSUMPTION: filter_status == True means "replace filter" (false seen with 90 % left).
FILTER_STATUS_REPLACE_VALUE = True
# Observed and confirmed by Birdie: filter_cover is true in normal operation
# and false while the cover is open.
FILTER_COVER_OPEN_VALUE = False
# ASSUMPTION: reset_filter is a trigger written as true (not tested; the
# device does not report DP 108).
RESET_FILTER_VALUE = True


class CommandError(ValueError):
    """A command value is not valid for the device."""


# ------------------------------------------------------------------ helpers


def _to_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _to_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return int(value)


def _known(value: Any, options: Collection[str]) -> str | None:
    return value if isinstance(value, str) and value in options else None


def _flag(value: Any, true_means: bool) -> bool | None:
    flag = _to_bool(value)
    return None if flag is None else flag == true_means


def _decode_mode(value: Any) -> tuple[str | None, int | None]:
    if value in PRESET_MODES:
        return value, None
    if isinstance(value, str) and value.startswith(MODE_MANUAL):
        suffix = value.removeprefix(MODE_MANUAL)
        if suffix.isdigit() and FAN_SPEED_MIN <= int(suffix) <= FAN_SPEED_MAX:
            return MODE_MANUAL, int(suffix)
    return None, None


# -------------------------------------------------------------------- state


@dataclass(frozen=True)
class PurifierState:
    """Decoded state; ``dps`` keeps every DP as reported, including unknown ones."""

    power: bool | None = None
    mode: str | None = None
    fan_speed: int | None = None
    pm25: int | None = None
    air_quality: str | None = None
    filter_life_percent: int | None = None
    filter_remaining_minutes: int | None = None
    filter_replace_needed: bool | None = None
    filter_cover_open: bool | None = None
    hardware_version: str | None = None
    fault: str | None = None
    dps: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dps(cls, dps: Mapping[str, Any]) -> PurifierState:
        """Decode DP values; unexpected types or values become None."""
        mode, fan_speed = _decode_mode(dps.get(DP_MODE))
        total = _to_int(dps.get(DP_FILTER_TOTAL_MINUTES))
        used = _to_int(dps.get(DP_FILTER_USED_MINUTES))
        remaining = max(0, total - used) if total is not None and used is not None else None
        life = (
            round(min(100.0, remaining * 100.0 / total))
            if total is not None and total > 0 and remaining is not None
            else None
        )
        error = dps.get(DP_DEVICE_ERROR)
        fault = _FAULTS_BY_DP.get(error) if isinstance(error, str) else None
        hardware = dps.get(DP_HW_VERSION)
        # With a broken PM2.5 sensor the device keeps reporting a meaningless
        # PM2.5 value (1 on a real unit) and an air quality presumably derived
        # from it; show both as unknown instead.
        sensor_ok = fault != FAULT_PM25_SENSOR
        return cls(
            power=_to_bool(dps.get(DP_SWITCH)),
            mode=mode,
            fan_speed=fan_speed,
            pm25=_to_int(dps.get(DP_PM25)) if sensor_ok else None,
            air_quality=(
                _known(dps.get(DP_AIR_QUALITY), AIR_QUALITY_LEVELS) if sensor_ok else None
            ),
            filter_life_percent=life,
            filter_remaining_minutes=remaining,
            filter_replace_needed=_flag(dps.get(DP_FILTER_STATUS), FILTER_STATUS_REPLACE_VALUE),
            filter_cover_open=_flag(dps.get(DP_FILTER_COVER), FILTER_COVER_OPEN_VALUE),
            hardware_version=str(hardware) if isinstance(hardware, str) and hardware else None,
            fault=fault,
            dps=dict(dps),
        )

    def reported(self, *dp_ids: str) -> bool:
        """True if the device has reported all of these DPs."""
        return all(dp_id in self.dps for dp_id in dp_ids)

    @property
    def has_fault(self) -> bool | None:
        if self.fault is None:
            return None
        return self.fault != FAULT_NONE


# ----------------------------------------------------------------- commands


def power_dps(on: bool) -> dict[str, Any]:
    return {DP_SWITCH: bool(on)}


def mode_dps(mode: str) -> dict[str, Any]:
    if mode not in PRESET_MODES:
        raise CommandError(f"mode expects one of {list(PRESET_MODES)}")
    return {DP_MODE: mode}


def fan_speed_dps(speed: int) -> dict[str, Any]:
    if isinstance(speed, bool) or not isinstance(speed, int):
        raise CommandError("fan speed must be an integer")
    if not FAN_SPEED_MIN <= speed <= FAN_SPEED_MAX:
        raise CommandError(f"fan speed expects {FAN_SPEED_MIN} to {FAN_SPEED_MAX}")
    return {DP_MODE: f"{MODE_MANUAL}{speed}"}


def reset_filter_dps() -> dict[str, Any]:
    return {DP_RESET_FILTER: RESET_FILTER_VALUE}


def apply_dps(state: PurifierState, dps: Mapping[str, Any]) -> PurifierState:
    """The state the DP writes are expected to produce (optimistic update)."""
    written = {key: value for key, value in dps.items() if key != DP_RESET_FILTER}
    return PurifierState.from_dps({**state.dps, **written})
