"""
detect_bpm: aubio reads the file directly first; when that open/read fails
(the Linux wheel can't decode MP3) the file is decoded once by ffmpeg to a
temporary mono WAV and aubio runs on that. A "not enough beats" verdict is
never retried, a missing/failing ffmpeg is a TOOL ERROR, and the temporary
WAV is always removed.
"""

import math
import struct
import sys
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import core.bpm_detector as bd
from core.mp3_file import STATUS_ERROR, STATUS_OK, STATUS_TOOL_ERROR
from core.tool_locator import find_tool

EXE = Path("ffmpeg.exe")


def _fake_aubio(monkeypatch, fail_on=None, bpm=128.0, beats=(1.0, 1.5)):
    """A fake aubio whose source() raises for paths ending in `fail_on`;
    records every path it was asked to open."""
    opened: list[str] = []

    def source(path, samplerate, hop):
        opened.append(path)
        if fail_on and path.endswith(fail_on):
            raise RuntimeError("AUBIO ERROR: source_wavread: could not find RIFF header")
        src = MagicMock()
        src.samplerate = 44100
        src.side_effect = [(b"\x00", bd.HOP_S), (b"\x00", 10)]
        return src

    tempo = MagicMock()
    tempo.side_effect = [True, True]
    tempo.get_last_s.side_effect = list(beats)
    tempo.get_bpm.return_value = bpm
    fake = MagicMock()
    fake.source.side_effect = source
    fake.tempo.return_value = tempo
    monkeypatch.setitem(sys.modules, "aubio", fake)
    return opened


def _ffmpeg(written: list):
    """A fake run_tool that 'decodes' by recording the command and leaving a file."""

    def run(args, timeout=None, **kwargs):
        written.append(list(args))
        return MagicMock(returncode=0, stderr="")

    return run


def test_direct_success_never_touches_ffmpeg(monkeypatch):
    opened = _fake_aubio(monkeypatch)
    with patch("core.bpm_detector.run_tool") as run, patch("core.bpm_detector.find_tool") as find:
        assert bd.detect_bpm(Path("song.mp3")) == (128.0, STATUS_OK, "")
    assert opened == ["song.mp3"]
    run.assert_not_called()
    find.assert_not_called()


def test_decode_failure_retries_once_through_ffmpeg_and_cleans_up(monkeypatch):
    opened = _fake_aubio(monkeypatch, fail_on="song.mp3")
    commands: list[list[str]] = []
    seen_temp = []

    def run(args, timeout=None, **kwargs):
        commands.append(list(args))
        seen_temp.append(Path(args[-1]).exists())  # mkstemp created it for ffmpeg to fill
        return MagicMock(returncode=0, stderr="")

    with patch("core.bpm_detector.find_tool", return_value=EXE), patch("core.bpm_detector.run_tool", side_effect=run):
        assert bd.detect_bpm(Path("song.mp3")) == (128.0, STATUS_OK, "")
    (cmd,) = commands
    assert cmd[0] == str(EXE) and "-i" in cmd and cmd[cmd.index("-i") + 1] == "song.mp3"
    for flag, value in (("-ac", "1"), ("-ar", str(bd.FFMPEG_DECODE_RATE)), ("-t", str(bd.FFMPEG_DECODE_SECONDS)), ("-f", "wav")):
        assert cmd[cmd.index(flag) + 1] == value
    assert "-vn" in cmd
    wav = cmd[-1]
    assert seen_temp == [True] and opened == ["song.mp3", wav]
    assert not Path(wav).exists()  # removed after success


def test_ffmpeg_missing_is_a_tool_error_with_a_clear_message(monkeypatch):
    _fake_aubio(monkeypatch, fail_on="song.mp3")
    with patch("core.bpm_detector.find_tool", return_value=None), patch("core.bpm_detector.run_tool") as run:
        bpm, status, message = bd.detect_bpm(Path("song.mp3"))
    assert bpm is None and status == STATUS_TOOL_ERROR
    assert "BPM needs ffmpeg to decode this file" in message and "RIFF" in message
    run.assert_not_called()


