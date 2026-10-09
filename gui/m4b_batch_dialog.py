"""
gui/m4b_batch_dialog.py

File > Create M4B Audiobook... when the selected files come from more than one folder: each
folder is one book (the way library apps such as Audiobookshelf see it) and gets its own .m4b.
The dialog lists the books with their title, author, series and number to check or correct
(filled from each folder's tags), and sets what they share: the quality, where the audiobooks go,
and whether metadata.opf and a cover image are written beside each.

Where they go:
  - "beside": in each folder, next to the MP3 files, as <Title>.m4b;
  - "library": under a library folder in Audiobookshelf's layout,
    <library>/<Author>/[<Series>/]<Title>/<Title>.m4b -- a folder of its own per book.

An audiobook that already exists is skipped, never replaced (the single-book dialog asks
instead). The originals are never touched.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton, QRadioButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout,
)

from core.audiobook_lookup import DEFAULT_REGION, BookMatch
from core.m4b_builder import (
    BITRATE_CHOICES_KBPS, DEFAULT_BITRATE_KBPS, DEFAULT_GENRE, BookChapter, BookSpec, chapter_title, defaults_for,
    library_output,
)
from core.mp3_file import MP3File
from gui.m4b_dialog import SIDECAR_LABEL, SIDECAR_TIP
from redactor_common.core.rename_pattern import sanitize_filename

LAYOUT_BESIDE = "beside"
LAYOUT_LIBRARY = "library"
COL_INCLUDE, COL_TITLE, COL_AUTHOR, COL_NARRATOR, COL_SERIES, COL_NUMBER, COL_CHAPTERS, COL_FOLDER = range(8)
HEADERS = ["Make", "Title", "Author", "Narrator", "Series", "#", "Chapters", "Folder"]


class M4bBatchDialog(QDialog):
    def __init__(
        self,
        books: list[list[MP3File]],
        bitrate_kbps: int = DEFAULT_BITRATE_KBPS,
        sidecar: bool = False,
        layout: str = LAYOUT_BESIDE,
        library_root: str = "",
        region: str = DEFAULT_REGION,
        lookup_fetch=None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Create M4B Audiobooks")
        self.resize(980, 560)
        self._books = books
        self._defaults = [defaults_for(book) for book in books]
        self._library_root = library_root
        self._region = region
        self._lookup_fetch = lookup_fetch
        # what a look-up found beyond the visible cells, per row: publisher, year, language, description, cover
        self._extras: dict[int, dict] = {}

        root = QVBoxLayout(self)
        intro = QLabel(
            f"{len(books)} folders, one audiobook each (a chapter per MP3 file, in disc / track / name order). "
            "Check the details below. The MP3s are re-encoded to AAC; the originals are not touched, and an "
            "audiobook that already exists is skipped, not replaced."
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        self.table = QTableWidget(len(books), len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        header = self.table.horizontalHeader()
        for column, mode in (
            (COL_INCLUDE, QHeaderView.ResizeMode.ResizeToContents), (COL_TITLE, QHeaderView.ResizeMode.Stretch),
            (COL_AUTHOR, QHeaderView.ResizeMode.Stretch), (COL_NARRATOR, QHeaderView.ResizeMode.Stretch),
            (COL_SERIES, QHeaderView.ResizeMode.Stretch),
            (COL_NUMBER, QHeaderView.ResizeMode.ResizeToContents), (COL_CHAPTERS, QHeaderView.ResizeMode.ResizeToContents),
            (COL_FOLDER, QHeaderView.ResizeMode.Stretch),
        ):
            header.setSectionResizeMode(column, mode)
        for row, (book, defaults) in enumerate(zip(books, self._defaults)):
            include = QTableWidgetItem()
            include.setFlags(include.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            include.setCheckState(Qt.CheckState.Checked)
            self.table.setItem(row, COL_INCLUDE, include)
            self.table.setItem(row, COL_TITLE, QTableWidgetItem(defaults.title))
            self.table.setItem(row, COL_AUTHOR, QTableWidgetItem(defaults.author))
            self.table.setItem(row, COL_NARRATOR, QTableWidgetItem(""))
            self.table.setItem(row, COL_SERIES, QTableWidgetItem(""))
            self.table.setItem(row, COL_NUMBER, QTableWidgetItem(""))
            for column, text in ((COL_CHAPTERS, str(len(book))), (COL_FOLDER, str(Path(book[0].path).parent))):
                item = QTableWidgetItem(text)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(row, column, item)
        root.addWidget(self.table, 1)
        lookup_row = QHBoxLayout()
        self.lookup_btn = QPushButton("Look Up Selected…")
        self.lookup_btn.setToolTip(
            "Search Audible (then Open Library) for the selected book and fill in its narrator, series, "
            "publisher, year, description and cover."
        )
        self.lookup_btn.clicked.connect(self._look_up)
        lookup_row.addWidget(self.lookup_btn)
        lookup_row.addStretch(1)
        root.addLayout(lookup_row)

        options = QFormLayout()
        self.bitrate_combo = QComboBox()
        for kbps in BITRATE_CHOICES_KBPS:
            self.bitrate_combo.addItem(f"{kbps} kbps", kbps)
        index = self.bitrate_combo.findData(bitrate_kbps)
        self.bitrate_combo.setCurrentIndex(index if index >= 0 else self.bitrate_combo.findData(DEFAULT_BITRATE_KBPS))
        options.addRow("Quality:", self.bitrate_combo)

        self.beside_radio = QRadioButton("Beside the MP3 files, in each folder")
        self.library_radio = QRadioButton("In a library folder, as Author / Series / Title / Title.m4b")
        group = QButtonGroup(self)
        group.addButton(self.beside_radio)
        group.addButton(self.library_radio)
        (self.library_radio if layout == LAYOUT_LIBRARY else self.beside_radio).setChecked(True)
        library_row = QHBoxLayout()
        library_row.addWidget(self.library_radio)
        self.library_edit = QLineEdit(library_root)
        self.library_edit.setPlaceholderText("library folder")
        library_row.addWidget(self.library_edit, 1)
        self.library_choose = QPushButton("Choose…")
        self.library_choose.clicked.connect(self._choose_library)
        library_row.addWidget(self.library_choose)
        options.addRow("Save:", self.beside_radio)
        options.addRow("", library_row)
        root.addLayout(options)
        self.sidecar_check = QCheckBox(SIDECAR_LABEL)
        self.sidecar_check.setToolTip(SIDECAR_TIP)
        self.sidecar_check.setChecked(sidecar)
        root.addWidget(self.sidecar_check)
        self.beside_radio.toggled.connect(self._sync_library_controls)
        self._sync_library_controls()

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Create Audiobooks")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    # -- look up ----------------------------------------------------------------

    def region(self) -> str:
        return self._region

    def _look_up(self) -> None:
        from gui.m4b_lookup_dialog import M4bLookupDialog

        rows = self.table.selectionModel().selectedRows()
        if not rows:
            QMessageBox.information(self, "Look Up Audiobook", "Select a book in the list first.")
            return
        row = rows[0].row()
        minutes = sum(f.duration_seconds or 0 for f in self._books[row]) / 60 or None
        dialog = M4bLookupDialog(
            self.table.item(row, COL_TITLE).text(), self.table.item(row, COL_AUTHOR).text(), minutes, self._region,
            fetch=self._lookup_fetch, parent=self,
        )
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        self._region = dialog.region()
        self.apply_match(row, dialog.selected_match(), dialog.chosen_cover())

    def apply_match(self, row: int, match: BookMatch, cover: tuple[bytes, str] | None) -> None:
        """Fills the row from a looked-up book; a field the result has nothing for keeps what it had."""
        for column, value in (
            (COL_TITLE, match.title), (COL_AUTHOR, match.author_text), (COL_NARRATOR, match.narrator_text),
            (COL_SERIES, match.series), (COL_NUMBER, match.series_index),
        ):
            if value:
                self.table.item(row, column).setText(value)
        extras = self._extras.setdefault(row, {})
        for key, value in (
            ("publisher", match.publisher), ("year", match.year), ("language", match.language),
            ("description", match.description),
        ):
            if value:
                extras[key] = value
        if cover is not None:
            extras["cover"], extras["cover_mime"] = cover

    # -- options ---------------------------------------------------------------

    def _sync_library_controls(self) -> None:
        library = self.library_radio.isChecked()
        self.library_edit.setEnabled(library)
        self.library_choose.setEnabled(library)

    def _choose_library(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose Library Folder", self.library_edit.text())
        if folder:
            self.library_edit.setText(folder)

    def layout_choice(self) -> str:
        return LAYOUT_LIBRARY if self.library_radio.isChecked() else LAYOUT_BESIDE

    def library_root(self) -> str:
        return self.library_edit.text().strip()

    def bitrate_kbps(self) -> int:
        return int(self.bitrate_combo.currentData())

    def sidecar(self) -> bool:
        return self.sidecar_check.isChecked()

    def _included_rows(self) -> list[int]:
        return [
            row for row in range(self.table.rowCount())
            if self.table.item(row, COL_INCLUDE).checkState() == Qt.CheckState.Checked
        ]

    def _on_accept(self) -> None:
        rows = self._included_rows()
        if not rows:
            QMessageBox.information(self, "Create M4B Audiobooks", "Tick at least one book to make.")
            return
        if any(not self.table.item(row, COL_TITLE).text().strip() for row in rows):
            QMessageBox.information(self, "Create M4B Audiobooks", "Every book needs a title.")
            return
        if self.layout_choice() == LAYOUT_LIBRARY and not self.library_root():
            QMessageBox.information(self, "Create M4B Audiobooks", "Choose the library folder to save into.")
            return
        self.accept()

    # -- result ----------------------------------------------------------------

    def specs(self) -> list[BookSpec]:
        """One BookSpec per ticked book, in the list's order."""
        specs = []
        for row in self._included_rows():
            book, defaults, extras = self._books[row], self._defaults[row], self._extras.get(row, {})
            title = self.table.item(row, COL_TITLE).text().strip()
            author = self.table.item(row, COL_AUTHOR).text().strip()
            series = self.table.item(row, COL_SERIES).text().strip()
            if self.layout_choice() == LAYOUT_LIBRARY:
                output = library_output(self.library_root(), author, title, series)
            else:
                output = Path(book[0].path).parent / f"{sanitize_filename(title) or 'Audiobook'}.m4b"
            specs.append(BookSpec(
                chapters=[BookChapter(Path(f.path), chapter_title(f)) for f in book],
                output=output,
                title=title,
                author=author,
                narrator=self.table.item(row, COL_NARRATOR).text().strip(),
                year=extras.get("year", defaults.year),
                genre=DEFAULT_GENRE,
                series=series,
                series_index=self.table.item(row, COL_NUMBER).text().strip(),
                publisher=extras.get("publisher", defaults.publisher),
                language=extras.get("language", defaults.language),
                description=extras.get("description", ""),
                cover=extras.get("cover", defaults.cover),
                cover_mime=extras.get("cover_mime", defaults.cover_mime),
                bitrate_kbps=self.bitrate_kbps(),
                write_sidecar=self.sidecar(),
            ))
        return specs
