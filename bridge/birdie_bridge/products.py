"""Birdie products served by the bridge.

The Tuya product id (PID) is mapped to a Birdie model so clients never depend
on Tuya identifiers. Only mapped products are listed; each model defines which
commands it accepts (see ``model.py``).
"""

from __future__ import annotations

from collections.abc import Mapping

from .model import Model
from .pedestal import PEDESTAL

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