@pytest.mark.parametrize(
    "outcome",
    [
        __import__("subprocess").TimeoutExpired("ffmpeg", 120),
        OSError("not executable"),
        UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad"),
        MagicMock(returncode=1, stderr="Invalid data found when processing input"),
        MagicMock(returncode=1, stderr=""),
    ],
)
def test_ffmpeg_failures_are_tool_errors_and_leave_no_temp_file(monkeypatch, outcome):
    _fake_aubio(monkeypatch, fail_on="song.mp3")
    temp = []

    def run(args, timeout=None, **kwargs):
        temp.append(args[-1])
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    with patch("core.bpm_detector.find_tool", return_value=EXE), patch("core.bpm_detector.run_tool", side_effect=run):
        bpm, status, message = bd.detect_bpm(Path("song.mp3"))
    assert bpm is None and status == STATUS_TOOL_ERROR
    assert "BPM needs ffmpeg to decode this file" in message
    assert temp and not Path(temp[0]).exists()


def test_failure_analysing_the_decoded_wav_is_a_tool_error_and_cleans_up(monkeypatch):
    opened = _fake_aubio(monkeypatch, fail_on=".wav")
    # the direct open of song.mp3 works here, but then reading fails...
    fake = sys.modules["aubio"]
    real_source = fake.source.side_effect

    def source(path, samplerate, hop):
        if path == "song.mp3":
            raise RuntimeError("first failure")
        return real_source(path, samplerate, hop)

    fake.source.side_effect = source
    temp = []

    def run(args, timeout=None, **kwargs):
        temp.append(args[-1])
        return MagicMock(returncode=0, stderr="")

    with patch("core.bpm_detector.find_tool", return_value=EXE), patch("core.bpm_detector.run_tool", side_effect=run):
        bpm, status, message = bd.detect_bpm(Path("song.mp3"))
    assert bpm is None and status == STATUS_TOOL_ERROR and "BPM detection failed" in message
    assert not Path(temp[0]).exists()


def test_not_enough_beats_is_a_verdict_and_is_not_retried(monkeypatch):
    _fake_aubio(monkeypatch)
    sys.modules["aubio"].tempo.return_value.side_effect = [False, False]
    with patch("core.bpm_detector.run_tool") as run, patch("core.bpm_detector.find_tool") as find:
        bpm, status, message = bd.detect_bpm(Path("song.mp3"))
    assert bpm is None and status == STATUS_ERROR and "not enough" in message
    run.assert_not_called()
    find.assert_not_called()


# -- real tools ---------------------------------------------------------------

def _click_track(path: Path, bpm=120, seconds=20, rate=22050) -> None:
    """A mono WAV of short 1 kHz bursts on every beat."""
    samples = []
    beat = int(rate * 60 / bpm)
    burst = int(rate * 0.03)
    for i in range(rate * seconds):
        pos = i % beat
        value = int(30000 * math.sin(2 * math.pi * 1000 * pos / rate) * (1 - pos / burst)) if pos < burst else 0
        samples.append(value)
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(struct.pack(f"<{len(samples)}h", *samples))


def test_real_bpm_of_a_click_track_mp3_end_to_end(tmp_path):
    pytest.importorskip("aubio")
    exe = find_tool(bd.FFMPEG_EXE_NAME)
    if exe is None:
        pytest.skip("ffmpeg not available")
    from core.subprocess_utils import run_tool

    wav = tmp_path / "clicks.wav"
    mp3 = tmp_path / "clicks.mp3"
    _click_track(wav)
    done = run_tool([str(exe), "-y", "-v", "error", "-i", str(wav), "-b:a", "128k", str(mp3)], timeout=120)
    if done.returncode != 0 or not mp3.exists():
        pytest.skip("this ffmpeg can't encode MP3")
    bpm, status, message = bd.detect_bpm(mp3)
    assert status == STATUS_OK, message
    # aubio may report the beat or a half/double of it; any of them is the click track's tempo
    assert any(abs(bpm - target) <= 4 for target in (60, 120, 240)), bpm
