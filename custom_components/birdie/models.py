"""Data models for the Birdie integration."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from .const import BIRDIE_STATE_DOWN, BIRDIE_STATE_VALUES, UNKNOWN_STATE


@dataclass(frozen=True, slots=True)
class BirdieData:
    """Latest parsed Birdie values."""

    address: str
    name: str | None = None
    available: bool = False
    co2_ppm: int | None = None
    temperature_celsius: float | None = None
    humidity_percent: float | None = None
    birdie_state: int | None = None
    battery_percent: int | None = None
    co2_threshold_ppm: int | None = None
    cool_down_minutes: int | None = None
    firmware_version: str | None = None
    hardware_version: str | None = None

    @property
    def birdie_state_value(self) -> str:
        """Return the Home Assistant state value for the Birdie state."""
        if self.birdie_state is None:
            return UNKNOWN_STATE
        return BIRDIE_STATE_VALUES.get(self.birdie_state, UNKNOWN_STATE)

    @property
    def co2_alarm(self) -> bool | None:
        """Return whether the Birdie CO2 alarm is active."""
        if self.birdie_state is None:
            return None
        return self.birdie_state == BIRDIE_STATE_DOWN

    def updated(self, **changes: Any) -> BirdieData:
        """Return a copy with selected fields updated."""
        return replace(self, **changes)
