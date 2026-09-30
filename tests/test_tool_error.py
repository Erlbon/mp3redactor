"""
TOOL ERROR: an external tool that itself failed (timeout, launch failure,
crash with no verdict) is reported as STATUS_TOOL_ERROR -- never stamped
into the file, never shown as a bad file -- while genuine verdicts from
mp3val / ffmpeg stay ERROR/WARNING.
"""

import os
import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from mutagen.id3 import ID3, TXXX  # noqa: E402

from core.ffmpeg_probe import deep_check_integrity  # noqa: E402
from core.mp3_file import (  # noqa: E402
    MP3File, STATUS_ERROR, STATUS_OK, STATUS_TOOL_ERROR, STATUS_WARNING, parse_scan_stamp,
    scan_display, scan_tooltip,
)
from core.mp3val_runner import check_integrity, fix_integrity  # noqa: E402
from core.scan_service import run_deep_check, run_integrity_check, run_integrity_fix  # noqa: E402
from core.tag_reader import load_tags  # noqa: E402
from core.tag_writer import save_tags  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "tiny.mp3"
STAMP = "OK;2026-09-30T14:05:11Z"
EXE = Path("tool.exe")


def _loaded(tmp_path, stamp: str | None = None) -> MP3File:
    path = tmp_path / "song.mp3"
    shutil.copyfile(FIXTURE, path)
    if stamp:
        tags = ID3(path)
        tags.add(TXXX(encoding=3, desc="REDACTOR_INTEGRITY", text=[stamp]))
        tags.save(path)
    mp3 = MP3File(path=path)
    load_tags(mp3)
    return mp3


def test_status_value():
    assert STATUS_TOOL_ERROR == "TOOL ERROR"
    assert parse_scan_stamp("TOOL ERROR;2026-09-30T14:05:11Z") is None


# -- mp3val runner -----------------------------------------------------------

@pytest.mark.parametrize("fn", [check_integrity, fix_integrity])
@pytest.mark.parametrize(
    "failure",
    [
        subprocess.TimeoutExpired("mp3val", 30),
        OSError("permission denied"),
        UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad"),
    ],
)
def test_mp3val_tool_failures_are_tool_error(fn, failure):
    with patch("core.mp3val_runner.run_tool", side_effect=failure):
        status, message = fn(Path("song.mp3"), tool_path=EXE)
    assert status == STATUS_TOOL_ERROR
    assert message


def test_mp3val_nonzero_exit_without_findings_is_tool_error():
    result = MagicMock(returncode=2, stdout="Analyzing song.mp3\n", stderr="crashed")
    with patch("core.mp3val_runner.run_tool", return_value=result):
        assert check_integrity(Path("song.mp3"), tool_path=EXE) == (STATUS_TOOL_ERROR, "crashed")


def test_mp3val_verdicts_are_unchanged():
    def run(stdout, code=0):
        result = MagicMock(returncode=code, stdout=stdout, stderr="")
        with patch("core.mp3val_runner.run_tool", return_value=result):
            return check_integrity(Path("song.mp3"), tool_path=EXE)[0]

    assert run("ERROR: bad frame\n") == STATUS_ERROR
    assert run("ERROR: bad frame\n", code=1) == STATUS_ERROR
    assert run("WARNING: minor\n") == STATUS_WARNING
    assert run("WARNING: minor\n", code=1) == STATUS_WARNING
    assert run("Analyzing song.mp3\n") == STATUS_OK


# -- ffmpeg deep check -------------------------------------------------------

@pytest.mark.parametrize(
    "failure",
    [
        subprocess.TimeoutExpired("ffmpeg", 120),
        OSError("not executable"),
        UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad"),
    ],
)
def test_deep_check_tool_failures_are_tool_error(failure):
    with patch("core.ffmpeg_probe.run_tool", side_effect=failure):
        status, message = deep_check_integrity(Path("song.mp3"), tool_path=EXE)
    assert status == STATUS_TOOL_ERROR
    assert message


def test_deep_check_nonzero_exit_with_no_stderr_is_tool_error():
    result = MagicMock(returncode=1, stderr="  ")
    with patch("core.ffmpeg_probe.run_tool", return_value=result):
        status, message = deep_check_integrity(Path("song.mp3"), tool_path=EXE)
    assert status == STATUS_TOOL_ERROR and "1" in message


