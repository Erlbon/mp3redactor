"""
core/notes_debug.py -- a troubleshooting log for the Create Notes review list, OFF unless asked for.

On when the environment variable MP3REDACTOR_NOTES_DEBUG=1 is set, or when Preferences > Create Notes > "Log the Create Notes
list for troubleshooting" is ticked (off by default). It writes `mp3redactor_notes_debug.log` next to the settings file and
records only: what happened (a step name), how many rows the list has, the scroll values, which row is current (its index) and
the folder NAMES of the rows touched. Never file contents, note text or paths above the folder name. Preferences can delete it.
Nothing is created, and nothing is written, while it is off. A logging failure is swallowed: it never disturbs the list.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Optional

LOG_NAME = "mp3redactor_notes_debug.log"
MAX_BYTES = 1_000_000  # a log that grows past this is started again

_lock = threading.Lock()
_flag: Optional[bool] = None  # the Preferences switch; None = not read yet


def log_path() -> Path:
    from core.app_paths import base_dir

    return base_dir() / LOG_NAME


def set_enabled(enabled: Optional[bool]) -> None:
    """The Preferences switch (the running app follows at once). None forgets it (read from the settings again)."""
    global _flag
    _flag = enabled


def enabled() -> bool:
    if os.environ.get("MP3REDACTOR_NOTES_DEBUG", "").strip() == "1":
        return True
    global _flag
    if _flag is None:
        try:
            from core.settings import load_settings

            _flag = bool(load_settings().notes_debug_log)
        except Exception:  # noqa: BLE001 -- an unreadable settings file means off
            _flag = False
    return bool(_flag)


def log_exists() -> bool:
    return log_path().is_file()


def delete_log() -> str:
    """Removes the log; '' or a plain-language problem."""
    try:
        log_path().unlink(missing_ok=True)
        return ""
    except OSError as exc:
        return f"The log could not be deleted ({exc})."


def record(step: str, **fields) -> None:
    """One line: time, step and the fields (counts, scroll values, indexes, folder names). Does nothing while off."""
    if not enabled():
        return
    try:
        parts = [time.strftime("%H:%M:%S"), step]
        for key, value in fields.items():
            if isinstance(value, (list, tuple)):
                value = "[" + ", ".join(str(v) for v in value[:12]) + (", ..." if len(value) > 12 else "") + "]"
            parts.append(f"{key}={value}")
        line = "  ".join(parts) + "\n"
        path = log_path()
        with _lock:
            if path.is_file() and path.stat().st_size > MAX_BYTES:
                path.unlink()
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(line)
    except Exception:  # noqa: BLE001 -- a troubleshooting log never breaks anything
        pass
