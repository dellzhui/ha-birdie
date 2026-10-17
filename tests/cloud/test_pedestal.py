"""Tests for the Pedestal model (synthetic data)."""

from __future__ import annotations

import pytest

from custom_components.birdie.cloud.pedestal import (
    CommandError,
    PedestalState,
    apply_commands,
    reset_filter_command,
    set_fan_speed_command,
    set_mode_command,
    set_power_command,
)

from ..helpers import pedestal_device


def test_state_from_api() -> None:
    state = PedestalState.from_api(pedestal_device()["state"])
    assert state.power is True
    assert state.mode == "auto"
    assert state.fan_speed is None
    assert state.pm25 == 12
    assert state.air_quality == "good"
    assert state.filter_life_percent == 75
    assert state.filter_remaining_minutes == 97200
    assert state.filter_replace_needed is False
    assert state.filter_cover_open is False
    assert state.fault == "none"
    assert state.has_fault is False


def test_state_tolerates_missing_and_odd_values() -> None:
    state = PedestalState.from_api(
        {"power": "yes", "pm25": 35.7, "fan_speed": True, "fault": 3, "future_field": 1}
    )
    assert state.power is None
    assert state.pm25 == 35
    assert state.fan_speed is None
    assert state.fault is None
    assert state.mode is None
    assert state.raw["future_field"] == 1
    assert PedestalState().has_fault is None
    assert PedestalState.from_api({"fault": "overcurrent"}).has_fault is True


def test_commands() -> None:
    assert set_power_command(True) == {"name": "set_power", "params": {"on": True}}
    assert set_mode_command("sleep") == {"name": "set_mode", "params": {"mode": "sleep"}}
    assert set_fan_speed_command(3) == {"name": "set_fan_speed", "params": {"speed": 3}}
    assert reset_filter_command() == {"name": "reset_filter", "params": {}}


@pytest.mark.parametrize("mode", ["manual", "turbo", "manual2"])
def test_invalid_mode(mode: str) -> None:
    with pytest.raises(CommandError):
        set_mode_command(mode)


@pytest.mark.parametrize("speed", [0, 5, True])
def test_invalid_fan_speed(speed: int) -> None:
    with pytest.raises(CommandError):
        set_fan_speed_command(speed)


def test_apply_commands() -> None:
    state = PedestalState.from_api(pedestal_device()["state"])
    state = apply_commands(state, [set_power_command(False), set_fan_speed_command(2)])
    assert (state.power, state.mode, state.fan_speed) == (False, "manual", 2)
    state = apply_commands(state, [set_mode_command("auto"), reset_filter_command()])
    assert (state.mode, state.fan_speed) == ("auto", None)
    assert state.pm25 == 12  # untouched fields are kept
