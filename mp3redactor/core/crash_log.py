"""
Global crash logging, installed as the very first thing main() does.

Two layers, matching the epub tool's v51/v52 approach (which caught a real
zlib.error crash that ad-hoc try/except hadn't anticipated):

1. sys.excepthook -- catches any uncaught Python exception that reaches the
   top of the stack and writes a full traceback to the crash log instead of
   just vanishing (frozen GUI apps have no console to show it in).
2. faulthandler -- catches native-level crashes (segfaults inside a C
   extension such as aubio's bindings) that sys.excepthook can never see,
   since those don't raise a Python exception at all.
"""

import faulthandler
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

from core.app_paths import base_dir

MAX_LOG_BYTES = 2 * 1024 * 1024  # 2 MB cap; matches epub tool's sizing


def _log_path(target_dir: Path | None = None) -> Path:
    d = target_dir if target_dir is not None else base_dir()
    return d / "mp3redactor_crash.log"


def _trim_if_needed(path: Path) -> None:
    try:
        if path.exists() and path.stat().st_size > MAX_LOG_BYTES:
            # Keep only the tail -- most recent crashes are the useful ones.
            data = path.read_bytes()[-MAX_LOG_BYTES:]
            path.write_bytes(data)
    except OSError:
        pass  # logging must never itself crash the app


def _write_crash(exc_type, exc_value, exc_tb, target_dir: Path | None = None) -> None:
    path = _log_path(target_dir)
    try:
        timestamp = datetime.now(timezone.utc).isoformat()
        text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"\n--- {timestamp} ---\n{text}")
        _trim_if_needed(path)
    except OSError:
        pass  # nowhere left to report a failure writing the crash log itself


def install(target_dir: Path | None = None) -> None:
    """
    Install the global excepthook and enable faulthandler. Call once, as
    literally the first line of main(), before any other module (Qt
    included) has a chance to run and potentially crash unlogged.
    """

    def _hook(exc_type, exc_value, exc_tb):
        _write_crash(exc_type, exc_value, exc_tb, target_dir)
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = _hook

    try:
        fh_path = _log_path(target_dir)
        fh_path.parent.mkdir(parents=True, exist_ok=True)
        faulthandler.enable(file=open(fh_path, "a", encoding="utf-8"))
    except OSError:
        pass  # native-crash logging is best-effort; excepthook still covers Python-level crashes
