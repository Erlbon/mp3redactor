"""Save All reports its result after EVERY run (status line with counts or the reason nothing was saved, a box listing
each problem file, an unexpected error in the "Something went wrong" box), never stops at the first bad file, and
names a file left marked unsaved without an error. Real MainWindow offscreen."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtWidgets import QMessageBox  # noqa: E402
from redactor_common.gui import crash_dialog  # noqa: E402

import core.scan_service as scan_service  # noqa: E402
import gui.main_window as mw  # noqa: E402
from core.mp3_file import MP3File  # noqa: E402
from core.tag_reader import load_tags  # noqa: E402
from tests.test_cover_gui import _app, _load, window  # noqa: E402,F401


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(mw, "crash_log_path", lambda: tmp_path / "logs" / "mp3redactor_crash.log")
    monkeypatch.setattr(crash_dialog, "_reporter", None)
    monkeypatch.setattr(mw, "history_enabled", lambda: False)


@pytest.fixture
def reporter(tmp_path):
    rep = crash_dialog.install(tmp_path / "logs" / "mp3redactor_crash.log")
    yield rep
    if rep.dialog is not None:
        rep.dialog.close()
    _app.processEvents()


@pytest.fixture
def boxes(monkeypatch):
    seen = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda parent, title, text, *a: seen.append((title, text))))
    return seen


@pytest.fixture
def three(window, tmp_path):
    return _load(window, tmp_path, ["a.mp3", "b.mp3", "c.mp3"])


def _dirty(mp3, title):
    mp3.title = title
    mp3.dirty = True


def _title_on_disk(mp3):
    fresh = MP3File(path=mp3.path)
    load_tags(fresh)
    return fresh.title


def test_all_clean_says_why_nothing_was_saved(window, three):
    window.save_changed()
    assert window.statusBar().currentMessage() == "Nothing to save: no file has unsaved changes"


def test_all_changed_files_with_load_errors_name_them(window, three):
    for mp3 in three[:2]:
        mp3.dirty = True
        mp3.load_error = "broken"
    window.save_changed()
    message = window.statusBar().currentMessage()
    assert message.startswith("Nothing to save: all 2 changed file(s) have load errors")
    assert "a.mp3" in message and "b.mp3" in message


def test_a_failing_file_is_listed_in_the_box_and_the_others_are_saved(window, three, boxes, monkeypatch):
    for i, mp3 in enumerate(three):
        _dirty(mp3, f"New {i}")
    real = scan_service.save_tags

    def flaky(mp3, *a, **k):
        if mp3.filename == "b.mp3":
            mp3.save_error = "disk says no"
            return
        return real(mp3, *a, **k)

    monkeypatch.setattr(scan_service, "save_tags", flaky)
    window.save_changed()
    assert window.statusBar().currentMessage() == "Saved 2 file(s), 1 failed"
    assert len(boxes) == 1 and "b.mp3: disk says no" in boxes[0][1]
    assert _title_on_disk(three[0]) == "New 0" and _title_on_disk(three[2]) == "New 2"


def test_an_unexpected_exception_in_the_middle_shows_the_box_and_the_rest_is_saved(
        window, three, boxes, reporter, monkeypatch):
    for i, mp3 in enumerate(three):
        _dirty(mp3, f"New {i}")
    real = scan_service.save_tags

    def explode(mp3, *a, **k):
        if mp3.filename == "b.mp3":
            raise ValueError("kaboom")
        return real(mp3, *a, **k)

    monkeypatch.setattr(scan_service, "save_tags", explode)
    window.save_changed()
    _app.processEvents()
    assert reporter.dialog is not None and "kaboom" in reporter.dialog.details_text()
    assert _title_on_disk(three[0]) == "New 0" and _title_on_disk(three[2]) == "New 2"  # the loop went on
    assert window.statusBar().currentMessage() == "Saved 2 file(s), 1 failed"
    assert "b.mp3: unexpected error: kaboom" in boxes[0][1]


def test_an_exception_outside_the_per_file_loop_is_reported_and_shown_in_the_status_bar(
        window, three, reporter, monkeypatch):
    _dirty(three[0], "x")

    def boom(*a, **k):
        raise RuntimeError("no way")

    monkeypatch.setattr(window, "_save_dirty_files", boom)
    window.save_changed()
    _app.processEvents()
    assert reporter.dialog is not None
    assert reporter.dialog.summary_label.text().startswith("Save All stopped because of an unexpected error")
    assert "no way" in window.statusBar().currentMessage()


def test_the_invariant_names_a_file_left_unsaved_without_an_error(window, three, boxes, monkeypatch):
    _dirty(three[0], "x")
    monkeypatch.setattr(scan_service, "save_tags", lambda mp3, *a, **k: None)  # "saves" but leaves dirty, no error
    window.save_changed()
    assert "still marked unsaved" in window.statusBar().currentMessage()
    assert boxes and "1 file(s) are still marked unsaved after saving: a.mp3" in boxes[0][1]


def test_the_save_log_is_opt_in_and_has_no_file_names(window, three, tmp_path, monkeypatch):
    log = tmp_path / "logs" / "save_log.txt"
    _dirty(three[0], "x")
    window.save_changed()
    assert not log.exists()  # data logging is off by default
    monkeypatch.setattr(mw, "history_enabled", lambda: True)
    _dirty(three[1], "y")
    window.save_changed()
    text = log.read_text(encoding="utf-8")
    assert text.count("\n") == 1 and "saved=1" in text and "b.mp3" not in text
