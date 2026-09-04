"""
core/tool_locator.py

Thin project-specific wrapper around redactor_common.core.tool_locator
-- the actual override/bundled-dir/PATH lookup logic now lives there
(shared with every other Redactor project that shells out to an
external CLI tool) so a fix made once benefits all of them. This file
just supplies this project's bundled-tools directory convention
(tools/ next to the exe, via core.app_paths.bundled_tool_path) and
keeps the original find_tool(exe_name, override=None) signature so
every existing call site (core/mp3val_runner.py, etc.) and this
project's own tests keep working unchanged.
"""

from __future__ import annotations

from pathlib import Path
import shutil  # noqa: F401 -- re-exported so `core.tool_locator.shutil.which`
                # can still be patched directly in this project's tests;
                # patching a module attribute this way mutates the real
                # shutil module regardless of which file's import statement
                # brought it in, so this stays valid even though the actual
                # shutil.which() call now happens inside redactor_common.

from core.app_paths import bundled_tool_path
from redactor_common.core import tool_locator as _shared


def find_tool(exe_name: str, override: str | Path | None = None) -> Path | None:
    bundled = bundled_tool_path(exe_name)
    return _shared.find_tool(exe_name, tools_dir=bundled.parent, override=override)
