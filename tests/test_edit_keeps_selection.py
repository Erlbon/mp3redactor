"""
Edits keep the selection on the same FILES (2026-09-29).

_rebuild_table() refills rows in self.files order and then re-sorts, so
a selection kept by row number landed on a different file: with the
default descending sort, Apply on a.mp3 left c.mp3 selected and the
next Apply silently edited c.mp3. Also covers the family's stale-panel
check (cbzredactor 2026-09-29#05): an edit made outside the panel must
survive clicking another file.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import gui.main_window as mw  # noqa: E402
from tests.test_cover_gui import _app, _load, window  # noqa: E402,F401


def _select(window, mp3):
    col = window._col_index["filename"]
    for row in range(window.table.rowCount()):
        if window.table.item(row, col).data(mw.Qt.ItemDataRole.UserRole) is mp3:
            window.table.selectRow(row)
            _app.processEvents()
            return
    raise AssertionError(f"{mp3.filename} not in table")


def _apply(window, key, value):
    panel = window.tag_panel
    panel._editors[key].setText(value)
    panel._checkboxes[key].setChecked(True)
    panel.apply_bulk_edit()
    _app.processEvents()


def test_a_second_apply_edits_the_same_file(window, tmp_path):
    a, b, c = _load(window, tmp_path, ["a.mp3", "b.mp3", "c.mp3"])
    _select(window, a)
    _apply(window, "album", "First")
    assert window._selected_files() == [a]
    assert window.tag_panel._editors["album"].text() == "First"
    _apply(window, "genre", "Jazz")
    assert (a.genre, b.genre, c.genre) == ("Jazz", "", "")


def test_a_multi_selection_survives_an_edit(window, tmp_path):
    a, b, c = _load(window, tmp_path, ["a.mp3", "b.mp3", "c.mp3"])
    _select(window, a)
    col = window._col_index["filename"]
    for row in range(window.table.rowCount()):
        if window.table.item(row, col).data(mw.Qt.ItemDataRole.UserRole) is b:
            window.table.selectionModel().select(
                window.table.model().index(row, 0),
                mw.QItemSelectionModel.SelectionFlag.Select | mw.QItemSelectionModel.SelectionFlag.Rows,
            )
    _app.processEvents()
    _apply(window, "album", "Shared")
    assert {id(f) for f in window._selected_files()} == {id(a), id(b)}
    assert (a.album, b.album, c.album) == ("Shared", "Shared", "")


def test_an_edit_to_the_selected_file_survives_clicking_another(window, tmp_path, monkeypatch):
    a, b = _load(window, tmp_path, ["a.mp3", "b.mp3"])
    _select(window, a)
    dialog = mw.AutoNumberingDialog
    monkeypatch.setattr(dialog, "exec", lambda d: dialog.DialogCode.Accepted)
    monkeypatch.setattr(dialog, "result_field_key", lambda d: "album")
    monkeypatch.setattr(dialog, "accepted_changes", lambda d: {0: "New Name"})
    window.open_auto_numbering_dialog()
    assert a.album == "New Name"
    assert window.tag_panel._editors["album"].text() == "New Name"
    _select(window, b)
    assert a.album == "New Name"
