"""GUI edit safety (2026-09-30 review): files that failed to load are
never edited/saved, and Undo doesn't restore a stale path or claim a
saved state."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import gui.main_window as mw  # noqa: E402
from core.tag_reader import load_tags  # noqa: E402
from core.tag_writer import save_tags  # noqa: E402
from PyQt6.QtWidgets import QLabel  # noqa: E402
from tests.test_cover_gui import _app, _load, window  # noqa: E402,F401
from tests.test_edit_keeps_selection import _apply, _select  # noqa: E402


def test_bulk_edit_skips_a_file_that_failed_to_load(window, tmp_path):
    a, b = _load(window, tmp_path, ["a.mp3", "b.mp3"])
    b.load_error = "failed to read tags: boom"
    window.table.selectAll()
    window._apply_bulk_edit({"title": "New"})
    assert a.title == "New" and a.dirty
    assert b.title == "" and not b.dirty


def test_save_changed_does_not_write_a_load_error_file(window, tmp_path):
    (a,) = _load(window, tmp_path, ["a.mp3"])
    before = a.path.read_bytes()
    a.load_error = "failed to read tags: boom"
    a.dirty = True
    window.save_changed()
    assert a.path.read_bytes() == before
    a.dirty = False


def test_undo_after_rename_keeps_the_new_path(window, tmp_path):
    (a,) = _load(window, tmp_path, ["a.mp3"])
    _select(window, a)
    _apply(window, "album", "X")
    new_path = a.path.with_name("renamed.mp3")
    os.rename(a.path, new_path)
    a.path = new_path
    window.undo_last_action()
    assert a.path == new_path and new_path.exists()
    assert a.album == ""
    a.dirty = False


def test_undo_after_save_marks_the_file_dirty_and_saves_the_old_value(window, tmp_path):
    (a,) = _load(window, tmp_path, ["a.mp3"])
    _select(window, a)
    _apply(window, "album", "X")
    assert save_tags(a) and not a.dirty
    window.undo_last_action()
    assert a.album == "" and a.dirty
    assert save_tags(a)
    fresh = type(a)(path=a.path)
    load_tags(fresh)
    assert fresh.album == ""


def test_scan_with_progress_returns_the_scan_result(window):
    assert window._scan_with_progress("Scanning...", lambda: [1, 2]) == [1, 2]


def test_load_paths_scans_under_progress(window, tmp_path, monkeypatch):
    import shutil

    from tests.test_cover_gui import FIXTURE

    shutil.copyfile(FIXTURE, tmp_path / "a.mp3")
    labels = []
    real = window._scan_with_progress
    monkeypatch.setattr(window, "_scan_with_progress", lambda label, scan: (labels.append(label), real(label, scan))[1])
    window._load_paths([tmp_path])
    assert labels == ["Scanning for MP3 files..."]
    assert [f.filename for f in window.files] == ["a.mp3"]


def test_export_copy_runs_under_progress_and_reports_errors(window, tmp_path, monkeypatch):
    (a,) = _load(window, tmp_path, ["a.mp3"])
    _select(window, a)
    out = tmp_path / "copy.mp3"

    class FakeDialog:
        DialogCode = mw.QDialog.DialogCode

        def __init__(self, *args, **kwargs):
            self.pattern_edit = type("E", (), {"text": lambda self: "x"})()

        def exec(self):
            return self.DialogCode.Accepted

        def is_export_mode(self):
            return True

        def planned_renames(self):
            return [(a, str(a.path), str(out))]

    calls = []
    real = mw.run_with_progress
    monkeypatch.setattr(mw, "RenamePatternDialog", FakeDialog)
    monkeypatch.setattr(mw, "run_with_progress", lambda *args, **kw: (calls.append(args[3]), real(*args, **kw))[1])
    window.open_rename_dialog()
    assert calls == ["Copying files..."]
    assert out.exists() and a.path.exists()


def test_lyrics_dialog_header_shows_raw_tag_text_as_plain_text(window, tmp_path):
    from gui.lyrics_dialog import LyricsDialog

    (a,) = _load(window, tmp_path, ["a.mp3"])
    a.artist = "<b>AC</b>/DC"
    dialog = LyricsDialog(a, window)
    header = dialog.findChildren(QLabel)[0]
    assert header.textFormat() == mw.Qt.TextFormat.PlainText
    assert "<b>AC</b>/DC" in header.text()
