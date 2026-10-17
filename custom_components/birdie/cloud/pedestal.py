"""Birdie Pedestal air purifier data model.

Maps Tuya data points (DPs) to a typed state object and builds commands.
Nothing here performs I/O.

DP codes, types, ranges and transfer modes come from the product definition
in Birdie's Tuya Developer console (product "Birdie Pedestal", PID
a2zf7q0zxcutn5dy, screenshot received 2026-10-17). They describe the product
schema, not observed device behaviour: the meaning of the boolean DPs and the
effect of ``reset_filter``
are still unconfirmed. At runtime, commands are validated against the
specification the cloud reports for the device.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

_LOGGER = logging.getLogger(__name__)


class DP(StrEnum):
    """Data point codes of the Birdie Pedestal (rw = send and report, ro = report only)."""

    SWITCH = "switch"  # DP 1, bool, rw
    PM25 = "pm25"  # DP 2, value 0-500 ug/m3, ro
    MODE = "mode"  # DP 3, enum, rw
    AIR_QUALITY = "air_quality"  # DP 21, enum, ro
    FILTER_TOTAL_MINUTES = "filter_total_minutes"  # DP 105, value 0-129600 min, ro
    FILTER_USED_MINUTES = "filter_used_minutes"  # DP 106, value 0-129600 min, ro
    FILTER_STATUS = "filter_status"  # DP 107, bool, ro (true/false meaning unconfirmed)
    RESET_FILTER = "reset_filter"  # DP 108, bool, rw
    FILTER_COVER = "filter_cover"  # DP 109, bool, ro (true/false meaning unconfirmed)
    HW_VERSION = "hw_version"  # DP 110, string, ro (MCU hardware version)
    DEVICE_ERROR = "device_error"  # DP 111, enum, ro


DP_IDS: Mapping[DP, int] = {
    DP.SWITCH: 1,
    DP.PM25: 2,
    DP.MODE: 3,
    DP.AIR_QUALITY: 21,
    DP.FILTER_TOTAL_MINUTES: 105,
    DP.FILTER_USED_MINUTES: 106,
    DP.FILTER_STATUS: 107,
    DP.RESET_FILTER: 108,
    DP.FILTER_COVER: 109,
    DP.HW_VERSION: 110,
    DP.DEVICE_ERROR: 111,
}

MODES: tuple[str, ...] = ("auto", "sleep", "manual1", "manual2", "manual3", "manual4")
MANUAL_MODES: tuple[str, ...] = ("manual1", "manual2", "manual3", "manual4")
AIR_QUALITY_LEVELS: tuple[str, ...] = ("good", "moderate", "poor", "very_poor")
# "ocp" is presumably over-current protection; "normal" means no fault.
DEVICE_STATUSES: tuple[str, ...] = ("normal", "fan", "pm25", "ocp")
DEVICE_STATUS_OK = "normal"

# Bridge model key for this product (docs/bridge-api.md, section 3.1).
MODEL_PEDESTAL = "pedestal"

# ASSUMPTIONS to confirm on a real device: the console only says "Bool".
FILTER_STATUS_REPLACE_VALUE = True  # filter_status == True means "replace filter"
FILTER_COVER_OPEN_VALUE = True  # filter_cover == True means "cover open"


class CommandError(ValueError):
    """A command value is not valid for the device."""


# ------------------------------------------------------------------ helpers


def _to_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in ("true", "false"):
        return value.lower() == "true"
    return None


def _to_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_str(value: Any) -> str | None:
    return None if value is None else str(value)


# -------------------------------------------------------------- specification


@dataclass(frozen=True)
class DataPointSpec:
    """Type information for one data point as reported by the cloud."""

    code: str
    type: str
    values: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, item: Mapping[str, Any]) -> DataPointSpec:
        raw = item.get("values")
        values: Any = raw
        if isinstance(raw, str):
            try:
                values = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                values = {}
        return cls(
            code=str(item.get("code", "")),
            type=str(item.get("type", "")),
            values=values if isinstance(values, dict) else {},
        )

    def validate(self, value: Any) -> Any:
        """Return ``value`` if acceptable for this DP, else raise ``CommandError``."""
        # The API reports Boolean/Integer; the console labels them Bool/Value.
        kind = {"bool": "boolean", "value": "integer"}.get(self.type.lower(), self.type.lower())
        if kind == "boolean":
            if not isinstance(value, bool):
                raise CommandError(f"{self.code} expects a boolean")
        elif kind == "enum":
            allowed = self.values.get("range") or []
            if value not in allowed:
                raise CommandError(f"{self.code} expects one of {allowed}")
        elif kind == "integer":
            if isinstance(value, bool) or not isinstance(value, int):
                raise CommandError(f"{self.code} expects an integer")
            low, high = self.values.get("min"), self.values.get("max")
            if (low is not None and value < low) or (high is not None and value > high):
                raise CommandError(f"{self.code} expects a value in [{low}, {high}]")
        return value


@dataclass(frozen=True)
class DeviceSpecification:
    """Writable functions and readable status DPs reported by the cloud."""

    category: str | None
    functions: Mapping[str, DataPointSpec]
    status: Mapping[str, DataPointSpec]

    @classmethod
    def from_api(cls, result: Mapping[str, Any]) -> DeviceSpecification:
        def index(items: Iterable[Mapping[str, Any]] | None) -> dict[str, DataPointSpec]:
            specs = (DataPointSpec.from_api(item) for item in items or [])
            return {spec.code: spec for spec in specs if spec.code}

        return cls(
            category=result.get("category"),
            functions=index(result.get("functions")),
            status=index(result.get("status")),
        )

    def is_writable(self, code: str) -> bool:
        return code in self.functions


# ---------------------------------------------------------------------- state


@dataclass(frozen=True)
class PedestalState:
    """Decoded device state. ``raw`` keeps every reported DP, including unknown ones."""

    power: bool | None = None
    pm25: int | None = None
    mode: str | None = None
    air_quality: str | None = None
    filter_total_minutes: int | None = None
    filter_used_minutes: int | None = None
    filter_status: bool | None = None
    filter_cover: bool | None = None
    hw_version: str | None = None
    device_error: str | None = None
    raw: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_status(cls, status: Mapping[str, Any] | Iterable[Mapping[str, Any]]) -> PedestalState:
        """Build from a ``{code: value}`` map (bridge) or ``[{code, value}]`` list (Tuya)."""
        raw: dict[str, Any] = {}
        if isinstance(status, Mapping):
            raw.update((str(code), value) for code, value in status.items())
        else:
            for item in status:
                code = item.get("code")
                if isinstance(code, str):
                    raw[code] = item.get("value")
        return cls(
            power=_to_bool(raw.get(DP.SWITCH)),
            pm25=_to_int(raw.get(DP.PM25)),
            mode=_to_str(raw.get(DP.MODE)),
            air_quality=_to_str(raw.get(DP.AIR_QUALITY)),
            filter_total_minutes=_to_int(raw.get(DP.FILTER_TOTAL_MINUTES)),
            filter_used_minutes=_to_int(raw.get(DP.FILTER_USED_MINUTES)),
            filter_status=_to_bool(raw.get(DP.FILTER_STATUS)),
            filter_cover=_to_bool(raw.get(DP.FILTER_COVER)),
            hw_version=_to_str(raw.get(DP.HW_VERSION)),
            device_error=_to_str(raw.get(DP.DEVICE_ERROR)),
            raw=raw,
        )

    @property
    def filter_replace_needed(self) -> bool | None:
        if self.filter_status is None:
            return None
        return self.filter_status == FILTER_STATUS_REPLACE_VALUE

    @property
    def filter_cover_open(self) -> bool | None:
        if self.filter_cover is None:
            return None
        return self.filter_cover == FILTER_COVER_OPEN_VALUE

    @property
    def has_fault(self) -> bool | None:
        """True when DP 111 reports anything other than ``normal``."""
        if self.device_error is None:
            return None
        return self.device_error != DEVICE_STATUS_OK

    @property
    def filter_remaining_minutes(self) -> int | None:
        if self.filter_total_minutes is None or self.filter_used_minutes is None:
            return None
        return max(0, self.filter_total_minutes - self.filter_used_minutes)

    @property
    def filter_life_percent(self) -> float | None:
        """Remaining filter life in percent (0-100), derived from DP 105/106."""
        total, remaining = self.filter_total_minutes, self.filter_remaining_minutes
        if not total or total <= 0 or remaining is None:
            return None
        return round(min(100.0, remaining * 100.0 / total), 1)


# ------------------------------------------------------------------- commands


Command = dict[str, Any]


def _command(code: DP, value: Any, spec: DeviceSpecification | None) -> Command:
    if spec is not None:
        function = spec.functions.get(code)
        if function is None:
            raise CommandError(f"Device does not expose writable data point {code!s}")
        function.validate(value)
    return {"code": str(code), "value": value}


def power_command(on: bool, spec: DeviceSpecification | None = None) -> Command:
    return _command(DP.SWITCH, bool(on), spec)


def mode_command(mode: str, spec: DeviceSpecification | None = None) -> Command:
    if spec is None and mode not in MODES:
        raise CommandError(f"mode expects one of {list(MODES)}")
    return _command(DP.MODE, mode, spec)


def reset_filter_command(spec: DeviceSpecification | None = None) -> Command:
    # Assumes a boolean trigger DP; the runtime specification overrides this
    # assumption once a real device has been inspected.
    return _command(DP.RESET_FILTER, True, spec)
