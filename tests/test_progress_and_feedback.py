"""
2026-10-13 tidy-up: slow work runs off the GUI thread under a progress
dialog (P1), routine results go to the status bar instead of a box (T1),
actions that did nothing say why (T2), and a path on the command line or
dropped on the window is opened (T3). Headless, with the real handlers.
"""

import os
import shutil
import threading
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtCore import QMimeData, QPointF, Qt, QTimer, QUrl  # noqa: E402
from PyQt6.QtGui import QDropEvent  # noqa: E402
from PyQt6.QtWidgets import QInputDialog, QMessageBox, QProgressDialog  # noqa: E402

import gui.main_window as mw  # noqa: E402
from core.mp3_file import STATUS_ERROR, STATUS_OK  # noqa: E402
from redactor_common.gui.work_progress import run_work_with_progress  # noqa: E402
from tests.test_cover_gui import FIXTURE, _app, _image_bytes, _load, _pump_until, _select_all, window  # noqa: E402,F401


@pytest.fixture
def no_info_boxes(monkeypatch):
    """Fails the test if a routine result still opens an information box."""
    shown = []
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: shown.append(a[1:3])))
    return shown


def _status(window) -> str:
    return window.statusBar().currentMessage()


# --- P1: the work runs on a worker thread, the window keeps painting ---------------------------------------------


def test_run_work_with_progress_runs_off_the_gui_thread_and_keeps_the_event_loop_going(window):
    ticks = []
    timer = QTimer()
    timer.timeout.connect(lambda: ticks.append(1))
    timer.start(10)
    seen = {}

    def work(progress):
        seen["thread"] = threading.current_thread()
        deadline = threading.Event()
        deadline.wait(0.4)  # a "long ffmpeg run"
        progress.on_progress(1)
        return "done"

    result, cancelled = run_work_with_progress(window, 1, "Working...", work)
    timer.stop()
    assert result == "done" and not cancelled
    assert seen["thread"] is not threading.main_thread()
    assert len(ticks) >= 5  # the GUI thread kept handling events while the work ran


def test_run_work_with_progress_cancel_reaches_the_work_and_reraises_errors(window):
    shown = []

    def press_cancel():
        for dialog in window.findChildren(QProgressDialog):
            if dialog.isVisible():
                dialog.canceled.emit()  # what the Cancel button sends
                shown.append(dialog.labelText())

    QTimer.singleShot(500, press_cancel)  # after the dialog has appeared (SHOW_AFTER_MS)

    def work(progress):
        for _ in range(500):
            if progress.should_cancel():
                return "stopped"
            threading.Event().wait(0.01)
        return "ran to the end"

    assert run_work_with_progress(window, 3, "Working...", work) == ("stopped", True)
    assert len(shown) == 1 and shown[0].startswith("Cancelling")

    def boom(_progress):
        raise ValueError("bad file")

    with pytest.raises(ValueError, match="bad file"):
        run_work_with_progress(window, 1, "Working...", boom)


def test_import_conversion_runs_on_a_worker_and_reports_in_the_status_bar(window, tmp_path, monkeypatch, no_info_boxes):
    src = tmp_path / "song.flac"
    src.write_bytes(b"flac")
    monkeypatch.setattr(QInputDialog, "getItem", staticmethod(lambda *a, **k: ("192 kbps", True)))
    threads = []

    def fake_convert(conversions, bitrate_kbps, progress, ffmpeg_path, should_cancel):
        threads.append(threading.current_thread())
        results = []
        for i, (source, dest) in enumerate(conversions, start=1):
            shutil.copyfile(FIXTURE, dest)
            results.append((source, dest, STATUS_OK, ""))
            progress(i, len(conversions))
        return results

    monkeypatch.setattr(mw, "run_import_conversion", fake_convert)
    window._convert_paths_and_load([str(src)])
    assert threads and threads[0] is not threading.main_thread()
    assert [mp3.filename for mp3 in window.files] == ["song.mp3"]
    assert no_info_boxes == [] and "Converted and loaded 1 file(s)" in _status(window)


