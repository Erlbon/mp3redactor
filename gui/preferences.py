"""
Preferences for the mp3 redactor, on redactor_common's shared PreferencesDialog.

Pages: Filenames (shared section), Library (library folder), Fixing (delete
mp3val .bak backups), History (the opt-in change history, core/history.py,
with a Delete History... PrefAction button) and Tools (the same locate-tools rows as Tools > External
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
    PrefAction,
    PrefSection,
    PrefSpec,
    filenames_section,
)
from PyQt6.QtWidgets import QMessageBox
from redactor_common.gui.preferences_dialog import PreferencesDialog

from core import history, notes_cache
from core.settings import Settings
from gui.external_tools_dialog import ToolPathsWidget

KEY_LIBRARY_ROOT = "library_root"
KEY_DELETE_BACKUP = "delete_backup_after_fix"
KEY_HISTORY = "history_enabled"
KEY_NOTES_CACHE = "notes_cache_enabled"
KEY_NOTES_DISCOGS = "notes_discogs_always"
KEY_NOTES_SKIP_SINGLES = "notes_skip_singles"
NOTES_DISCOGS_LABEL = "Also ask Discogs for every artist (slower, adds members and profile)"
NOTES_SKIP_SINGLES_LABEL = "Leave singles out of the artist note's discography (faster: fewer requests for a big artist)"
NOTES_CACHE_LABEL = "Keep the answers of the Create Notes lookups on disk (a repeated or resumed run is then instant)"
CLEAR_NOTES_CACHE_LABEL = "Clear the notes lookup cache…"
HISTORY_LABEL = "Keep a history of tag changes (lets you undo a Save or Redact later)"
DELETE_HISTORY_LABEL = "Delete History…"

# Shared key -> Settings field. The app's own keys map to themselves.
FIELD_BY_KEY = {
    KEY_ASCII_FILENAMES: "ascii_filenames",
    KEY_ZERO_PAD_NUMBERS: "rename_zero_pad",
    KEY_ZERO_PAD_WIDTH: "rename_zero_pad_width",
    KEY_AUTO_NUMBER_PADDING: "auto_number_padding",
    KEY_LIBRARY_ROOT: "library_root",
    KEY_DELETE_BACKUP: "delete_backup_after_fix",
    KEY_HISTORY: "history_enabled",
    KEY_NOTES_CACHE: "notes_cache_enabled",
    KEY_NOTES_DISCOGS: "notes_discogs_always",
    KEY_NOTES_SKIP_SINGLES: "notes_skip_singles",
}


def history_help() -> str:
    return (
        "Off by default. When on, every Save, Save All, Redact and command-line change records the old and "
        "new text of each changed tag field (never covers or other images), so Tools > History can put the "
        "old values back later. It keeps the last 90 days or 50,000 changes, whichever is less, in "
        f"{history.journal_path()}. You can switch it off again at any time; Delete History sends the file to the Recycle Bin."
    )


def notes_cache_help() -> str:
    return (
        "On by default. Tools > Create Notes keeps the answers of MusicBrainz, Wikipedia and Discogs for 30 days "
        "(one day for 'not found' and empty results; errors never) in "
        f"{notes_cache.cache_path()}, at most 200 MB, so repeating or resuming a run does not ask the services "
        "again. It holds only those public answers and the artist and album names that were looked up, nothing "
        "else about you; it is never stored in a library folder. Switch it off to ask every time."
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
        PrefSection(
            "history", "History",
            (PrefSpec(KEY_HISTORY, HISTORY_LABEL, "bool", False, help=history_help()),),
            actions=(PrefAction(
                DELETE_HISTORY_LABEL, delete_history,
                help="Sends the history file to the Recycle Bin at once (asks first). It is not a setting, "
                     "so OK and Cancel do not undo it.",
            ),),
        ),
        PrefSection(
            "notes", "Create Notes",
            (PrefSpec(KEY_NOTES_DISCOGS, NOTES_DISCOGS_LABEL, "bool", False,
                      help="Off by default: Discogs is asked only when MusicBrainz has no verified match for an artist "
                           "(or an album), or when Wikipedia has no introduction (then for the profile and the members). "
                           "Switch it on to ask Discogs for every artist: slower (3 more requests per artist at about one "
                           "per second, run side by side with MusicBrainz). Needs the Discogs token (Tools > API Keys)."),
             PrefSpec(KEY_NOTES_SKIP_SINGLES, NOTES_SKIP_SINGLES_LABEL, "bool", False,
                      help="Off by default: the artist note lists the FULL MusicBrainz discography. Every 100 release groups "
                           "cost one request, so an artist with hundreds of singles costs several; with this on only the "
                           "albums and EPs (compilations, live albums and soundtracks included) are asked for, which saves "
                           "requests only for such artists (an artist with fewer than 100 release groups costs the same). "
                           "The note then has no Singles section."),
             PrefSpec(KEY_NOTES_CACHE, NOTES_CACHE_LABEL, "bool", True, help=notes_cache_help()),),
            actions=(PrefAction(
                CLEAR_NOTES_CACHE_LABEL, clear_notes_cache,
                help="Deletes every stored answer at once (asks first). It is not a setting, so OK and Cancel "
                     "do not undo it.",
            ),),
        ),
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
    return dialog


def delete_history(parent) -> None:
    """Preferences > History > Delete History... (a PrefAction): asks, then
    sends the journal file to the Recycle Bin at once (it is not a setting,
    so OK/Cancel do not apply)."""
    if not history.journal_exists():
        QMessageBox.information(parent, "Delete History", "There is no history to delete.")
        return
    answer = QMessageBox.question(
        parent, "Delete History",
        f"Delete the whole history of tag changes?\n{history.journal_path()}\n\n"
        "The file goes to the Recycle Bin; the old values in it can then no longer be restored from Tools > History.",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
    )
    if answer != QMessageBox.StandardButton.Yes:
        return
    problem = history.delete_journal()
    if problem:
        QMessageBox.warning(parent, "Delete History", f"Not deleted: {problem}")
    else:
        QMessageBox.information(parent, "Delete History", "The history was moved to the Recycle Bin.")


def clear_notes_cache(parent) -> None:
    """Preferences > Create Notes > Clear the notes lookup cache... (a PrefAction): asks, then empties the cache."""
    path = notes_cache.cache_path()
    if not path.exists():
        QMessageBox.information(parent, "Clear the notes lookup cache", "There is nothing in the cache.")
        return
    answer = QMessageBox.question(
        parent, "Clear the notes lookup cache",
        f"Delete every stored answer of the Create Notes lookups?\n{path}\n\n"
        "The next lookups ask MusicBrainz, Wikipedia and Discogs again.",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
    )
    if answer != QMessageBox.StandardButton.Yes:
        return
    count = notes_cache.clear_default_cache()
    QMessageBox.information(parent, "Clear the notes lookup cache", f"Removed {count} stored answer(s).")
