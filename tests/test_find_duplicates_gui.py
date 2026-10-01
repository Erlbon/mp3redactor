"""Analyze > Find Duplicates wired into the main window: the shared review
dialog is captured instead of exec()-ed, so the real handlers (select in
list, trash with the unsaved-edits guard, dismissal) run headless."""

import os
import shutil

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtCore import QThreadPool  # noqa: E402
from PyQt6.QtWidgets import QMessageBox  # noqa: E402

from redactor_common.core.duplicates import TIER_IDENTICAL  # noqa: E402
from redactor_common.gui import duplicates_dialog as shared  # noqa: E402

from core.mp3_file import MP3File  # noqa: E402
from tests.test_cover_gui import FIXTURE, _app, _load, window  # noqa: E402,F401


@pytest.fixture
def dialogs(monkeypatch):
    """Dialogs the window opened; `on_open` (set by a test) runs inside exec()."""
    class Opened(list):
        hooks: dict = {}

    opened = Opened()
    hooks = {"on_open": None}

    def fake_exec(self):
        opened.append(self)
        if hooks["on_open"]:
            hooks["on_open"](self)
        return 0

    monkeypatch.setattr(shared.DuplicatesDialog, "exec", fake_exec)
    opened.hooks = hooks
    return opened


@pytest.fixture
def recycle(monkeypatch):
    import gui.main_window as mw

    gone = []

    def fake_trash(path):
        gone.append(path)
        os.remove(path)

    monkeypatch.setattr(mw, "move_to_trash", fake_trash)
    return gone


def _member_item(dialog, mp3):
    top = dialog.tree.topLevelItem(0)
    for i in range(top.childCount()):
        if top.child(i).data(0, shared.MEMBER_ROLE).item is mp3:
            return top.child(i)
    raise AssertionError("member not in the dialog")


def test_find_duplicates_is_in_the_analyze_menu_with_the_reserved_mnemonic(window):
    act = window.action_registry["find_duplicates"]
    assert act.text() == "Find D&uplicates…"
    assert act.isEnabled() and act.receivers(act.triggered) > 0


def test_needs_two_readable_files(window, tmp_path, dialogs, monkeypatch):
    seen = []
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: seen.append(a[2])))
    _load(window, tmp_path, ["a.mp3"])
    window.open_find_duplicates_dialog()
    assert dialogs == [] and "at least two" in seen[0]


def test_identical_files_open_the_review_dialog_with_nothing_selected(window, tmp_path, dialogs):
    files = _load(window, tmp_path, ["a.mp3", "b.mp3", "sub/c.mp3"])
    files[1].path.write_bytes(FIXTURE.read_bytes())
    window.table.clearSelection()   # acts on ALL loaded files, not the selection
    window.table.selectRow(0)
    window.open_find_duplicates_dialog()
    assert len(dialogs) == 1
    dialog = dialogs[0]
    assert dialog.tree.topLevelItemCount() == 1
    group = dialog.tree.topLevelItem(0).data(0, shared.GROUP_ROLE)
    assert group.tier == TIER_IDENTICAL and len(group.members) == 3
    assert dialog.tree.selectedItems() == []
    assert [dialog.tree.headerItem().text(c) for c in range(dialog.tree.columnCount())][:4] == [
        "Artist", "Title", "Album", "Track",
    ]
    assert "Compared all 3 loaded files" in dialog.summary_label.text()


def test_unreadable_files_are_skipped_and_counted(window, tmp_path, dialogs):
    files = _load(window, tmp_path, ["a.mp3", "b.mp3", "c.mp3"])
    files[2].load_error = "broken"
    window.open_find_duplicates_dialog()
    assert "1 file(s) that could not be read were skipped" in dialogs[0].summary_label.text()


def test_select_in_list_reselects_rows_and_closes(window, tmp_path, dialogs):
    files = _load(window, tmp_path, ["a.mp3", "b.mp3", "c.mp3"])

    def pick(dialog):
        dialog.tree.setCurrentItem(_member_item(dialog, files[1]))
        _member_item(dialog, files[1]).setSelected(True)
        _member_item(dialog, files[2]).setSelected(True)
        dialog.select_button.click()

    dialogs.hooks["on_open"] = pick
    window.open_find_duplicates_dialog()
    assert {id(m) for m in window._selected_files()} == {id(files[1]), id(files[2])}


def test_trashing_removes_the_file_from_the_list(window, tmp_path, dialogs, recycle, monkeypatch):
    files = _load(window, tmp_path, ["a.mp3", "b.mp3", "c.mp3"])
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))

    def trash_b(dialog):
        _member_item(dialog, files[1]).setSelected(True)
        dialog.trash_button.click()

    dialogs.hooks["on_open"] = trash_b
    window.open_find_duplicates_dialog()
    assert recycle == [str(files[1].path)]
    assert [id(m) for m in window.files] == [id(files[0]), id(files[2])]
    assert window.table.rowCount() == 2


def test_a_file_with_unsaved_edits_is_never_trashed(window, tmp_path, dialogs, recycle, monkeypatch):
    files = _load(window, tmp_path, ["a.mp3", "b.mp3", "c.mp3"])
    files[1].dirty = True
    warnings = []
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: warnings.append(a[2])))

    def trash_b_and_c(dialog):
        _member_item(dialog, files[1]).setSelected(True)
        _member_item(dialog, files[2]).setSelected(True)
        dialog.trash_button.click()

    dialogs.hooks["on_open"] = trash_b_and_c
    window.open_find_duplicates_dialog()
    assert recycle == [str(files[2].path)]                      # the clean one went
    assert os.path.exists(files[1].path) and files[1].dirty     # the edited one stayed, edits intact
    assert any("unsaved changes" in w and files[1].path.name in w for w in warnings)
    assert [id(m) for m in window.files] == [id(files[0]), id(files[1])]


def test_not_duplicates_is_remembered_in_the_settings_folder_store(window, tmp_path, dialogs):
    import gui.main_window as mw

    files = _load(window, tmp_path, ["a.mp3", "b.mp3"])

    def dismiss(dialog):
        dialog.tree.topLevelItem(0).setSelected(True)
        dialog.dismiss_button.click()

    dialogs.hooks["on_open"] = dismiss
    window.open_find_duplicates_dialog()
    assert mw._duplicates_store().count() == 1

    dialogs.hooks["on_open"] = None
    window.open_find_duplicates_dialog()
    assert dialogs[1].tree.topLevelItemCount() == 0 and dialogs[1]._hidden_count() == 1

    # a third copy is a different set: the group is back
    shutil.copyfile(FIXTURE, tmp_path / "c.mp3")
    third = MP3File(path=tmp_path / "c.mp3")
    from core.tag_reader import load_tags
    load_tags(third)
    window.files.append(third)
    window.open_find_duplicates_dialog()
    assert dialogs[2].tree.topLevelItemCount() == 1
    assert len(dialogs[2].tree.topLevelItem(0).data(0, shared.GROUP_ROLE).members) == 3
