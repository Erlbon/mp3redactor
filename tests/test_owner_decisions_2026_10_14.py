"""The owner's 2026-10-14 decisions: no Ctrl+S alias (M1), "Open" wording for
the actions that open files (M2), Number Tracks folded into Auto-Number as a
preset (M3), Tools > MusicBrainz Database Setup (M4) and Edit > Filter List
(M5)."""

import os
import re
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtGui import QAction, QKeySequence  # noqa: E402
from PyQt6.QtWidgets import QInputDialog, QMenu, QMessageBox  # noqa: E402

import gui.main_window as mw  # noqa: E402
from gui import auto_number_presets  # noqa: E402
from redactor_common.core import labels  # noqa: E402
from tests.test_cover_gui import _app, _load, _select_all, window  # noqa: E402,F401

ROOT = Path(__file__).parent.parent


def _plain(text: str) -> str:
    return labels.plain_label(text)


def _menu(window, heading: str) -> QMenu:
    return next(a.menu() for a in window.menuBar().actions() if _plain(a.text()) == heading)


def _labels(menu: QMenu) -> list[str]:
    return [_plain(a.text()) for a in menu.actions() if not a.isSeparator()]


# -- M1: no Ctrl+S alias ---------------------------------------------------


def test_save_all_has_only_its_family_shortcut(window):
    assert window.action_registry["save_all"].shortcuts() == [QKeySequence("Ctrl+Shift+A")]


def test_ctrl_s_and_f8_are_not_bound_to_anything(window):
    for key in ("Ctrl+S", "F8"):
        seq = QKeySequence(key)
        assert not any(seq in act.shortcuts() for act in window.findChildren(QAction)), key


def test_docs_do_not_mention_ctrl_s():
    for name in ("README.md", "ABOUT.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert not re.search(r"Ctrl\+S\b", text), name
    assert "with_aliases" not in (ROOT / "gui" / "main_window.py").read_text(encoding="utf-8")


# -- M2: "Open" wording ----------------------------------------------------


def test_no_files_message_says_open(window, monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: shown.append(a[2])))
    window.files = []
    window._rebuild_table()
    window.open_rename_dialog()
    assert shown and shown[0].startswith("Open ") and "Load" not in shown[0]


