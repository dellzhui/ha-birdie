"""Tuya Cloud data center endpoints.

Source: https://developer.tuya.com/en/docs/iot/api-request?id=Ka4a8uuo1j4t4
"""

from __future__ import annotations

# Keys are short, stable identifiers used in configuration (e.g. TUYA_REGION).
ENDPOINTS: dict[str, str] = {
    "cn": "https://openapi.tuyacn.com",
    "us": "https://openapi.tuyaus.com",
    "us_east": "https://openapi-ueaz.tuyaus.com",
    "eu": "https://openapi.tuyaeu.com",
    "eu_west": "https://openapi-weaz.tuyaeu.com",
    "in": "https://openapi.tuyain.com",
    "sg": "https://openapi-sg.iotbing.com",
}


def resolve_endpoint(region_or_url: str) -> str:
    """Return the base URL for a region key, or the value itself if it is a URL."""
    value = region_or_url.strip()
    if value.startswith("https://"):
        return value.rstrip("/")
    try:
        return ENDPOINTS[value.lower()]
    except KeyError:
        known = ", ".join(sorted(ENDPOINTS))
        raise ValueError(f"Unknown Tuya region {value!r}; expected one of: {known}") from None
