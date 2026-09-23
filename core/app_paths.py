"""
Where the app's writable files (settings, crash log) and bundled
sidecar tools live, via redactor_common.core.app_paths (2026-09-23) --
the frozen-vs-dev resolution every Redactor app used to reimplement.
This project's own root is passed in, since the shared package lives
in site-packages and can't find it from its own location.
"""

from pathlib import Path

from redactor_common.core import app_paths as _shared

APP_SLUG = "mp3redactor"
PROJECT_ROOT = Path(__file__).resolve().parent.parent

is_frozen = _shared.is_frozen


def base_dir() -> Path:
    """The exe's folder when frozen, the project root in dev mode."""
    return _shared.base_dir(PROJECT_ROOT)


def bundled_tool_path(exe_name: str) -> Path:
    """Where a sidecar CLI tool (mp3val.exe, keyfinder-cli.exe) would
    live if bundled next to a frozen build: base_dir()/tools/<exe>.
    Callers fall back to PATH -- see core.tool_locator.find_tool()."""
    return _shared.tools_dir(PROJECT_ROOT) / exe_name


def asset_path(relative: str) -> Path:
    """A bundled DATA asset (icon.ico, README.md) -- sys._MEIPASS when
    frozen, unlike the writable files above."""
    return _shared.asset_path(relative, PROJECT_ROOT)


def crash_log_path() -> Path:
    return _shared.crash_log_path(APP_SLUG, PROJECT_ROOT)