def test_no_load_or_add_wording_for_the_open_actions():
    old = re.compile(r"\b(Load|Add) (Files|Folder)\b|Load some|Load at least|Import & Convert")
    for rel in ("README.md", "ABOUT.md", "gui/main_window.py", "core/settings.py", "core/scan_service.py"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert not old.search(text), (rel, old.search(text).group(0) if old.search(text) else "")


# -- M3: Number Tracks is an Auto-Number preset -----------------------------


def test_number_tracks_menu_item_is_gone(window):
    assert "number_tracks" not in window.action_registry
    assert "Number Tracks" not in _labels(_menu(window, "Metadata"))
    assert "Auto-Number" in _labels(_menu(window, "Edit"))
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "Number Tracks…" not in readme


def _preset_dialog(files, padding=3, remembered=None):
    fields = [(key, label, key == "track") for key, label, _m in mw.FIELDS]
    return auto_number_presets.AutoNumberingDialog(
        files, fields,
        get_value=lambda mp3, key: getattr(mp3, key, "") or "",
        get_display_name=lambda mp3: mp3.filename,
        item_noun="file",
        padding=padding,
        on_padding_changed=(remembered.append if remembered is not None else None),
    )


def test_number_tracks_preset_numbers_track_one_to_n_unpadded(window, tmp_path):
    files = _load(window, tmp_path, ["a.mp3", "b.mp3", "c.mp3"])
    for mp3 in files:
        mp3.track = ""
    remembered: list[int] = []
    dialog = _preset_dialog(files, padding=3, remembered=remembered)
    assert dialog.preset_combo.currentText() == auto_number_presets.PRESET_CUSTOM
    dialog.preset_combo.setCurrentText(auto_number_presets.PRESET_NUMBER_TRACKS)
    assert dialog.result_field_key() == "track"
    assert (dialog.start_spin.value(), dialog.increment_spin.value(), dialog.padding_spin.value()) == (1, 1, 0)
    assert dialog.accepted_changes() == {0: "1", 1: "2", 2: "3"}
    # The preset's padding is not taken as the user's remembered choice.
    assert remembered == []
    dialog.close()


def test_preset_can_be_preselected(window, tmp_path):
    files = _load(window, tmp_path, ["a.mp3", "b.mp3"])
    for mp3 in files:
        mp3.track = ""
    fields = [(key, label, key == "track") for key, label, _m in mw.FIELDS]
    dialog = auto_number_presets.AutoNumberingDialog(
        files, fields, get_value=lambda mp3, key: getattr(mp3, key, "") or "",
        get_display_name=lambda mp3: mp3.filename, preset=auto_number_presets.PRESET_NUMBER_TRACKS,
    )
    assert dialog.result_field_key() == "track" and dialog.accepted_changes() == {0: "1", 1: "2"}
    dialog.close()


def test_auto_number_menu_uses_the_preset_dialog_and_numbers_tracks(window, tmp_path, monkeypatch):
    files = _load(window, tmp_path, ["a.mp3", "b.mp3"])
    for mp3 in files:
        mp3.track = ""
    _select_all(window)
    assert mw.AutoNumberingDialog is auto_number_presets.AutoNumberingDialog

    def run(dialog):
        dialog.preset_combo.setCurrentText(auto_number_presets.PRESET_NUMBER_TRACKS)
        return dialog.DialogCode.Accepted

    monkeypatch.setattr(auto_number_presets.AutoNumberingDialog, "exec", run)
    window.action_registry["auto_number"].trigger()
    in_table_order = window._selected_files()
    assert sorted(mp3.track for mp3 in files) == ["1", "2"]
    assert all(mp3.dirty for mp3 in in_table_order)
    window.undo_last_action()
    assert [mp3.track for mp3 in files] == ["", ""]


def test_context_menu_organize_offers_auto_number(window, tmp_path, monkeypatch):
    from tests.test_menu_skeleton import _context_menu_tree

    _load(window, tmp_path, ["a.mp3"])
    _select_all(window)
    tree = _context_menu_tree(window, monkeypatch)
    by_name = {t[0]: t[1] for t in tree if isinstance(t, tuple)}
    assert by_name["Organize"] == ["Rename / Export / Move", "Auto-Number"]


# -- M4: MusicBrainz Database Setup -----------------------------------------


def test_tools_menu_says_musicbrainz_database_setup(window):
    assert window.action_registry["musicbrainz_settings"].text() == "&MusicBrainz Database Setup…"
    assert "MusicBrainz Database Setup" in _labels(_menu(window, "Tools"))
    assert window.action_registry["musicbrainz_local_lookup"].text() == "Music&Brainz (Local Database)…"


# -- M5: Edit > Filter List -------------------------------------------------


def _titled(window, tmp_path):
    files = _load(window, tmp_path, ["a.mp3", "b.mp3", "c.mp3"])
    for mp3, title in zip(files, ("Alpha Song", "Beta Song", "Gamma")):
        mp3.title = title
    window._rebuild_table()
    return files


def _visible(window) -> set[str]:
    col = window._col_index["filename"]
    return {
        window.table.item(row, col).text()
        for row in range(window.table.rowCount()) if not window.table.isRowHidden(row)
    }


def test_filter_list_is_wired_with_the_family_shortcut(window):
    act = window.action_registry["filter_list"]
    assert act.isEnabled() and act.receivers(act.triggered) > 0
    assert act.shortcuts() == [QKeySequence("Ctrl+F")]
    assert "Filter List" in _labels(_menu(window, "Edit"))


def test_filter_hides_rows_case_insensitively_and_reports(window, tmp_path, monkeypatch):
    _titled(window, tmp_path)
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("SONG", True)))
    window.action_registry["filter_list"].trigger()
    assert _visible(window) == {"a.mp3", "b.mp3"}
    assert window.statusBar().currentMessage() == "Showing 2 of 3 files (filter: SONG)"
    window.set_list_filter("b.MP3")  # the filename counts too
    assert _visible(window) == {"b.mp3"}
    window.set_list_filter(str(tmp_path).upper())  # and the path
    assert _visible(window) == {"a.mp3", "b.mp3", "c.mp3"}


def test_empty_filter_text_clears(window, tmp_path, monkeypatch):
    _titled(window, tmp_path)
    window.set_list_filter("gamma")
    assert _visible(window) == {"c.mp3"}
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("", True)))
    window.filter_list()
    assert _visible(window) == {"a.mp3", "b.mp3", "c.mp3"}
    assert window.list_filter == ""


def test_hidden_rows_are_deselected_and_not_batch_targets(window, tmp_path):
    files = _titled(window, tmp_path)
    _select_all(window)
    window.set_list_filter("song")
    assert {mp3.filename for mp3 in window._selected_files()} == {"a.mp3", "b.mp3"}
    # Select All while filtered still only reaches what is shown.
    _select_all(window)
    assert {mp3.filename for mp3 in window._selected_files()} == {"a.mp3", "b.mp3"}
    assert len(files) == 3


def test_filter_survives_a_rebuild_and_a_sort(window, tmp_path):
    _titled(window, tmp_path)
    window.set_list_filter("gamma")
    window._rebuild_table()
    assert _visible(window) == {"c.mp3"}
    window.table.sortItems(window._col_index["filename"], Qt.SortOrder.AscendingOrder)
    assert _visible(window) == {"c.mp3"}
    window.table.sortItems(window._col_index["filename"], Qt.SortOrder.DescendingOrder)
    assert _visible(window) == {"c.mp3"}


def test_clear_list_and_a_new_open_clear_the_filter(window, tmp_path):
    _titled(window, tmp_path)
    window.set_list_filter("gamma")
    window.clear_list()
    assert window.list_filter == ""
    _titled(window, tmp_path)
    window.set_list_filter("gamma")
    window._load_paths([tmp_path])
    assert window.list_filter == ""
    assert _visible(window) == {"a.mp3", "b.mp3", "c.mp3"}


def test_readme_documents_filter_list():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "Filter List" in readme and "Ctrl+F" in readme
