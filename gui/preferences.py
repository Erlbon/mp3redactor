"""
Preferences for the mp3 redactor, on redactor_common's shared PreferencesDialog.

Pages: Filenames (shared section), Library (library folder), Fixing (delete
mp3val .bak backups), History (the opt-in change history, core/history.py,
with a Delete History button) and Tools (the same locate-tools rows as Tools > External
Tools, as an extra page).

Storage is unchanged: the shared keys map onto the existing Settings fields
(same ini keys as before), and the backend edits the live Settings object in
place and saves once per OK/Apply.
"""

from __future__ import annotations

from typing import Callable

from redactor_common.core.preferences import (
    KEY_ASCII_FILENAMES,
    KEY_AUTO_NUMBER_PADDING,
    KEY_ZERO_PAD_NUMBERS,
    KEY_ZERO_PAD_WIDTH,
    CallbackBackend,
    PrefSection,
    PrefSpec,
    filenames_section,
)
from PyQt6.QtWidgets import QHBoxLayout, QMessageBox, QPushButton
from redactor_common.gui.preferences_dialog import PreferencesDialog

from core import history
from core.settings import Settings
from gui.external_tools_dialog import ToolPathsWidget

KEY_LIBRARY_ROOT = "library_root"
KEY_DELETE_BACKUP = "delete_backup_after_fix"
KEY_HISTORY = "history_enabled"
HISTORY_LABEL = "Keep a history of tag changes (lets you undo a Save or Redact later)"

# Shared key -> Settings field. The app's own keys map to themselves.
FIELD_BY_KEY = {
    KEY_ASCII_FILENAMES: "ascii_filenames",
    KEY_ZERO_PAD_NUMBERS: "rename_zero_pad",
    KEY_ZERO_PAD_WIDTH: "rename_zero_pad_width",
    KEY_AUTO_NUMBER_PADDING: "auto_number_padding",
    KEY_LIBRARY_ROOT: "library_root",
    KEY_DELETE_BACKUP: "delete_backup_after_fix",
    KEY_HISTORY: "history_enabled",
}


def history_help() -> str:
    return (
        "Off by default. When on, every Save, Save All, Redact and command-line change records the old and "
        "new text of each changed tag field (never covers or other images), so Tools > History can put the "
        "old values back later. It keeps the last 90 days or 50,000 changes, whichever is less, in "
        f"{history.journal_path()}. You can switch it off again at any time; Delete History removes the file."
    )


def preference_sections() -> list[PrefSection]:
    return [
        # mp3 stores 0-99 digits (0 = no padding), wider than the shared 1-9.
        filenames_section(width_min=0, width_max=99),
        PrefSection("library", "Library", (
            PrefSpec(
                KEY_LIBRARY_ROOT, "Library folder", "path", "",
                help="The top folder of your music library. Rename and Export by "
                     "pattern start from it. Leave blank for none.",
                path_mode="folder",
            ),
        )),
        PrefSection("fixing", "Fixing", (
            PrefSpec(
                KEY_DELETE_BACKUP, "Delete .bak backup files automatically after a successful fix", "bool", False,
                help="Off by default: mp3val keeps a .bak copy of each original file when "
                     "fixing integrity issues. Turn this on to delete those backups once a fix completes.",
            ),
        )),
        PrefSection("history", "History", (
            PrefSpec(KEY_HISTORY, HISTORY_LABEL, "bool", False, help=history_help()),
        )),
    ]


def settings_backend(settings: Settings, save: Callable[[Settings], None]) -> CallbackBackend:
    """Reads and writes the live Settings object (in place), saving once per write."""

    def get(key: str) -> object:
        return getattr(settings, FIELD_BY_KEY[key])

    def set_many(values: dict) -> None:
        for key, value in values.items():
            setattr(settings, FIELD_BY_KEY[key], value)
        save(settings)
        if KEY_HISTORY in values:
            history.set_history_enabled(settings.history_enabled)  # the running app follows at once

    return CallbackBackend(get, set_many)


def build_preferences_dialog(settings: Settings, save: Callable[[Settings], None], parent=None) -> PreferencesDialog:
    """The dialog; tool paths are written to `settings` (and saved) on OK and on Apply."""
    holder: list[ToolPathsWidget] = []

    def tools_page() -> ToolPathsWidget:
        widget = ToolPathsWidget(settings)
        holder.append(widget)
        return widget

    dialog = PreferencesDialog(
        preference_sections(), settings_backend(settings, save), parent,
        title="Preferences", extra_pages=[("Tools", tools_page)],
    )

    def save_tool_paths() -> None:
        if holder and holder[0].apply_to(settings):
            save(settings)

    dialog.apply_button.clicked.connect(save_tool_paths)
    dialog.accepted.connect(save_tool_paths)
    _add_delete_history_button(dialog)
    return dialog


def _add_delete_history_button(dialog: PreferencesDialog) -> None:
    """A Delete History button under the History checkbox: asks, then removes
    the journal file at once (it is not a setting, so OK/Cancel do not apply)."""
    container = dialog.control(KEY_HISTORY).parentWidget()
    button = QPushButton("Delete History…")
    button.setToolTip("Removes the history file (asks first).")

    def delete() -> None:
        if not history.journal_exists():
            QMessageBox.information(dialog, "Delete History", "There is no history to delete.")
            return
        answer = QMessageBox.question(
            dialog, "Delete History",
            f"Delete the whole history of tag changes?\n{history.journal_path()}\n\n"
            "The old values in it can then no longer be restored.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        problem = history.delete_journal()
        if problem:
            QMessageBox.warning(dialog, "Delete History", f"Not deleted: {problem}")
        else:
            QMessageBox.information(dialog, "Delete History", "The history was deleted.")

    button.clicked.connect(delete)
    dialog.delete_history_button = button
    row = QHBoxLayout()
    row.setContentsMargins(22, 4, 0, 0)
    row.addWidget(button)
    row.addStretch(1)
    container.layout().addLayout(row)
