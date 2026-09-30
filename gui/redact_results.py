"""
gui/redact_results.py

The Redact results dialog: redactor_common's RedactResultsDialog plus what
this app needs to say around it -- that Redact is not on the in-app Undo
stack (the Recycle Bin copy of each original is the undo), and the notes
steps left behind ("mp3val not found", "lookup unavailable"), which the
engine's report has no place for.
"""

from __future__ import annotations

from PyQt6.QtWidgets import QLabel, QWidget

from redactor_common.core.pipeline import RedactReport
from redactor_common.gui.redact_dialog import RedactResultsDialog

UNDO_NOTE = (
    "Redact can't be undone with Undo. Each original file was sent to the Recycle Bin: "
    "restore it from there to go back."
)


class Mp3RedactResultsDialog(RedactResultsDialog):
    def __init__(self, report: RedactReport, notes: str = "", parent: QWidget | None = None):
        self._notes = notes.strip()
        super().__init__(report, parent)
        self.setToolTip(UNDO_NOTE)
        header = QLabel(UNDO_NOTE)
        header.setWordWrap(True)
        header.setToolTip(UNDO_NOTE)
        self.layout().insertWidget(0, header)
        self.header_label = header
        self.report_view.setPlainText(self.full_text())

    def full_text(self) -> str:
        """The engine's report plus the notes, as shown, copied and saved."""
        text = self.report.to_text()
        if self._notes:
            text += "\nNOTES\n-----\n" + self._notes + "\n"
        return text

    def copy_report(self) -> None:
        from PyQt6.QtWidgets import QApplication

        QApplication.clipboard().setText(self.full_text())

    def save_report_to(self, path: str) -> None:
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(self.full_text())
