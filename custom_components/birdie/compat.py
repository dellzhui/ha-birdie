"""Compatibility shims across supported Home Assistant versions."""

from __future__ import annotations

try:  # Home Assistant 2026.x and later
    from homeassistant.const import UnitOfRatio

    PARTS_PER_MILLION: str = UnitOfRatio.PARTS_PER_MILLION
except ImportError:  # older releases; the old constant is removed in 2027.8
    pass

try:  # newer releases
    from homeassistant.const import UnitOfDensity

    MICROGRAMS_PER_CUBIC_METER: str = UnitOfDensity.MICROGRAMS_PER_CUBIC_METER
except ImportError:  # older releases; the old constant is removed in 2027.8
    pass
