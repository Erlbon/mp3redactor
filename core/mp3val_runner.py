"""
File integrity check and fix. check_integrity()/fix_integrity() use the
in-process Python port of mp3val 0.1.8 (core.mp3_validator) and fall back
to the external mp3val.exe only when the port itself fails (a bug in the
port, not a verdict on the file) and the tool is present;
check_integrity_with_exe()/fix_integrity_with_exe() run mp3val.exe alone
(the fallback, and tools/compare_mp3val.py's reference).

Both give the same output: one or more lines per file of the form
    WARNING: "FULL PATH" (offset 0x..): description...
    ERROR: "FULL PATH": description...
    INFO: "FULL PATH": frame counts, tags, CBR/VBR
(no WARNING/ERROR lines at all means the file is clean.) The port gave
the same lines, and the same rebuilt files byte for byte, as mp3val.exe
on 1,253 test files (real encodes, synthetic streams, damaged and fuzzed
files). Unlike mp3val.exe it opens any file name; mp3val.exe cannot
open a path with letters outside the Windows ANSI code page.

Fixing (mp3val's -f flag) is a separate, deliberate action from checking
-- same "not automatic" treatment the epub tool gave Rebuild Manifest,
since it mutates the file on disk. A fix keeps the original as
FILENAME.bak by default; -nb (delete_backup) deletes that backup once
done instead, controlled by core.settings.Settings.delete_backup_after_fix
(off by default -- keep the backup unless the user opts in via Settings).

mp3val.exe is run through redactor_common's run_tool() (via
core.subprocess_utils): no console window even though this app is built
--windowed (same class of bug the epub tool hit for Calibre, v35),
stdin=DEVNULL, UTF-8 output.
"""

import subprocess
from pathlib import Path

from core import mp3_validator
from core.mp3_file import (
    STATUS_ERROR, STATUS_OK, STATUS_TOOL_ERROR, STATUS_TOOL_MISSING, STATUS_WARNING,
)
from core.subprocess_utils import run_tool
from core.tool_locator import find_tool

MP3VAL_EXE_NAME = "mp3val.exe"
TIMEOUT_SECONDS = 30


def check_integrity(
    path: Path, tool_path: Path | None = None, override_path: str | None = None
) -> tuple[str, str]:
    """
    Returns (status, message). Read-only -- never touches the file. See
    fix_integrity() for the mutating counterpart.

    status is one of STATUS_OK / STATUS_WARNING / STATUS_ERROR /
    STATUS_TOOL_ERROR (and STATUS_TOOL_MISSING only from the exe-only
    functions). message is empty for STATUS_OK, otherwise the
    concatenated WARNING/ERROR lines (or a short explanation for
    TOOL_ERROR). STATUS_ERROR is only ever mp3val's verdict on the file;
    a file that cannot be read, or one mp3val.exe would hang or crash
    on, is STATUS_TOOL_ERROR, which is never stamped.

    tool_path/override_path only matter for the mp3val.exe fallback:
    tool_path injects a specific binary, override_path is the user's
    path from Tools > External Tools (core.settings.Settings.mp3val_path)
    passed to find_tool().
    """
    return _run_native(path, fix=False, delete_backup=False, tool_path=tool_path, override_path=override_path)


def fix_integrity(
    path: Path,
    tool_path: Path | None = None,
    delete_backup: bool = False,
    override_path: str | None = None,
) -> tuple[str, str]:
    """
    Returns (status, message), same vocabulary as check_integrity(), but
    rebuilds the file when the check finds something to fix (mp3val -f).

    The original is kept as FILENAME.bak. Pass delete_backup=True to
    delete that backup once the fix completes (mp3val -nb) -- off by
    default, since keeping the backup is the safer choice for a mutating
    operation; exposed as a Settings toggle in the GUI (core.settings).

    Like mp3val -f, the returned status and message are the check's
    findings from before the rebuild, so STATUS_WARNING/STATUS_ERROR
    come back after a fix too; callers re-check the file to see what
    remains (not every problem is fixable: "Too few MPEG frames", "No
    supported tags" and the like are only reported).
    """
    return _run_native(
        path, fix=True, delete_backup=delete_backup, tool_path=tool_path, override_path=override_path
    )


