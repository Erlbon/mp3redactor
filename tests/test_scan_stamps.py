"""
Persisted validation-scan stamps: TXXX:REDACTOR_INTEGRITY / _DEEP_CHECK
("STATUS;UTC time") written on Save, read back on load, shown in the scan
columns in place of "UNCHECKED".
"""

import os
import shutil
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from mutagen.id3 import ID3, TALB, TIT2, TPE1, TXXX  # noqa: E402

from core.mp3_file import (  # noqa: E402
    MP3File, STATUS_ERROR, STATUS_OK, STATUS_TOOL_ERROR, STATUS_TOOL_MISSING, STATUS_UNCHECKED,
    STATUS_WARNING,
    parse_scan_stamp, scan_display, scan_tooltip,
)
from core.scan_service import run_deep_check, run_integrity_check, run_integrity_fix  # noqa: E402
from core.tag_reader import load_tags  # noqa: E402
from core.tag_writer import save_tags  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "tiny.mp3"
STAMP = "OK;2026-09-30T14:05:11Z"


def _copy(tmp_path, name="song.mp3") -> Path:
    dest = tmp_path / name
    shutil.copyfile(FIXTURE, dest)
    return dest


def _loaded(tmp_path) -> MP3File:
    mp3 = MP3File(path=_copy(tmp_path))
    load_tags(mp3)
    return mp3


def _put_frame(path, desc, text):
    tags = ID3(path)
    tags.add(TXXX(encoding=3, desc=desc, text=[text]))
    tags.save(path)


def test_parse_is_tolerant():
    assert parse_scan_stamp(STAMP)[0] == STATUS_OK
    for bad in ("", "garbage", "OK", "OK;", "OK;yesterday", "UNCHECKED;2026-09-30T14:05:11Z",
                "TOOL MISSING;2026-09-30T14:05:11Z", ";2026-09-30T14:05:11Z", None):
        assert parse_scan_stamp(bad) is None


def test_load_reads_stamp_without_marking_dirty(tmp_path):
    path = _copy(tmp_path)
    _put_frame(path, "REDACTOR_INTEGRITY", "WARNING;2026-09-30T14:05:11Z")
    _put_frame(path, "REDACTOR_DEEP_CHECK", STAMP)
    mp3 = MP3File(path=path)
    load_tags(mp3)
    assert mp3.integrity_status == STATUS_WARNING
    assert mp3.integrity_scanned_at == "2026-09-30T14:05:11Z"
    assert mp3.deep_check_status == STATUS_OK
    assert mp3.dirty is False
    assert not mp3.tag_changed("integrity_stamp")
    assert "·" in scan_display(mp3.deep_check_status, mp3.deep_check_stamp)


def test_garbled_frame_is_ignored_and_left_alone_on_save(tmp_path):
    path = _copy(tmp_path)
    _put_frame(path, "REDACTOR_INTEGRITY", "banana")
    mp3 = MP3File(path=path)
    load_tags(mp3)
    assert mp3.integrity_status == STATUS_UNCHECKED
    assert mp3.integrity_scanned_at == ""
    mp3.apply_tags({"title": "T"})
    assert save_tags(mp3)
    assert str(ID3(path)["TXXX:REDACTOR_INTEGRITY"].text[0]) == "banana"


def test_round_trip_keeps_v23_and_other_frames(tmp_path):
    path = _copy(tmp_path)
    tags = ID3(path)
    tags.add(TIT2(encoding=3, text=["Title"]))
    tags.add(TPE1(encoding=3, text=["A", "B"]))
    tags.add(TXXX(encoding=3, desc="Other", text=["keep"]))
    tags.save(path, v2_version=3)
    mp3 = MP3File(path=path)
    load_tags(mp3)
    with patch("core.scan_service.check_integrity", return_value=(STATUS_WARNING, "w")):
        run_integrity_check([mp3])
    with patch("core.scan_service.deep_check_integrity", return_value=(STATUS_OK, "")), \
            patch("core.scan_service.probe_format", return_value=("", None, None, STATUS_ERROR, "")):
        run_deep_check([mp3], max_workers=1)
    assert mp3.dirty
    stamp_before = mp3.integrity_stamp
    assert save_tags(mp3) and not mp3.dirty

    on_disk = ID3(path)
    assert on_disk.version[:2] == (2, 3)
    assert "/".join(on_disk["TPE1"].text) == "A/B"  # untouched (v2.3 stores multi values joined)
    assert str(on_disk["TXXX:Other"].text[0]) == "keep"
    assert str(on_disk["TXXX:REDACTOR_INTEGRITY"].text[0]) == stamp_before

    again = MP3File(path=path)
    load_tags(again)
    assert again.integrity_status == STATUS_WARNING
    assert again.integrity_stamp == stamp_before
    assert again.deep_check_status == STATUS_OK
    assert again.deep_check_scanned_at == mp3.deep_check_scanned_at
    assert again.dirty is False


def test_save_without_scan_does_not_touch_stamp_frames(tmp_path):
    mp3 = _loaded(tmp_path)
    mp3.apply_tags({"album": "X"})
    assert save_tags(mp3)
    tags = ID3(mp3.path)
    assert "TXXX:REDACTOR_INTEGRITY" not in tags and "TXXX:REDACTOR_DEEP_CHECK" not in tags


def test_scan_sets_stamp_and_dirty(tmp_path):
    mp3 = _loaded(tmp_path)
    with patch("core.scan_service.check_integrity", return_value=(STATUS_OK, "")):
        run_integrity_check([mp3])
    assert mp3.dirty and mp3.integrity_scanned_at.endswith("Z")
    assert parse_scan_stamp(mp3.integrity_stamp)[0] == STATUS_OK


