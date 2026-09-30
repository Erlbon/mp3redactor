"""The main window's menus follow redactor_common's standard menu skeleton:
heading order, where each action lives, that every action is connected, the
toolbar, the right-click menu, and the Remove / Clear List / Save / Search
and Replace entries that arrived with it."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QMenu, QMessageBox, QToolBar  # noqa: E402

import gui.main_window as mw  # noqa: E402
from redactor_common.core import labels  # noqa: E402
from tests.test_cover_gui import _app, _load, _select_all, window  # noqa: E402,F401

HEADINGS = ["File", "Edit", "View", "Metadata", "Analyze", "Tools", "Help"]


def _plain(text: str) -> str:
    return labels.plain_label(text)


def _menu_tree(menu: QMenu) -> list:
    """Nested [(plain label, children or None)] of a menu, separators as '---'."""
    out = []
    for act in menu.actions():
        if act.isSeparator():
            out.append("---")
        elif act.menu() is not None:
            out.append((_plain(act.text()), _menu_tree(act.menu())))
        else:
            out.append(_plain(act.text()))
    return out


def _top_menus(window) -> dict:
    return {_plain(a.text()): a.menu() for a in window.menuBar().actions() if a.menu()}


def test_headings_in_skeleton_order(window):
    assert [_plain(a.text()) for a in window.menuBar().actions()] == HEADINGS


def test_menu_contents_and_order(window):
    menus = _top_menus(window)
    assert _menu_tree(menus["File"]) == [
        "Open Files", "Open Folder", "Import and Convert", "---",
        "Save All", "---",
        "Rename File", "Undo Last Rename", "Rename / Export / Move", "---",
        "Export Settings", "Import Settings", "---",
        "Remove from List", "Clear List", "---",
        "Exit",
    ]
    edit = [e for e in _menu_tree(menus["Edit"]) if e != "---"]
    assert edit[0:2] == ["Undo", "Redo"] and edit[2].startswith("Apply to")
    assert edit[3:] == [
        "Redact", "Edit Redact Recipe", "Search and Replace", "Change Case", "Auto-Number",
    ]
    assert _menu_tree(menus["View"]) == [
        "Show Metadata Panel", "---", "Zoom In", "Zoom Out", "Reset Zoom", "---",
        "Refresh List", "Command Palette",
    ]
    assert _menu_tree(menus["Metadata"]) == [
        "Parse Filename", "---",
        ("Look Up", ["MusicBrainz", "Lyrics"]), "---",
        "Edit Lyrics", "Number Tracks", "---",
        ("Cover", [
            "Set from Image File", "Set from Folder Image", "Remove Cover", "---",
            "Export Cover to Image File",
        ]),
    ]
    assert _menu_tree(menus["Analyze"]) == [
        "Check Integrity", "Fix Integrity Issues", "Deep Check Integrity", "---",
        "Detect BPM", "Detect Key", "Measure Loudness",
    ]
    assert _menu_tree(menus["Tools"]) == [
        "Preferences", "---", "External Tools", "---", "Columns", "Genres", "Languages",
    ]
    assert _menu_tree(menus["Help"]) == ["Changelog", "Credits", "---", f"About {mw.APP_NAME}"]


def test_every_action_is_connected_or_planned(window):
    """No dead entries: each enabled menu action has a receiver; the only
    greyed ones are the documented (planned) settings export/import."""
    registry = window.action_registry
    planned = {"export_settings", "import_settings"}
    for key in registry.keys():
        act = registry[key]
        if key in planned:
            assert not act.isEnabled(), key
            continue
        if key in ("undo", "redo", "apply"):  # enabled by state
            continue
        assert act.isEnabled(), key
        assert act.receivers(act.triggered) > 0, key


def test_old_action_keys_still_in_the_registry(window):
    registry = window.action_registry
    for key in (
        "check_integrity", "fix_integrity", "deep_check", "check_bpm", "check_key",
        "measure_loudness", "fetch_lyrics", "edit_lyrics", "cover_set", "cover_from_folder",
        "cover_remove", "cover_export", "parse_filename", "musicbrainz_lookup", "import_convert",
        "redact", "redact_recipe", "undo", "redo", "apply", "preferences", "external_tools",
        "columns", "genres", "languages", "refresh_list", "rename_file", "rename_export_move",
        "about", "changelog", "credits", "exit", "number_tracks", "remove_from_list", "clear_list",
        "search_replace", "change_case", "auto_number", "save_all",
    ):
        assert key in registry, key


def test_roles_and_standard_shortcuts(window):
    from PyQt6.QtGui import QAction, QKeySequence

    registry = window.action_registry
    assert registry["preferences"].menuRole() == QAction.MenuRole.PreferencesRole
    assert registry["about"].menuRole() == QAction.MenuRole.AboutRole
    assert registry["exit"].menuRole() == QAction.MenuRole.QuitRole
    assert registry["remove_from_list"].shortcut() == QKeySequence("Delete")
    assert registry["save_all"].shortcut() == QKeySequence("Ctrl+Shift+A")
    assert registry["search_replace"].shortcut() == QKeySequence("Ctrl+H")


def test_toolbar_contents(window):
    bar = window.findChildren(QToolBar)[0]
    texts = [_plain(a.text()) for a in bar.actions() if not a.isSeparator() and a.text()]
    assert texts[:2] == ["Open Files", "Open Folder"]
    assert texts[2] == "Save All" and "Save" not in texts
    assert texts[3].startswith("Apply to") and texts[4] == "Redact"
    assert texts[5:7] == ["Undo", "Redo"]
    assert "Panel" in texts


def test_panel_menu_item_mirrors_the_panel(window):
    action = window.action_show_panel
    assert action.isChecked()
    window._toggle_tag_panel()
    assert not action.isChecked()
    window._toggle_tag_panel()
    assert action.isChecked()


def test_apply_text_follows_the_selection(window, tmp_path):
    _load(window, tmp_path, ["a.mp3", "b.mp3"])
    _select_all(window)
    assert window.action_apply_bulk_edit.text() == "&Apply to 2 Selected"
    assert window.action_apply_bulk_edit.isEnabled()


def _context_menu_tree(window, monkeypatch, pos=None):
    """Runs _show_context_menu with QMenu.exec patched to capture the menu."""
    captured = []
    monkeypatch.setattr(QMenu, "exec", lambda self, *a, **k: captured.append(_menu_tree(self)))
    from PyQt6.QtCore import QPoint

    row_y = window.table.rowViewportPosition(0) + 5
    window._show_context_menu(pos or QPoint(10, row_y))
    return captured[0]


def test_context_menu_core_and_submenus(window, tmp_path, monkeypatch):
    _load(window, tmp_path, ["a.mp3"])
    _select_all(window)
    tree = _context_menu_tree(window, monkeypatch)
    top = [t if isinstance(t, str) else t[0] for t in tree if t != "---"]
    assert top == [
        "Open in Default App", "Open Containing Folder", "Copy Path", "Rename File",
        "Look Up", "Organize", "Analyze", "Cover", "Edit Lyrics", "Redact", "Remove from List",
    ]
    by_name = {t[0]: t[1] for t in tree if isinstance(t, tuple)}
    assert by_name["Look Up"] == ["MusicBrainz", "Lyrics"]
    assert by_name["Organize"] == ["Rename / Export / Move", "Number Tracks"]
    assert by_name["Analyze"] == [
        "Check Integrity", "Fix Integrity Issues", "Deep Check Integrity", "---",
        "Detect BPM", "Detect Key", "Measure Loudness",
    ]
    assert by_name["Cover"] == ["Set from Image File", "Set from Folder Image"]


def test_context_menu_multi_selection_omits_single_file_rows(window, tmp_path, monkeypatch):
    _load(window, tmp_path, ["a.mp3", "b.mp3"])
    _select_all(window)
    tree = _context_menu_tree(window, monkeypatch)
    top = [t if isinstance(t, str) else t[0] for t in tree if t != "---"]
    assert "Rename File" not in top and "Edit Lyrics" not in top
    assert top[-2:] == ["Redact", "Remove from List"]


def test_remove_from_list_and_clear_list(window, tmp_path, monkeypatch):
    a, b, c = _load(window, tmp_path, ["a.mp3", "b.mp3", "c.mp3"])
    window.table.clearSelection()
    window.table.selectRow(0)
    doomed = window._selected_files()[0]
    window.remove_selected_from_list()
    assert len(window.files) == 2 and all(f is not doomed for f in window.files)
    assert window.table.rowCount() == 2
    window.clear_list()
    assert window.files == [] and window.table.rowCount() == 0


def test_remove_from_list_asks_before_dropping_unsaved_edits(window, tmp_path, monkeypatch):
    (a,) = _load(window, tmp_path, ["a.mp3"])
    a.dirty = True
    _select_all(window)
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *x, **k: QMessageBox.StandardButton.No)
    )
    window.remove_selected_from_list()
    assert window.files == [a]
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *x, **k: QMessageBox.StandardButton.Yes)
    )
    window.remove_selected_from_list()
    assert window.files == []


def test_save_all_saves_every_changed_file_regardless_of_selection(window, tmp_path, monkeypatch):
    a, b = _load(window, tmp_path, ["a.mp3", "b.mp3"])
    saved = []
    monkeypatch.setattr(mw, "save_dirty_tags", lambda files: saved.extend(f.filename for f in files))
    a.dirty = b.dirty = True
    window.table.clearSelection()
    window.table.selectRow(0)
    window.save_changed()
    assert sorted(saved) == ["a.mp3", "b.mp3"]
    assert "save" not in window.action_registry and not hasattr(window, "save_selected")


class _FakeDialog:
    """Stands in for a shared preview dialog: accepts, changing item 0's field."""

    DialogCode = mw.QDialog.DialogCode

    def __init__(self, items, fields, **kwargs):
        self.items = items

    def exec(self):
        return self.DialogCode.Accepted

    def result_field_key(self):
        return "title"

    def accepted_changes(self):
        return {0: "Changed"}


def test_search_and_replace_and_change_case_edit_in_memory_and_undo(window, tmp_path, monkeypatch):
    (a,) = _load(window, tmp_path, ["a.mp3"])
    _select_all(window)
    for dialog_name, handler in (
        ("SearchReplaceDialog", window.open_search_replace_dialog),
        ("CaseConversionDialog", window.open_case_conversion_dialog),
    ):
        a.dirty = False
        a.title = "Original"
        monkeypatch.setattr(mw, dialog_name, _FakeDialog)
        handler()
        assert a.title == "Changed" and a.dirty
        window.undo_last_action()
        assert a.title == "Original"
