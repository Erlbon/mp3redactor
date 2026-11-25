"""
gui/whitespace_dialog.py

Edit > Tidy Whitespace...: one row per tag value with stray whitespace
(core/whitespace.py finds them), the hidden characters made visible, a tick
per row. "Tidy ticked" accepts the dialog; the main window then applies the
ticked fixes in memory like Search and Replace (Save writes, Undo reverts).
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from core.whitespace import MULTILINE_KEYS, WhitespaceFix, show_whitespace
from redactor_common.gui.owned_dialog import OwnedDialog

LEGEND = (
    "Marks: · untidy space, → tab, ↵ line break, ⍽ non-breaking space, "
    "¤ zero-width character"
)
_INDEX_ROLE = Qt.ItemDataRole.UserRole


class TidyWhitespaceDialog(OwnedDialog):
    def __init__(self, fixes: list[WhitespaceFix], checked_files: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Tidy Whitespace")
        self.resize(900, 460)
        self._fixes = list(fixes)
        files = len({id(fix.item) for fix in self._fixes})
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            f"{checked_files} file(s) checked; {len(self._fixes)} value(s) in {files} file(s) need tidying. "
            "Nothing is written until you Save; Undo takes it back."
        ))
        legend = QLabel(LEGEND)
        legend.setWordWrap(True)
        layout.addWidget(legend)

        self.table = QTableWidget(len(self._fixes), 4, self)
        self.table.setHorizontalHeaderLabels(["File", "Field", "Now", "After"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        for row, fix in enumerate(self._fixes):
            multiline = fix.key in MULTILINE_KEYS
            name = QTableWidgetItem(getattr(fix.item, "filename", "") or str(getattr(fix.item, "path", "")))
            name.setFlags(name.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            name.setCheckState(Qt.CheckState.Checked)
            name.setData(_INDEX_ROLE, row)
            self.table.setItem(row, 0, name)
            self.table.setItem(row, 1, QTableWidgetItem(fix.label))
            self.table.setItem(row, 2, QTableWidgetItem(show_whitespace(fix.old, multiline)))
            self.table.setItem(row, 3, QTableWidgetItem(show_whitespace(fix.new, multiline)))
        self.table.resizeColumnsToContents()
        self.table.setSortingEnabled(True)  # rows carry their fix index, so sorting never desyncs
        self.table.itemChanged.connect(lambda _item: self._update_button())
        layout.addWidget(self.table, 1)

        buttons = QHBoxLayout()
        tick_all = QPushButton("Tick All")
        tick_all.clicked.connect(lambda: self._set_all(Qt.CheckState.Checked))
        untick_all = QPushButton("Untick All")
        untick_all.clicked.connect(lambda: self._set_all(Qt.CheckState.Unchecked))
        self.tidy_button = QPushButton()
        self.tidy_button.setDefault(True)
        self.tidy_button.clicked.connect(self.accept)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        buttons.addWidget(tick_all)
        buttons.addWidget(untick_all)
        buttons.addStretch(1)
        buttons.addWidget(self.tidy_button)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        self._update_button()

    def _set_all(self, state: Qt.CheckState) -> None:
        for row in range(self.table.rowCount()):
            self.table.item(row, 0).setCheckState(state)

    def _update_button(self) -> None:
        count = len(self.ticked_fixes())
        self.tidy_button.setText(f"Tidy Ticked ({count})")
        self.tidy_button.setEnabled(count > 0)

    def ticked_fixes(self) -> list[WhitespaceFix]:
        indexes = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item is not None and item.checkState() == Qt.CheckState.Checked:
                indexes.append(item.data(_INDEX_ROLE))
        return [self._fixes[index] for index in sorted(indexes)]
