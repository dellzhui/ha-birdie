"""Birdie Pedestal air purifier model.

Decodes the state object the Birdie cloud reports for a Pedestal and builds
the commands it accepts (docs/bridge-api.md, sections 3.3 and 4.5). The cloud
describes the device in Birdie terms; nothing here depends on the platform
the device is connected to. No I/O.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

MODEL_PEDESTAL = "pedestal"

MODE_AUTO = "auto"
MODE_SLEEP = "sleep"
MODE_MANUAL = "manual"
PRESET_MODES: tuple[str, ...] = (MODE_AUTO, MODE_SLEEP)
FAN_SPEED_MIN = 1
FAN_SPEED_MAX = 4

AIR_QUALITY_LEVELS: tuple[str, ...] = ("good", "moderate", "poor", "very_poor")
FAULTS: tuple[str, ...] = ("none", "fan", "pm25_sensor", "overcurrent")
FAULT_NONE = "none"


class CommandError(ValueError):
    """A command value is not valid for the device."""


def _to_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _to_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return int(value)


def _to_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


@dataclass(frozen=True)
class PedestalState:
    """Decoded state. ``raw`` keeps the state object as reported, including unknown fields."""

    power: bool | None = None
    mode: str | None = None
    fan_speed: int | None = None
    pm25: int | None = None
    air_quality: str | None = None
    filter_life_percent: int | None = None
    filter_remaining_minutes: int | None = None
    filter_replace_needed: bool | None = None
    filter_cover_open: bool | None = None
    fault: str | None = None
    raw: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, state: Mapping[str, Any]) -> PedestalState:
        """Build from the bridge's state object; unexpected types become None."""
        return cls(
            power=_to_bool(state.get("power")),
            mode=_to_str(state.get("mode")),
            fan_speed=_to_int(state.get("fan_speed")),
            pm25=_to_int(state.get("pm25")),
            air_quality=_to_str(state.get("air_quality")),
            filter_life_percent=_to_int(state.get("filter_life_percent")),
            filter_remaining_minutes=_to_int(state.get("filter_remaining_minutes")),
            filter_replace_needed=_to_bool(state.get("filter_replace_needed")),
            filter_cover_open=_to_bool(state.get("filter_cover_open")),
            fault=_to_str(state.get("fault")),
            raw=dict(state),
        )

    @property
    def has_fault(self) -> bool | None:
        if self.fault is None:
            return None
        return self.fault != FAULT_NONE


# ------------------------------------------------------------------- commands


Command = dict[str, Any]


def set_power_command(on: bool) -> Command:
    return {"name": "set_power", "params": {"on": bool(on)}}


def set_mode_command(mode: str) -> Command:
    if mode not in PRESET_MODES:
        raise CommandError(f"mode expects one of {list(PRESET_MODES)}")
    return {"name": "set_mode", "params": {"mode": mode}}


def set_fan_speed_command(speed: int) -> Command:
    if isinstance(speed, bool) or not FAN_SPEED_MIN <= speed <= FAN_SPEED_MAX:
        raise CommandError(f"fan speed expects {FAN_SPEED_MIN} to {FAN_SPEED_MAX}")
    return {"name": "set_fan_speed", "params": {"speed": int(speed)}}


def reset_filter_command() -> Command:
    return {"name": "reset_filter", "params": {}}


def apply_commands(state: PedestalState, commands: Iterable[Command]) -> PedestalState:
    """Return the state the commands are expected to produce (optimistic update)."""
    raw = dict(state.raw)
    for command in commands:
        params = command["params"]
        if command["name"] == "set_power":
            raw["power"] = params["on"]
        elif command["name"] == "set_mode":
            raw.update(mode=params["mode"], fan_speed=None)
        elif command["name"] == "set_fan_speed":
            raw.update(mode=MODE_MANUAL, fan_speed=params["speed"])
    return PedestalState.from_api(raw)
