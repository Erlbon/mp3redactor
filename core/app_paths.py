"""
Shared path helpers so every module agrees on where the app's writable
files (settings, logs, bundled-tool lookups) live, whether running from
source or as a frozen PyInstaller .exe.
"""

import sys
from pathlib import Path


def is_frozen() -> bool:
    """True when running as a PyInstaller-built executable."""
    return getattr(sys, "frozen", False)


def base_dir() -> Path:
    """
    Directory the app should treat as its "home" for writable state
    (settings.ini, crash log) and for locating bundled sidecar binaries
    (mp3val.exe, keyfinder-cli.exe) shipped next to the frozen .exe.

    Frozen: the directory containing the .exe (sys.executable).
    Dev mode: the project root (two levels up from this file, core/ -> root).
    """
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def bundled_tool_path(exe_name: str) -> Path:
    """
    Where a sidecar CLI tool (mp3val.exe, keyfinder-cli.exe) would live if
    bundled alongside a frozen build, e.g. base_dir()/tools/mp3val.exe.
    Callers should fall back to searching PATH if this doesn't exist --
    see core.tool_locator.find_tool().
    """
    return base_dir() / "tools" / exe_name


def asset_path(relative: str) -> Path:
    """
    Resolves a bundled DATA asset (icon.ico, README.md -- anything passed
    to PyInstaller via --add-data), as opposed to an external CLI tool.
    These follow different resolution rules: --add-data content is
    unpacked to PyInstaller's own temp extraction folder (sys._MEIPASS)
    at runtime in a --onefile build, NOT to the directory containing the
    actual .exe the way bundled_tool_path() assumes. In dev mode (not
    frozen), sys._MEIPASS doesn't exist, so this just resolves relative
    to the project root, same as base_dir().
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass) / relative
    return base_dir() / relative
