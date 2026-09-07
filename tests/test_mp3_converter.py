"""
core/mp3_converter.py -- converting a non-MP3 file to MP3 via the real
bundled ffmpeg.exe (tools/, gitignored), same "exercise the actual
process" approach as test_ffmpeg_probe.py.
"""

import wave
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from core.mp3_converter import convert_to_mp3
from core.mp3_file import STATUS_ERROR, STATUS_OK, STATUS_TOOL_MISSING
from core.tool_locator import find_tool

_FFMPEG = find_tool("ffmpeg.exe")
requires_ffmpeg = pytest.mark.skipif(_FFMPEG is None, reason="ffmpeg.exe not bundled in tools/")


def _write_wav(path: Path, seconds: float = 2.0) -> None:
    sr = 44100
    n = int(sr * seconds)
    t = np.linspace(0, seconds, n, endpoint=False)
    audio = (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    pcm = (audio * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


@requires_ffmpeg
def test_convert_to_mp3_produces_a_real_readable_mp3(tmp_path):
    src = tmp_path / "tone.wav"
    _write_wav(src)
    dest = tmp_path / "tone.mp3"

    status, message = convert_to_mp3(src, dest, bitrate_kbps=192)

    assert status == STATUS_OK
    assert message == ""
    assert dest.exists()

    # Independently confirm it's a genuinely valid, readable MP3 --
    # not just that ffmpeg exited 0 -- via mutagen, the same library
    # this app's own tag reading/writing uses.
    from mutagen.mp3 import MP3

    audio = MP3(dest)
    assert audio.info.sample_rate == 44100
    assert 1.5 < audio.info.length < 2.5


@requires_ffmpeg
def test_convert_to_mp3_overwrites_an_existing_dest(tmp_path):
    src = tmp_path / "tone.wav"
    _write_wav(src)
    dest = tmp_path / "tone.mp3"
    dest.write_bytes(b"not a real mp3")  # pre-existing junk at the dest path

    status, _message = convert_to_mp3(src, dest, bitrate_kbps=128)

    assert status == STATUS_OK
    assert dest.read_bytes() != b"not a real mp3"


@requires_ffmpeg
def test_convert_to_mp3_reports_error_for_a_nonexistent_source(tmp_path):
    status, message = convert_to_mp3(tmp_path / "does-not-exist.flac", tmp_path / "out.mp3")
    assert status == STATUS_ERROR
    assert message != ""


def test_convert_to_mp3_tool_missing_when_not_found(tmp_path):
    with patch("core.mp3_converter.find_tool", return_value=None):
        status, message = convert_to_mp3(tmp_path / "in.wav", tmp_path / "out.mp3")
    assert status == STATUS_TOOL_MISSING
    assert "ffmpeg" in message
