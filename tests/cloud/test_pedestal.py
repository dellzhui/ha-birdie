"""Tests for the Pedestal model and transport-agnostic API (synthetic data)."""

from __future__ import annotations

import pytest

from custom_components.birdie.cloud.pedestal import (
    CommandError,
    DeviceSpecification,
    PedestalState,
    mode_command,
    power_command,
    reset_filter_command,
)

from ..helpers import load_fixture, pedestal_status


@pytest.fixture
def spec() -> DeviceSpecification:
    return DeviceSpecification.from_api(load_fixture("pedestal_specification.json"))


def test_state_from_status() -> None:
    state = PedestalState.from_status(load_fixture("pedestal_status.json"))
    assert state.power is True
    assert state.pm25 == 12
    assert state.mode == "auto"
    assert state.air_quality == "good"
    assert state.filter_remaining_minutes == 129600 - 32400
    assert state.filter_life_percent == 75.0
    assert state.filter_status is False
    assert state.filter_cover is False
    assert state.device_error == "normal"
    assert state.has_fault is False
    assert state.raw["hw_version"] == "1.0.0"


def test_state_tolerates_missing_and_odd_values() -> None:
    state = PedestalState.from_status(
        [
            {"code": "pm25", "value": "35"},
            {"code": "switch", "value": "false"},
            {"code": "filter_total_minutes", "value": 0},
            {"code": "filter_used_minutes", "value": 10},
            {"code": "future_dp", "value": 1},
            {"value": "no code"},
        ]
    )
    assert state.pm25 == 35
    assert state.power is False
    assert state.mode is None
    assert state.filter_life_percent is None  # total of 0 is not meaningful
    assert state.raw["future_dp"] == 1


def test_filter_life_clamped() -> None:
    state = PedestalState.from_status(
        [
            {"code": "filter_total_minutes", "value": 100},
            {"code": "filter_used_minutes", "value": 150},
        ]
    )
    assert state.filter_remaining_minutes == 0
    assert state.filter_life_percent == 0.0


def test_specification_parsing(spec: DeviceSpecification) -> None:
    assert spec.category == "kj"
    assert spec.is_writable("mode")
    assert not spec.is_writable("pm25")
    assert spec.functions["mode"].values["range"][0] == "auto"


def test_commands_validated_against_spec(spec: DeviceSpecification) -> None:
    assert power_command(True, spec) == {"code": "switch", "value": True}
    assert mode_command("manual3", spec) == {"code": "mode", "value": "manual3"}
    assert reset_filter_command(spec) == {"code": "reset_filter", "value": True}
    with pytest.raises(CommandError):
        mode_command("turbo", spec)


def test_commands_without_spec_use_known_modes() -> None:
    assert mode_command("sleep") == {"code": "mode", "value": "sleep"}
    with pytest.raises(CommandError):
        mode_command("turbo")


def test_command_rejected_when_dp_not_writable() -> None:
    spec = DeviceSpecification.from_api({"functions": [{"code": "switch", "type": "Boolean"}]})
    with pytest.raises(CommandError):
        reset_filter_command(spec)


def test_spec_driven_validation_overrides_assumptions() -> None:
    # If a real device reports reset_filter as an Enum, the boolean assumption is rejected.
    spec = DeviceSpecification.from_api(
        {"functions": [{"code": "reset_filter", "type": "Enum", "values": '{"range":["reset"]}'}]}
    )
    with pytest.raises(CommandError):
        reset_filter_command(spec)


def test_fault_detection() -> None:
    assert PedestalState.from_status([{"code": "device_error", "value": "ocp"}]).has_fault is True
    assert PedestalState().has_fault is None


def test_console_type_labels_are_accepted() -> None:
    # The Tuya console labels types Bool/Value; the API uses Boolean/Integer.
    spec = DeviceSpecification.from_api(
        {
            "functions": [
                {"code": "switch", "type": "Bool"},
                {"code": "x", "type": "Value", "values": '{"min":0,"max":5}'},
            ]
        }
    )
    assert spec.functions["switch"].validate(True) is True
    with pytest.raises(CommandError):
        spec.functions["switch"].validate(1)
    with pytest.raises(CommandError):
        spec.functions["x"].validate(6)


def test_state_from_bridge_mapping() -> None:
    state = PedestalState.from_status(pedestal_status())
    assert state.power is True
    assert state.mode == "auto"
    assert state.filter_life_percent == 75.0


def test_filter_flags_follow_assumed_semantics() -> None:
    state = PedestalState.from_status({"filter_status": True, "filter_cover": False})
    assert state.filter_replace_needed is True
    assert state.filter_cover_open is False
    assert PedestalState().filter_replace_needed is None
