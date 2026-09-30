"""
Runs keyfinder-cli against a file and returns the detected musical key.

Much simpler output to parse than mp3val's: keyfinder-cli prints just
the key to stdout on success (nothing at all if the file is silent --
genuinely has no key, not a failure) and exits 0. Any non-zero exit, or
an OSError launching it, is treated as STATUS_ERROR. See
https://github.com/Erlbon/keyfinder-cli-windows for how the Windows
binary is built (no prebuilt one exists upstream).

The call goes through redactor_common's run_tool() (via
core.subprocess_utils): no console window, stdin=DEVNULL, UTF-8 output.
"""

import subprocess
from pathlib import Path

from core.mp3_file import STATUS_ERROR, STATUS_OK, STATUS_TOOL_MISSING
from core.subprocess_utils import run_tool
from core.tool_locator import find_tool

KEYFINDER_EXE_NAME = "keyfinder-cli.exe"
# Decodes + FFT-analyzes the whole file rather than just scanning
# headers like mp3val does -- a longer timeout than mp3val's 30s is
# warranted, though still generous for anything but a very long track.
TIMEOUT_SECONDS = 60


def detect_key(
    path: Path, tool_path: Path | None = None, override_path: str | None = None
) -> tuple[str, str, str]:
    """
    Returns (key, status, message). status is one of STATUS_OK /
    STATUS_ERROR / STATUS_TOOL_MISSING -- keyfinder-cli has no
    WARNING-equivalent. key is the detected key (e.g. "A", standard
    notation, keyfinder-cli's default) or "" both on any error and for
    a genuinely silent file (still STATUS_OK in that case -- silence
    having no key isn't a failure).

    tool_path lets callers/tests inject a specific binary directly,
    bypassing find_tool() entirely. override_path is the user's
    manually-set path from Tools > External Tools
    (core.settings.Settings.keyfinder_cli_path) -- same convention as
    core.mp3val_runner.check_integrity()'s override_path.
    """
    exe = tool_path if tool_path is not None else find_tool(KEYFINDER_EXE_NAME, override=override_path)
    if exe is None:
        return "", STATUS_TOOL_MISSING, "keyfinder-cli not found (not bundled and not on PATH)"

    try:
        # run_tool(): no console window, stdin=DEVNULL, UTF-8 output
        # decoded with errors="replace" (text=True's locale decoding
        # could raise UnicodeDecodeError on non-ASCII output).
        result = run_tool([str(exe), str(path)], timeout=TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        return "", STATUS_ERROR, f"keyfinder-cli timed out after {TIMEOUT_SECONDS}s"
    except OSError as e:
        return "", STATUS_ERROR, f"failed to launch keyfinder-cli: {e}"

    if result.returncode != 0:
        message = result.stderr.strip() or f"keyfinder-cli exited with code {result.returncode}"
        return "", STATUS_ERROR, message

    key = result.stdout.strip()
    # A message even on the "empty but OK" case -- otherwise a genuinely
    # silent file's blank Key cell is visually indistinguishable from
    # one that was simply never checked, with no tooltip to tell them
    # apart.
    message = "" if key else "no key detected (silent audio)"
    return key, STATUS_OK, message
