"""
core/lock_retry.py

Retry a file operation that fails because another program briefly holds the
file (Windows: a virus scanner looking at the freshly written temp copy, the
search indexer, a thumbnailer). Such a lock raises PermissionError (winerror
5 access denied, 32 sharing violation, 33 lock violation) and is gone a
moment later, so Save and Redact retry a few times instead of reporting a
failed save. Only lock errors are retried; anything else (disk full, path too
long, a real permission problem) fails at once, and the last error is
re-raised unchanged once the attempts run out.

An app-local copy of what redactor_common's core/os_utils will offer; switch
to that once this app pins it.
"""

from __future__ import annotations

import time
from typing import Callable, TypeVar

ATTEMPTS = 6
DELAY = 0.15  # seconds between attempts
LOCK_WINERRORS = (5, 32, 33)
LOCK_HINT = " (the file is locked by another program, such as a virus scanner or indexer -- try again in a moment)"

T = TypeVar("T")


def sleep(seconds: float) -> None:
    """Indirection so tests can replace the pause."""
    time.sleep(seconds)


def is_lock_error(exc: BaseException) -> bool:
    return isinstance(exc, PermissionError) or (
        isinstance(exc, OSError) and getattr(exc, "winerror", None) in LOCK_WINERRORS
    )


def retry_on_lock(operation: Callable[[], T], attempts: int = ATTEMPTS, delay: float = DELAY) -> T:
    """operation(), retried while it raises a lock error; the last error is
    re-raised. `operation` must be safe to run again."""
    for attempt in range(attempts):
        try:
            return operation()
        except Exception as exc:  # noqa: BLE001 -- anything but a lock error is re-raised at once
            if not is_lock_error(exc) or attempt == attempts - 1:
                raise
            sleep(delay)
    raise AssertionError("unreachable")  # attempts < 1


def lock_hint(exc: BaseException) -> str:
    """Text to append to an error message when `exc` was a lock error."""
    return LOCK_HINT if is_lock_error(exc) else ""