def test_tool_missing_does_not_stamp(tmp_path):
    mp3 = _loaded(tmp_path)
    with patch("core.scan_service.check_integrity", return_value=(STATUS_TOOL_MISSING, "no mp3val")):
        run_integrity_check([mp3])
    assert mp3.integrity_status == STATUS_TOOL_MISSING
    assert mp3.integrity_stamp == "" and mp3.dirty is False


def test_crashed_deep_check_does_not_stamp(tmp_path):
    mp3 = _loaded(tmp_path)
    with patch("core.scan_service.deep_check_integrity", side_effect=RuntimeError("boom")), \
            patch("core.scan_service.probe_format", return_value=("", None, None, STATUS_ERROR, "")):
        run_deep_check([mp3], max_workers=1)
    assert mp3.deep_check_status == STATUS_TOOL_ERROR
    assert mp3.deep_check_stamp == "" and mp3.dirty is False


def test_tool_missing_keeps_an_older_stamp_and_does_not_delete_it(tmp_path):
    path = _copy(tmp_path)
    _put_frame(path, "REDACTOR_INTEGRITY", STAMP)
    mp3 = MP3File(path=path)
    load_tags(mp3)
    with patch("core.scan_service.check_integrity", return_value=(STATUS_TOOL_MISSING, "x")):
        run_integrity_check([mp3])
    assert mp3.integrity_status == STATUS_TOOL_MISSING
    assert scan_display(mp3.integrity_status, mp3.integrity_stamp) == STATUS_TOOL_MISSING
    mp3.apply_tags({"title": "T"})
    assert save_tags(mp3)
    assert str(ID3(path)["TXXX:REDACTOR_INTEGRITY"].text[0]) == STAMP


def test_fix_stamp_survives_reload_from_disk(tmp_path):
    path = _copy(tmp_path)
    _put_frame(path, "REDACTOR_INTEGRITY", STAMP)  # the OLD stamp, on disk
    mp3 = MP3File(path=path)
    load_tags(mp3)

    def fake_fix(p, **kwargs):
        t = ID3(p)
        t.add(TALB(encoding=3, text=["Fixed"]))
        t.save(p)
        return STATUS_WARNING, "left over"

    with patch("core.scan_service.fix_integrity", side_effect=fake_fix):
        run_integrity_fix([mp3])
    assert mp3.album == "Fixed"
    assert mp3.integrity_status == STATUS_WARNING
    assert mp3.integrity_stamp != STAMP and mp3.integrity_stamp.startswith("WARNING;")
    assert mp3.dirty
    assert save_tags(mp3)
    assert str(ID3(path)["TXXX:REDACTOR_INTEGRITY"].text[0]).startswith("WARNING;")


def test_reload_keeps_unsaved_scan_and_takes_changed_disk_stamp(tmp_path):
    from core.scan_service import reload_from_disk

    mp3 = _loaded(tmp_path)
    with patch("core.scan_service.check_integrity", return_value=(STATUS_ERROR, "e")):
        run_integrity_check([mp3])
    pending = mp3.integrity_stamp
    reload_from_disk(mp3)
    assert mp3.integrity_stamp == pending and mp3.integrity_status == STATUS_ERROR

    other = MP3File(path=_copy(tmp_path, "other.mp3"))  # unscanned
    load_tags(other)
    _put_frame(other.path, "REDACTOR_DEEP_CHECK", STAMP)
    reload_from_disk(other)
    assert other.deep_check_status == STATUS_OK and other.deep_check_stamp == STAMP
    assert other.dirty is False


def test_display_and_tooltip_formats():
    assert scan_display(STATUS_UNCHECKED, "") == "UNCHECKED"
    text = scan_display(STATUS_OK, STAMP)
    assert text.startswith("OK · 20") and len(text) == len("OK · 2026-09-30 14:05")
    # A status that differs from the stamp's (e.g. an unstamped ERROR) is bare.
    assert scan_display(STATUS_ERROR, STAMP) == "ERROR"
    tip = scan_tooltip(STATUS_OK, STAMP, "msg")
    assert tip.startswith("Last scanned 20") and tip.endswith("\nmsg")
    assert scan_tooltip(STATUS_OK, "", "") == ""


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


def test_gui_shows_stamp_and_save_writes_it(window, tmp_path):
    stamped = _copy(tmp_path, "a.mp3")
    _put_frame(stamped, "REDACTOR_INTEGRITY", STAMP)
    plain = _copy(tmp_path, "b.mp3")
    files = []
    for p in (stamped, plain):
        mp3 = MP3File(path=p)
        load_tags(mp3)
        files.append(mp3)
    window.files = files
    window._rebuild_table()
    col = window._col_index["integrity"]
    by_name = {window.table.item(r, 0).data(0x0100).filename: r for r in range(2)}
    assert window.table.item(by_name["a.mp3"], col).text().startswith("OK · ")
    assert window.table.item(by_name["b.mp3"], col).text() == "UNCHECKED"
    assert not any(m.dirty for m in files)

    with patch("core.scan_service.check_integrity", return_value=(STATUS_OK, "")):
        window.table.selectRow(by_name["b.mp3"])
        window.run_integrity_check()
    assert window.table.item(by_name["b.mp3"], col).text().startswith("OK · ")
    assert files[1].dirty
    window.save_changed()
    assert not files[1].dirty
    assert str(ID3(plain)["TXXX:REDACTOR_INTEGRITY"].text[0]).startswith("OK;")
