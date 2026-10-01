"""
BPM detection using aubio's Python bindings directly (import aubio),
rather than shelling out to the `aubio tempo` CLI -- aubio ships real
bindings, so there's no need to pay subprocess overhead or parse text
output the way mp3val/keyfinder-cli require.

aubio is treated as an optional dependency: import failures are caught
and reported as STATUS_TOOL_MISSING (same vocabulary mp3val's runner
uses for a missing binary) rather than raising, so the app can still
scan/validate files that have nothing to do with BPM when it isn't
installed.

Installed via the `aubio-ledfx` PyPI package (see requirements.txt) --
a fork providing prebuilt Windows wheels, since plain `aubio` is
source-only and needs MSVC Build Tools to build. Same module name
either way, so `import aubio` below is unaffected by which one is
installed.
"""

import os
import subprocess
import tempfile
from pathlib import Path

from core.ffmpeg_probe import FFMPEG_EXE_NAME
from core.mp3_file import STATUS_ERROR, STATUS_OK, STATUS_TOOL_ERROR, STATUS_TOOL_MISSING
from core.subprocess_utils import run_tool
from core.tool_locator import find_tool

WIN_S = 1024  # FFT window size
HOP_S = WIN_S // 2  # hop size (50% overlap)

# The ffmpeg fallback (see detect_bpm) decodes to a temporary mono WAV: cap
# its length so a very long file can't fill the temp folder (22.05 kHz mono
# 16-bit is about 2.6 MB a minute, so 10 minutes is about 26 MB), and bound
# the time. Ten minutes is plenty to find a track's tempo.
FFMPEG_DECODE_SECONDS = 600
FFMPEG_DECODE_RATE = 22050
FFMPEG_TIMEOUT_SECONDS = 120


def detect_bpm(path: Path) -> tuple[float | None, str, str]:
    """
    Returns (bpm, status, message).

    bpm is None unless status is STATUS_OK. status is one of
    STATUS_OK / STATUS_ERROR / STATUS_TOOL_ERROR / STATUS_TOOL_MISSING.
    STATUS_ERROR is only the "no tempo found" verdict (too few beats);
    aubio failing to open or decode the file is STATUS_TOOL_ERROR, which
    is never written to a tag.

    aubio reads the file directly first. Its Linux wheel can't decode MP3
    ("could not find RIFF header"), so when that open/read fails the file is
    decoded once by ffmpeg to a temporary mono WAV (the first
    FFMPEG_DECODE_SECONDS) and aubio runs on that; without ffmpeg, or if
    ffmpeg fails, the result is STATUS_TOOL_ERROR. A "not enough beats"
    verdict is a result, not a failure, and is never retried.
    """
    try:
        import aubio
    except ImportError:
        return None, STATUS_TOOL_MISSING, "aubio is not installed"

    try:
        return _analyze(aubio, str(path))
    except Exception as direct_error:  # noqa: BLE001 -- any aubio/decoder failure is a per-file result, not a crash
        return _detect_via_ffmpeg(aubio, path, direct_error)


def _detect_via_ffmpeg(aubio, path: Path, direct_error: Exception) -> tuple[float | None, str, str]:
    exe = find_tool(FFMPEG_EXE_NAME)
    if exe is None:
        return None, STATUS_TOOL_ERROR, (
            f"BPM needs ffmpeg to decode this file (ffmpeg not found; aubio said: {direct_error})"
        )
    fd, wav_path = tempfile.mkstemp(prefix="mp3redactor-bpm-", suffix=".wav")
    os.close(fd)
    try:
        try:
            result = run_tool(
                [
                    str(exe), "-y", "-v", "error", "-i", str(path), "-vn", "-ac", "1",
                    "-ar", str(FFMPEG_DECODE_RATE), "-t", str(FFMPEG_DECODE_SECONDS), "-f", "wav", wav_path,
                ],
                timeout=FFMPEG_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            return None, STATUS_TOOL_ERROR, (
                f"BPM needs ffmpeg to decode this file (ffmpeg timed out after {FFMPEG_TIMEOUT_SECONDS}s)"
            )
        except (OSError, UnicodeDecodeError) as e:
            return None, STATUS_TOOL_ERROR, f"BPM needs ffmpeg to decode this file (failed to run ffmpeg: {e})"
        if result.returncode != 0:
            detail = (result.stderr or "").strip() or f"exit code {result.returncode}"
            return None, STATUS_TOOL_ERROR, f"BPM needs ffmpeg to decode this file (ffmpeg failed: {detail})"
        try:
            return _analyze(aubio, wav_path)
        except Exception as e:  # noqa: BLE001
            return None, STATUS_TOOL_ERROR, f"BPM detection failed: {e}"
    finally:
        try:
            os.remove(wav_path)
        except OSError:
            pass


def _analyze(aubio, source_path: str) -> tuple[float | None, str, str]:
    """Runs aubio's tempo detection on one file. Returns the (bpm, status,
    message) result; an open/decode failure raises."""
    src = aubio.source(source_path, 0, HOP_S)
    samplerate = src.samplerate
    tempo_detector = aubio.tempo("default", WIN_S, HOP_S, samplerate)

    beat_times = []
    while True:
        samples, read = src()
        if tempo_detector(samples):
            beat_times.append(tempo_detector.get_last_s())
        if read < HOP_S:
            break

    if len(beat_times) < 2:
        return None, STATUS_ERROR, "not enough detected beats to estimate a tempo"

    bpm = tempo_detector.get_bpm()
    if not bpm or bpm <= 0:
        # get_bpm() can be unreliable right after a short read; fall
        # back to the median inter-beat interval, same approach the
        # aubio CLI itself uses (see its process_tempo.flush()).
        intervals = [b - a for a, b in zip(beat_times, beat_times[1:])]
        intervals.sort()
        median_interval = intervals[len(intervals) // 2]
        if median_interval <= 0:
            return None, STATUS_ERROR, "could not derive a tempo from detected beats"
        bpm = 60.0 / median_interval

    return round(bpm, 1), STATUS_OK, ""
