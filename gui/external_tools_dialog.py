"""
"Locate External Tools" dialog -- mirrors the video tool's ffmpeg/
MKVToolNix locator dialog: one row per external CLI binary, a Browse
button to point at it directly, a Clear button to go back to
auto-detect, and a live found/not-found indicator so the user can see
immediately whether the path (or auto-detection) actually resolves.

Also the Tools page of Preferences (ToolPathsWidget).
"""

import sys

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

from core.acoustid_lookup import FPCALC_EXE_NAME
from core.ffmpeg_probe import FFMPEG_EXE_NAME, FFPROBE_EXE_NAME
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
            self.path_edit.parentWidget(), f"Locate {self.exe_name}", "",
            # Linux/Mac tools have no .exe: any file there.
            "Executable (*.exe)" if sys.platform == "win32" else "All files (*)",
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


class ToolPathsWidget(QWidget):
    """The locate-tools rows (header + one row per binary). Used by the
    Locate External Tools dialog and as the Tools page of Preferences."""

    def __init__(self, settings: Settings, parent=None) -> None:
        super().__init__(parent)
        header = QLabel(
            "If mp3val, keyfinder-cli, ffmpeg, ffprobe or fpcalc (Chromaprint -- identifies songs by their "
            "sound in Look Up via MusicBrainz) aren't on your system "
            "PATH, point directly at their executables here. Leave blank to "
            "auto-detect via PATH (the default)."
        )
        header.setWordWrap(True)

        grid_widget = QWidget()
        grid = QGridLayout(grid_widget)
        self._mp3val_row = _ToolRow(grid, 0, "mp3val:", MP3VAL_EXE_NAME, settings.mp3val_path)
        self._keyfinder_row = _ToolRow(
            grid, 1, "keyfinder-cli:", KEYFINDER_EXE_NAME, settings.keyfinder_cli_path
        )
        self._ffmpeg_row = _ToolRow(grid, 2, "ffmpeg:", FFMPEG_EXE_NAME, settings.ffmpeg_path)
        self._ffprobe_row = _ToolRow(grid, 3, "ffprobe:", FFPROBE_EXE_NAME, settings.ffprobe_path)
        self._fpcalc_row = _ToolRow(grid, 4, "fpcalc:", FPCALC_EXE_NAME, settings.fpcalc_path)

        layout = QVBoxLayout(self)
        layout.addWidget(header)
        layout.addWidget(grid_widget)
        layout.addStretch()

    def result_paths(self) -> tuple[str, str, str, str, str]:
        """Returns (mp3val_path, keyfinder_cli_path, ffmpeg_path, ffprobe_path, fpcalc_path)."""
        return (
            self._mp3val_row.current_path(),
            self._keyfinder_row.current_path(),
            self._ffmpeg_row.current_path(),
            self._ffprobe_row.current_path(),
            self._fpcalc_row.current_path(),
        )

    def apply_to(self, settings: Settings) -> bool:
        """Copies the paths into `settings`; True if any of them changed."""
        mp3val, keyfinder, ffmpeg, ffprobe, fpcalc = self.result_paths()
        new = {
            "mp3val_path": mp3val, "keyfinder_cli_path": keyfinder, "ffmpeg_path": ffmpeg,
            "ffprobe_path": ffprobe, "fpcalc_path": fpcalc,
        }
        changed = False
        for field, value in new.items():
            if getattr(settings, field) != value:
                setattr(settings, field, value)
                changed = True
        return changed


class ExternalToolsDialog(QDialog):
    def __init__(self, settings: Settings, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Locate External Tools")
        self.resize(560, 220)

        self._paths = ToolPathsWidget(settings)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Done")
        buttons.accepted.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.addWidget(self._paths)
        button_row = QHBoxLayout()
        button_row.addStretch()
        button_row.addWidget(buttons)
        layout.addLayout(button_row)

    def result_paths(self) -> tuple[str, str, str, str, str]:
        return self._paths.result_paths()
