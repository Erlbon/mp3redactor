"""
Typed-but-unapplied edits in the bulk-edit panel are never lost when the
table selection changes (2026-11-04, as epubredactor 2026-11-04#01/#02):
they are applied to the PREVIOUS selection through the normal Apply path
(one undo entry, a status-bar message), then the new selection is loaded.
Refresh List, Clear List, Open Folder and quitting apply them first, so
they count in the "unsaved changes" question. Real MainWindow, offscreen.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtWidgets import QMessageBox  # noqa: E402

import gui.main_window as mw  # noqa: E402
from tests.test_cover_gui import _app, _load, window  # noqa: E402,F401


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))


def _select(window, *files):
    """Selects exactly these files the way a click does (itemSelectionChanged fires)."""
    wanted = {id(mp3) for mp3 in files}
    col = window._col_index["filename"]
    model = window.table.selectionModel()
    model.clearSelection()
    for row in range(window.table.rowCount()):
        if id(window.table.item(row, col).data(mw.Qt.ItemDataRole.UserRole)) in wanted:
            model.select(
                window.table.model().index(row, 0),
                mw.QItemSelectionModel.SelectionFlag.Select | mw.QItemSelectionModel.SelectionFlag.Rows,
            )
    _app.processEvents()
    assert {id(f) for f in window._selected_files()} == wanted


def _type(window, key, text):
    editor = window.tag_panel._editors[key]
    editor.setText(text)
    editor.textEdited.emit(text)
    assert window.tag_panel._checkboxes[key].isChecked()


def test_selection_change_applies_pending_edit_to_the_previous_selection_with_one_undo(window, tmp_path):
    a, b, c = _load(window, tmp_path, ["a.mp3", "b.mp3", "c.mp3"])
    _select(window, a, b)
    _type(window, "album", "Pending Album")
    assert not window.undo_manager.can_undo()

    _select(window, c)

    assert a.album == "Pending Album" and b.album == "Pending Album"
    assert c.album != "Pending Album"
    assert a.dirty and b.dirty and not c.dirty
    # the panel now shows C, with nothing ticked
    assert window.tag_panel.current_files() == [c]
    assert not window.tag_panel._checkboxes["album"].isChecked()
    assert window._selected_files() == [c]

    window.undo_last_action()  # ONE undo restores both files
    assert a.album != "Pending Album" and b.album != "Pending Album"
    assert not window.undo_manager.can_undo()


def test_status_bar_says_how_many_files_got_the_pending_edits(window, tmp_path):
    a, b, c = _load(window, tmp_path, ["a.mp3", "b.mp3", "c.mp3"])
    _select(window, a, b)
    _type(window, "album", "Pending Album")
    _select(window, c)
    assert window.statusBar().currentMessage() == "Applied pending edits to 2 files"


def test_no_auto_apply_when_the_previous_selection_was_empty(window, tmp_path):
    a, b, c = _load(window, tmp_path, ["a.mp3", "b.mp3", "c.mp3"])
    window.table.clearSelection()
    _app.processEvents()
    _type(window, "album", "Nobody Gets This")
    window.statusBar().clearMessage()
    _select(window, c)
    assert all(mp3.album != "Nobody Gets This" for mp3 in (a, b, c))
    assert not any(mp3.dirty for mp3 in (a, b, c))
    assert not window.undo_manager.can_undo()
    assert window.statusBar().currentMessage() == ""


def test_nothing_ticked_or_unchanged_values_make_no_undo_entry_and_no_message(window, tmp_path):
    a, b, c = _load(window, tmp_path, ["a.mp3", "b.mp3", "c.mp3"])
    _select(window, a)
    window.statusBar().clearMessage()
    _select(window, b)  # nothing ticked
    assert not window.undo_manager.can_undo()
    _type(window, "title", b.title)  # what is already there
    _select(window, c)
    assert not window.undo_manager.can_undo()
    assert not any(mp3.dirty for mp3 in (a, b, c))
    assert window.statusBar().currentMessage() == ""


def test_a_refresh_of_the_same_selection_keeps_the_pending_edit_pending(window, tmp_path):
    """Undo, a rebuild, or any operation that re-reads the selection into the panel keeps what was typed (still
    ticked, not applied), so nothing is lost and nothing is applied on top of the operation behind your back."""
    a, b, c = _load(window, tmp_path, ["a.mp3", "b.mp3", "c.mp3"])
    _select(window, a, b)
    _type(window, "album", "Still Pending")
    window._rebuild_table()
    _app.processEvents()
    panel = window.tag_panel
    assert panel._checkboxes["album"].isChecked()
    assert panel._editors["album"].text() == "Still Pending"
    assert not window.undo_manager.can_undo()
    assert not a.dirty
    _select(window, c)
    assert a.album == "Still Pending" and b.album == "Still Pending"


def test_apply_still_works_and_unticks(window, tmp_path):
    a, b = _load(window, tmp_path, ["a.mp3", "b.mp3"])
    _select(window, a)
    _type(window, "album", "Applied")
    window.tag_panel.apply_bulk_edit()
    _app.processEvents()
    assert a.album == "Applied" and b.album != "Applied"
    assert not window.tag_panel._checkboxes["album"].isChecked()
    _select(window, b)
    assert b.album != "Applied"
    assert window.undo_manager.can_undo()


def _record_questions(monkeypatch, answer):
    asked = []

    def question(*args, **kwargs):
        asked.append(args[2] if len(args) > 2 else "")
        return answer
    monkeypatch.setattr(QMessageBox, "question", staticmethod(question))
    return asked


@pytest.mark.parametrize("command", ["refresh_list", "clear_list"])
def test_list_commands_ask_about_pending_edits_and_keep_them_when_cancelled(window, tmp_path, monkeypatch, command):
    """Refresh List / Clear List with only a typed, not Applied edit: it is applied first (undoable), so it counts
    as an unsaved change in the "discard?" question; answering No loses nothing."""
    a, b, c = _load(window, tmp_path, ["a.mp3", "b.mp3", "c.mp3"])
    _select(window, a, b)
    _type(window, "album", "Asked About")
    asked = _record_questions(monkeypatch, QMessageBox.StandardButton.No)
    getattr(window, command)()
    assert len(asked) == 1 and "unsaved" in asked[0]
    assert len(window.files) == 3
    assert a.album == "Asked About" and b.album == "Asked About"
    assert a.dirty and b.dirty
    window.undo_last_action()
    assert a.album != "Asked About"


@pytest.mark.parametrize("command", ["refresh_list", "clear_list"])
def test_list_commands_without_pending_edits_ask_nothing(window, tmp_path, monkeypatch, command):
    a, b = _load(window, tmp_path, ["a.mp3", "b.mp3"])
    _select(window, a, b)
    asked = _record_questions(monkeypatch, QMessageBox.StandardButton.No)
    getattr(window, command)()
    assert asked == []
    assert not window.undo_manager.can_undo()


def test_open_folder_asks_about_pending_edits(window, tmp_path, monkeypatch):
    (a,) = _load(window, tmp_path, ["a.mp3"])
    _select(window, a)
    _type(window, "album", "Asked About")
    asked = _record_questions(monkeypatch, QMessageBox.StandardButton.No)
    other = tmp_path / "other"
    other.mkdir()
    (other / "x.mp3").write_bytes((tmp_path / "a.mp3").read_bytes())
    window._load_paths([other / "x.mp3"])
    assert len(asked) == 1
    assert window.files == [a] and a.album == "Asked About"


def test_quitting_asks_about_pending_edits(window, tmp_path, monkeypatch):
    (a,) = _load(window, tmp_path, ["a.mp3"])
    _select(window, a)
    _type(window, "album", "Do Not Lose Me")
    asked = _record_questions(monkeypatch, QMessageBox.StandardButton.No)
    window.close()
    assert len(asked) == 1 and window.isVisible()
    assert a.album == "Do Not Lose Me"


def test_remove_from_list_drops_pending_edits_for_the_removed_files_without_an_undo_entry(window, tmp_path):
    a, b, c = _load(window, tmp_path, ["a.mp3", "b.mp3", "c.mp3"])
    _select(window, a, b)
    _type(window, "album", "Gone Too")
    window.remove_selected_from_list()
    _app.processEvents()
    assert window.files == [c]
    assert c.album != "Gone Too"
    assert not window.undo_manager.can_undo()
    assert not window.tag_panel._checkboxes["album"].isChecked()


def test_selecting_from_find_duplicates_applies_pending_edits_to_the_previous_selection(window, tmp_path):
    a, b = _load(window, tmp_path, ["a.mp3", "b.mp3"])
    _select(window, a)
    _type(window, "album", "Before Duplicates")
    window._select_files_from_duplicates([b])
    assert a.album == "Before Duplicates" and b.album != "Before Duplicates"
    assert window.tag_panel.current_files() == [b]
