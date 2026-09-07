"""
Shells out to ffmpeg/ffprobe for three things a plain ID3-tag read
can't give:

- a genuine full decode of the file (mp3val, core/mp3val_runner.py,
  only validates frame headers -- a file with clean headers can still
  contain corrupt/truncated audio data, and vice versa)
- measured loudness, as the basis for a ReplayGain-style tag
- richer format details than mutagen surfaces on load (the actual
  encoder, sample rate, channel count)

Same external-tool conventions as core/mp3val_runner.py and
core/keyfinder_runner.py: core.tool_locator.find_tool() for bundled/
PATH/override resolution, core.subprocess_utils.no_window_kwargs() so
neither binary pops up a console window from this --windowed app.

Every subprocess.run() call here also passes stdin=subprocess.DEVNULL
-- unlike mp3val/keyfinder-cli, ffmpeg/ffprobe can try to read from
stdin (interactive "y/N" prompts, key-press handling mid-run), and
inheriting whatever stdin this process happens to have (invalid/absent
for a --windowed frozen app with no console, or some other unreadable
handle depending on how the parent was itself launched) can make that
read block forever -- discovered by hitting exactly this hang while
testing against the real binaries, not a hypothetical.
"""

import json
import math
import subprocess
from pathlib import Path

from core.mp3_file import STATUS_ERROR, STATUS_OK, STATUS_TOOL_MISSING
from core.subprocess_utils import no_window_kwargs
from core.tool_locator import find_tool

FFMPEG_EXE_NAME = "ffmpeg.exe"
FFPROBE_EXE_NAME = "ffprobe.exe"

# A full decode (deep check, loudness) is much slower than mp3val's
# header-only scan or keyfinder-cli's analysis window -- a long track
# genuinely needs real time here, not just a generous safety margin.
DEEP_CHECK_TIMEOUT_SECONDS = 120
LOUDNESS_TIMEOUT_SECONDS = 120
PROBE_TIMEOUT_SECONDS = 30

# ReplayGain 2.0's reference loudness -- the target every track's gain
# is computed relative to, so a library tagged this way (or by any
# other ReplayGain-aware tool) ends up perceptually equal-loudness when
# a player applies the tag. See https://wiki.hydrogenaud.io/index.php?title=ReplayGain_2.0_specification
REPLAYGAIN_REFERENCE_LUFS = -18.0


def deep_check_integrity(
    path: Path, tool_path: Path | None = None, override_path: str | None = None
) -> tuple[str, str]:
    """
    Fully decodes the file (ffmpeg -v error -i <path> -f null -, i.e.
    decode everything and discard the output) rather than just
    validating frame headers the way mp3val does -- catches truncated
    or corrupt audio data a header-only scan can miss, at the cost of
    being much slower (a real decode, not a header scan).

    Returns (status, message): STATUS_OK / STATUS_ERROR /
    STATUS_TOOL_MISSING. message is ffmpeg's stderr (decode errors) on
    STATUS_ERROR, empty on STATUS_OK. -v error means ffmpeg prints
    nothing at all on a clean decode -- any stderr output at that
    verbosity is a genuine problem.
    """
    exe = tool_path if tool_path is not None else find_tool(FFMPEG_EXE_NAME, override=override_path)
    if exe is None:
        return STATUS_TOOL_MISSING, "ffmpeg.exe not found (not bundled and not on PATH)"

    try:
        result = subprocess.run(
            [str(exe), "-v", "error", "-i", str(path), "-f", "null", "-"],
            capture_output=True,
            text=True,
            timeout=DEEP_CHECK_TIMEOUT_SECONDS,
            stdin=subprocess.DEVNULL,
            **no_window_kwargs(),
        )
    except subprocess.TimeoutExpired:
        return STATUS_ERROR, f"ffmpeg timed out after {DEEP_CHECK_TIMEOUT_SECONDS}s"
    except OSError as e:
        return STATUS_ERROR, f"failed to launch ffmpeg: {e}"

    stderr = result.stderr.strip()
    if result.returncode != 0 or stderr:
        return STATUS_ERROR, stderr or f"ffmpeg exited with code {result.returncode}"
    return STATUS_OK, ""


