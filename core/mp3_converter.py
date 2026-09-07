"""
Converts a non-MP3 audio file (FLAC/WAV/OGG/M4A/AAC/...) to MP3 via
ffmpeg's libmp3lame encoder -- backs the Import menu's "Import &&
Convert to MP3..." action, so files in other formats can join a
library this app otherwise only loads/edits/saves .mp3s for. Same
external-tool conventions as the rest of core/ -- see
core/mp3val_runner.py.

Passes stdin=subprocess.DEVNULL, same reason as core/ffmpeg_probe.py's
module docstring: ffmpeg can try to read stdin (interactive prompts,
key-press handling) and block forever on an unreadable/absent handle
otherwise -- -y already avoids the specific "overwrite?" prompt this
call could hit, but stdin=DEVNULL is the actually-robust fix (not
tied to which prompt might come up) and costs nothing to also have
here.
"""

import subprocess
from pathlib import Path

from core.mp3_file import STATUS_ERROR, STATUS_OK, STATUS_TOOL_MISSING
from core.subprocess_utils import no_window_kwargs
from core.tool_locator import find_tool

FFMPEG_EXE_NAME = "ffmpeg.exe"

# A full re-encode, not a header scan or even a decode-and-discard --
# needs real time for a long track.
CONVERT_TIMEOUT_SECONDS = 300

# Common constant-bitrate presets, matching what most taggers/rippers
# offer rather than exposing every possible number libmp3lame accepts.
BITRATE_CHOICES_KBPS: list[int] = [128, 192, 256, 320]
DEFAULT_BITRATE_KBPS = 192

# Extensions offered in the Import file picker -- anything ffmpeg's own
# demuxers commonly handle for a "bring this into my MP3 library"
# workflow. Not exhaustive (ffmpeg reads far more than this), just the
# formats someone converting audio *to* MP3 would realistically have.
IMPORTABLE_EXTENSIONS: frozenset[str] = frozenset({
    ".flac", ".wav", ".wave", ".ogg", ".oga", ".m4a", ".aac", ".wma", ".opus", ".aiff", ".aif",
})


def convert_to_mp3(
    src_path: Path,
    dest_path: Path,
    bitrate_kbps: int = DEFAULT_BITRATE_KBPS,
    tool_path: Path | None = None,
    override_path: str | None = None,
) -> tuple[str, str]:
    """
    Returns (status, message): STATUS_OK / STATUS_ERROR /
    STATUS_TOOL_MISSING. Overwrites dest_path if it already exists
    (-y) -- callers are responsible for deciding whether that's safe
    (see gui/main_window.py's import flow, which always derives
    dest_path from src_path's own name in the same folder and refuses
    to proceed if a file already sits there, rather than silently
    clobbering it).
    """
    exe = tool_path if tool_path is not None else find_tool(FFMPEG_EXE_NAME, override=override_path)
    if exe is None:
        return STATUS_TOOL_MISSING, "ffmpeg.exe not found (not bundled and not on PATH)"

    try:
        result = subprocess.run(
            [
                str(exe), "-y", "-i", str(src_path),
                "-codec:a", "libmp3lame", "-b:a", f"{bitrate_kbps}k",
                str(dest_path),
            ],
            capture_output=True,
            text=True,
            timeout=CONVERT_TIMEOUT_SECONDS,
            stdin=subprocess.DEVNULL,
            **no_window_kwargs(),
        )
    except subprocess.TimeoutExpired:
        return STATUS_ERROR, f"ffmpeg timed out after {CONVERT_TIMEOUT_SECONDS}s"
    except OSError as e:
        return STATUS_ERROR, f"failed to launch ffmpeg: {e}"

    if result.returncode != 0:
        stderr_lines = result.stderr.strip().splitlines()
        message = stderr_lines[-1] if stderr_lines else f"ffmpeg exited with code {result.returncode}"
        return STATUS_ERROR, message
    return STATUS_OK, ""