def test_deep_check_decode_errors_stay_a_verdict():
    for code in (0, 1):
        result = MagicMock(returncode=code, stderr="[mp3float] Header missing\n")
        with patch("core.ffmpeg_probe.run_tool", return_value=result):
            assert deep_check_integrity(Path("s.mp3"), tool_path=EXE) == (
                STATUS_ERROR, "[mp3float] Header missing",
            )
    with patch("core.ffmpeg_probe.run_tool", return_value=MagicMock(returncode=0, stderr="")):
        assert deep_check_integrity(Path("s.mp3"), tool_path=EXE) == (STATUS_OK, "")


# -- record_scan / stamps ----------------------------------------------------

def test_record_scan_tool_error_neither_stamps_nor_dirties(tmp_path):
    mp3 = _loaded(tmp_path)
    mp3.record_scan("integrity", STATUS_TOOL_ERROR, "mp3val timed out")
    mp3.record_scan("deep_check", STATUS_TOOL_ERROR, "ffmpeg timed out")
    assert mp3.integrity_status == STATUS_TOOL_ERROR and mp3.deep_check_status == STATUS_TOOL_ERROR
    assert mp3.integrity_stamp == "" and mp3.deep_check_stamp == ""
    assert mp3.dirty is False


def test_previous_stamp_survives_a_tool_error_scan_and_save(tmp_path):
    mp3 = _loaded(tmp_path, stamp=STAMP)
    with patch("core.scan_service.check_integrity", return_value=(STATUS_TOOL_ERROR, "timed out")):
        run_integrity_check([mp3])
    assert mp3.integrity_status == STATUS_TOOL_ERROR
    assert mp3.integrity_stamp == STAMP and mp3.dirty is False
    mp3.apply_tags({"title": "T"})
    assert save_tags(mp3)
    assert str(ID3(mp3.path)["TXXX:REDACTOR_INTEGRITY"].text[0]) == STAMP


def test_save_writes_no_frame_for_tool_error(tmp_path):
    mp3 = _loaded(tmp_path)
    with patch("core.scan_service.check_integrity", return_value=(STATUS_TOOL_ERROR, "x")):
        run_integrity_check([mp3])
    with patch("core.scan_service.deep_check_integrity", return_value=(STATUS_TOOL_ERROR, "x")), \
            patch("core.scan_service.probe_format", return_value=("", None, None, STATUS_ERROR, "")):
        run_deep_check([mp3], max_workers=1)
    mp3.apply_tags({"title": "T"})
    assert save_tags(mp3)
    tags = ID3(mp3.path)
    assert "TXXX:REDACTOR_INTEGRITY" not in tags and "TXXX:REDACTOR_DEEP_CHECK" not in tags


def test_fix_tool_error_reloads_but_does_not_stamp(tmp_path):
    mp3 = _loaded(tmp_path, stamp=STAMP)
    with patch("core.scan_service.fix_integrity", return_value=(STATUS_TOOL_ERROR, "timed out")), \
            patch("core.scan_service.reload_from_disk") as reload:
        run_integrity_fix([mp3])
    reload.assert_called_once_with(mp3)
    assert mp3.integrity_status == STATUS_TOOL_ERROR
    assert mp3.integrity_stamp == STAMP and mp3.dirty is False


def test_display_and_tooltip():
    assert scan_display(STATUS_TOOL_ERROR, STAMP) == "TOOL ERROR"  # stamp is for an older OK
    assert scan_tooltip(STATUS_TOOL_ERROR, STAMP, "mp3val timed out after 30s") == "mp3val timed out after 30s"


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


def test_gui_cell_shows_tool_error_with_message_and_warning_colour(window, tmp_path):
    import gui.main_window as mw

    mp3 = _loaded(tmp_path, stamp=STAMP)
    window.files = [mp3]
    window._rebuild_table()
    with patch("core.scan_service.check_integrity", return_value=(STATUS_TOOL_ERROR, "mp3val timed out")):
        window.table.selectRow(0)
        window.run_integrity_check()
    item = window.table.item(0, window._col_index["integrity"])
    assert item.text() == "TOOL ERROR"
    assert "mp3val timed out" in item.toolTip()
    assert mw.STATUS_COLORS[STATUS_TOOL_ERROR] != mw.STATUS_COLORS[STATUS_ERROR]
    assert item.foreground().color() == mw.STATUS_COLORS[STATUS_TOOL_ERROR]
    assert not mp3.dirty