def test_the_analyze_checks_run_on_a_worker_thread(window, tmp_path, monkeypatch):
    _load(window, tmp_path, ["a.mp3", "b.mp3"])
    _select_all(window)
    threads = []

    def fake_key_detection(files, progress, should_cancel, **kwargs):
        threads.append(("key", threading.current_thread()))
        for i, mp3 in enumerate(files, start=1):
            mp3.key_value, mp3.key_status = "Am", STATUS_OK
            progress(i, len(files))

    def fake_integrity(files, **kwargs):
        threads.append(("integrity", threading.current_thread()))
        for mp3 in files:
            mp3.integrity_status = STATUS_ERROR

    monkeypatch.setattr(mw, "run_key_detection", fake_key_detection)
    monkeypatch.setattr(mw, "run_integrity_check", fake_integrity)
    window.run_key_detection()
    window.run_integrity_check()
    assert [kind for kind, _t in threads] == ["key", "integrity", "integrity"]
    assert all(t is not threading.main_thread() for _k, t in threads)
    assert all(mp3.key_value == "Am" and mp3.integrity_status == STATUS_ERROR for mp3 in window.files)
    col = window._col_index["key"]
    assert window.table.item(0, col).text() == "Am"  # the table shows the results afterwards


def test_the_m4b_build_runs_on_a_worker_thread(window, tmp_path, monkeypatch, no_info_boxes):
    from core.m4b_builder import BuildResult

    files = _load(window, tmp_path, ["a.mp3"])
    threads = []

    class Spec:
        title = "Book"
        chapters = [object()]
        output = tmp_path / "Book.m4b"
        trash = None

    def fake_build(spec, ffmpeg_path, ffprobe_path, progress, should_cancel):
        threads.append(threading.current_thread())
        progress(1, 2)
        return BuildResult(STATUS_ERROR, "ffmpeg failed")

    warnings = []
    monkeypatch.setattr(mw, "build_m4b", fake_build)
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: warnings.append(a[2])))
    window._build_audiobooks([Spec()], skip_existing=False)
    assert threads and threads[0] is not threading.main_thread()
    assert warnings == ["ffmpeg failed"] and files


def test_a_cancelled_conversion_kills_ffmpeg_and_is_not_reported_as_a_failure(tmp_path, monkeypatch):
    import core.mp3_converter as conv
    from core.scan_service import run_import_conversion

    calls = []

    class FakeStoppable:
        def run(self, command, timeout, cancelled):
            calls.append(cancelled())
            Path(command[-1]).write_bytes(b"half")  # what ffmpeg had written when it was killed
            import subprocess

            return subprocess.CompletedProcess(command, 1, "", "killed")

    monkeypatch.setattr(conv, "StoppableProcesses", FakeStoppable)
    monkeypatch.setattr(conv, "find_tool", lambda *a, **k: Path("ffmpeg"))
    pairs = [(tmp_path / "a.flac", tmp_path / "a.mp3"), (tmp_path / "b.flac", tmp_path / "b.mp3")]
    results = run_import_conversion(pairs, should_cancel=lambda: True)
    assert results == [] and calls == [True]  # stopped in the first file, the second never started
    assert sorted(p.name for p in tmp_path.iterdir()) == []  # the half-written temp file is gone


# --- T1/T2: routine results and no-ops in the status bar ---------------------------------------------------------


def test_refresh_list_reports_in_the_status_bar(window, tmp_path, no_info_boxes):
    _load(window, tmp_path, ["a.mp3"])
    window.settings.include_subfolders = False
    shutil.copyfile(FIXTURE, tmp_path / "new.mp3")
    window.refresh_list()
    assert no_info_boxes == []
    assert sorted(mp3.filename for mp3 in window.files) == ["a.mp3", "new.mp3"]
    assert _status(window).startswith("Found 1 new file(s)")
    window.files = []
    window.refresh_list()
    assert _status(window) == "Nothing to refresh: no files are loaded."


def test_refresh_summary_wording():
    assert mw.refresh_summary(0, 2, 5, 5, True) == (
        "No new files; reloaded everything from disk. 2 file(s) no longer on disk left the list."
    )
    assert mw.refresh_summary(1, 0, 2, 7, False).startswith("Refresh cancelled: 2 of 7 file(s) reloaded")


def test_a_cancelled_load_says_how_much_was_loaded(window, tmp_path, monkeypatch):
    for name in ("a.mp3", "b.mp3", "c.mp3"):
        shutil.copyfile(FIXTURE, tmp_path / name)
    window.settings.include_subfolders = False

    def cancel_after_one(parent, items, step, label, **kwargs):
        step(list(items)[0], 0)
        return False

    monkeypatch.setattr(mw, "run_with_progress", cancel_after_one)
    window._load_paths([tmp_path])
    assert len(window.files) == 1
    assert _status(window) == "Loading cancelled: 1 of 3 file(s) loaded."


