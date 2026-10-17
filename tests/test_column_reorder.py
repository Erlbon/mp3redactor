"""Tools > Columns... reorders the table's columns (redactor_common's ColumnSettingsDialog + apply_column_order),
in the real MainWindow: the dialog lists the table's CURRENT visual order (a header drag included), the new order is
applied to the header and persisted by key, survives a second window and Export/Import Settings, and the hidden
set and widths are kept."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtWidgets import QMessageBox  # noqa: E402
from redactor_common.core import settings_bundle as sb  # noqa: E402
from redactor_common.gui.column_settings_dialog import ColumnSettingsDialog  # noqa: E402

import gui.main_window as mw  # noqa: E402
from tests.test_cover_gui import _app  # noqa: E402,F401


@pytest.fixture
def make_window(monkeypatch, tmp_path):
    """Real settings file in a temp folder, so a second window really reloads what the first saved."""
    monkeypatch.setattr("core.settings.base_dir", lambda: tmp_path / "cfg")
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))
    windows = []

    def make():
        w = mw.MainWindow()
        windows.append(w)
        return w

    yield make
    for w in windows:
        w.close()


def _visual(window):
    header = window.table.horizontalHeader()
    return [window._column_keys[header.logicalIndex(v)] for v in range(header.count())]


def _run_dialog(monkeypatch, window, edit):
    seen = {}

    def fake_exec(self):
        seen["order"] = self.column_order()
        edit(self)
        return 1

    monkeypatch.setattr(ColumnSettingsDialog, "exec", fake_exec)
    window.open_column_settings_dialog()
    return seen


def _select(dialog, key):
    dialog.list_widget.setCurrentRow(dialog.column_order().index(key))


def test_dialog_lists_visual_order_including_a_header_drag(make_window, monkeypatch):
    window = make_window()
    header = window.table.horizontalHeader()
    header.moveSection(header.visualIndex(window._col_index["title"]), 3)  # the user drags a header
    expected = _visual(window)
    seen = _run_dialog(monkeypatch, window, lambda d: None)
    assert seen["order"] == expected
    assert seen["order"] != window._column_keys


def test_move_in_dialog_reorders_table_and_persists(make_window, monkeypatch):
    window = make_window()
    window.table.setColumnHidden(window._col_index["genre"], True)
    header = window.table.horizontalHeader()
    header.resizeSection(window._col_index["title"], 222)
    before = _visual(window)

    def edit(dialog):
        _select(dialog, "title")
        for _ in range(2):
            dialog.up_button.click()

    _run_dialog(monkeypatch, window, edit)
    expected = list(before)
    expected.remove("title")
    expected.insert(before.index("title") - 2, "title")
    assert _visual(window) == expected
    assert window.settings.column_order == expected
    assert window.table.isColumnHidden(window._col_index["genre"])
    assert header.sectionSize(window._col_index["title"]) == 222
    second = make_window()
    assert _visual(second) == expected  # restored by key in a new window
    assert second.table.isColumnHidden(second._col_index["genre"])


def test_header_drag_then_dialog_shows_it_and_unchecking_keeps_order(make_window, monkeypatch):
    window = make_window()
    header = window.table.horizontalHeader()
    header.moveSection(header.visualIndex(window._col_index["title"]), 1)
    assert window.settings.column_order == _visual(window)  # header drag persisted
    order_after_drag = _visual(window)

    def edit(dialog):
        for i in range(dialog.list_widget.count()):
            if dialog.list_widget.item(i).data(Qt.ItemDataRole.UserRole) == "genre":
                dialog.list_widget.item(i).setCheckState(Qt.CheckState.Unchecked)

    _run_dialog(monkeypatch, window, edit)
    assert _visual(window) == order_after_drag
    assert window.table.isColumnHidden(window._col_index["genre"])


def test_order_travels_through_export_import(make_window):
    window = make_window()
    header = window.table.horizontalHeader()
    header.moveSection(header.visualIndex(window._col_index["title"]), 1)
    wanted = _visual(window)
    adapter = window._settings_adapter()
    chosen = sb.default_selection(adapter)
    bundle = sb.parse_bundle(sb.dump_bundle(sb.build_bundle(adapter, chosen)), "mp3redactor")
    header.moveSection(header.visualIndex(window._col_index["title"]), 6)
    assert _visual(window) != wanted
    result = sb.apply_bundle(adapter, bundle, chosen)
    window._on_settings_imported(result)
    assert _visual(window) == wanted
