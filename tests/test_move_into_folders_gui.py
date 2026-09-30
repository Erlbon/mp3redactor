"""Rename / Export Files' third mode, "Move into folders", in the main
window: the real RenamePatternDialog (driven from exec()) and the shared
move runner, recording into the app's rename log so Undo Last Rename works."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QFileDialog, QMessageBox  # noqa: E402

import gui.main_window as mw  # noqa: E402
from tests.test_cover_gui import _app, _load, _select_all, window  # noqa: E402,F401


def _drive_dialog(monkeypatch, root, pattern):
    """The real dialog, switched to move mode with `pattern`; its root is
    picked through the real folder button when none is set yet."""

    class MoveDialog(mw.RenamePatternDialog):
        def exec(self):
            if not self.library_root():
                monkeypatch.setattr(QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: str(root)))
                self._choose_root()
            self.move_radio.setChecked(True)
            self.pattern_edit.setText(pattern)
            self._refresh_preview()
            return self.DialogCode.Accepted

    monkeypatch.setattr(mw, "RenamePatternDialog", MoveDialog)


def test_move_mode_moves_files_remembers_root_and_pattern_and_undo_restores(window, tmp_path, monkeypatch):
    a, b = _load(window, tmp_path, ["a.mp3", "b.mp3"])
    a.apply_tags({"artist": "Band", "album": "One", "title": "First"})
    b.apply_tags({"artist": "Band", "album": "Two", "title": "Second"})
    old_paths = [a.path, b.path]
    root = tmp_path / "library"
    root.mkdir()
    saved = []
    monkeypatch.setattr(mw, "save_settings", lambda s, *a_, **k: saved.append((s.library_root, s.move_pattern)))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a_, **k: QMessageBox.StandardButton.Yes))
    _drive_dialog(monkeypatch, root, "%artist%/%album%/%title%")
    _select_all(window)

    window.open_rename_dialog()

    assert a.path == root / "Band" / "One" / "First.mp3" and a.path.exists()
    assert b.path == root / "Band" / "Two" / "Second.mp3" and b.path.exists()
    assert not any(p.exists() for p in old_paths)
    assert window.settings.library_root == str(root)
    assert window.settings.move_pattern == "%artist%/%album%/%title%"
    assert window.settings.rename_pattern == ""  # the rename pattern is not overwritten by a folder pattern
    assert saved[-1] == (str(root), "%artist%/%album%/%title%")
    col = window._col_index["filename"]
    shown = {window.table.item(r, col).text() for r in range(window.table.rowCount())}
    assert shown == {"First.mp3", "Second.mp3"}  # the rows took on their new paths
    assert not window.undo_manager.can_undo()  # moves are file operations, not on the tag Undo stack

    window.undo_last_rename()  # one batch covers both files; the created folders are tidied away

    assert [a.path, b.path] == old_paths and all(p.exists() for p in old_paths)
    assert list(root.iterdir()) == []


def test_rename_and_export_modes_still_remember_the_rename_pattern(window, tmp_path, monkeypatch):
    (a,) = _load(window, tmp_path, ["a.mp3"])
    a.apply_tags({"title": "Song"})
    monkeypatch.setattr(mw, "save_settings", lambda *a_, **k: None)

    class RenameDialog(mw.RenamePatternDialog):
        def exec(self):
            self.pattern_edit.setText("%title%")
            return self.DialogCode.Accepted

    monkeypatch.setattr(mw, "RenamePatternDialog", RenameDialog)
    _select_all(window)
    window.open_rename_dialog()
    assert a.path.name == "Song.mp3" and window.settings.rename_pattern == "%title%"
    assert window.settings.move_pattern == "" and window.settings.library_root == ""
