"""The shared Path column (redactor_common.core.path_column) in the real MainWindow: directly after Filename, visible
by default, shows the folder, full path in the tooltip and for Ctrl+C, sorts as text, is migrated once for old saved
column settings, and follows a rename and a move into folders."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtWidgets import QMessageBox  # noqa: E402
from redactor_common.core.path_column import lint_path_column  # noqa: E402
from redactor_common.gui.path_column import selected_column_text  # noqa: E402

import gui.main_window as mw  # noqa: E402
from core.settings import Settings  # noqa: E402
from tests.test_cover_gui import _app, _load, _select_all, window  # noqa: E402,F401
from tests.test_move_into_folders_gui import _drive_dialog  # noqa: E402


def _col(w, key="path"):
    return w._column_keys.index(key)


def _row_of(w, mp3):
    return next(r for r in range(w.table.rowCount()) if w.table.item(r, 0).data(Qt.ItemDataRole.UserRole) is mp3)


def test_path_is_directly_after_filename_and_visible_by_default(window):
    assert lint_path_column(mw.ALL_COLUMN_KEYS) == []
    assert lint_path_column(window._column_keys) == []
    assert "path" not in mw.DEFAULT_HIDDEN_COLUMNS
    assert window.table.horizontalHeaderItem(_col(window)).text() == "Path"


def test_cell_shows_the_folder_and_the_tooltip_the_full_path(window, tmp_path):
    (mp3,) = _load(window, tmp_path, ["Album/a.mp3"])
    item = window.table.item(_row_of(window, mp3), _col(window))
    assert item.text() == str(tmp_path / "Album")
    assert item.toolTip() == str(tmp_path / "Album" / "a.mp3")
    assert item.data(Qt.ItemDataRole.UserRole) is mp3  # the row -> file mapping still works from this cell


def test_ctrl_c_text_is_the_full_path(window, tmp_path):
    # The key handling itself (real Ctrl+C, real clipboard) is tested in redactor_common.
    (mp3,) = _load(window, tmp_path, ["Album/a.mp3"])
    assert getattr(window.table, "_cell_copy_filter", None) is not None
    window.table.selectRow(0)
    window.table.setCurrentCell(0, _col(window))
    assert selected_column_text(window.table) == str(mp3.path)


def test_sorting_the_path_column_is_text_order(window, tmp_path):
    _load(window, tmp_path, ["Vol 10/a.mp3", "vol 2/b.mp3", "Vol 1/c.mp3"])
    window.table.sortItems(_col(window), Qt.SortOrder.AscendingOrder)
    shown = [os.path.basename(window.table.item(r, _col(window)).text()) for r in range(window.table.rowCount())]
    assert shown == ["Vol 1", "vol 2", "Vol 10"]


def _window_with_settings(monkeypatch, settings):
    monkeypatch.setattr(mw, "load_settings", lambda *a, **k: settings)
    saved = []
    monkeypatch.setattr(mw, "save_settings", lambda s, *a, **k: saved.append(s.path_column_seeded))
    return mw.MainWindow(), saved


def test_old_saved_order_and_hidden_set_are_migrated_once(monkeypatch):
    old_order = ["filename", "title", "artist", "path"] + [k for k in mw.ALL_COLUMN_KEYS if k not in (
        "filename", "title", "artist", "path")]
    settings = Settings(column_order=old_order, hidden_columns=["path", "encoder"], has_column_preference=True)
    w, saved = _window_with_settings(monkeypatch, settings)
    try:
        assert _col(w) == _col(w, "filename") + 1
        assert not w.table.isColumnHidden(_col(w))
        assert w.table.isColumnHidden(_col(w, "encoder"))  # the other choices stay
        assert settings.path_column_seeded and saved == [True]
        # the user hides it again: a second start (flag set) leaves that choice alone
        settings.hidden_columns = ["path"]
    finally:
        w.close()
    w2, saved2 = _window_with_settings(monkeypatch, settings)
    try:
        assert w2.table.isColumnHidden(_col(w2)) and saved2 == []
    finally:
        w2.close()


def test_the_flag_is_saved_and_loaded(tmp_path):
    from core.settings import load_settings, save_settings

    save_settings(Settings(path_column_seeded=True), tmp_path)
    assert load_settings(tmp_path).path_column_seeded is True
    assert Settings().path_column_seeded is False


def test_a_path_cell_follows_a_rename(window, tmp_path, monkeypatch):
    (mp3,) = _load(window, tmp_path, ["a.mp3"])
    new_dir = tmp_path / "renamed"
    new_dir.mkdir()

    def fake_prompt(parent, path, setter, log=None):
        setter(str(new_dir / "z.mp3"))
        return True

    monkeypatch.setattr(mw, "prompt_rename_single_file", fake_prompt)
    window.rename_single_file(mp3)
    item = window.table.item(_row_of(window, mp3), _col(window))
    assert item.text() == str(new_dir) and item.toolTip() == str(new_dir / "z.mp3")


def test_a_path_cell_follows_a_move_into_folders(window, tmp_path, monkeypatch):
    (a,) = _load(window, tmp_path, ["a.mp3"])
    a.apply_tags({"artist": "Band", "album": "One", "title": "First"})
    root = tmp_path / "library"
    root.mkdir()
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a_, **k: QMessageBox.StandardButton.No))
    _drive_dialog(monkeypatch, root, "%artist%/%album%/%title%")
    _select_all(window)
    window.open_rename_dialog()
    item = window.table.item(_row_of(window, a), _col(window))
    assert item.text() == str(root / "Band" / "One")
    assert item.toolTip() == str(root / "Band" / "One" / "First.mp3")
