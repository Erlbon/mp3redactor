"""
Global crash logging via redactor_common.core.crash_log (2026-09-23):
an excepthook writing full tracebacks to mp3redactor_crash.log next to
the app, plus faulthandler for native crashes (e.g. inside aubio's C
bindings) that never raise a Python exception. The shared version also
trims whole entries (this project's own copy cut the oldest entry off
mid-traceback) and can show an "Unexpected Error" dialog on top.
"""

from redactor_common.core import crash_log as _shared

from core.app_paths import crash_log_path


def log_path() -> str:
    return str(crash_log_path())


def install(also_call=None) -> None:
    _shared.install(log_path(), also_call=also_call)
