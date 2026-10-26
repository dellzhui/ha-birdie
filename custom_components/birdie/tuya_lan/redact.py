"""Keep local keys out of tinytuya's debug log.

tinytuya logs the local key in a few debug messages (for example when a
status request fails). This filter replaces every registered key in records
from the ``tinytuya`` loggers, so enabling debug logging for troubleshooting
does not expose it.
"""

from __future__ import annotations

import logging
import threading
from collections import Counter

REDACTED = "**REDACTED**"

_lock = threading.Lock()
_secrets: Counter[str] = Counter()


class _RedactLocalKeys(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        with _lock:
            secrets = list(_secrets)
        if not secrets:
            return True
        try:
            message = record.getMessage()
        except Exception:  # never break logging
            return True
        redacted = message
        for secret in secrets:
            redacted = redacted.replace(secret, REDACTED)
        if redacted != message:
            record.msg = redacted
            record.args = None
        return True


_FILTER = _RedactLocalKeys()


def _install() -> None:
    """Attach the filter to every tinytuya logger (filters do not propagate)."""
    names = ["tinytuya"] + [
        name for name in logging.root.manager.loggerDict if name.startswith("tinytuya.")
    ]
    for name in names:
        logger = logging.getLogger(name)
        if _FILTER not in logger.filters:
            logger.addFilter(_FILTER)


def _variants(secret: str) -> set[str]:
    # tinytuya logs keys as bytes (%r), which escapes some characters.
    return {secret, repr(secret.encode("latin1", "replace"))[2:-1]}


def register_secret(secret: str) -> None:
    """Redact ``secret`` from tinytuya log records until it is unregistered."""
    if not secret:
        return
    _install()
    with _lock:
        _secrets.update(_variants(secret))


def unregister_secret(secret: str) -> None:
    """Undo one ``register_secret`` call."""
    if not secret:
        return
    with _lock:
        for variant in _variants(secret):
            if _secrets[variant] <= 1:
                _secrets.pop(variant, None)
            else:
                _secrets[variant] -= 1
