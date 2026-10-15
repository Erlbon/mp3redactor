"""
gui/notes_evidence_view.py -- the "Local files" tab of the Create Notes review page: what the folder holds, as the files carry it.
The cover (a ~160 px thumbnail; an artist: up to 6 album covers with their titles under them), the tracks / albums in their original
script, the tags and the folder name. No network. The files are read in a background thread (they may be on a share), the answer
is cached per folder and scaled to a small PNG, so the window never waits and a row seen twice is instant.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, Qt, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPlainTextEdit, QScrollArea, QVBoxLayout, QWidget

from core.notes_evidence import Evidence, evidence_lines, read_evidence

THUMB = 160
CACHE_LIMIT = 48
_CACHE: "OrderedDict[tuple[str, str], Evidence]" = OrderedDict()


def make_thumb(data: bytes, size: int = THUMB) -> Optional[bytes]:
    """A PNG of the picture scaled to fit size x size (aspect ratio kept); None when the bytes are not a picture. Uses QImage,
    which is safe in a worker thread; a few KB for a thumbnail."""
    image = QImage.fromData(data)
    if image.isNull():
        return None
    scaled = image.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
    raw = QByteArray()
    buffer = QBuffer(raw)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    scaled.save(buffer, "PNG")
    return bytes(raw)


def load_evidence(folder: Path, kind: str, loader=None) -> Evidence:
    """read_evidence + the thumbnails; the big pictures are dropped, so what is cached is small."""
    evidence = read_evidence(folder, kind, loader)
    for album in evidence.albums:
        if album.cover:
            album.thumb = make_thumb(album.cover)
            album.cover = None
    return evidence


def clear_cache() -> None:
    _CACHE.clear()


class LocalEvidenceView(QWidget):
    """Cover thumbnails above, the text below. show_folder(folder, kind) starts (or re-uses) the read."""

    _ready = pyqtSignal(object, int)
    loaded = pyqtSignal()  # an answer has been shown (tests and callers wait on it)
    loader = None  # tests may give a counting loader

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._token = 0
        self._pending: Optional[tuple] = None
        self.evidence: Optional[Evidence] = None
        self.shown_for: Optional[tuple] = None
        self.thumb_labels: list[QLabel] = []
        self.caption_labels: list[QLabel] = []
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.strip = QWidget()
        self.strip_layout = QHBoxLayout(self.strip)
        self.strip_layout.setContentsMargins(0, 0, 0, 0)
        self.strip_layout.addStretch(1)
        self.strip_area = QScrollArea()
        self.strip_area.setWidgetResizable(True)
        self.strip_area.setWidget(self.strip)
        self.strip_area.setFixedHeight(THUMB + 56)
        self.strip_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.text = QPlainTextEdit()  # the system UI font with its fallbacks: Cyrillic, Greek, CJK ... show
        self.text.setReadOnly(True)
        self.text.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.addWidget(self.status)
        layout.addWidget(self.strip_area)
        layout.addWidget(self.text, 1)
        self._ready.connect(self._show)
        self.clear()

    # -- API ----------------------------------------------------------------------------------------------------

    def clear(self) -> None:
        self._token += 1
        self.evidence = None
        self.shown_for = None
        self._pending = None
        self._set_strip([])
        self.text.clear()
        self.status.setText("")

    def show_folder(self, folder: Path, kind: str) -> None:
        key = (str(folder), kind)
        self._token += 1
        token = self._token
        self._pending = key
        cached = _CACHE.get(key)
        if cached is not None:
            _CACHE.move_to_end(key)
            self._show(cached, token)
            return
        self._set_strip([])
        self.text.clear()
        self.status.setText("Reading the files ...")
        loader = type(self).loader

        def work() -> None:
            evidence = load_evidence(Path(folder), kind, loader)
            _CACHE[key] = evidence
            while len(_CACHE) > CACHE_LIMIT:
                _CACHE.popitem(last=False)
            try:
                self._ready.emit(evidence, token)
            except RuntimeError:  # the dialog was closed meanwhile
                pass

        threading.Thread(target=work, name="notes-evidence", daemon=True).start()

    # -- showing ------------------------------------------------------------------------------------------------

    def _show(self, evidence: Evidence, token: int) -> None:
        if token != self._token:  # another row was selected while this one was read
            return
        self.evidence = evidence
        self.shown_for = self._pending
        self.status.setText("")
        self._set_strip(evidence.albums[:6] if evidence.kind == "artist" else evidence.albums[:1])
        self.text.setPlainText("\n".join(evidence_lines(evidence)))
        self.loaded.emit()

    def _set_strip(self, albums) -> None:
        while self.strip_layout.count():
            item = self.strip_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self.thumb_labels, self.caption_labels = [], []
        for album in albums:
            box = QVBoxLayout()
            picture = QLabel()
            picture.setAlignment(Qt.AlignmentFlag.AlignCenter)
            picture.setFixedSize(THUMB, THUMB)
            picture.setStyleSheet("border:1px solid palette(mid);")
            if album.thumb:
                pixmap = QPixmap()
                pixmap.loadFromData(album.thumb)
                picture.setPixmap(pixmap)
                picture.setProperty("has_cover", True)
            else:
                picture.setText("no cover")
                picture.setProperty("has_cover", False)
            caption = QLabel(album.title or album.folder_name)
            caption.setWordWrap(True)
            caption.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
            caption.setFixedWidth(THUMB)
            holder = QWidget()
            holder.setLayout(box)
            box.setContentsMargins(0, 0, 8, 0)
            box.addWidget(picture)
            box.addWidget(caption)
            self.strip_layout.addWidget(holder)
            self.thumb_labels.append(picture)
            self.caption_labels.append(caption)
        self.strip_layout.addStretch(1)
        self.strip_area.setVisible(bool(albums))
