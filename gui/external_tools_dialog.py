"""
"Locate External Tools" dialog: one row per external CLI binary, a Browse
button to point at it directly, a Clear button to go back to auto-detect,
and a live found/not-found indicator so the user can see immediately
whether the path (or auto-detection) actually resolves. The rows and the
page are redactor_common's ToolPathRow/ToolPathsWidget (commit="deferred":
nothing is stored until the dialog or Preferences is accepted); this file
keeps the tool list, the intro and the download links.

Also the Tools page of Preferences (ToolPathsWidget).
"""

from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QVBoxLayout,
)
from redactor_common.gui.tool_path_row import COMMIT_DEFERRED, ToolPathRow
from redactor_common.gui.tool_path_row import ToolPathsWidget as SharedToolPathsWidget

from core.acoustid_lookup import FPCALC_EXE_NAME
from core.ffmpeg_probe import FFMPEG_EXE_NAME, FFPROBE_EXE_NAME
from core.keyfinder_runner import KEYFINDER_EXE_NAME
from core.mp3val_runner import MP3VAL_EXE_NAME
from core.settings import Settings
from core.tool_locator import find_tool

# (row label, executable, Settings field), in the order shown.
TOOLS = (
    ("mp3val:", MP3VAL_EXE_NAME, "mp3val_path"),
    ("keyfinder-cli:", KEYFINDER_EXE_NAME, "keyfinder_cli_path"),
    ("ffmpeg:", FFMPEG_EXE_NAME, "ffmpeg_path"),
    ("ffprobe:", FFPROBE_EXE_NAME, "ffprobe_path"),
    ("fpcalc:", FPCALC_EXE_NAME, "fpcalc_path"),
)

INTRO = (
    "If mp3val, keyfinder-cli, ffmpeg, ffprobe or fpcalc (Chromaprint -- identifies songs by their "
    "sound in Look Up via MusicBrainz) aren't on your system "
    "PATH, point directly at their executables here. Leave blank to "
    "auto-detect via PATH (the default)."
)
DOWNLOADS_HTML = (
    "Downloads: "
    '<a href="https://acoustid.org/chromaprint">fpcalc (Chromaprint)</a> -- the zip for Windows '
    "contains fpcalc.exe &middot; "
    '<a href="https://github.com/Erlbon/keyfinder-cli-windows/releases/latest">keyfinder-cli</a> &middot; '
    '<a href="https://ffmpeg.org/download.html">ffmpeg / ffprobe</a> &middot; '
    '<a href="https://mp3val.sourceforge.net/">mp3val</a>'
)


def _tool_row(label: str, exe_name: str, initial: str) -> ToolPathRow:
    # The probe is this app's find_tool, which also looks in the bundled tools/ folder next to the exe.
    return ToolPathRow(
        label, exe_name, initial,
        probe=lambda path: find_tool(exe_name, override=path or None) is not None,
        commit=COMMIT_DEFERRED, read_only=True, missing_text="\u2717 not found",
    )


class ToolPathsWidget(SharedToolPathsWidget):
    """The locate-tools rows (intro, download links, one row per binary). Used by the
    Locate External Tools dialog and as the Tools page of Preferences."""

    def __init__(self, settings: Settings, parent=None) -> None:
        rows = [_tool_row(label, exe, getattr(settings, field)) for label, exe, field in TOOLS]
        super().__init__(rows, INTRO, links_html=DOWNLOADS_HTML, parent=parent)
        self._mp3val_row, self._keyfinder_row, self._ffmpeg_row, self._ffprobe_row, self._fpcalc_row = rows

    def result_paths(self) -> tuple[str, str, str, str, str]:
        """Returns (mp3val_path, keyfinder_cli_path, ffmpeg_path, ffprobe_path, fpcalc_path)."""
        return tuple(row.current_path() for row in self.rows)

    def apply_to(self, settings: Settings) -> bool:
        """Copies the paths into `settings`; True if any of them changed."""
        changed = False
        for (_label, _exe, field), value in zip(TOOLS, self.result_paths()):
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
