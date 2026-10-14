"""File > Preview (Ctrl+P): one selected file opens redactor_common's read-only player window; anything else is a
status message. show_media_preview is replaced by a stub -- no QMediaPlayer is ever created in tests (it can crash
the interpreter at exit)."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QKeySequence  # noqa: E402

import redactor_common.gui.media_preview as media_preview  # noqa: E402
from tests.test_cover_gui import _load, window  # noqa: E402,F401


def _capture(monkeypatch):
    calls = []
    monkeypatch.setattr(media_preview, "show_media_preview", lambda parent, path, video=False: calls.append((path, video)))
    return calls


def test_preview_action_is_ctrl_p_and_not_ctrl_alt(window):
    act = window.action_registry["preview"]
    assert act.shortcuts() == [QKeySequence("Ctrl+P")]
    assert act.text().replace("&", "") == "Preview…"


def test_one_selected_file_opens_audio_preview(window, tmp_path, monkeypatch):
    files = _load(window, tmp_path, ["a.mp3", "b.mp3"])
    calls = _capture(monkeypatch)
    window.table.selectRow(0)
    window.action_registry["preview"].trigger()
    chosen = window._selected_files()[0]
    assert chosen in files
    assert [(str(p), v) for p, v in calls] == [(str(chosen.path), False)]


def test_no_or_several_files_selected_gives_a_message(window, tmp_path, monkeypatch):
    _load(window, tmp_path, ["a.mp3", "b.mp3"])
    calls = _capture(monkeypatch)
    window.table.clearSelection()
    window.preview_selected_file()
    assert "Select a file" in window.statusBar().currentMessage()
    window.table.selectAll()
    window.preview_selected_file()
    assert "exactly one" in window.statusBar().currentMessage()
    assert calls == []
