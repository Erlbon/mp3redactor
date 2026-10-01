"""
TOOL ERROR for the BPM, key, loudness, format-probe and lyrics paths: a tool
that itself fails (timeout, launch/OS error, undecodable output, non-zero exit
with no usable output) is STATUS_TOOL_ERROR -- shown, never written to a tag,
never marks the file dirty -- while genuine verdicts keep their status.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from mutagen.id3 import ID3  # noqa: E402

import core.redact_steps as rs  # noqa: E402
from core.bpm_detector import detect_bpm  # noqa: E402
from core.ffmpeg_probe import measure_loudness, probe_format  # noqa: E402
from core.keyfinder_runner import detect_key  # noqa: E402
from core.lyrics_fetcher import fetch_lyrics  # noqa: E402
from core.mp3_file import (  # noqa: E402
    MP3File, STATUS_ERROR, STATUS_OK, STATUS_TOOL_ERROR,
)
from core.scan_service import (  # noqa: E402
    run_bpm_check, run_key_detection, run_loudness_measurement, run_lyrics_fetch,
)
from core.tag_reader import load_tags  # noqa: E402
from core.tag_writer import save_tags  # noqa: E402
from redactor_common.core.pipeline import FileStatus  # noqa: E402
from tests.test_redact_steps import make_file, run  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "tiny.mp3"
EXE = Path("tool.exe")

FAILURES = [
    subprocess.TimeoutExpired("tool", 30),
    OSError("permission denied"),
    UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad"),
]


def _loaded(tmp_path) -> MP3File:
    path = tmp_path / "song.mp3"
    shutil.copyfile(FIXTURE, path)
    mp3 = MP3File(path=path)
    load_tags(mp3)
    return mp3


# -- keyfinder-cli -----------------------------------------------------------

@pytest.mark.parametrize("failure", FAILURES)
def test_key_tool_failures_are_tool_error(failure):
    with patch("core.keyfinder_runner.run_tool", side_effect=failure):
        key, status, message = detect_key(Path("song.mp3"), tool_path=EXE)
    assert (key, status) == ("", STATUS_TOOL_ERROR)
    assert message


def test_key_nonzero_exit_is_tool_error():
    result = MagicMock(returncode=3, stdout="", stderr="")
    with patch("core.keyfinder_runner.run_tool", return_value=result):
        key, status, message = detect_key(Path("song.mp3"), tool_path=EXE)
    assert status == STATUS_TOOL_ERROR and "3" in message


def test_key_verdicts_are_unchanged():
    ok = MagicMock(returncode=0, stdout="Am\n", stderr="")
    silent = MagicMock(returncode=0, stdout="", stderr="")
    with patch("core.keyfinder_runner.run_tool", return_value=ok):
        assert detect_key(Path("s.mp3"), tool_path=EXE) == ("Am", STATUS_OK, "")
    with patch("core.keyfinder_runner.run_tool", return_value=silent):
        key, status, message = detect_key(Path("s.mp3"), tool_path=EXE)
    assert (key, status) == ("", STATUS_OK) and "no key" in message  # silence is not an error


# -- ffmpeg loudness ---------------------------------------------------------

@pytest.mark.parametrize("failure", FAILURES)
def test_loudness_tool_failures_are_tool_error(failure):
    with patch("core.ffmpeg_probe.run_tool", side_effect=failure):
        lufs, gain, status, message = measure_loudness(Path("song.mp3"), tool_path=EXE)
    assert (lufs, gain, status) == (None, None, STATUS_TOOL_ERROR)
    assert message


@pytest.mark.parametrize("returncode,stderr", [(1, ""), (0, "garbage, no summary"), (1, "Invalid data")])
def test_loudness_without_a_measurement_is_tool_error(returncode, stderr):
    result = MagicMock(returncode=returncode, stderr=stderr)
    with patch("core.ffmpeg_probe.run_tool", return_value=result):
        lufs, gain, status, message = measure_loudness(Path("song.mp3"), tool_path=EXE)
    assert (lufs, gain, status) == (None, None, STATUS_TOOL_ERROR)
    assert message


def test_loudness_verdicts_are_unchanged():
    measured = MagicMock(returncode=0, stderr='log\n{\n"input_i" : "-20.00"\n}\n')
    silent = MagicMock(returncode=0, stderr='{\n"input_i" : "-inf"\n}\n')
    with patch("core.ffmpeg_probe.run_tool", return_value=measured):
        assert measure_loudness(Path("s.mp3"), tool_path=EXE) == (-20.0, 2.0, STATUS_OK, "")
    with patch("core.ffmpeg_probe.run_tool", return_value=silent):
        lufs, gain, status, message = measure_loudness(Path("s.mp3"), tool_path=EXE)
    assert (lufs, gain, status) == (None, None, STATUS_OK) and "silent" in message


# -- ffprobe -----------------------------------------------------------------

@pytest.mark.parametrize("failure", FAILURES)
def test_probe_tool_failures_are_tool_error(failure):
    with patch("core.ffmpeg_probe.run_tool", side_effect=failure):
        *_info, status, message = probe_format(Path("song.mp3"), tool_path=EXE)
    assert status == STATUS_TOOL_ERROR and message


def test_probe_without_json_is_tool_error():
    result = MagicMock(returncode=1, stdout="", stderr="")
    with patch("core.ffmpeg_probe.run_tool", return_value=result):
        *_info, status, message = probe_format(Path("song.mp3"), tool_path=EXE)
    assert status == STATUS_TOOL_ERROR and "1" in message


def test_probe_verdicts_are_unchanged():
    no_stream = MagicMock(returncode=1, stdout="{\r\n\r\n}\r\n", stderr="")
    good = MagicMock(
        returncode=0, stderr="",
        stdout='{"streams":[{"sample_rate":"44100","channels":2}],"format":{"tags":{"encoder":"LAME3.100"}}}',
    )
    with patch("core.ffmpeg_probe.run_tool", return_value=no_stream):
        *_info, status, _msg = probe_format(Path("s.mp3"), tool_path=EXE)
    assert status == STATUS_ERROR  # ffprobe answered: no audio stream in the file
    with patch("core.ffmpeg_probe.run_tool", return_value=good):
        assert probe_format(Path("s.mp3"), tool_path=EXE) == ("LAME3.100", 44100, 2, STATUS_OK, "")


# -- aubio -------------------------------------------------------------------

def test_bpm_aubio_failure_is_tool_error(monkeypatch):
    fake = MagicMock()
    fake.source.side_effect = RuntimeError("could not open file")
    monkeypatch.setitem(sys.modules, "aubio", fake)
    with patch("core.bpm_detector.find_tool", return_value=None):  # no ffmpeg fallback either
        bpm, status, message = detect_bpm(Path("song.mp3"))
    assert bpm is None and status == STATUS_TOOL_ERROR and "could not open" in message


def test_bpm_too_few_beats_stays_a_verdict(monkeypatch):
    source = MagicMock()
    source.samplerate = 44100
    source.side_effect = [(b"\x00", 100)]
    tempo = MagicMock(return_value=False)
    fake = MagicMock()
    fake.source.return_value = source
    fake.tempo.return_value = tempo
    monkeypatch.setitem(sys.modules, "aubio", fake)
    bpm, status, _message = detect_bpm(Path("song.mp3"))
    assert bpm is None and status == STATUS_ERROR


# -- lyrics ------------------------------------------------------------------

@patch("lyricy.Lyricy.search")
def test_lyrics_nothing_found_stays_a_verdict_but_network_failure_is_tool_error(mock_search):
    mock_search.return_value = []
    assert fetch_lyrics("Artist Title")[1] == STATUS_ERROR
    mock_search.side_effect = ConnectionError("down")
    assert fetch_lyrics("Artist Title")[1] == STATUS_TOOL_ERROR


# -- scan service: nothing is learned, nothing is dirtied, values survive -----

def test_scan_tool_errors_keep_values_and_never_dirty(tmp_path):
    mp3 = _loaded(tmp_path)
    mp3.bpm, mp3.bpm_status = 120.0, STATUS_OK
    mp3.key_value, mp3.key_status = "Am", STATUS_OK
    mp3.loudness_lufs, mp3.loudness_gain_db, mp3.loudness_status = -20.0, 2.0, STATUS_OK
    mp3.lyrics, mp3.lyrics_status = "la la", STATUS_OK
    with patch("core.scan_service.detect_bpm", return_value=(None, STATUS_TOOL_ERROR, "aubio failed")), \
            patch("core.scan_service.detect_key", return_value=("", STATUS_TOOL_ERROR, "timed out")), \
            patch("core.scan_service.measure_loudness",
                  return_value=(None, None, STATUS_TOOL_ERROR, "ffmpeg timed out")), \
            patch("core.scan_service.fetch_lyrics", return_value=("", STATUS_TOOL_ERROR, "offline")):
        run_bpm_check([mp3], max_workers=1)
        run_key_detection([mp3], max_workers=1)
        run_loudness_measurement([mp3], max_workers=1)
        run_lyrics_fetch([mp3], max_workers=1)
    assert (mp3.bpm_status, mp3.key_status, mp3.loudness_status, mp3.lyrics_status) == (STATUS_TOOL_ERROR,) * 4
    assert "aubio failed" in mp3.bpm_message and "timed out" in mp3.key_message
    assert (mp3.bpm, mp3.key_value, mp3.loudness_gain_db, mp3.lyrics) == (120.0, "Am", 2.0, "la la")
    assert mp3.dirty is False


def test_save_after_tool_errors_writes_nothing_for_them(tmp_path):
    mp3 = _loaded(tmp_path)
    tags = ID3(mp3.path)
    tags.delall("TBPM")
    tags.save(mp3.path)
    mp3.bpm, mp3.key_value = 120.0, "Am"
    mp3.loudness_gain_db = 2.0
    mp3.bpm_status = mp3.key_status = mp3.loudness_status = STATUS_TOOL_ERROR
    mp3.apply_tags({"title": "T"})
    assert save_tags(mp3)
    saved = ID3(mp3.path)
    assert "TBPM" not in saved and "TKEY" not in saved
    assert "TXXX:REPLAYGAIN_TRACK_GAIN" not in saved


def test_existing_tags_survive_a_tool_error_and_save(tmp_path):
    mp3 = _loaded(tmp_path)
    mp3.bpm, mp3.bpm_status = 100.0, STATUS_OK
    mp3.key_value, mp3.key_status = "C", STATUS_OK
    mp3.dirty = True
    assert save_tags(mp3)
    reloaded = MP3File(path=mp3.path)  # not _loaded(): that would overwrite the saved file
    load_tags(reloaded)
    assert reloaded.key_value == "C"
    with patch("core.scan_service.detect_key", return_value=("", STATUS_TOOL_ERROR, "x")):
        run_key_detection([reloaded], max_workers=1)
    reloaded.apply_tags({"title": "T"})
    assert save_tags(reloaded)
    assert str(ID3(reloaded.path)["TKEY"].text[0]) == "C"


def test_scan_verdict_error_behaviour_is_unchanged():
    mp3 = MP3File(path=Path("song.mp3"))
    mp3.bpm = 120.0
    with patch("core.scan_service.detect_bpm", return_value=(None, STATUS_ERROR, "not enough beats")):
        run_bpm_check([mp3], max_workers=1)
    assert mp3.bpm is None and mp3.bpm_status == STATUS_ERROR and mp3.dirty is False


# -- Redact steps ------------------------------------------------------------

def test_redact_bpm_key_loudness_tool_errors_fail_the_step_and_write_nothing(tmp_path, monkeypatch, recycle_bin):
    mp3 = make_file(tmp_path)
    before = mp3.path.read_bytes()
    monkeypatch.setattr(rs, "detect_bpm", lambda p: (None, STATUS_TOOL_ERROR, "aubio failed"))
    monkeypatch.setattr(rs, "detect_key", lambda p, override_path=None: ("", STATUS_TOOL_ERROR, "keyfinder timed out"))
    monkeypatch.setattr(
        rs, "measure_loudness", lambda p, override_path=None: (None, None, STATUS_TOOL_ERROR, "ffmpeg timed out"),
    )
    entry, _env = run(mp3, {"bpm", "key", "loudness"})
    assert entry.status is FileStatus.FAILED and len(entry.failures) == 3
    assert mp3.path.read_bytes() == before
    assert not mp3.dirty


def test_redact_bpm_genuine_miss_is_still_only_a_note(tmp_path, monkeypatch, recycle_bin):
    mp3 = make_file(tmp_path)
    monkeypatch.setattr(rs, "detect_bpm", lambda p: (None, STATUS_ERROR, "not enough detected beats"))
    entry, _env = run(mp3, {"bpm"})
    assert entry.status is not FileStatus.FAILED
    assert any("BPM not detected" in n for n in entry.notes)


# -- GUI cells ---------------------------------------------------------------

@pytest.fixture
def window(monkeypatch):
    from PyQt6.QtWidgets import QApplication, QMessageBox

    import gui.main_window as mw

    QApplication.instance() or QApplication([])
    monkeypatch.setattr(mw, "save_settings", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))
    w = mw.MainWindow()
    w.show()
    yield w
    for mp3 in w.files:
        mp3.dirty = False
    w.close()


def test_gui_cells_show_tool_error_in_the_integrity_colour(window, tmp_path):
    import gui.main_window as mw

    mp3 = _loaded(tmp_path)
    window.files = [mp3]
    with patch("core.scan_service.detect_bpm", return_value=(None, STATUS_TOOL_ERROR, "aubio failed")), \
            patch("core.scan_service.detect_key", return_value=("", STATUS_TOOL_ERROR, "keyfinder timed out")), \
            patch("core.scan_service.measure_loudness",
                  return_value=(None, None, STATUS_TOOL_ERROR, "ffmpeg timed out")), \
            patch("core.scan_service.fetch_lyrics", return_value=("", STATUS_TOOL_ERROR, "offline")):
        window._rebuild_table()
        window.table.selectRow(0)
        window.run_bpm_check()
        window.run_key_detection()
        window.run_loudness_measurement()
        window.run_lyrics_fetch()
    orange = mw.STATUS_COLORS[STATUS_TOOL_ERROR]
    for column, message in (
        ("bpm", "aubio failed"), ("key", "keyfinder timed out"),
        ("loudness", "ffmpeg timed out"), ("lyrics", "offline"),
    ):
        item = window.table.item(0, window._col_index[column])
        assert item.text() == "TOOL ERROR", column
        assert message in item.toolTip(), column
        assert item.foreground().color() == orange, column
    assert not mp3.dirty