def measure_loudness(
    path: Path, tool_path: Path | None = None, override_path: str | None = None
) -> tuple[float | None, float | None, str, str]:
    """
    Single-pass loudnorm measurement -- this only measures, it never
    rewrites the audio itself (that would need a second ffmpeg pass
    with the measured values fed back in via linear=true, which this
    app has no use for: it tags the file with the *result*, same
    "detect once, write a value" shape as BPM/key, rather than
    re-encoding anything). ffmpeg prints a JSON summary block to
    stderr when loudnorm runs without that second pass.

    Returns (lufs, gain_db, status, message). gain_db is the
    ReplayGain-style track gain relative to REPLAYGAIN_REFERENCE_LUFS
    -- what actually gets written to TXXX:REPLAYGAIN_TRACK_GAIN, see
    core/tag_writer.py's _write_loudness_frame(). Both are None unless
    status is STATUS_OK.

    A genuinely silent file measures "-inf" LUFS -- not a failure (the
    same "silence isn't an error" call BPM/key detection makes), but
    there's no finite gain to compute from it, so that case returns
    STATUS_OK with lufs=None (nothing to write, same as BPM staying
    None -- see core/tag_writer.py's docstring on why a None here
    means "leave any existing tag alone" rather than "clear it").
    """
    exe = tool_path if tool_path is not None else find_tool(FFMPEG_EXE_NAME, override=override_path)
    if exe is None:
        return None, None, STATUS_TOOL_MISSING, "ffmpeg.exe not found (not bundled and not on PATH)"

    try:
        result = subprocess.run(
            [str(exe), "-i", str(path), "-af", "loudnorm=print_format=json", "-f", "null", "-"],
            capture_output=True,
            text=True,
            timeout=LOUDNESS_TIMEOUT_SECONDS,
            stdin=subprocess.DEVNULL,
            **no_window_kwargs(),
        )
    except subprocess.TimeoutExpired:
        return None, None, STATUS_ERROR, f"ffmpeg timed out after {LOUDNESS_TIMEOUT_SECONDS}s"
    except OSError as e:
        return None, None, STATUS_ERROR, f"failed to launch ffmpeg: {e}"

    lufs = _parse_loudnorm_input_i(result.stderr)
    if lufs is None:
        message = result.stderr.strip() or f"ffmpeg exited with code {result.returncode}"
        return None, None, STATUS_ERROR, message

    if not math.isfinite(lufs):
        # Genuinely silent audio -- a real result, not a failure, but
        # no gain to compute (see docstring above).
        return None, None, STATUS_OK, "no measurable loudness (silent audio)"

    gain_db = round(REPLAYGAIN_REFERENCE_LUFS - lufs, 2)
    return round(lufs, 2), gain_db, STATUS_OK, ""


def _parse_loudnorm_input_i(stderr: str) -> float | None:
    # loudnorm's JSON summary is the last {...} block in stderr,
    # printed after all the regular progress/log lines -- found by
    # locating the final matching brace pair rather than assuming a
    # fixed position, since ffmpeg's surrounding log verbosity varies
    # (codec info, stream mapping, etc. all print to stderr too).
    start = stderr.rfind("{")
    end = stderr.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        data = json.loads(stderr[start:end + 1])
        return float(data["input_i"])  # loudnorm quotes its numbers as strings, e.g. "-inf"
    except (json.JSONDecodeError, KeyError, ValueError, TypeError):
        return None


def probe_format(
    path: Path, tool_path: Path | None = None, override_path: str | None = None
) -> tuple[str, int | None, int | None, str, str]:
    """
    Returns (encoder, sample_rate_hz, channels, status, message) via
    ffprobe -- richer than what mutagen surfaces (already read for
    duration/bitrate on load, see core/tag_reader.py), specifically the
    actual encoder tag (e.g. "LAME3.100", "Lavf63.1.101") and sample
    rate/channel count. Note the encoder tag lives at the *format*
    level (container metadata), not the stream level -- ffprobe reports
    those as two separate objects, easy to look in the wrong one.
    """
    exe = tool_path if tool_path is not None else find_tool(FFPROBE_EXE_NAME, override=override_path)
    if exe is None:
        return "", None, None, STATUS_TOOL_MISSING, "ffprobe.exe not found (not bundled and not on PATH)"

    try:
        result = subprocess.run(
            [
                str(exe), "-v", "quiet", "-print_format", "json",
                "-show_format", "-show_streams", "-select_streams", "a:0", str(path),
            ],
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT_SECONDS,
            stdin=subprocess.DEVNULL,
            **no_window_kwargs(),
        )
    except subprocess.TimeoutExpired:
        return "", None, None, STATUS_ERROR, f"ffprobe timed out after {PROBE_TIMEOUT_SECONDS}s"
    except OSError as e:
        return "", None, None, STATUS_ERROR, f"failed to launch ffprobe: {e}"

    try:
        data = json.loads(result.stdout)
        stream = data["streams"][0]
    except (json.JSONDecodeError, KeyError, IndexError, TypeError):
        message = result.stderr.strip() or "ffprobe returned no audio stream info"
        return "", None, None, STATUS_ERROR, message

    encoder = str(data.get("format", {}).get("tags", {}).get("encoder", "") or "")
    sample_rate = int(stream["sample_rate"]) if stream.get("sample_rate") else None
    channels = int(stream["channels"]) if stream.get("channels") is not None else None
    return encoder, sample_rate, channels, STATUS_OK, ""
