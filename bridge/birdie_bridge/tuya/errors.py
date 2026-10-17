"""Exceptions raised by the Tuya Cloud client.

Callers should only need to distinguish a few categories:

* ``TuyaAuthError``          - credentials/signature/permission problem; retrying won't help.
* ``TuyaPlanError``          - cloud plan, API subscription or quota problem on the project.
* ``TuyaTokenError``         - access token invalid/expired; the client retries once itself.
* ``TuyaDeviceOfflineError`` - the device is not connected to the cloud.
* ``TuyaCommandError``       - the device rejected the command code or value.
* ``TuyaRateLimitError``     - request frequency limit hit; back off.
* ``TuyaConnectionError``    - network/timeout/malformed response; transient.

Codes: https://developer.tuya.com/en/docs/iot/error-code?id=K989ruxx88swc
"""

from __future__ import annotations


class TuyaError(Exception):
    """Base class for all Tuya client errors."""


class TuyaConnectionError(TuyaError):
    """Network failure, timeout, HTTP error or an unparsable response."""


class TuyaApiError(TuyaError):
    """The API returned ``success: false``."""

    def __init__(self, code: int | None, msg: str, tid: str | None = None) -> None:
        self.code = code
        self.msg = msg
        self.tid = tid
        detail = f"Tuya API error {code}: {msg}"
        if tid:
            detail += f" (tid={tid})"
        super().__init__(detail)


class TuyaAuthError(TuyaApiError):
    """Invalid credentials or signature, or missing API/device permission."""


class TuyaPlanError(TuyaApiError):
    """Cloud plan missing/expired, API not subscribed, or call quota exhausted."""


class TuyaTokenError(TuyaApiError):
    """The access token is invalid or expired."""


class TuyaDeviceOfflineError(TuyaApiError):
    """The target device is offline."""


class TuyaCommandError(TuyaApiError):
    """The device does not support the command code or value."""


class TuyaRateLimitError(TuyaApiError):
    """A request frequency limit was exceeded."""


# Single place to adjust when Tuya documents new codes.
_CODE_CLASSES: dict[int, type[TuyaApiError]] = {
    # token
    1010: TuyaTokenError,  # token is expired
    1011: TuyaTokenError,  # token invalid
    1012: TuyaTokenError,  # token status invalid
    1400: TuyaTokenError,  # token invalid
    # credentials / permission
    1001: TuyaAuthError,  # secret invalid
    1002: TuyaAuthError,  # access_token is null
    1004: TuyaAuthError,  # sign invalid
    1005: TuyaAuthError,  # clientId invalid
    1106: TuyaAuthError,  # permission deny
    1114: TuyaAuthError,  # IP not in allowlist
    2007: TuyaAuthError,  # IP cross-region
    60009001: TuyaAuthError,  # not in the permission package
    # cloud plan / subscription / quota
    28841001: TuyaPlanError,  # no cloud development plan
    28841002: TuyaPlanError,  # plan expired
    28841003: TuyaPlanError,  # bill overdue
    28841004: TuyaPlanError,  # trial edition quota exhausted
    28841101: TuyaPlanError,  # API not subscribed
    28841102: TuyaPlanError,  # API subscription expired
    28841103: TuyaPlanError,  # API bill overdue
    28841104: TuyaPlanError,  # API quota exhausted
    28841105: TuyaPlanError,  # project not authorized for this API
    28841106: TuyaPlanError,  # API not subscribed
    # device state / command
    2001: TuyaDeviceOfflineError,  # device is offline
    40000801: TuyaDeviceOfflineError,  # device offline (IoT Core v2.0)
    1103: TuyaCommandError,  # commands issue error
    2003: TuyaCommandError,  # function not support
    2008: TuyaCommandError,  # command or value not support
    40000003: TuyaCommandError,  # code not exist
    40000308: TuyaCommandError,  # wrong command or value format
    # frequency control
    1110: TuyaRateLimitError,  # concurrent request over limit
    1111: TuyaRateLimitError,  # system busy
    1199: TuyaRateLimitError,  # requests are too frequent
}


def api_error_from_response(code: int | None, msg: str, tid: str | None) -> TuyaApiError:
    """Map a failed API envelope to the most specific exception type."""
    cls = _CODE_CLASSES.get(code, TuyaApiError) if code is not None else TuyaApiError
    return cls(code, msg, tid)
