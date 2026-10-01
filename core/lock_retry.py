"""
core/lock_retry.py

Retry a file operation that fails because another program briefly holds the
file (Windows: a virus scanner looking at the freshly written temp copy, the
search indexer, a thumbnailer). Save and Redact retry a few times instead of
reporting a failed save. Only lock errors are retried; anything else (disk
full, path too long, a real permission problem) fails at once, and the last
error is re-raised unchanged once the attempts run out.

A thin adapter over redactor_common's core/os_utils (retry_on_lock,
is_lock_error, LOCK_HINT): the callers in tag_writer, mp3_converter, settings
and the Redact steps keep passing a no-argument callable, and the retry
count, pause and the pause function stay patchable here.
"""

from __future__ import annotations

import time
from typing import Callable, TypeVar

from redactor_common.core.os_utils import LOCK_HINT as _SHARED_LOCK_HINT
from redactor_common.core.os_utils import is_lock_error
from redactor_common.core.os_utils import retry_on_lock as _retry_on_lock

ATTEMPTS = 6
DELAY = 0.15  # seconds before the second attempt; each further pause is 1.4x longer
LOCK_HINT = f" ({_SHARED_LOCK_HINT} -- try again in a moment)"

T = TypeVar("T")

__all__ = ["ATTEMPTS", "DELAY", "LOCK_HINT", "is_lock_error", "lock_hint", "retry_on_lock", "sleep"]


def sleep(seconds: float) -> None:
    """Indirection so tests can replace the pause."""
    time.sleep(seconds)


def retry_on_lock(operation: Callable[[], T], attempts: int | None = None, delay: float | None = None) -> T:
    """operation(), retried while it raises a lock error; the last error is
    re-raised. `operation` must be safe to run again."""
    return _retry_on_lock(
        operation,
        attempts=ATTEMPTS if attempts is None else attempts,
        delay=DELAY if delay is None else delay,
        sleep=lambda seconds: sleep(seconds),  # looked up per call, so a patched sleep() is used
    )


def lock_hint(exc: BaseException) -> str:
    """Text to append to an error message when `exc` was a lock error."""
    return LOCK_HINT if is_lock_error(exc) else ""
