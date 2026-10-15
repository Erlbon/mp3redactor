"""The Include Subfolders checkbox on the toolbar (right after Open Folder) mirrors File > Include Subfolders both
ways, there is still one saved setting (settings.include_subfolders), and it really changes what Open Folder finds."""

import os
import shutil
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QCheckBox, QToolBar  # noqa: E402

from tests.test_cover_gui import window  # noqa: E402,F401


def _toolbar(window):
    return window.findChildren(QToolBar)[0]


def test_checkbox_sits_right_after_open_folder(window):
    tb = _toolbar(window)
    assert window.include_subfolders_checkbox.text() == "Include Subfolders"
    actions = tb.actions()
    folder_idx = actions.index(window.action_load_folder)
    box_action = actions[folder_idx + 1]
    assert tb.widgetForAction(box_action) is window.include_subfolders_checkbox
    assert actions[folder_idx + 2] is window.action_play


def test_checkbox_mirrors_setting_and_menu_action(window):
    box = window.include_subfolders_checkbox
    act = window.action_include_subfolders
    assert box.isChecked() == window.settings.include_subfolders == act.isChecked()
    start = box.isChecked()
    box.setChecked(not start)
    assert act.isChecked() == (not start)
    assert window.settings.include_subfolders == (not start)
    act.setChecked(start)
    assert box.isChecked() == start
    assert window.settings.include_subfolders == start
    assert len([c for c in _toolbar(window).findChildren(QCheckBox)]) == 1  # one checkbox on the toolbar


def test_checkbox_changes_what_open_folder_finds(window, tmp_path, monkeypatch):
    import gui.main_window as mw

    monkeypatch.setattr(mw, "find_convertible", lambda *a, **k: [])
    sub = tmp_path / "root" / "sub"
    sub.mkdir(parents=True)
    song = Path(r"C:\Temp\Claude\exebuild\samples\Song.mp3")
    if song.exists():
        shutil.copyfile(song, sub / "Song.mp3")
    else:  # fall back to a stand-in; only the file listing is under test
        (sub / "Song.mp3").write_bytes(b"\xff\xfb\x90\x00" * 64)
    window.include_subfolders_checkbox.setChecked(False)
    window._load_paths([tmp_path / "root"])
    assert len(window.files) == 0
    window.include_subfolders_checkbox.setChecked(True)
    window._load_paths([tmp_path / "root"])
    assert len(window.files) == 1