def _run_native(
    path: Path, fix: bool, delete_backup: bool, tool_path: Path | None, override_path: str | None
) -> tuple[str, str]:
    try:
        if fix:
            lines = mp3_validator.fix_file(Path(path), delete_backup=delete_backup)
        else:
            lines = mp3_validator.check_file(Path(path))
    except OSError as e:
        return STATUS_TOOL_ERROR, f"mp3val could not open the file: {e}"
    except ValueError:
        # mp3val.exe: "Cannot open input file ... or it is empty"
        return STATUS_TOOL_ERROR, "mp3val could not open the file: it is empty"
    except mp3_validator.ValidatorLoopError as e:
        return STATUS_TOOL_ERROR, f"mp3val check stopped: {e} (mp3val.exe hangs on this file)"
    except mp3_validator.ValidatorCrashError as e:
        return STATUS_TOOL_ERROR, f"mp3val check stopped: {e} (mp3val.exe crashes on this file)"
    except Exception as e:  # a bug in the port: try the real tool
        exe = tool_path if tool_path is not None else find_tool(MP3VAL_EXE_NAME, override=override_path)
        message = f"built-in mp3val check failed: {type(e).__name__}: {e}"
        if exe is None:
            return STATUS_TOOL_ERROR, message
        extra_args = (["-f", "-nb"] if delete_backup else ["-f"]) if fix else []
        status, exe_message = _run_exe(path, extra_args, tool_path=exe)
        if status == STATUS_TOOL_ERROR:
            return STATUS_TOOL_ERROR, f"{message}; mp3val.exe: {exe_message}"
        return status, exe_message
    return _parse_output("\n".join(lines))


def check_integrity_with_exe(
    path: Path, tool_path: Path | None = None, override_path: str | None = None
) -> tuple[str, str]:
    """
    Runs mp3val.exe (read-only, no -f). Same (status, message) contract
    as check_integrity(), plus STATUS_TOOL_MISSING when mp3val.exe is not
    found. tool_path lets callers/tests inject a specific binary directly,
    bypassing find_tool() entirely; override_path (the user's path from
    Settings) is passed to find_tool() and ignored if tool_path is given.
    """
    return _run_exe(path, extra_args=[], tool_path=tool_path, override_path=override_path)


def fix_integrity_with_exe(
    path: Path,
    tool_path: Path | None = None,
    delete_backup: bool = False,
    override_path: str | None = None,
) -> tuple[str, str]:
    """Runs mp3val.exe -f (and -nb with delete_backup). See
    fix_integrity() and check_integrity_with_exe()."""
    extra_args = ["-f", "-nb"] if delete_backup else ["-f"]
    return _run_exe(path, extra_args=extra_args, tool_path=tool_path, override_path=override_path)


def _run_exe(
    path: Path,
    extra_args: list[str],
    tool_path: Path | None,
    override_path: str | None = None,
) -> tuple[str, str]:
    exe = tool_path if tool_path is not None else find_tool(MP3VAL_EXE_NAME, override=override_path)
    if exe is None:
        return STATUS_TOOL_MISSING, "mp3val not found (not bundled and not on PATH)"

    try:
        # run_tool(): no console window, stdin=DEVNULL, UTF-8 output
        # decoded with errors="replace" (mp3val echoes the file name, so
        # text=True's locale decoding could raise on a non-ASCII one).
        result = run_tool([str(exe), *extra_args, str(path)], timeout=TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        return STATUS_TOOL_ERROR, f"mp3val timed out after {TIMEOUT_SECONDS}s"
    except (OSError, UnicodeDecodeError) as e:
        return STATUS_TOOL_ERROR, f"failed to run mp3val: {e}"

    status, message = _parse_output(result.stdout, result.returncode, result.stderr)
    if status == STATUS_TOOL_ERROR and not str(path).isascii():
        message += (
            " (mp3val cannot open a path with letters outside the Windows code page,"
            " for example Polish or Japanese letters; rename the file or folder to check it)"
        )
    return status, message


def _parse_output(stdout: str, returncode: int = 0, stderr: str = "") -> tuple[str, str]:
    warning_lines = []
    error_lines = []
    for line in (stderr or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("Cannot open input file"):
            # mp3val exits 0 when it cannot open the file at all (for
            # example a name with letters outside the ANSI code page) and
            # prints only this, on stderr: without this check the file
            # would pass as clean although it was never read.
            return STATUS_TOOL_ERROR, f"mp3val could not open the file: {stripped}"
    for line in stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("Cannot open input file"):
            # mp3val exits 0 when it cannot open the file at all (for
            # example a name with letters outside the ANSI code page) and
            # prints no WARNING/ERROR line: without this the file would
            # pass as clean although it was never read.
            return STATUS_TOOL_ERROR, f"mp3val could not open the file: {stripped}"
        if stripped.startswith("WARNING:"):
            warning_lines.append(stripped)
        elif stripped.startswith("ERROR:"):
            error_lines.append(stripped)

    if error_lines:
        return STATUS_ERROR, "\n".join(error_lines + warning_lines)
    if warning_lines:
        return STATUS_WARNING, "\n".join(warning_lines)
    if returncode != 0:
        # A crash or bad invocation prints no WARNING/ERROR lines -- not
        # a clean file, but not a verdict on it either: the tool failed.
        detail = (stderr or "").strip() or (stdout or "").strip()
        return STATUS_TOOL_ERROR, detail or f"mp3val exited with code {returncode}"
    return STATUS_OK, ""
