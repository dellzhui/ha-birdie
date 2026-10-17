"""Birdie Pedestal air purifier: Tuya data points <-> bridge API.

This is the only place that knows the Pedestal's Tuya DP codes and values.
DP codes, types and ranges come from the product definition in Birdie's Tuya
Developer console (product "Birdie Pedestal", PID a2zf7q0zxcutn5dy). They
describe the product schema, not observed device behaviour; the assumptions
marked below are still to be confirmed on a real device
(docs/remote-testing.md). Fixing one only requires a bridge deployment, not a
Home Assistant release.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from typing import Any

from .model import Command, CommandRejected, Model, TuyaCommand, WritableFunctions

# Tuya DP codes (rw = send and report, ro = report only).
DP_SWITCH = "switch"  # DP 1, bool, rw
DP_PM25 = "pm25"  # DP 2, value 0-500 ug/m3, ro
DP_MODE = "mode"  # DP 3, enum auto/sleep/manual1-4, rw
DP_AIR_QUALITY = "air_quality"  # DP 21, enum, ro
DP_FILTER_TOTAL_MINUTES = "filter_total_minutes"  # DP 105, value 0-129600 min, ro
DP_FILTER_USED_MINUTES = "filter_used_minutes"  # DP 106, value 0-129600 min, ro
DP_FILTER_STATUS = "filter_status"  # DP 107, bool, ro
DP_RESET_FILTER = "reset_filter"  # DP 108, bool, rw
DP_FILTER_COVER = "filter_cover"  # DP 109, bool, ro
DP_HW_VERSION = "hw_version"  # DP 110, string, ro
DP_DEVICE_ERROR = "device_error"  # DP 111, enum normal/fan/pm25/ocp, ro

# Birdie modes. The Tuya ``mode`` DP mixes modes and speeds (manual1..4); the
# API separates them into ``mode`` and ``fan_speed``.
MODE_AUTO = "auto"
MODE_SLEEP = "sleep"
MODE_MANUAL = "manual"
FAN_SPEED_MIN = 1
FAN_SPEED_MAX = 4

AIR_QUALITY_LEVELS = frozenset({"good", "moderate", "poor", "very_poor"})

# Tuya ``device_error`` value -> Birdie fault. "ocp" is presumably
# over-current protection.
FAULTS: Mapping[str, str] = {
    "normal": "none",
    "fan": "fan",
    "pm25": "pm25_sensor",
    "ocp": "overcurrent",
}

# ASSUMPTIONS to confirm on a real device: the console only says "Bool".
FILTER_STATUS_REPLACE_VALUE = True  # filter_status == True means "replace filter"
FILTER_COVER_OPEN_VALUE = True  # filter_cover == True means "cover open"


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


def _known(value: Any, options: Collection[str]) -> bool:
    return isinstance(value, str) and value in options


def _flag(value: Any, true_means: bool) -> bool | None:
    flag = _to_bool(value)
    return None if flag is None else flag == true_means


def _decode_mode(value: Any) -> tuple[str | None, int | None]:
    if value in (MODE_AUTO, MODE_SLEEP):
        return value, None
    if isinstance(value, str) and value.startswith(MODE_MANUAL):
        speed = _to_int(value.removeprefix(MODE_MANUAL))
        if speed is not None and FAN_SPEED_MIN <= speed <= FAN_SPEED_MAX:
            return MODE_MANUAL, speed
    return None, None


# -------------------------------------------------------------------- state


def decode_state(status: Mapping[str, Any]) -> dict[str, Any]:
    """Return the Pedestal state object (docs/bridge-api.md, section 3.3)."""
    mode, fan_speed = _decode_mode(status.get(DP_MODE))
    total = _to_int(status.get(DP_FILTER_TOTAL_MINUTES))
    used = _to_int(status.get(DP_FILTER_USED_MINUTES))
    remaining = max(0, total - used) if total is not None and used is not None else None
    life = (
        round(min(100.0, remaining * 100.0 / total))
        if total and total > 0 and remaining is not None
        else None
    )
    air_quality = status.get(DP_AIR_QUALITY)
    fault = status.get(DP_DEVICE_ERROR)
    return {
        "power": _to_bool(status.get(DP_SWITCH)),
        "mode": mode,
        "fan_speed": fan_speed,
        "pm25": _to_int(status.get(DP_PM25)),
        "air_quality": air_quality if _known(air_quality, AIR_QUALITY_LEVELS) else None,
        "filter_life_percent": life,
        "filter_remaining_minutes": remaining,
        "filter_replace_needed": _flag(status.get(DP_FILTER_STATUS), FILTER_STATUS_REPLACE_VALUE),
        "filter_cover_open": _flag(status.get(DP_FILTER_COVER), FILTER_COVER_OPEN_VALUE),
        "fault": FAULTS.get(fault) if isinstance(fault, str) else None,
    }


def hardware_version(status: Mapping[str, Any]) -> str | None:
    value = status.get(DP_HW_VERSION)
    return str(value) if value not in (None, "") else None


# ----------------------------------------------------------------- commands


def _param(command: Command, name: str) -> Any:
    params = command["params"]
    if name not in params:
        raise CommandRejected(f"{command['name']} needs parameter {name!r}")
    return params[name]


def _check_mode_supported(value: str, functions: WritableFunctions) -> None:
    allowed = functions.get(DP_MODE, {}).get("range") or []
    if value not in allowed:
        raise CommandRejected("The device does not support this mode or speed")


def _translate(command: Command, functions: WritableFunctions) -> TuyaCommand:
    name = command["name"]
    value: Any
    if name == "set_power":
        on = _param(command, "on")
        if not isinstance(on, bool):
            raise CommandRejected("set_power expects a boolean 'on'")
        code, value = DP_SWITCH, on
    elif name == "set_mode":
        mode = _param(command, "mode")
        if mode not in (MODE_AUTO, MODE_SLEEP):
            raise CommandRejected(
                f"set_mode expects 'mode' of {MODE_AUTO!r} or {MODE_SLEEP!r}; "
                "use set_fan_speed for manual speeds"
            )
        _check_mode_supported(mode, functions)
        code, value = DP_MODE, mode
    elif name == "set_fan_speed":
        speed = _param(command, "speed")
        if (
            isinstance(speed, bool)
            or not isinstance(speed, int)
            or not FAN_SPEED_MIN <= speed <= FAN_SPEED_MAX
        ):
            raise CommandRejected(
                f"set_fan_speed expects an integer 'speed' from {FAN_SPEED_MIN} to {FAN_SPEED_MAX}"
            )
        value = f"{MODE_MANUAL}{speed}"
        _check_mode_supported(value, functions)
        code = DP_MODE
    elif name == "reset_filter":
        # ASSUMPTION: reset_filter is a trigger written as true.
        code, value = DP_RESET_FILTER, True
    else:
        raise CommandRejected(f"Unknown command {name!r}")
    if code not in functions:
        raise CommandRejected(f"The device does not accept {name}")
    return {"code": code, "value": value}


def translate_commands(
    commands: Sequence[Command], functions: WritableFunctions
) -> list[TuyaCommand]:
    """Translate Birdie commands into Tuya commands; raise ``CommandRejected``."""
    result = [_translate(command, functions) for command in commands]
    codes = [item["code"] for item in result]
    if len(set(codes)) != len(codes):
        raise CommandRejected("Conflicting commands in one request")
    return result


PEDESTAL = Model(
    key="pedestal",
    name="Birdie Pedestal",
    decode_state=decode_state,
    hardware_version=hardware_version,
    translate_commands=translate_commands,
)
