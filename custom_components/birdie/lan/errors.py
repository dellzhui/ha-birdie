"""Errors raised by the LAN client."""

from __future__ import annotations


class LanError(Exception):
    """Base class for LAN errors."""


class DeviceUnreachableError(LanError):
    """No TCP connection to the device, or it stopped answering."""


class InvalidLocalKeyError(LanError):
    """The device answered but did not accept the local key or protocol version.

    The local key changes whenever the device is paired again in the app.
    """
