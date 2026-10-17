"""What the bridge needs to know about a Birdie model.

Each model translates between the Tuya data points (DPs) of its product and
the Birdie terms of the bridge API (docs/bridge-api.md, sections 3.3 and 4.5).
Clients never see Tuya DP codes or values.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

# A command as received from the client: {"name": str, "params": {...}}.
Command = Mapping[str, Any]
# A Tuya command: {"code": str, "value": Any}.
TuyaCommand = dict[str, Any]
# Writable DPs reported by Tuya for one device: code -> decoded ``values``.
WritableFunctions = Mapping[str, Mapping[str, Any]]


class CommandRejected(ValueError):
    """A command is unknown for the model or has invalid parameters (HTTP 422)."""


@dataclass(frozen=True)
class Model:
    """A Birdie model and its Tuya translation."""

    key: str
    name: str
    # Tuya status ({code: value}) -> Birdie state object.
    decode_state: Callable[[Mapping[str, Any]], dict[str, Any]]
    # Tuya status -> hardware version string, if the product reports one.
    hardware_version: Callable[[Mapping[str, Any]], str | None]
    # Birdie commands -> Tuya commands, validated against the device's DPs.
    # Raises CommandRejected.
    translate_commands: Callable[[Sequence[Command], WritableFunctions], list[TuyaCommand]]
