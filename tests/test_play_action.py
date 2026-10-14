"""File > Play (Ctrl+P), the toolbar button: one selected file opens redactor_common's read-only player window; anything else is a
status message. show_media_preview is replaced by a stub -- no QMediaPlayer is ever created in tests (it can crash
the interpreter at exit)."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QKeySequence  # noqa: E402

import redactor_common.gui.media_preview as media_preview  # noqa: E402
from tests.test_cover_gui import _load, window  # noqa: E402,F401


def _capture(monkeypatch):
    calls = []
    monkeypatch.setattr(media_preview, "show_media_preview", lambda parent, path, video=False, **kw: calls.append((path, video)))
    return calls


def test_play_action_is_ctrl_p_and_not_ctrl_alt(window):
    act = window.action_registry["play"]
    assert act.shortcuts() == [QKeySequence("Ctrl+P")]
    assert act.text().replace("&", "") == "Play"


def test_one_selected_file_opens_audio_player(window, tmp_path, monkeypatch):
    files = _load(window, tmp_path, ["a.mp3", "b.mp3"])
    calls = _capture(monkeypatch)
    window.table.selectRow(0)
    window.action_registry["play"].trigger()
    chosen = window._selected_files()[0]
    assert chosen in files
    assert [(str(p), v) for p, v in calls] == [(str(chosen.path), False)]


def test_no_or_several_files_selected_gives_a_message(window, tmp_path, monkeypatch):
    _load(window, tmp_path, ["a.mp3", "b.mp3"])
    calls = _capture(monkeypatch)
    window.table.clearSelection()
    window.play_selected_file()
    assert "Select a file to play" in window.statusBar().currentMessage()
    window.table.selectAll()
    window.play_selected_file()
    assert "exactly one" in window.statusBar().currentMessage()
    assert calls == []


def test_toolbar_has_a_play_button(window, tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QToolBar

    bar = window.findChild(QToolBar)
    assert window.action_play in bar.actions()
    _load(window, tmp_path, ["a.mp3"])
    calls = _capture(monkeypatch)
    window.table.selectRow(0)
    bar.widgetForAction(window.action_play).click()
    assert len(calls) == 1 and isinstance(calls[0][0], str)