def test_rename_file_f2_says_why_it_did_nothing(window, tmp_path, monkeypatch):
    monkeypatch.setattr(window, "rename_single_file", lambda mp3: pytest.fail("renamed"))
    _load(window, tmp_path, ["a.mp3", "b.mp3"])
    window.table.clearSelection()
    window.rename_selected_file()
    assert _status(window) == "Rename File: select the file to rename first."
    _select_all(window)
    window.rename_selected_file()
    assert "2 are selected" in _status(window) and "Rename / Export / Move" in _status(window)


def test_cover_actions_say_why_nothing_happened(window, tmp_path, no_info_boxes):
    _load(window, tmp_path, ["album/a.mp3", "album/b.mp3"])
    _select_all(window)
    window.remove_cover()
    assert _status(window) == "Nothing to remove the cover from: none of the selected files has a cover."
    window.export_cover()
    assert _status(window) == "Nothing to export the cover of: none of the selected files has a cover."
    window.set_cover_from_folder_images()  # no cover.jpg next to them: a status line, not a box
    assert no_info_boxes == []
    assert _status(window) == "Set a cover on 0 of 2 file(s). No cover.jpg / folder.jpg / front.jpg next to 2 file(s)."
    (tmp_path / "album" / "cover.jpg").write_bytes(_image_bytes("JPEG"))
    window.set_cover_from_folder_images()
    assert no_info_boxes == [] and _status(window).startswith("Set a cover on 2 of 2 file(s).")


def test_a_lookup_with_nothing_to_apply_says_so(window):
    class EmptyDialog:
        DialogCode = QInputDialog.DialogCode

        def exec(self):
            return self.DialogCode.Accepted

        def file_changes(self):
            return []

        def cover_changes(self):
            return []

    window._apply_lookup_dialog(EmptyDialog(), "Discogs Lookup")
    assert _status(window).startswith("Discogs Lookup: nothing to apply")


# --- T3: a path on the command line, files dropped on the window -------------------------------------------------


def test_command_line_paths_split_existing_and_missing(tmp_path):
    (tmp_path / "a.mp3").write_bytes(b"x")
    found, missing = mw.command_line_paths([str(tmp_path), str(tmp_path / "a.mp3"), "-style", str(tmp_path / "nope")])
    assert found == [tmp_path, tmp_path / "a.mp3"] and missing == [str(tmp_path / "nope")]


def test_a_folder_on_the_command_line_is_opened_at_start(window, tmp_path):
    (tmp_path / "sub").mkdir()
    shutil.copyfile(FIXTURE, tmp_path / "a.mp3")
    shutil.copyfile(FIXTURE, tmp_path / "sub" / "b.mp3")
    window.settings.include_subfolders = False  # File > Include Subfolders is respected
    mw.open_paths_at_start(window, [str(tmp_path)])
    assert _pump_until(lambda: window.files)
    assert [mp3.filename for mp3 in window.files] == ["a.mp3"]


def test_the_entry_point_hands_the_command_line_to_the_window(monkeypatch, tmp_path):
    import main

    opened = []
    monkeypatch.setattr(mw, "MainWindow", lambda: "window")
    monkeypatch.setattr(mw, "open_paths_at_start", lambda window, args: opened.append((window, args)))
    monkeypatch.setattr(main.sys, "argv", ["mp3redactor.exe", str(tmp_path)])
    assert main._window() == "window" and opened == [("window", [str(tmp_path)])]


def test_files_and_folders_dropped_on_the_window_are_opened(window, tmp_path):
    (tmp_path / "album").mkdir()
    shutil.copyfile(FIXTURE, tmp_path / "album" / "a.mp3")
    shutil.copyfile(FIXTURE, tmp_path / "b.mp3")
    window.settings.include_subfolders = True
    assert window.acceptDrops()
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(tmp_path / "album")), QUrl.fromLocalFile(str(tmp_path / "b.mp3"))])
    event = QDropEvent(
        QPointF(10, 10), Qt.DropAction.CopyAction, mime, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
    )
    window.dropEvent(event)
    assert _pump_until(lambda: len(window.files) == 2)
    assert sorted(mp3.filename for mp3 in window.files) == ["a.mp3", "b.mp3"]


# --- T4: Redact's integrity step and the results header about .bak files -----------------------------------------


def test_integrity_fix_is_off_by_default_and_the_texts_explain_the_bak_files():
    from core.redact_steps import IntegrityStep

    step = IntegrityStep()
    fix = next(o for o in step.options if o.key == "fix")
    assert fix.default is False
    assert "off by default" in step.description and "<name>.mp3.bak" in step.description
    assert "by hand" in step.description
    note = mw.REDACT_UNDO_NOTE
    assert "off by" in note and "<name>.mp3.bak" in note and ".redact-orig.mp3" in note and "delete them" in note
