"""
Locates external CLI tools this app shells out to (mp3val, keyfinder-cli).
Both are the only two v1 needs with no usable Python binding, so unlike
mutagen/aubio/lyricy they must be found as separate executables.

Lookup order:
  1. An explicit override the user set via Settings > Locate External
     Tools (core.settings.Settings.mp3val_path / .keyfinder_cli_path).
     If given and it doesn't actually exist, this is treated as NOT
     FOUND rather than silently falling through to auto-detect -- an
     explicit path the user pointed at is either right or wrong, and
     silently ignoring a broken one would make the Settings dialog's
     "found" indicator meaningless.
  2. A tools/<name>.exe sitting next to a frozen build (bundled, no
     separate install required by the user).
  3. Whatever's on PATH, for dev-mode runs or users who already have the
     tool installed system-wide.

Returns None (never raises) when nothing is found -- callers are expected
to surface STATUS_TOOL_MISSING rather than crash, since a missing sidecar
tool is a deployment/config issue, not a bug in a specific file.
"""

import shutil
from pathlib import Path

from core.app_paths import bundled_tool_path


def find_tool(exe_name: str, override: str | Path | None = None) -> Path | None:
    if override:
        overridden = Path(override)
        return overridden if overridden.exists() else None

    bundled = bundled_tool_path(exe_name)
    if bundled.exists():
        return bundled

    on_path = shutil.which(exe_name)
    if on_path:
        return Path(on_path)

    return None
