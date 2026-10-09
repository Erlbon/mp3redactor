"""
gui/m4b_dialog.py

Tools > Create M4B Audiobook...: one chaptered .m4b from the selected MP3
files, one chapter per file (core/m4b_builder.py does the work). The dialog
only collects the choices:

  - the book's title, author, narrator, year and genre (filled in from the
    first file's tags),
  - the cover (the first file's embedded picture, else a cover/folder image
    beside it; or any image file),
  - the AAC bitrate (speech needs far less than music),
  - the chapter list in reading order (disc, track, name), each title
    editable, with Move Up / Move Down,
  - where to save the .m4b.

The originals are never touched.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from core.cover_art import sniff_mime
from core.m4b_builder import (
    BITRATE_CHOICES_KBPS, DEFAULT_BITRATE_KBPS, DEFAULT_GENRE, BookChapter, BookSpec, chapter_title, defaults_for,
    order_files,
)
from core.mp3_file import MP3File
from redactor_common.core.rename_pattern import sanitize_filename

COL_NUMBER, COL_TITLE, COL_LENGTH, COL_FILE = range(4)
SIDECAR_LABEL = "Also write metadata.opf and a cover image beside the audiobook (Audiobookshelf, Calibre)"
SIDECAR_TIP = (
    "Library apps read a book's details from metadata.opf and its cover from cover.jpg in the book's own "
    "folder, so use this when the audiobook is alone in its folder. Earlier copies of those two files there "
    "are replaced."
)
COVER_BOX = 150


def _format_length(seconds: float | None) -> str:
    if not seconds:
        return ""
    total = int(round(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


class M4bDialog(QDialog):
    def __init__(
        self, files: list[MP3File], bitrate_kbps: int = DEFAULT_BITRATE_KBPS, sidecar: bool = False, parent=None
    ):
        super().__init__(parent)
        self.setWindowTitle("Create M4B Audiobook")
        self.resize(820, 640)
        self._files = order_files(files)
        self._by_path: dict[str, MP3File] = {str(f.path): f for f in self._files}
        self._cover: bytes | None = None
        self._cover_mime = ""
        first = self._files[0]
        defaults = defaults_for(self._files)

        root = QVBoxLayout(self)
        root.addWidget(QLabel(
            f"One chapter per file, {len(self._files)} in all. The MP3s are re-encoded to AAC (the "
            "format audiobook players expect), so the result is not bit-exact; the originals are not touched."
        ))
        root.itemAt(0).widget().setWordWrap(True)

        top = QHBoxLayout()
        form = QFormLayout()
        self.title_edit = QLineEdit(defaults.title)
        self.author_edit = QLineEdit(defaults.author)
        self.narrator_edit = QLineEdit()
        self.narrator_edit.setPlaceholderText("optional (stored as Composer)")
        self.year_edit = QLineEdit(defaults.year)
        self.series_edit = QLineEdit()
        self.series_edit.setPlaceholderText("optional")
        self.series_number_edit = QLineEdit()
        self.series_number_edit.setPlaceholderText("book number in the series")
        self.publisher_edit = QLineEdit(defaults.publisher)
        self._language = defaults.language  # carried through, not edited here
        self.genre_edit = QLineEdit(DEFAULT_GENRE)
        self.bitrate_combo = QComboBox()
        for kbps in BITRATE_CHOICES_KBPS:
            self.bitrate_combo.addItem(f"{kbps} kbps", kbps)
        index = self.bitrate_combo.findData(bitrate_kbps)
        self.bitrate_combo.setCurrentIndex(index if index >= 0 else self.bitrate_combo.findData(DEFAULT_BITRATE_KBPS))
        self.bitrate_combo.setToolTip("64 kbps is the usual choice for spoken audio; music-like audio may want 96-128.")
        form.addRow("Title:", self.title_edit)
        form.addRow("Author:", self.author_edit)
        form.addRow("Narrator:", self.narrator_edit)
        form.addRow("Series:", self.series_edit)
        form.addRow("Series number:", self.series_number_edit)
        form.addRow("Publisher:", self.publisher_edit)
        form.addRow("Year:", self.year_edit)
        form.addRow("Genre:", self.genre_edit)
        form.addRow("Quality:", self.bitrate_combo)
        top.addLayout(form, 1)

        cover_col = QVBoxLayout()
        self.cover_label = QLabel()
        self.cover_label.setFixedSize(COVER_BOX, COVER_BOX)
        self.cover_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cover_label.setStyleSheet("border: 1px solid gray;")
        cover_col.addWidget(self.cover_label)
        cover_buttons = QHBoxLayout()
        self.cover_choose = QPushButton("Choose…")
        self.cover_choose.clicked.connect(self._choose_cover)
        self.cover_clear = QPushButton("Remove")
        self.cover_clear.clicked.connect(lambda: self._set_cover(None, ""))
        cover_buttons.addWidget(self.cover_choose)
        cover_buttons.addWidget(self.cover_clear)
        cover_col.addLayout(cover_buttons)
        top.addLayout(cover_col)
        root.addLayout(top)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["#", "Chapter title (click to edit)", "Length", "File"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(COL_NUMBER, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_TITLE, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_LENGTH, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_FILE, QHeaderView.ResizeMode.Stretch)
        for f in self._files:
            self._append_row(f)
        root.addWidget(self.table, 1)

        order_row = QHBoxLayout()
        self.up_btn = QPushButton("Move Up")
        self.down_btn = QPushButton("Move Down")
        self.names_btn = QPushButton("Titles from File Names")
        self.up_btn.clicked.connect(lambda: self._move(-1))
        self.down_btn.clicked.connect(lambda: self._move(1))
        self.names_btn.clicked.connect(self._titles_from_names)
        for button in (self.up_btn, self.down_btn, self.names_btn):
            order_row.addWidget(button)
        order_row.addStretch(1)
        total = sum(f.duration_seconds or 0 for f in self._files)
        order_row.addWidget(QLabel(f"Total length about {_format_length(total)}"))
        root.addLayout(order_row)

        out_row = QHBoxLayout()
        out_row.addWidget(QLabel("Save as:"))
        self.output_edit = QLineEdit(self._default_output())
        out_row.addWidget(self.output_edit, 1)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse_output)
        out_row.addWidget(browse)
        root.addLayout(out_row)
        self.sidecar_check = QCheckBox(SIDECAR_LABEL)
        self.sidecar_check.setToolTip(SIDECAR_TIP)
        self.sidecar_check.setChecked(sidecar)
        root.addWidget(self.sidecar_check)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Create Audiobook")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self._set_cover(defaults.cover, defaults.cover_mime)

    # -- chapters -------------------------------------------------------------

    def _append_row(self, mp3: MP3File) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        number = QTableWidgetItem(str(row + 1))
        number.setFlags(number.flags() & ~Qt.ItemFlag.ItemIsEditable)
        title = QTableWidgetItem(chapter_title(mp3))
        length = QTableWidgetItem(_format_length(mp3.duration_seconds))
        length.setFlags(length.flags() & ~Qt.ItemFlag.ItemIsEditable)
        name = QTableWidgetItem(Path(mp3.path).name)
        name.setFlags(name.flags() & ~Qt.ItemFlag.ItemIsEditable)
        name.setData(Qt.ItemDataRole.UserRole, str(mp3.path))
        for column, item in ((COL_NUMBER, number), (COL_TITLE, title), (COL_LENGTH, length), (COL_FILE, name)):
            self.table.setItem(row, column, item)

    def _row_values(self, row: int) -> tuple[str, str, str]:
        return (
            self.table.item(row, COL_TITLE).text(),
            self.table.item(row, COL_LENGTH).text(),
            self.table.item(row, COL_FILE).data(Qt.ItemDataRole.UserRole),
        )

    def _set_row(self, row: int, title: str, length: str, path: str) -> None:
        self.table.item(row, COL_TITLE).setText(title)
        self.table.item(row, COL_LENGTH).setText(length)
        file_item = self.table.item(row, COL_FILE)
        file_item.setText(Path(path).name)
        file_item.setData(Qt.ItemDataRole.UserRole, path)

    def _move(self, step: int) -> None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        row = rows[0].row()
        other = row + step
        if not 0 <= other < self.table.rowCount():
            return
        a, b = self._row_values(row), self._row_values(other)
        self._set_row(row, *b)
        self._set_row(other, *a)
        self.table.selectRow(other)

    def _titles_from_names(self) -> None:
        for row in range(self.table.rowCount()):
            self.table.item(row, COL_TITLE).setText(Path(self._row_values(row)[2]).stem)

    def chapters(self) -> list[BookChapter]:
        chapters = []
        for row in range(self.table.rowCount()):
            title, _length, path = self._row_values(row)
            chapters.append(BookChapter(Path(path), title.strip() or Path(path).stem))
        return chapters

    # -- cover ----------------------------------------------------------------

    def _set_cover(self, data: bytes | None, mime: str) -> None:
        self._cover, self._cover_mime = data, mime
        pixmap = QPixmap()
        if data and pixmap.loadFromData(data):
            self.cover_label.setPixmap(pixmap.scaled(
                COVER_BOX, COVER_BOX, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation,
            ))
            self.cover_label.setText("")
        else:
            self._cover, self._cover_mime = None, ""
            self.cover_label.setPixmap(QPixmap())
            self.cover_label.setText("No cover")
        self.cover_clear.setEnabled(self._cover is not None)

    def _load_cover_file(self, path: Path) -> None:
        try:
            data = Path(path).read_bytes()
        except OSError as exc:
            QMessageBox.warning(self, "Cover", f"Could not read {Path(path).name}: {exc}")
            return
        self._set_cover(data, sniff_mime(data) or "image/jpeg")

    def _choose_cover(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose Cover Image", str(Path(self._files[0].path).parent), "Images (*.jpg *.jpeg *.png)"
        )
        if path:
            self._load_cover_file(Path(path))

    # -- output ---------------------------------------------------------------

    def _default_output(self) -> str:
        name = sanitize_filename(self.title_edit.text().strip()) or "Audiobook"
        return str(Path(self._files[0].path).parent / f"{name}.m4b")

    def _browse_output(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Audiobook As", self.output_edit.text(), "Audiobook (*.m4b)"
        )
        if path:
            self.output_edit.setText(path)

    def _on_accept(self) -> None:
        if not self.title_edit.text().strip():
            QMessageBox.information(self, "Create M4B Audiobook", "Give the book a title.")
            return
        output = self.output_path()
        if output is None:
            QMessageBox.information(self, "Create M4B Audiobook", "Choose where to save the audiobook.")
            return
        if output.exists():
            reply = QMessageBox.question(
                self, "Replace File?", f"{output.name} already exists. Replace it?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        self.accept()

    def output_path(self) -> Path | None:
        text = self.output_edit.text().strip()
        if not text:
            return None
        path = Path(text)
        return path if path.suffix.lower() == ".m4b" else path.with_name(path.name + ".m4b")

    def bitrate_kbps(self) -> int:
        return int(self.bitrate_combo.currentData())

    def spec(self) -> BookSpec:
        return BookSpec(
            chapters=self.chapters(),
            output=self.output_path(),
            title=self.title_edit.text().strip(),
            author=self.author_edit.text().strip(),
            narrator=self.narrator_edit.text().strip(),
            year=self.year_edit.text().strip(),
            genre=self.genre_edit.text().strip(),
            cover=self._cover,
            cover_mime=self._cover_mime,
            bitrate_kbps=self.bitrate_kbps(),
            write_sidecar=self.sidecar_check.isChecked(),
            series=self.series_edit.text().strip(),
            series_index=self.series_number_edit.text().strip(),
            publisher=self.publisher_edit.text().strip(),
            language=self._language,
        )
