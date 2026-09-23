"""
Cover art in the GUI (2026-09-23): Set/From Folder/Remove/Export on the
selection, undo, the Cover column's lazily loaded thumbnails, and the
side panel's background-loaded preview. File dialogs and message boxes
are patched so the real handlers run headless.
"""

import os
import shutil
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtCore import QBuffer, QByteArray, QIODevice  # noqa: E402
from PyQt6.QtGui import QColor, QImage  # noqa: E402
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox  # noqa: E402

from core import cover_art  # noqa: E402
from core.mp3_file import MP3File  # noqa: E402
from core.tag_reader import load_tags  # noqa: E402

_app = QApplication.instance() or QApplication([])
FIXTURE = Path(__file__).parent / "fixtures" / "tiny.mp3"


def _image_bytes(fmt: str, color=(200, 40, 40), size=(300, 300)) -> bytes:
    img = QImage(*size, QImage.Format.Format_RGB32)
    img.fill(QColor(*color))
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, fmt)
    return bytes(data)


def _pump_until(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        _app.processEvents()
        time.sleep(0.01)
    return predicate()


@pytest.fixture
def window(monkeypatch, tmp_path):
    import gui.main_window as mw

    monkeypatch.setattr(mw, "save_settings", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))
    w = mw.MainWindow()
    w.resize(1100, 500)
    w.show()
    yield w
    for mp3 in w.files:
        mp3.dirty = False
    w.close()


def _load(window, tmp_path, names):
    files = []
    for name in names:
        folder = tmp_path / name.split("/")[0] if "/" in name else tmp_path
        folder.mkdir(exist_ok=True)
        dest = tmp_path / name
        shutil.copyfile(FIXTURE, dest)
        mp3 = MP3File(path=dest)
        load_tags(mp3)
        files.append(mp3)
    window.files = files
    window._rebuild_table()
    return files


def _select_all(window):
    window.table.selectAll()
    _app.processEvents()


def test_set_cover_from_file_preview_thumbnail_undo_and_save(window, tmp_path, monkeypatch):
    files = _load(window, tmp_path, ["a.mp3", "b.mp3"])
    image = tmp_path / "art.webp"
    image.write_bytes(_image_bytes("WEBP") or _image_bytes("BMP"))  # not JPEG/PNG: gets converted
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(image), "")))

    _select_all(window)
    window.set_cover_from_file()
    assert all(mp3.has_cover and mp3.cover_change_pending for mp3 in files)
    assert all(cover_art.sniff_mime(mp3.cover_pending) in ("image/jpeg", "image/png") for mp3 in files)
    col = window._col_index["cover"]
    assert window.table.item(0, col).text() == "Yes"

    window._visible_rows.check_now()
    assert _pump_until(lambda: not window.table.item(0, col).icon().isNull())

    window.table.selectRow(0)
    _app.processEvents()
    window._cover_preview.wait_for_done()
    label = window.tag_panel.cover_label
    assert _pump_until(lambda: label._original_pixmap is not None)
    assert "Not saved" in window.tag_panel.cover_note.text()

    window.undo_last_action()
    assert not any(mp3.cover_change_pending or mp3.has_cover for mp3 in files)
    window.redo_last_action()
    assert all(mp3.cover_change_pending for mp3 in files)

    window.save_changed()
    assert all(cover_art.read_cover(mp3.path) is not None for mp3 in files)
    assert not any(mp3.cover_change_pending or mp3.dirty for mp3 in files)


def test_remove_cover_only_targets_files_with_one(window, tmp_path, monkeypatch):
    files = _load(window, tmp_path, ["a.mp3", "b.mp3"])
    files[0].set_cover(_image_bytes("PNG"), "image/png")
    window._rebuild_table()
    _select_all(window)
    window.remove_cover()
    assert files[0].cover_remove_pending and not files[0].has_cover
    assert not files[1].cover_change_pending  # had none: left untouched, not dirtied
    assert "Remove Cover" in window.action_undo.text()


def test_cover_from_folder_image_per_album(window, tmp_path):
    files = _load(window, tmp_path, ["album1/a.mp3", "album1/b.mp3", "album2/c.mp3", "album3/d.mp3"])
    red, blue = _image_bytes("JPEG", (220, 0, 0)), _image_bytes("PNG", (0, 0, 220))
    (tmp_path / "album1" / "cover.jpg").write_bytes(red)
    (tmp_path / "album2" / "Folder.png").write_bytes(blue)
    _select_all(window)
    window.set_cover_from_folder_images()
    by_name = {mp3.filename: mp3 for mp3 in files}
    assert by_name["a.mp3"].cover_pending == red and by_name["b.mp3"].cover_pending == red
    assert by_name["c.mp3"].cover_pending == blue and by_name["c.mp3"].cover_pending_mime == "image/png"
    assert not by_name["d.mp3"].cover_change_pending  # no image next to it


def test_export_writes_the_cover_bytes(window, tmp_path, monkeypatch):
    files = _load(window, tmp_path, ["a.mp3"])
    data = _image_bytes("JPEG")
    files[0].set_cover(data, "image/jpeg")
    window._rebuild_table()
    out = tmp_path / "exported.jpg"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(out), "")))
    window.table.selectRow(0)
    _app.processEvents()
    window.export_cover()
    assert out.read_bytes() == data


def test_thumbnails_load_only_for_visible_rows(window, tmp_path, monkeypatch):
    files = _load(window, tmp_path, [f"{i:03}.mp3" for i in range(120)])
    reads = []
    for mp3 in files:
        mp3.has_cover = True  # pretend, so each row is a candidate
        monkeypatch.setattr(mp3, "cover_image_bytes", lambda mp3=mp3: reads.append(mp3) or _image_bytes("PNG"))
    window._rebuild_table()
    window.table.sortItems(0)
    window._visible_rows.check_now()
    assert _pump_until(lambda: len(reads) > 0)
    for _ in range(20):
        _app.processEvents()
    assert 0 < len(reads) < 80  # viewport + buffer, not all 120

    reads.clear()
    window.table.setColumnHidden(window._col_index["cover"], True)
    window.table.scrollToBottom()
    window._visible_rows.check_now()
    for _ in range(20):
        _app.processEvents()
    assert reads == []  # Cover column hidden: nothing loaded
