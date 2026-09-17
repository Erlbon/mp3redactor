"""
gui/lyrics_dialog.py

Per-file lyrics editor -- Edit Lyrics... (double-click a file's Lyrics
cell, or the table's right-click menu / Operations menu). A dedicated
dialog rather than a core.fields.FIELDS row in the bulk-edit tag panel
(gui/tag_panel.py): full song lyrics are long, multi-line text, and
that panel's fields are all single-line editors (even Comment, despite
FIELDS' own "multiline" flag never actually being read by anything) --
exactly the shape ID3's own USLT frame, and every real tagger (mp3tag
included), already treats lyrics as, never a single-line field.

Fetch from LRCLIB (core.lyrics_fetcher.fetch_lyrics(), no API key
needed) blocks the UI briefly for one network round trip -- deliberately
NOT threaded the way the bulk "Fetch Lyrics for Selected Files" action
is (core.scan_service.run_lyrics_fetch()): a single request is fast
enough that a QProgressDialog/thread would be more machinery than the
wait actually warrants, same judgment call the Rename dialog already
makes for its own single-file disk operation.

Save writes the edited text into the in-memory MP3File and marks it
dirty (does not touch disk itself -- that's the normal Save flow,
core.tag_writer._write_lyrics_frame()); MainWindow.open_lyrics_dialog()
owns pushing this onto the undo stack, same as every other in-memory
tag edit.
"""

from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from core.lyrics_fetcher import build_query, fetch_lyrics
from core.mp3_file import MP3File, STATUS_OK


class LyricsDialog(QDialog):
    def __init__(self, mp3: MP3File, parent=None):
        super().__init__(parent)
        self.mp3 = mp3
        self.setWindowTitle(f"Lyrics -- {mp3.filename}")
        self.resize(560, 640)

        layout = QVBoxLayout(self)

        header = QLabel(f"<b>{mp3.artist or '(no artist)'}</b> – {mp3.title or mp3.filename}")
        header.setWordWrap(True)
        layout.addWidget(header)

        self.text_edit = QTextEdit()
        self.text_edit.setPlainText(mp3.lyrics)
        self.text_edit.setAcceptRichText(False)
        layout.addWidget(self.text_edit, 1)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("color: gray; font-size: 11px;")
        layout.addWidget(self.status_label)
        self._set_initial_status()

        button_row = QHBoxLayout()
        fetch_btn = QPushButton("Fetch from LRCLIB")
        fetch_btn.clicked.connect(self._fetch)
        button_row.addWidget(fetch_btn)
        button_row.addStretch(1)
        layout.addLayout(button_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _set_initial_status(self) -> None:
        if self.mp3.lyrics:
            self.status_label.setText("Loaded from this file's existing tag.")
        elif self.mp3.lyrics_message:
            self.status_label.setText(self.mp3.lyrics_message)

    def _fetch(self) -> None:
        query = build_query(self.mp3)
        self.status_label.setText(f'Searching LRCLIB for "{query}"...')
        QApplication.processEvents()

        lyrics, status, message = fetch_lyrics(query)
        if status == STATUS_OK:
            self.text_edit.setPlainText(lyrics)
            self.status_label.setText(f'Found lyrics for "{query}".')
        else:
            self.status_label.setText(message or "No lyrics found.")
            QMessageBox.information(self, "No Lyrics Found", message or "No lyrics found.")

    def result_lyrics(self) -> str:
        return self.text_edit.toPlainText().strip()
