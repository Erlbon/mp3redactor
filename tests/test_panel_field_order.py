"""Drag-to-reorder of the side-panel fields (redactor_common's field_order), in the real MainWindow offscreen:
a real mouse drag on a grip moves the field, the order is saved by field key, a second window restores it,
Export/Import carries it, Reset restores the default, and editing after a reorder still writes the right values to
the right fields (values and tick boxes stay with the field, not the position)."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PyQt6.QtGui import QMouseEvent  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402
from redactor_common.core import settings_bundle as sb  # noqa: E402

import gui.main_window as mw  # noqa: E402
from core.mp3_file import MP3File  # noqa: E402
from core.tag_reader import load_tags  # noqa: E402
from tests.test_cover_gui import _app, _load  # noqa: E402,F401


@pytest.fixture
def make_window(monkeypatch, tmp_path):
    monkeypatch.setattr("core.settings.base_dir", lambda: tmp_path / "cfg")
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))
    windows = []

    def make():
        w = mw.MainWindow()
        w.resize(1300, 900)
        w.show()
        _app.processEvents()
        windows.append(w)
        return w

    yield make
    for w in windows:
        for mp3 in w.files:
            mp3.dirty = False
        w.close()


def _shown(window):
    _app.processEvents()
    return window.tag_panel._field_order.visible_keys()


def _drag(window, key, to_key, below=False):
    panel = window.tag_panel
    grip = panel._field_order.grip(key)
    assert grip is not None and grip.isVisible()
    target = panel._row_widgets[to_key]
    target_global = target.mapToGlobal(QPoint(5, target.height() - 1 if below else 1))
    start = QPoint(grip.width() // 2, grip.height() // 2)
    QTest.mousePress(grip, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    for point in (grip.mapToGlobal(start) + QPoint(0, 12), target_global):
        QApplication.sendEvent(grip, QMouseEvent(
            QEvent.Type.MouseMove, QPointF(grip.mapFromGlobal(point)), QPointF(point),
            Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    QTest.mouseRelease(grip, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, grip.mapFromGlobal(target_global))
    _app.processEvents()


def test_drag_moves_the_field_and_saves_by_key(make_window):
    window = make_window()
    before = _shown(window)
    last = before[-1]
    first = before[0]
    _drag(window, last, first)
    after = _shown(window)
    assert after == [last] + before[:-1]
    assert window.settings.panel_field_order[:2] == [last, first]


def test_a_second_window_restores_the_order_and_reset_restores_the_default(make_window):
    window = make_window()
    default = _shown(window)
    _drag(window, default[-1], default[0])
    moved = _shown(window)
    second = make_window()
    assert _shown(second) == moved
    second.tag_panel.reset_field_order()
    assert _shown(second) == default and second.settings.panel_field_order == []


def test_order_travels_through_export_import(make_window):
    window = make_window()
    default = _shown(window)
    _drag(window, default[-1], default[0])
    wanted = _shown(window)
    adapter = window._settings_adapter()
    chosen = sb.default_selection(adapter)
    bundle = sb.parse_bundle(sb.dump_bundle(sb.build_bundle(adapter, chosen)), "mp3redactor")
    window.tag_panel.reset_field_order()
    assert _shown(window) == default
    result = sb.apply_bundle(adapter, bundle, chosen)
    window._on_settings_imported(result)
    assert _shown(window) == wanted


def test_edit_two_files_after_a_reorder_writes_the_right_values_to_the_right_fields(make_window, tmp_path):
    window = make_window()
    files = _load(window, tmp_path, ["a.mp3", "b.mp3"])
    default = _shown(window)
    _drag(window, "title", default[-1], below=True)  # Title moves to the very end
    _drag(window, "artist", default[0])  # Artist moves to the top
    window.table.selectAll()
    _app.processEvents()
    panel = window.tag_panel
    panel._editors["title"].setText("One Title")
    panel._editors["title"].textEdited.emit("One Title")
    panel._editors["album"].setText("One Album")
    panel._editors["album"].textEdited.emit("One Album")
    assert panel._checkboxes["title"].isChecked() and panel._checkboxes["album"].isChecked()
    assert not panel._checkboxes["artist"].isChecked()  # the tick stayed with its own field
    window.save_changed()
    for mp3 in files:
        fresh = MP3File(path=mp3.path)
        load_tags(fresh)
        assert fresh.title == "One Title" and fresh.album == "One Album"
