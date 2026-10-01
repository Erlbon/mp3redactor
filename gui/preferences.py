"""
Preferences for the mp3 redactor, on redactor_common's shared PreferencesDialog.

Pages: Filenames (shared section), Library (library folder), Fixing (delete
mp3val .bak backups) and Tools (the same locate-tools rows as Tools > External
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
from redactor_common.gui.preferences_dialog import PreferencesDialog

from core.settings import Settings
from gui.external_tools_dialog import ToolPathsWidget

KEY_LIBRARY_ROOT = "library_root"
KEY_DELETE_BACKUP = "delete_backup_after_fix"

# Shared key -> Settings field. The app's own keys map to themselves.
FIELD_BY_KEY = {
    KEY_ASCII_FILENAMES: "ascii_filenames",
    KEY_ZERO_PAD_NUMBERS: "rename_zero_pad",
    KEY_ZERO_PAD_WIDTH: "rename_zero_pad_width",
    KEY_AUTO_NUMBER_PADDING: "auto_number_padding",
    KEY_LIBRARY_ROOT: "library_root",
    KEY_DELETE_BACKUP: "delete_backup_after_fix",
}


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
    ]


def settings_backend(settings: Settings, save: Callable[[Settings], None]) -> CallbackBackend:
    """Reads and writes the live Settings object (in place), saving once per write."""

    def get(key: str) -> object:
        return getattr(settings, FIELD_BY_KEY[key])

    def set_many(values: dict) -> None:
        for key, value in values.items():
            setattr(settings, FIELD_BY_KEY[key], value)
        save(settings)

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
    return dialog
