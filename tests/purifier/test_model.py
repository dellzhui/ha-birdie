"""Tests for the air purifier DP model (synthetic data)."""

from __future__ import annotations

import pytest

from custom_components.birdie.purifier.model import (
    CommandError,
    PurifierState,
    apply_dps,
    fan_speed_dps,
    mode_dps,
    power_dps,
    reset_filter_dps,
)

from ..helpers import PURIFIER_DPS


def test_state_from_dps() -> None:
    state = PurifierState.from_dps(PURIFIER_DPS)
    assert state.power is True
    assert state.mode == "auto"
    assert state.fan_speed is None
    assert state.pm25 == 12
    assert state.air_quality == "good"
    assert state.filter_life_percent == 75
    assert state.filter_remaining_minutes == 97200
    assert state.filter_replace_needed is False
    assert state.filter_cover_open is False
    assert state.hardware_version == "1.2"
    assert state.fault == "none"
    assert state.has_fault is False
    assert state.reported("1", "3")
    assert not state.reported("999")


# DPs a real purifier reported over the LAN (2026-10-26 probe, protocol 3.4).
REAL_DEVICE_DPS = {
    "1": True,
    "2": 1,
    "3": "auto",
    "21": "good",
    "105": 129600,
    "106": 13311,
    "107": False,
    "109": True,
    "110": "HW1.0.0",
    "111": "pm25",
}


def test_state_from_real_device_report() -> None:
    state = PurifierState.from_dps(REAL_DEVICE_DPS)
    assert state.power is True
    assert state.mode == "auto"
    assert state.pm25 == 1
    assert state.air_quality == "good"
    assert state.filter_life_percent == 90
    assert state.filter_remaining_minutes == 116289
    assert state.filter_replace_needed is False
    assert state.filter_cover_open is False
    assert state.hardware_version == "HW1.0.0"
    assert state.fault == "pm25_sensor"
    assert not state.reported("108")


@pytest.mark.parametrize(
    ("value", "mode", "speed"),
    [
        ("auto", "auto", None),
        ("sleep", "sleep", None),
        ("manual1", "manual", 1),
        ("manual4", "manual", 4),
        ("manual5", None, None),
        ("manual", None, None),
        ("manual-1", None, None),
        ("turbo", None, None),
        (3, None, None),
        (None, None, None),
    ],
)
def test_mode_decoding(value: object, mode: str | None, speed: int | None) -> None:
    state = PurifierState.from_dps({"3": value})
    assert (state.mode, state.fan_speed) == (mode, speed)


@pytest.mark.parametrize(
    ("dps", "life", "remaining"),
    [
        ({"105": 1000, "106": 0}, 100, 1000),
        ({"105": 1000, "106": 1000}, 0, 0),
        ({"105": 1000, "106": 1500}, 0, 0),  # overused: clamp
        ({"105": 0, "106": 0}, None, 0),  # no total: no percentage
        ({"105": 1000}, None, None),
        ({"105": "1000", "106": 0}, None, None),
    ],
)
def test_filter_lifetime(dps: dict, life: int | None, remaining: int | None) -> None:
    state = PurifierState.from_dps(dps)
    assert (state.filter_life_percent, state.filter_remaining_minutes) == (life, remaining)


@pytest.mark.parametrize(
    ("dp", "value", "field", "expected"),
    [
        ("1", "true", "power", None),
        ("1", 1, "power", None),
        ("2", True, "pm25", None),
        ("2", 35.0, "pm25", 35),
        ("21", "excellent", "air_quality", None),
        ("107", True, "filter_replace_needed", True),
        ("109", False, "filter_cover_open", True),
        ("109", True, "filter_cover_open", False),
        ("110", "", "hardware_version", None),
        ("111", "ocp", "fault", "overcurrent"),
        ("111", "smoke", "fault", None),
        ("111", ["fan"], "fault", None),
    ],
)
def test_unexpected_values(dp: str, value: object, field: str, expected: object) -> None:
    assert getattr(PurifierState.from_dps({dp: value}), field) == expected


def test_unknown_dps_are_kept() -> None:
    state = PurifierState.from_dps({**PURIFIER_DPS, "150": 7})
    assert state.dps["150"] == 7


def test_commands() -> None:
    assert power_dps(False) == {"1": False}
    assert mode_dps("sleep") == {"3": "sleep"}
    assert fan_speed_dps(3) == {"3": "manual3"}
    assert reset_filter_dps() == {"108": True}
    for bad in ("manual", "manual2", "turbo"):
        with pytest.raises(CommandError):
            mode_dps(bad)
    for bad in (0, 5, True, 2.5):
        with pytest.raises(CommandError):
            fan_speed_dps(bad)  # type: ignore[arg-type]


def test_apply_dps() -> None:
    state = PurifierState.from_dps(PURIFIER_DPS)
    updated = apply_dps(state, {**power_dps(False), **fan_speed_dps(2)})
    assert (updated.power, updated.mode, updated.fan_speed) == (False, "manual", 2)
    # A filter reset is a trigger: no optimistic change.
    assert apply_dps(state, reset_filter_dps()) == state
