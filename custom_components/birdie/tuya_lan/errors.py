"""Errors raised by the Tuya LAN client."""

from __future__ import annotations


class TuyaLanError(Exception):
    """Base class for Tuya LAN errors."""


class DeviceUnreachableError(TuyaLanError):
    """No TCP connection to the device, or it stopped answering."""


class InvalidLocalKeyError(TuyaLanError):
    """The device answered but did not accept the local key or protocol version.

    The local key changes whenever the device is paired again in the app.
    """
