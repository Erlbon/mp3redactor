"""Right-click > Save Selected (N): saves only the selected files that have unsaved changes, through the same
path as File > Save All; greyed when none of the selection is changed; a failing file does not block the others."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtCore import QPoint, Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMenu, QMessageBox  # noqa: E402

import core.scan_service as scan_service  # noqa: E402
from core.mp3_file import MP3File  # noqa: E402
from core.tag_reader import load_tags  # noqa: E402
from tests.test_cover_gui import _app, _load, window  # noqa: E402,F401


def _title_on_disk(mp3):
    fresh = MP3File(path=mp3.path)
    load_tags(fresh)
    return fresh.title


def _dirty(mp3, title):
    mp3.title = title
    mp3.dirty = True


def _row_of(window, mp3):
    return next(r for r in range(window.table.rowCount()) if window.table.item(r, 0).data(Qt.ItemDataRole.UserRole) is mp3)


def _select(window, files):
    """Select the given files' rows (the table may show them in another order than the load order)."""
    rows = [_row_of(window, mp3) for mp3 in files]
    window.table.clearSelection()
    sm = window.table.selectionModel()
    for row in rows:
        sm.select(window.table.model().index(row, 0), sm.SelectionFlag.Select | sm.SelectionFlag.Rows)
    _app.processEvents()


def _open_menu(window, files):
    _select(window, files)
    row = _row_of(window, files[0])
    shown = []
    original = QMenu.exec
    QMenu.exec = lambda self, *a: shown.extend(self.actions())
    try:
        window._show_context_menu(QPoint(5, window.table.rowViewportPosition(row) + 5))
    finally:
        QMenu.exec = original
    return shown


def _save_action(actions):
    return next(a for a in actions if a.text().startswith("Save Selected"))


@pytest.fixture
def three(window, tmp_path):
    return _load(window, tmp_path, ["a.mp3", "b.mp3", "c.mp3"])


def test_count_counts_only_changed_selected_files(window, three):
    _dirty(three[0], "New A")
    _dirty(three[2], "New C")
    action = _save_action(_open_menu(window, three[:2]))
    assert action.text() == "Save Selected (1)" and action.isEnabled()


def test_menu_item_saves_only_the_selected_changed_files(window, three):
    originals = [_title_on_disk(m) for m in three]
    _dirty(three[0], "New A")
    _dirty(three[1], "New B")
    _dirty(three[2], "New C")
    _save_action(_open_menu(window, three[:2])).trigger()
    assert _title_on_disk(three[0]) == "New A"
    assert _title_on_disk(three[1]) == "New B"
    assert _title_on_disk(three[2]) == originals[2]  # not selected: untouched on disk
    assert not three[0].dirty and not three[1].dirty
    assert three[2].dirty  # and still changed in the list


def test_greyed_when_nothing_selected_is_changed(window, three):
    _dirty(three[2], "New C")
    action = _save_action(_open_menu(window, three[:2]))
    assert action.text() == "Save Selected (0)" and not action.isEnabled()
    assert action.toolTip() == "The selected files have no unsaved changes"


def test_a_failing_file_does_not_block_the_others_and_shows_the_failure_box(window, three, monkeypatch):
    originals = [_title_on_disk(m) for m in three]
    for i, mp3 in enumerate(three):
        _dirty(mp3, f"New {i}")
    real = scan_service.save_tags

    def flaky(mp3, *a, **k):
        if mp3.filename == "b.mp3":
            mp3.save_error = "disk says no"
            return
        return real(mp3, *a, **k)

    monkeypatch.setattr(scan_service, "save_tags", flaky)
    boxes = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda parent, title, text, *a: boxes.append((title, text))))
    _select(window, three)
    window.save_selected_files(list(three))
    assert _title_on_disk(three[0]) == "New 0" and _title_on_disk(three[2]) == "New 2"
    assert _title_on_disk(three[1]) == originals[1]
    assert three[1].dirty
    assert len(boxes) == 1 and boxes[0][0] == "Some Files Failed to Save"
    assert "b.mp3" in boxes[0][1] and "disk says no" in boxes[0][1]
