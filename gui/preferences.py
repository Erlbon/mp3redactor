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
from PyQt6.QtWidgets import QLabel, QMessageBox, QVBoxLayout, QWidget
from redactor_common.gui.conversion_policy_widget import ConversionPolicyWidget
from redactor_common.gui.preferences_dialog import PreferencesDialog

from core import conversion_safety, history, notes_cache, notes_debug, recycle
from core.audio_quality import DEFAULT_GOOD_KBPS, DEFAULT_POOR_KBPS, MAX_KBPS, MIN_KBPS, normalise_limits
from core.settings import Settings
from gui.external_tools_dialog import ToolPathsWidget

KEY_LIBRARY_ROOT = "library_root"
KEY_RECYCLE_FALLBACK = "recycle_fallback_folder"  # redactor_common.core.trash.RECYCLE_FALLBACK_FOLDER_KEY
KEY_DELETE_BACKUP = "delete_backup_after_fix"
KEY_HISTORY = "history_enabled"
KEY_NOTES_CACHE = "notes_cache_enabled"
KEY_NOTES_DEBUG = "notes_debug_log"
NOTES_DEBUG_LABEL = "Log the Create Notes list for troubleshooting"
DELETE_NOTES_LOG_LABEL = "Delete the troubleshooting log…"
KEY_NOTES_DISCOGS = "notes_discogs_always"
KEY_NOTES_SKIP_SINGLES = "notes_skip_singles"
KEY_NOTES_NORWEGIAN = "notes_norwegian_numbers"
KEY_BAND_POOR = "band_poor_kbps"
KEY_BAND_GOOD = "band_good_kbps"
NOTES_DISCOGS_LABEL = "Also ask Discogs for every artist (slower, adds members and profile)"
NOTES_SKIP_SINGLES_LABEL = "Leave singles out of the artist note's discography (faster: fewer requests for a big artist)"
NOTES_NORWEGIAN_LABEL = "Number words in Norwegian (Fire = 4, To = 2 ...) when matching titles"
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
    KEY_RECYCLE_FALLBACK: "recycle_fallback_folder",
    KEY_DELETE_BACKUP: "delete_backup_after_fix",
    KEY_HISTORY: "history_enabled",
    KEY_NOTES_CACHE: "notes_cache_enabled",
    KEY_NOTES_DEBUG: "notes_debug_log",
    KEY_NOTES_DISCOGS: "notes_discogs_always",
    KEY_NOTES_SKIP_SINGLES: "notes_skip_singles",
    KEY_NOTES_NORWEGIAN: "notes_norwegian_numbers",
    KEY_BAND_POOR: "band_poor_kbps",
    KEY_BAND_GOOD: "band_good_kbps",
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
            PrefSpec(
                KEY_RECYCLE_FALLBACK, "Backup folder for drives without a Recycle Bin", "path", "",
                help=recycle.BACKUP_FOLDER_HELP,
                path_mode="folder",
            ),
        )),
        PrefSection("table_colours", "Table colours", (
            PrefSpec(
                KEY_BAND_POOR, "Poor below (kbps)", "int", DEFAULT_POOR_KBPS,
                minimum=MIN_KBPS, maximum=MAX_KBPS - 1,
                help="The Bitrate column is yellow below this (MP3 bitrate; a VBR file is judged on its average). "
                     "A stereo file under 32 kHz is yellow too. Default 128.",
            ),
            PrefSpec(
                KEY_BAND_GOOD, "Good from (kbps)", "int", DEFAULT_GOOD_KBPS,
                minimum=MIN_KBPS + 1, maximum=MAX_KBPS,
                help="Only the wording of the tooltip and of the Convert to MP3 hint: good from about this, "
                     "transparent from about 256. Must be above the poor limit (it is raised to just above it "
                     "otherwise). Default 192.",
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
             PrefSpec(KEY_NOTES_NORWEGIAN, NOTES_NORWEGIAN_LABEL, "bool", False,
                      help="Off by default. English number words always count as the number ('Seven' = '7', 'Maroon Five' = "
                           "'Maroon 5'). The Norwegian ones that are also everyday English words (en, to, tre, fire, fem, ni, "
                           "ti) do not, or 'Fire' would match 'Four' and 'Born to Run' 'Born 2 Run'. Switch this on for a "
                           "Norwegian library so that 'Fire' = '4', 'To' = '2' and 'Ti' = '10' when titles are compared."),
             PrefSpec(KEY_NOTES_CACHE, NOTES_CACHE_LABEL, "bool", True, help=notes_cache_help()),
             PrefSpec(KEY_NOTES_DEBUG, NOTES_DEBUG_LABEL, "bool", False,
                      help="Off by default. When on, the review list of Create Notes writes what it does (the number of rows, "
                           "the scroll values, which row is current, the folder NAMES of the rows it changes: no file contents, "
                           "no notes) to mp3redactor_notes_debug.log next to the settings file, so that you can send it if the "
                           "list misbehaves. Also on with the environment variable MP3REDACTOR_NOTES_DEBUG=1."),),
            actions=(PrefAction(
                DELETE_NOTES_LOG_LABEL, delete_notes_log,
                help="Deletes the troubleshooting log at once. It is not a setting, so OK and Cancel do not undo it.",
            ), PrefAction(
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
        if KEY_BAND_POOR in values or KEY_BAND_GOOD in values:  # poor < good, 32..320
            settings.band_poor_kbps, settings.band_good_kbps = normalise_limits(
                settings.band_poor_kbps, settings.band_good_kbps
            )
        save(settings)
        if KEY_NOTES_DEBUG in values:
            notes_debug.set_enabled(settings.notes_debug_log)  # the running app follows at once
        if KEY_RECYCLE_FALLBACK in values:
            recycle.set_recycle_fallback_folder(settings.recycle_fallback_folder)  # the running app follows at once
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

    policy_holder: list[ConversionPolicyWidget] = []

    def conversion_page() -> QWidget:
        """Preferences > Conversion: the shared policy (redactor_common) on top. The app has no further conversion
        options here: the bitrate is asked at each conversion, and its colour limits live under Table colours."""
        page = QWidget()
        layout = QVBoxLayout(page)
        intro = QLabel(
            "Used by File > Import and Convert and by the offer Open Folder makes when a folder holds audio in "
            "other formats (FLAC, WAV, M4A...). Until you press OK or Apply here, the app behaves as before: it asks, "
            "and the original stays beside the new MP3. The bitrate is asked at each conversion."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        widget = ConversionPolicyWidget(
            conversion_safety.get_policy(settings), never_text="Open Folder does not offer other audio formats")
        policy_holder.append(widget)
        layout.addWidget(widget)
        layout.addStretch(1)
        return page

    dialog = PreferencesDialog(
        preference_sections(), settings_backend(settings, save), parent,
        title="Preferences", extra_pages=[("Conversion", conversion_page), ("Tools", tools_page)],
    )

    def save_tool_paths() -> None:
        if holder and holder[0].apply_to(settings):
            save(settings)

    def save_conversion() -> None:
        # Only a changed page is saved: pressing OK elsewhere leaves a never-saved policy "never saved" (legacy behaviour).
        if policy_holder and policy_holder[0].policy() != conversion_safety.get_policy(settings):
            conversion_safety.save_policy(settings, policy_holder[0].policy())
            save(settings)

    dialog.apply_button.clicked.connect(save_tool_paths)
    dialog.accepted.connect(save_tool_paths)
    dialog.apply_button.clicked.connect(save_conversion)
    dialog.accepted.connect(save_conversion)
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


def delete_notes_log(parent) -> None:
    """Preferences > Create Notes > Delete the troubleshooting log... (a PrefAction)."""
    if not notes_debug.log_exists():
        QMessageBox.information(parent, "Delete the troubleshooting log", "There is no troubleshooting log.")
        return
    problem = notes_debug.delete_log()
    QMessageBox.information(parent, "Delete the troubleshooting log", problem or "The troubleshooting log was deleted.")
