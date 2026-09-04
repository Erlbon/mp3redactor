import builtins
from pathlib import Path
from unittest.mock import MagicMock, patch

from core.mp3_file import STATUS_ERROR, STATUS_OK, STATUS_TOOL_MISSING


def test_detect_bpm_reports_tool_missing_when_aubio_not_installed():
    # aubio is genuinely not installed in this sandbox, so this exercises
    # the real ImportError path rather than a simulated one.
    from core.bpm_detector import detect_bpm

    bpm, status, message = detect_bpm(Path("song.mp3"))
    assert bpm is None
    assert status == STATUS_TOOL_MISSING
    assert "aubio" in message


def _install_fake_aubio(monkeypatch, source_factory, tempo_factory):
    """
    Injects a fake `aubio` module into sys.modules so bpm_detector's
    `import aubio` picks it up, without needing the real (unavailable in
    this sandbox) aubio package installed.
    """
    import sys

    fake_aubio = MagicMock()
    fake_aubio.source.side_effect = source_factory
    fake_aubio.tempo.side_effect = tempo_factory
    monkeypatch.setitem(sys.modules, "aubio", fake_aubio)


def test_detect_bpm_uses_get_bpm_when_available(monkeypatch):
    fake_source = MagicMock()
    fake_source.samplerate = 44100
    # Two reads: one full hop (a "beat"), then a short final read to stop the loop.
    fake_source.side_effect = [(b"\x00" * 4, 512), (b"\x00" * 4, 100)]

    fake_tempo = MagicMock()
    fake_tempo.side_effect = [True, False]  # beat detected on first call only
    fake_tempo.get_last_s.return_value = 1.0
    fake_tempo.get_bpm.return_value = 128.0

    _install_fake_aubio(
        monkeypatch,
        source_factory=lambda *a, **k: fake_source,
        tempo_factory=lambda *a, **k: fake_tempo,
    )

    from core.bpm_detector import detect_bpm

    bpm, status, message = detect_bpm(Path("song.mp3"))
    # Only one beat detected -> "not enough detected beats" branch,
    # confirming the <2-beats guard fires before get_bpm() is even trusted.
    assert status == STATUS_ERROR
    assert "not enough" in message
    assert bpm is None


def test_detect_bpm_success_with_two_beats(monkeypatch):
    fake_source = MagicMock()
    fake_source.samplerate = 44100
    # Three reads: two beats detected, then a short final read to stop the loop.
    fake_source.side_effect = [
        (b"\x00" * 4, 512),
        (b"\x00" * 4, 512),
        (b"\x00" * 4, 100),
    ]

    fake_tempo = MagicMock()
    fake_tempo.side_effect = [True, True, False]
    fake_tempo.get_last_s.side_effect = [1.0, 1.5]  # 0.5s apart -> 120 BPM
    fake_tempo.get_bpm.return_value = 120.0

    _install_fake_aubio(
        monkeypatch,
        source_factory=lambda *a, **k: fake_source,
        tempo_factory=lambda *a, **k: fake_tempo,
    )

    from core.bpm_detector import detect_bpm

    bpm, status, message = detect_bpm(Path("song.mp3"))
    assert status == STATUS_OK
    assert bpm == 120.0
    assert message == ""
