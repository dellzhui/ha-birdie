"""Unit tests for the Pedestal translation between Tuya DPs and the bridge API."""

from __future__ import annotations

from typing import Any

import pytest

from birdie_bridge.model import CommandRejected
from birdie_bridge.pedestal import decode_state, hardware_version, translate_commands

from .fake_tuya import load_fixture

ALL_MODES = {"range": ["auto", "sleep", "manual1", "manual2", "manual3", "manual4"]}
FUNCTIONS: dict[str, dict[str, Any]] = {"switch": {}, "mode": ALL_MODES, "reset_filter": {}}


def status(**values: Any) -> dict[str, Any]:
    return dict(values)


def fixture_status() -> dict[str, Any]:
    return {item["code"]: item["value"] for item in load_fixture("pedestal_status.json")}


def test_decode_fixture() -> None:
    assert decode_state(fixture_status()) == {
        "power": True,
        "mode": "auto",
        "fan_speed": None,
        "pm25": 12,
        "air_quality": "good",
        "filter_life_percent": 75,
        "filter_remaining_minutes": 97200,
        "filter_replace_needed": False,
        "filter_cover_open": False,
        "fault": "none",
    }
    assert hardware_version(fixture_status()) == "1.0.0"


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("auto", ("auto", None)),
        ("sleep", ("sleep", None)),
        ("manual1", ("manual", 1)),
        ("manual4", ("manual", 4)),
        ("manual5", (None, None)),
        ("manualx", (None, None)),
        ("turbo", (None, None)),
        (None, (None, None)),
    ],
)
def test_decode_mode(mode: Any, expected: tuple[Any, Any]) -> None:
    state = decode_state(status(mode=mode))
    assert (state["mode"], state["fan_speed"]) == expected


def test_decode_missing_and_odd_values() -> None:
    state = decode_state(
        status(
            switch="false",
            pm25="35",
            air_quality="terrible",
            device_error=["not", "a", "string"],
            filter_total_minutes=0,
            filter_used_minutes=10,
        )
    )
    assert state["power"] is False
    assert state["pm25"] == 35
    assert state["air_quality"] is None
    assert state["fault"] is None
    assert state["filter_life_percent"] is None  # a total of 0 is not meaningful
    assert state["filter_remaining_minutes"] == 0
    assert decode_state({}) == dict.fromkeys(decode_state({}), None)
    assert hardware_version({}) is None


def test_decode_filter_clamped_and_flags() -> None:
    state = decode_state(
        status(
            filter_total_minutes=100,
            filter_used_minutes=150,
            filter_status=True,
            filter_cover=True,
            device_error="ocp",
        )
    )
    assert state["filter_life_percent"] == 0
    assert state["filter_replace_needed"] is True
    assert state["filter_cover_open"] is True
    assert state["fault"] == "overcurrent"


def test_translate_commands() -> None:
    commands = [
        {"name": "set_power", "params": {"on": True}},
        {"name": "set_fan_speed", "params": {"speed": 2}},
    ]
    assert translate_commands(commands, FUNCTIONS) == [
        {"code": "switch", "value": True},
        {"code": "mode", "value": "manual2"},
    ]
    assert translate_commands([{"name": "set_mode", "params": {"mode": "auto"}}], FUNCTIONS) == [
        {"code": "mode", "value": "auto"}
    ]
    assert translate_commands([{"name": "reset_filter", "params": {}}], FUNCTIONS) == [
        {"code": "reset_filter", "value": True}
    ]


def test_translate_respects_device_functions() -> None:
    functions = {"switch": {}, "mode": {"range": ["auto", "manual1", "manual2"]}}
    for command in (
        {"name": "set_mode", "params": {"mode": "sleep"}},
        {"name": "set_fan_speed", "params": {"speed": 3}},
        {"name": "reset_filter", "params": {}},
    ):
        with pytest.raises(CommandRejected):
            translate_commands([command], functions)


@pytest.mark.parametrize(
    "command",
    [
        {"name": "set_power", "params": {}},
        {"name": "set_power", "params": {"on": 1}},
        {"name": "set_mode", "params": {"mode": "manual"}},
        {"name": "set_fan_speed", "params": {"speed": 0}},
        {"name": "set_fan_speed", "params": {"speed": 2.0}},
        {"name": "unknown", "params": {}},
    ],
)
def test_translate_rejects(command: dict[str, Any]) -> None:
    with pytest.raises(CommandRejected):
        translate_commands([command], FUNCTIONS)


def test_translate_rejects_conflicts() -> None:
    with pytest.raises(CommandRejected):
        translate_commands(
            [
                {"name": "set_power", "params": {"on": True}},
                {"name": "set_power", "params": {"on": False}},
            ],
            FUNCTIONS,
        )
