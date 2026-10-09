"""
gui/m4b_lookup_dialog.py

"Look Up..." in Create M4B Audiobook: search Audible (then Open Library) by title and author and
pick the matching book (core/audiobook_lookup.py). The results are ranked with the files' running
time in mind, so the right edition comes first. Selecting a result shows its cover and description;
"Use This Book" hands it back to the audiobook dialog, which fills in title, author, narrator,
series and number, publisher, year, language, description and (optionally) the cover.

The network calls run on a worker thread (redactor_common's call_in_background) so the window keeps
painting. Nothing is written anywhere from here.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from core.audiobook_lookup import (
    AUDIBLE_REGIONS, DEFAULT_REGION, AudiobookLookupError, BookMatch, download_cover, search,
)
from redactor_common.gui.background_call import call_in_background

COLUMNS = ["Source", "Title", "Author", "Narrator", "Series", "#", "Year", "Length"]
COVER_BOX = 160


def _length_text(minutes: int | None) -> str:
    return f"{minutes // 60}:{minutes % 60:02d}" if minutes else ""


class M4bLookupDialog(QDialog):
    def __init__(
        self, title: str, author: str, runtime_minutes: float | None, region: str = DEFAULT_REGION,
        fetch=None, parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Look Up Audiobook")
        self.resize(900, 620)
        self._runtime = runtime_minutes
        self._fetch = fetch
        self._matches: list[BookMatch] = []
        self._covers: dict[str, tuple[bytes, str] | None] = {}

        root = QVBoxLayout(self)
        form = QFormLayout()
        self.title_edit = QLineEdit(title)
        self.author_edit = QLineEdit(author)
        self.region_combo = QComboBox()
        for code in AUDIBLE_REGIONS:
            self.region_combo.addItem(f"audible.{code}", code)
        index = self.region_combo.findData(region)
        self.region_combo.setCurrentIndex(index if index >= 0 else self.region_combo.findData(DEFAULT_REGION))
        self.region_combo.setToolTip("Which Audible store to search. Open Library is asked too when Audible finds little.")
        form.addRow("Title:", self.title_edit)
        form.addRow("Author:", self.author_edit)
        search_row = QHBoxLayout()
        search_row.addWidget(self.region_combo)
        self.search_btn = QPushButton("Search")
        self.search_btn.setDefault(True)
        self.search_btn.clicked.connect(self._search)
        search_row.addWidget(self.search_btn)
        search_row.addStretch(1)
        form.addRow("Store:", search_row)
        root.addLayout(form)
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        header = self.table.horizontalHeader()
        for column in range(len(COLUMNS)):
            header.setSectionResizeMode(
                column,
                QHeaderView.ResizeMode.Stretch if column in (1, 2, 3, 4) else QHeaderView.ResizeMode.ResizeToContents,
            )
        self.table.itemSelectionChanged.connect(self._on_selected)
        self.table.itemDoubleClicked.connect(lambda _item: self.accept() if self.selected_match() else None)
        root.addWidget(self.table, 1)

        detail = QHBoxLayout()
        self.cover_label = QLabel("No cover")
        self.cover_label.setFixedSize(COVER_BOX, COVER_BOX)
        self.cover_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cover_label.setStyleSheet("border: 1px solid gray;")
        detail.addWidget(self.cover_label)
        self.description_view = QPlainTextEdit()
        self.description_view.setReadOnly(True)
        self.description_view.setPlaceholderText("The book's description appears here.")
        detail.addWidget(self.description_view, 1)
        root.addLayout(detail)

        self.cover_check = QCheckBox("Also use this result's cover")
        self.cover_check.setChecked(True)
        root.addWidget(self.cover_check)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.use_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.use_btn.setText("Use This Book")
        self.use_btn.setEnabled(False)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    # -- searching --------------------------------------------------------------

    def region(self) -> str:
        return str(self.region_combo.currentData())

    def _search(self) -> None:
        self.status_label.setText("Searching...")
        self.table.setRowCount(0)
        self._matches = []
        self.description_view.clear()
        self._show_cover(None)
        try:
            outcome = call_in_background(
                search, self.title_edit.text(), self.author_edit.text(), self._runtime, self._fetch, self.region()
            )
        except AudiobookLookupError as exc:
            self.status_label.setText(str(exc))
            return
        self._matches = outcome.matches
        self.table.setRowCount(len(outcome.matches))
        for row, match in enumerate(outcome.matches):
            values = [
                match.source, match.title, match.author_text, match.narrator_text, match.series, match.series_index,
                match.year, _length_text(match.runtime_minutes),
            ]
            for column, text in enumerate(values):
                item = QTableWidgetItem(text)
                if column == 7 and match.runtime_minutes and self._runtime:
                    gap = match.runtime_minutes - self._runtime
                    item.setToolTip(f"{gap:+.0f} minutes against your files ({_length_text(int(self._runtime))})")
                self.table.setItem(row, column, item)
        notes = " ".join(outcome.notes)
        if outcome.matches:
            self.status_label.setText(
                f"{len(outcome.matches)} result(s), best first (title, author and running time). " + notes
            )
            self.table.selectRow(0)
        else:
            self.status_label.setText("Nothing found. Change the title or author and search again. " + notes)

    # -- selection ----------------------------------------------------------------

    def selected_match(self) -> BookMatch | None:
        rows = self.table.selectionModel().selectedRows()
        return self._matches[rows[0].row()] if rows else None

    def _on_selected(self) -> None:
        match = self.selected_match()
        self.use_btn.setEnabled(match is not None)
        if match is None:
            return
        self.description_view.setPlainText(match.description or "(no description)")
        self._show_cover(self._cover_for(match))

    def _cover_for(self, match: BookMatch) -> tuple[bytes, str] | None:
        if match.cover_url not in self._covers:
            try:
                self._covers[match.cover_url] = call_in_background(download_cover, match.cover_url, self._fetch)
            except AudiobookLookupError:
                self._covers[match.cover_url] = None
        return self._covers[match.cover_url]

    def _show_cover(self, cover: tuple[bytes, str] | None) -> None:
        pixmap = QPixmap()
        if cover and pixmap.loadFromData(cover[0]):
            self.cover_label.setPixmap(pixmap.scaled(
                COVER_BOX, COVER_BOX, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation,
            ))
            self.cover_label.setText("")
        else:
            self.cover_label.setPixmap(QPixmap())
            self.cover_label.setText("No cover")

    # -- result ----------------------------------------------------------------------

    def chosen_cover(self) -> tuple[bytes, str] | None:
        """The selected result's cover when "Also use this result's cover" is ticked and one was found."""
        match = self.selected_match()
        if match is None or not self.cover_check.isChecked():
            return None
        return self._cover_for(match)

    def accept(self) -> None:
        if self.selected_match() is None:
            QMessageBox.information(self, "Look Up Audiobook", "Select a result first.")
            return
        super().accept()
