"""Birdie products served by the bridge and the commands they accept.

The Tuya product id (PID) is mapped to a Birdie model key so the Home
Assistant integration never depends on Tuya identifiers. Only mapped products
are listed, and only allow-listed commands are forwarded to Tuya.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class CommandRule:
    """Validation for one writable DP. ``check`` returns an error message or None."""

    code: str
    check: Callable[[Any, Mapping[str, Any]], str | None]


@dataclass(frozen=True)
class Model:
    key: str
    name: str
    commands: Mapping[str, CommandRule] = field(default_factory=dict)


def _bool(value: Any, _spec_values: Mapping[str, Any]) -> str | None:
    return None if isinstance(value, bool) else "expects a boolean"


def _true(value: Any, _spec_values: Mapping[str, Any]) -> str | None:
    return None if value is True else "only accepts true"


def _enum_in_spec(value: Any, spec_values: Mapping[str, Any]) -> str | None:
    allowed = spec_values.get("range") or []
    if not isinstance(value, str) or value not in allowed:
        return f"expects one of {list(allowed)}"
    return None


PEDESTAL = Model(
    key="pedestal",
    name="Birdie Pedestal",
    commands={
        rule.code: rule
        for rule in (
            CommandRule("switch", _bool),
            CommandRule("mode", _enum_in_spec),
            CommandRule("reset_filter", _true),
        )
    },
)

MODELS: Mapping[str, Model] = {PEDESTAL.key: PEDESTAL}

# Tuya PID -> Birdie model key. Production PIDs belong here; additional PIDs
# (e.g. a developer's clone product) can be added through configuration.
DEFAULT_PRODUCT_MAP: Mapping[str, str] = {
    "a2zf7q0zxcutn5dy": PEDESTAL.key,  # Birdie Pedestal ("Developing" in Tuya console)
}


class ProductCatalog:
    """Resolves Tuya product ids to Birdie models."""

    def __init__(self, product_map: Mapping[str, str] | None = None) -> None:
        mapping = dict(DEFAULT_PRODUCT_MAP if product_map is None else product_map)
        unknown = {model for model in mapping.values() if model not in MODELS}
        if unknown:
            raise ValueError(f"Unknown model key(s) in product map: {sorted(unknown)}")
        self._map = mapping

    def model_for(self, product_id: str | None) -> Model | None:
        if not product_id:
            return None
        key = self._map.get(product_id)
        return MODELS[key] if key else None


def parse_product_map(value: str) -> dict[str, str]:
    """Parse ``pid=model,pid2=model`` (used for BRIDGE_EXTRA_PRODUCTS)."""
    result: dict[str, str] = {}
    for item in filter(None, (part.strip() for part in value.split(","))):
        pid, sep, model = item.partition("=")
        if not sep or not pid.strip() or not model.strip():
            raise ValueError(f"Invalid product mapping {item!r}; expected pid=model")
        result[pid.strip()] = model.strip()
    return result
