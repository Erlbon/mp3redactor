"""
Runs mp3val against a file and interprets its output.

mp3val prints one or more lines per file to stdout, of the form:
    Analyzing FILENAME
    WARNING: description...
    ERROR: description...
(no WARNING/ERROR lines at all means the file is clean.)

Fixing (mp3val's -f flag) is a separate, deliberate action from checking
-- same "not automatic" treatment the epub tool gave Rebuild Manifest,
since it mutates the file on disk. mp3val creates a FILENAME.bak backup
of the original before fixing by default; -nb has it delete that backup
once done instead, controlled by core.settings.Settings.delete_backup_after_fix
(off by default -- keep the backup unless the user opts in via Settings).

Every subprocess.run() call here passes core.subprocess_utils.no_window_kwargs()
so mp3val.exe never pops up (or flashes) its own console window, even
though this app itself is built --windowed -- same class of bug the
epub tool hit and fixed for Calibre (v35).
"""

import subprocess
from pathlib import Path

from core.mp3_file import STATUS_ERROR, STATUS_OK, STATUS_TOOL_MISSING, STATUS_WARNING
from core.subprocess_utils import no_window_kwargs
from core.tool_locator import find_tool

MP3VAL_EXE_NAME = "mp3val.exe"
TIMEOUT_SECONDS = 30


def check_integrity(
    path: Path, tool_path: Path | None = None, override_path: str | None = None
) -> tuple[str, str]:
    """
    Returns (status, message). Read-only -- does not pass -f, never
    touches the file. See fix_integrity() for the mutating counterpart.

    status is one of STATUS_OK / STATUS_WARNING / STATUS_ERROR /
    STATUS_TOOL_MISSING. message is empty for STATUS_OK, otherwise the
    concatenated WARNING/ERROR lines mp3val printed (or a short
    explanation for TOOL_MISSING / a launch failure).

    tool_path lets callers/tests inject a specific binary directly,
    bypassing find_tool() entirely. override_path is different -- it's
    the user's manually-set path from the Settings "Locate External
    Tools" dialog (core.settings.Settings.mp3val_path), passed through
    to find_tool() so it's tried before the bundled-tools/PATH search.
    Ignored if tool_path is given.
    """
    return _run(path, extra_args=[], tool_path=tool_path, override_path=override_path)


def fix_integrity(
    path: Path,
    tool_path: Path | None = None,
    delete_backup: bool = False,
    override_path: str | None = None,
) -> tuple[str, str]:
    """
    Returns (status, message), same vocabulary as check_integrity(), but
    passes mp3val's -f flag so it attempts to fix what it finds.

    By default mp3val leaves a FILENAME.bak backup of the original
    behind. Pass delete_backup=True to also pass -nb, which has mp3val
    delete that backup once fixing completes -- off by default, since
    keeping the backup is the safer choice for a mutating operation;
    exposed as a Settings toggle in the GUI (core.settings).

    The returned status reflects the file's state AFTER fixing (mp3val
    re-reports remaining issues, if any, in the same run) -- not every
    problem mp3val detects is fixable, so STATUS_WARNING/STATUS_ERROR can
    still come back even after a fix attempt. Callers should treat that
    as "some issues remain," not as the fix having silently failed.

    See check_integrity() for what override_path does.
    """
    extra_args = ["-f", "-nb"] if delete_backup else ["-f"]
    return _run(path, extra_args=extra_args, tool_path=tool_path, override_path=override_path)


def _run(
    path: Path,
    extra_args: list[str],
    tool_path: Path | None,
    override_path: str | None = None,
) -> tuple[str, str]:
    exe = tool_path if tool_path is not None else find_tool(MP3VAL_EXE_NAME, override=override_path)
    if exe is None:
        return STATUS_TOOL_MISSING, "mp3val.exe not found (not bundled and not on PATH)"

    try:
        result = subprocess.run(
            [str(exe), *extra_args, str(path)],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            **no_window_kwargs(),
        )
    except subprocess.TimeoutExpired:
        return STATUS_ERROR, f"mp3val timed out after {TIMEOUT_SECONDS}s"
    except OSError as e:
        return STATUS_ERROR, f"failed to launch mp3val: {e}"

    return _parse_output(result.stdout)


def _parse_output(stdout: str) -> tuple[str, str]:
    warning_lines = []
    error_lines = []
    for line in stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("WARNING:"):
            warning_lines.append(stripped)
        elif stripped.startswith("ERROR:"):
            error_lines.append(stripped)

    if error_lines:
        return STATUS_ERROR, "\n".join(error_lines + warning_lines)
    if warning_lines:
        return STATUS_WARNING, "\n".join(warning_lines)
    return STATUS_OK, ""
