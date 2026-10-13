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

import os
import subprocess
from pathlib import Path
from typing import Callable

from core.lock_retry import retry_on_lock
from core.mp3_file import STATUS_ERROR, STATUS_OK, STATUS_TOOL_MISSING
from core.subprocess_utils import StoppableProcesses
from redactor_common.core.subprocess_utils import run_tool
from redactor_common.core.rename_pattern import unique_path
from core.tool_locator import find_tool

FFMPEG_EXE_NAME = "ffmpeg.exe"

# A full re-encode, not a header scan or even a decode-and-discard --
# needs real time for a long track.
CONVERT_TIMEOUT_SECONDS = 300

# The message of a conversion stopped by Cancel (not a failure to report).
CANCELLED_MESSAGE = "Cancelled."

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


def find_importable_files(paths, recursive: bool = True) -> list[Path]:
    """The convertible audio files (FLAC, WAV, OGG, M4A... see IMPORTABLE_EXTENSIONS) in a mix of files and
    folders, de-duplicated and sorted; folders are searched including their subfolders unless `recursive` is
    False. Used when a folder holds no .mp3 at all, to offer converting what is there."""
    found: set[Path] = set()
    for p in paths:
        p = Path(p)
        if p.is_dir():
            for child in (p.rglob("*") if recursive else p.iterdir()):
                if child.is_file() and child.suffix.lower() in IMPORTABLE_EXTENSIONS:
                    found.add(child.resolve())
        elif p.is_file() and p.suffix.lower() in IMPORTABLE_EXTENSIONS:
            found.add(p.resolve())
    return sorted(found)


def plan_conversions(sources: list[Path]) -> tuple[list[tuple[Path, Path]], list[Path]]:
    """(src, dest) pairs plus the dests skipped because a file already
    sits there. dest is the source's name with .mp3 in the same folder;
    two sources that would share one (a.flac + a.wav -> a.mp3) no longer
    overwrite each other -- the later one becomes "a (2).mp3"."""
    claimed: set[str] = set()
    conversions: list[tuple[Path, Path]] = []
    skipped: list[Path] = []
    for src in sources:
        dest = src.with_suffix(".mp3")
        if dest.exists():
            # Never clobber an existing file.
            skipped.append(dest)
            continue
        if os.path.normcase(os.path.abspath(dest)) in claimed:
            dest = Path(unique_path(str(dest.parent), dest.stem, ".mp3", claimed))
        claimed.add(os.path.normcase(os.path.abspath(dest)))
        conversions.append((src, dest))
    return conversions, skipped


def convert_to_mp3(
    src_path: Path,
    dest_path: Path,
    bitrate_kbps: int = DEFAULT_BITRATE_KBPS,
    tool_path: Path | None = None,
    override_path: str | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> tuple[str, str]:
    """
    Returns (status, message): STATUS_OK / STATUS_ERROR /
    STATUS_TOOL_MISSING. With `should_cancel`, ffmpeg is polled and killed
    as soon as it returns True: (STATUS_ERROR, CANCELLED_MESSAGE). Encodes to a temporary file beside dest_path and
    moves it into place only on success, so a failed/timed-out run
    leaves no partial .mp3 behind. Overwrites dest_path if it already
    exists -- callers are responsible for deciding whether that's safe
    (see gui/main_window.py's import flow, which always derives
    dest_path from src_path's own name in the same folder and refuses
    to proceed if a file already sits there, rather than silently
    clobbering it).
    """
    exe = tool_path if tool_path is not None else find_tool(FFMPEG_EXE_NAME, override=override_path)
    if exe is None:
        return STATUS_TOOL_MISSING, "ffmpeg not found (not bundled and not on PATH)"

    # Hidden temp name in the same folder (same filesystem, so the final
    # os.replace is atomic); "-f mp3" because the extension no longer says so.
    tmp_path = dest_path.with_name(f".{dest_path.name}.converting")
    command = [
        str(exe), "-y", "-i", str(src_path),
        "-codec:a", "libmp3lame", "-b:a", f"{bitrate_kbps}k",
        "-f", "mp3", str(tmp_path),
    ]
    try:
        if should_cancel is None:
            result = run_tool(command, timeout=CONVERT_TIMEOUT_SECONDS)
        else:
            # Cancel kills ffmpeg at once instead of waiting for a long encode to finish.
            result = StoppableProcesses().run(command, CONVERT_TIMEOUT_SECONDS, should_cancel)
            if result.returncode != 0 and should_cancel():
                _remove_quietly(tmp_path)
                return STATUS_ERROR, CANCELLED_MESSAGE
    except subprocess.TimeoutExpired:
        _remove_quietly(tmp_path)
        return STATUS_ERROR, f"ffmpeg timed out after {CONVERT_TIMEOUT_SECONDS}s"
    except OSError as e:
        _remove_quietly(tmp_path)
        return STATUS_ERROR, f"failed to launch ffmpeg: {e}"

    if result.returncode != 0:
        _remove_quietly(tmp_path)
        stderr_lines = result.stderr.strip().splitlines()
        message = stderr_lines[-1] if stderr_lines else f"ffmpeg exited with code {result.returncode}"
        return STATUS_ERROR, message
    try:
        retry_on_lock(lambda: os.replace(tmp_path, dest_path))
    except OSError as e:
        _remove_quietly(tmp_path)
        return STATUS_ERROR, f"could not move the converted file into place: {e}"
    return STATUS_OK, ""


def _remove_quietly(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass
