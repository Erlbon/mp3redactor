"""Parse Filename > Metadata with a folder-path pattern, in the main window:
the real ParseFilenameDialog (driven from exec()) gets the library root the
Move into folders mode persists, and its accepted changes land on MP3File."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QFileDialog  # noqa: E402

import gui.main_window as mw  # noqa: E402
from tests.test_cover_gui import _app, _load, _select_all, window  # noqa: E402,F401


def _drive_dialog(monkeypatch, pattern, pick_root=None, seen=None):
    class PathDialog(mw.ParseFilenameDialog):
        def exec(self):
            if seen is not None:
                seen.update(root=self.library_root(), history=self.pattern_edit.text())
            if pick_root is not None:
                monkeypatch.setattr(QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: str(pick_root)))
                self._choose_root()
            self.pattern_edit.setText(pattern)
            self._refresh_preview()
            return self.DialogCode.Accepted

    monkeypatch.setattr(mw, "ParseFilenameDialog", PathDialog)


def _library_files(window, tmp_path):
    root = tmp_path / "library"
    folder = root / "Queen" / "Jazz"
    folder.mkdir(parents=True)
    files = _load(window, folder, ["03 - Fat Bottomed Girls.mp3", "04 - Dreamer.mp3"])
    return root, files


def test_path_pattern_fills_fields_from_the_folders_using_the_saved_root(window, tmp_path, monkeypatch):
    root, (a, b) = _library_files(window, tmp_path)
    window.settings.library_root = str(root)
    seen = {}
    monkeypatch.setattr(mw, "save_settings", lambda *a_, **k: None)
    _drive_dialog(monkeypatch, "%albumartist%/%album%/%track% - %title%", seen=seen)
    _select_all(window)

    window.open_parse_filename_dialog()

    assert seen["root"] == str(root)
    assert (a.albumartist, a.album, a.track, a.title) == ("Queen", "Jazz", "3", "Fat Bottomed Girls")
    assert (b.albumartist, b.album, b.track, b.title) == ("Queen", "Jazz", "4", "Dreamer")
    assert a.dirty and b.dirty
    assert "%albumartist%/%album%/%track% - %title%" in window.settings.pattern_history


def test_choosing_the_root_in_the_dialog_is_persisted_for_move_and_redact(window, tmp_path, monkeypatch):
    root, (a, _b) = _library_files(window, tmp_path)
    saved = []
    monkeypatch.setattr(mw, "save_settings", lambda s, *a_, **k: saved.append(s.library_root))
    _drive_dialog(monkeypatch, "%albumartist%/%album%/%track% - %title%", pick_root=root)
    _select_all(window)

    window.open_parse_filename_dialog()

    assert window.settings.library_root == str(root) and str(root) in saved
    assert a.album == "Jazz"


def test_a_path_pattern_in_the_history_does_not_become_the_filename_starting_pattern(window, tmp_path, monkeypatch):
    _library_files(window, tmp_path)
    window.settings.pattern_history = ["%genre%/%title%", "%track% - %title%"]
    seen = {}
    monkeypatch.setattr(mw, "save_settings", lambda *a_, **k: None)

    class Probe(mw.ParseFilenameDialog):
        def exec(self):
            seen["start"] = self.pattern_edit.text()
            return self.DialogCode.Rejected

    monkeypatch.setattr(mw, "ParseFilenameDialog", Probe)
    _select_all(window)
    window.open_parse_filename_dialog()
    assert seen["start"] == "%track% - %title%"


def test_parse_from_title_fills_artist_and_trims_title_and_remembers_the_source(window, tmp_path, monkeypatch):
    a, b = _load(window, tmp_path, ["01.mp3", "02.mp3"])
    a.apply_tags({"title": "Johnny Cash - Big River"})
    b.apply_tags({"title": "Untitled"})  # no " - ": left alone
    for mp3 in (a, b):
        mp3.dirty = False
    saved = []
    monkeypatch.setattr(mw, "save_settings", lambda s, *a_, **k: saved.append(s.parse_source))

    class FromTitle(mw.ParseFilenameDialog):
        def exec(self):
            self.source_combo.setCurrentIndex(self.source_combo.findData("title"))
            self.pattern_edit.setText("%artist% - %title%")
            return self.DialogCode.Accepted

    monkeypatch.setattr(mw, "ParseFilenameDialog", FromTitle)
    _select_all(window)
    window.open_parse_filename_dialog()

    assert (a.artist, a.title) == ("Johnny Cash", "Big River") and a.dirty
    assert b.title == "Untitled" and not b.dirty
    assert window.settings.parse_source == "title" and "title" in saved
    assert "Parse Field" in window.action_undo.text()

    seen = {}

    class Probe(mw.ParseFilenameDialog):
        def exec(self):
            seen["source"] = self.source_key()
            return self.DialogCode.Rejected

    monkeypatch.setattr(mw, "ParseFilenameDialog", Probe)
    window.open_parse_filename_dialog()
    assert seen["source"] == "title"  # opens on the field used last time
