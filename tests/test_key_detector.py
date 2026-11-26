"""core.key_detector: the Python port of libkeyfinder (keyfinder-cli's engine).

Agreement with the real keyfinder-cli on real music is measured by
tools/compare_keyfinder.py, not here (it needs the exe and audio files);
these tests pin the port's own behaviour: keys of plain chords, silence,
streaming in blocks giving the same answer as one block, and decoding."""

import math
import wave
from pathlib import Path

import numpy as np
import pytest

from core import key_detector as kd
from core.mp3_file import STATUS_OK, STATUS_TOOL_ERROR

FIXTURES = Path(__file__).parent / "fixtures"


def _triad(root_hz: float, minor: bool, fs: int, seconds: float) -> np.ndarray:
    t = np.arange(int(fs * seconds)) / fs
    third = 2 ** ((3 if minor else 4) / 12)
    fifth = 2 ** (7 / 12)
    out = np.zeros_like(t)
    for f in (root_hz, root_hz * third, root_hz * fifth, root_hz / 2):
        for h in range(1, 4):
            out += np.sin(2 * math.pi * f * h * t) / h
    return out * 8000


@pytest.mark.parametrize(
    "root_hz, minor, expected",
    [(261.63, False, "C"), (220.0, True, "Am"), (392.0, False, "G"), (293.66, True, "Dm")],
)
def test_plain_triads_give_their_key(root_hz, minor, expected):
    assert kd.key_name(kd.key_of_samples(_triad(root_hz, minor, 44100, 6), 44100)) == expected


def test_silence_has_no_key():
    assert kd.key_of_samples(np.zeros(44100 * 3), 44100) == kd.SILENCE
    assert kd.key_name(kd.SILENCE) == ""


def test_too_short_for_one_frame_is_still_analysed():
    # finalChromagram pads a short signal to one whole frame.
    assert kd.key_name(kd.key_of_samples(_triad(261.63, False, 44100, 0.5), 44100)) == "C"


def test_block_size_does_not_change_the_result():
    rng = np.random.default_rng(3)
    x = _triad(329.63, False, 48000, 4) + rng.standard_normal(48000 * 4) * 500 + rng.standard_normal(1)
    sums = []
    for block in (777, 4096, len(x)):
        a = kd.KeyAnalyser(48000)
        for i in range(0, len(x), block):
            a.feed(x[i:i + block])
        a.finish()
        sums.append(a._chroma_sum / a._hops)
    assert np.allclose(sums[0], sums[2], rtol=1e-12, atol=0)
    assert np.allclose(sums[1], sums[2], rtol=1e-12, atol=0)


def test_downsample_factor_matches_libkeyfinder():
    assert kd.downsample_factor(44100) == 10
    assert kd.downsample_factor(48000) == 11
    assert kd.downsample_factor(8000) == 1


def test_a_sample_rate_too_low_is_an_error():
    with pytest.raises(kd.KeyDetectionError):
        kd.KeyAnalyser(4000)


def test_lowpass_filter_is_161_symmetric_taps():
    coefficients, gain = kd._lowpass_coefficients(np, 44100)
    assert len(coefficients) == 161
    assert np.allclose(coefficients, coefficients[::-1])
    assert gain > 0


def test_detect_key_native_reads_a_wav(tmp_path):
    fs = 22050
    data = _triad(220.0, True, fs, 4)
    path = tmp_path / "a-minor.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(fs)
        w.writeframes(np.clip(data, -32767, 32767).astype("<i2").tobytes())
    assert kd.detect_key_native(path) == ("Am", STATUS_OK, "")


NON_ENGLISH_NAMES = [
    "Züri Punx.wav",               # u-umlaut (inside the Windows-1252 code page)
    "blåbær søt.wav",    # Norwegian letters (inside Windows-1252)
    "Łódź.wav",          # Polish (outside Windows-1252)
    "日本.wav",                 # Japanese
]


def _write_a_minor_wav(path: Path, fs: int = 22050) -> None:
    data = _triad(220.0, True, fs, 3)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(fs)
        w.writeframes(np.clip(data, -32767, 32767).astype("<i2").tobytes())


@pytest.mark.parametrize("name", NON_ENGLISH_NAMES)
def test_non_english_file_names_are_read(tmp_path, name):
    # The Windows build of keyfinder-cli could not open any file whose path
    # had a letter outside plain ASCII (its argv arrives in the ANSI code
    # page, FFmpeg wants UTF-8), so such files never got a key. The port
    # must read them, folder name included.
    folder = tmp_path / "Müsik ø"
    folder.mkdir()
    path = folder / name
    _write_a_minor_wav(path)
    assert kd.detect_key_native(path) == ("Am", STATUS_OK, "")


@pytest.mark.parametrize("name", NON_ENGLISH_NAMES)
def test_non_english_file_names_are_read_by_the_ffmpeg_fallback(tmp_path, name):
    from core.tool_locator import find_tool

    ffmpeg = find_tool("ffmpeg.exe")
    if ffmpeg is None:
        pytest.skip("ffmpeg not installed")
    path = tmp_path / name
    _write_a_minor_wav(path)
    rate, blocks, finish = kd._ffmpeg_blocks(str(ffmpeg), str(path))
    assert kd.key_name(kd._analyse_blocks(rate, blocks)) == "Am"
    assert finish() == ""


def test_detect_key_native_missing_file_without_ffmpeg_is_a_tool_error(tmp_path, monkeypatch):
    monkeypatch.setattr("core.tool_locator.find_tool", lambda *a, **k: None)
    key, status, message = kd.detect_key_native(tmp_path / "missing.mp3")
    assert (key, status) == ("", STATUS_TOOL_ERROR)
    assert "ffmpeg not found" in message


def test_detect_key_native_on_the_mp3_fixture():
    key, status, _ = kd.detect_key_native(FIXTURES / "tiny.mp3")
    assert status == STATUS_OK
    assert key in kd.STANDARD_NOTATION or key == ""
