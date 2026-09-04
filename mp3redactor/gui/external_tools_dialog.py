"""
"Locate External Tools" dialog -- mirrors the video tool's ffmpeg/
MKVToolNix locator dialog: one row per external CLI binary, a Browse
button to point at it directly, a Clear button to go back to
auto-detect, and a live found/not-found indicator so the user can see
immediately whether the path (or auto-detection) actually resolves.

Distinct from SettingsDialog (the fix-backup toggle) -- this is purely
about locating binaries, same separation of concerns the video tool
uses.
"""

from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.mp3val_runner import MP3VAL_EXE_NAME
from core.settings import Settings
from core.tool_locator import find_tool

FOUND_STYLE = "color: #2e7d32;"  # green
NOT_FOUND_STYLE = "color: #c62828;"  # red

KEYFINDER_EXE_NAME = "keyfinder-cli.exe"


class _ToolRow:
    """One label + path field + Browse/Clear + found-indicator row."""

    def __init__(self, layout: QGridLayout, row: int, label: str, exe_name: str, initial_path: str):
        self.exe_name = exe_name

        layout.addWidget(QLabel(label), row, 0)

        self.path_edit = QLineEdit(initial_path)
        self.path_edit.setPlaceholderText("(auto-detect via PATH)")
        self.path_edit.setReadOnly(True)
        layout.addWidget(self.path_edit, row, 1)

        browse_button = QPushButton("Browse...")
        browse_button.clicked.connect(self._browse)
        layout.addWidget(browse_button, row, 2)

        clear_button = QPushButton("Clear")
        clear_button.clicked.connect(self._clear)
        layout.addWidget(clear_button, row, 3)

        self.status_label = QLabel()
        layout.addWidget(self.status_label, row, 4)

        self._refresh_status()

    def _browse(self) -> None:
        # Import here (not at module top) to keep QFileDialog usage
        # local to the one place it's needed in this small dialog.
        from PyQt6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getOpenFileName(
            self.path_edit.parentWidget(), f"Locate {self.exe_name}", "", "Executable (*.exe)"
        )
        if path:
            self.path_edit.setText(path)
            self._refresh_status()

    def _clear(self) -> None:
        self.path_edit.clear()
        self._refresh_status()

    def _refresh_status(self) -> None:
        override = self.path_edit.text().strip() or None
        found = find_tool(self.exe_name, override=override)
        if found is not None:
            self.status_label.setText("\u2713 found")
            self.status_label.setStyleSheet(FOUND_STYLE)
        else:
            self.status_label.setText("\u2717 not found")
            self.status_label.setStyleSheet(NOT_FOUND_STYLE)

    def current_path(self) -> str:
        return self.path_edit.text().strip()


class ExternalToolsDialog(QDialog):
    def __init__(self, settings: Settings, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Locate External Tools")
        self.resize(560, 160)

        header = QLabel(
            "If mp3val or keyfinder-cli aren't on your system PATH, point "
            "directly at their executables here. Leave blank to auto-detect "
            "via PATH (the default)."
        )
        header.setWordWrap(True)

        grid_widget = QWidget()
        grid = QGridLayout(grid_widget)
        self._mp3val_row = _ToolRow(grid, 0, "mp3val:", MP3VAL_EXE_NAME, settings.mp3val_path)
        self._keyfinder_row = _ToolRow(
            grid, 1, "keyfinder-cli:", KEYFINDER_EXE_NAME, settings.keyfinder_cli_path
        )

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Done")
        buttons.accepted.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.addWidget(header)
        layout.addWidget(grid_widget)
        layout.addStretch()
        button_row = QHBoxLayout()
        button_row.addStretch()
        button_row.addWidget(buttons)
        layout.addLayout(button_row)

    def result_paths(self) -> tuple[str, str]:
        """Returns (mp3val_path, keyfinder_cli_path)."""
        return self._mp3val_row.current_path(), self._keyfinder_row.current_path()
