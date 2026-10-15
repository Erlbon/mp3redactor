"""
MainWindow: bulk-edit tag panel (left) + file table (right) in a
collapsible QSplitter, menu+toolbar for the v1 actions (Open
Files/Folder, bulk tag editing, Check Integrity, Detect BPM, Detect
Key). Tag editing is deliberately "basic" -- plain text fields only,
via core.fields.FIELDS -- no covers or external lookups the way the
epub tool's fuller tag panel has; those don't have an obvious MP3-tag
equivalent yet. Genre and Language do get the family's quick-pick "+"
button (core.fields.QUICK_PICK_FIELDS) plus Tools > Genres.../Languages... management dialogs.

The table's columns are field-key based (redactor_common.core.
table_settings), not index-based -- drag a header to reorder,
right-click a header for a show/hide checklist or "Add/Remove
Columns...", and both order and visibility persist across restarts
via core/settings.py. Column layout, row-to-file mapping via
Qt.UserRole (not list index -- a sort or filter must never desync the
row from the object it displays, same lesson as the epub tool), and
the progress-dialog pattern for long-running scans all follow the
sibling projects' conventions -- menu bar, progress dialogs,
About/Changelog, the bulk-edit panel's collapsible splitter, and now
column/genre/language management are all built on redactor_common,
the same shared package the epub and video tools use, rather than
reimplementing these independently the way this project originally
did.

Menu shape is the family's standard skeleton (redactor_common's
gui/standard_menus.py): File / Edit / View / Metadata / Analyze / Tools /
Help. Shared actions use the canonical labels and shortcuts; Metadata
holds Parse Filename, Look Up (MusicBrainz, Lyrics), lyrics/track
numbering and Cover, Analyze the integrity/BPM/key/loudness checks, and
every setting lives under Tools. The right-click menu is a short core
plus Look Up / Organize / Analyze / Cover submenus.
Both pattern-based dialogs (redactor_common.gui.rename_pattern_dialog /
parse_filename_dialog) were already generic, ready-to-consume modules
there -- this project just hadn't wired them in yet, unlike epub/cbz.
"""

import dataclasses
import os
import shutil
from pathlib import Path

from PyQt6.QtCore import QItemSelection, QItemSelectionModel, QSize, Qt, QTimer
from PyQt6.QtGui import QColor, QIcon, QImage
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QFileDialog,
    QHeaderView,
    QInputDialog,
    QMainWindow,
    QMessageBox,
    QSizePolicy,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QToolBar,
    QWidget,
)

from core.app_paths import asset_path
from core.cover_art import extension_for, find_folder_image
from core.fields import FIELDS, filename_values
from core.mp3_file import (
    MP3File,
    STATUS_ERROR,
    STATUS_OK,
    STATUS_TOOL_ERROR,
    STATUS_TOOL_MISSING,
    STATUS_UNCHECKED,
    STATUS_WARNING,
    scan_display,
    scan_tooltip,
)
from core.m4b_builder import build_m4b, group_by_folder
from core.mp3_converter import (
    BITRATE_CHOICES_KBPS, DEFAULT_BITRATE_KBPS, IMPORTABLE_EXTENSIONS, plan_conversions,
)
from core.mp3_genres import COMMON_MP3_GENRES
from core.mp3_genres import exclude_hidden as exclude_hidden_genres
from core.mp3_genres import merge_genres
from core.mp3_languages import DEFAULT_LANGUAGES
from core.mp3_languages import exclude_hidden as exclude_hidden_languages
from core.mp3_languages import merge_languages
from core.redact_steps import (
    FINALIZE_LABEL,
    Mp3Ctx,
    RedactEnv,
    build_catalogue,
    pin_patterns,
    recipe_from_setting,
    recipe_to_setting,
    save_stage,
)
from core.scan_service import (
    find_mp3_files,
    load_files,
    run_bpm_check,
    run_deep_check,
    run_import_conversion,
    run_integrity_check,
    run_integrity_fix,
    run_key_detection,
    run_loudness_measurement,
    run_lyrics_fetch,
    reload_from_disk,
    save_dirty_tags,
)
from core.history import (
    apply_restore,
    history_enabled,
    journal_exists,
    journal_for_dialog,
    make_rename_log,
    set_history_enabled,
    take_journal_problem,
)
from redactor_common.gui.history_dialog import HistoryDialog
from core.settings import (
    Settings,
    dedupe_and_trim_pattern_history,
    load_settings,
    save_settings,
)
from redactor_common.gui.qsettings_store import directory_for, resolve_start_directory
from core.settings_adapter import Mp3SettingsAdapter
from core.version import APP_NAME, APP_REPO_URL, APP_VERSION, RELEASE_LABEL
from gui.external_tools_dialog import ExternalToolsDialog
from gui.lyrics_dialog import LyricsDialog
from gui.m4b_batch_dialog import M4bBatchDialog
from gui.m4b_dialog import M4bDialog
from gui.notes_dialog import ask_scope as ask_notes_scope
from gui.notes_dialog import create_notes
from core.notes_flow import live_fetchers, selection_scope
from core.notes_cache import default_cache
from core.notes_local import ALL_KINDS
from core.notes_kinds import parse_kinds
from core.notes_scope import scope_choices
from gui.preferences import build_preferences_dialog
from gui.tag_panel import TagPanel
from redactor_common.core import secret_store
from redactor_common.core.temp_names import is_app_temp_name
from redactor_common.gui.convert_offer import YES as CONVERT_YES
from redactor_common.gui.convert_offer import find_convertible, offer_conversion
from redactor_common.core.error_summary import summarize_errors
from redactor_common.core.folder_refresh import find_new_files_in_loaded_folders
from redactor_common.core.table_settings import is_column_visible, merge_column_order, sanitize_hidden_fields
from redactor_common.core.undo import UndoManager
from redactor_common.gui.async_icon_cache import AsyncIconCache, IdentityWeakDict
from redactor_common.gui.async_preview import AsyncPreviewLoader
from redactor_common.gui.visible_rows import VisibleRowsWatcher
from gui.cover_image import IMAGE_FILE_FILTER, CoverImageError, cover_from_file
from redactor_common.gui.about_dialog import AboutDialog, ChangelogDialog, CreditsDialog
from redactor_common.gui.action_factory import make_action
from gui.auto_number_presets import AutoNumberingDialog
from redactor_common.gui.collapsible_splitter import SplitterPaneCollapser
from redactor_common.gui.colors import DIRTY_COLOR, HIGHLIGHT_TEXT_COLOR, TABLE_SELECTION_STYLESHEET
from redactor_common.gui.column_menu import show_column_header_context_menu
from redactor_common.gui.column_settings_dialog import ColumnSettingsDialog
from redactor_common.gui.manage_list_dialog import ManageListDialog
from redactor_common.core import labels
from redactor_common.gui.menu_builder import MenuAction, Separator, Submenu
from redactor_common.gui.standard_menus import (
    AppMenu,
    StandardMenuSpec,
    build_standard_menu_bar,
    look_up_submenu,
    set_apply_count,
    standard_edit_items,
    standard_file_items,
    standard_help_items,
    standard_tools_items,
    standard_view_items,
)
from redactor_common.gui.command_palette import add_command_palette
from redactor_common.gui.case_conversion_dialog import CaseConversionDialog
from redactor_common.gui.search_replace_dialog import FILENAME_FIELD_KEY, SearchReplaceDialog
from redactor_common.core.os_utils import rename_no_clobber
from redactor_common.core.rename_pattern import unique_path, validate_filename_stem
from redactor_common.gui.context_menu import show_table_context_menu
from redactor_common.core.path_parser import split_pattern_history
from redactor_common.gui.parse_filename_dialog import ParseFilenameDialog
from redactor_common.gui.folder_options import (
    add_include_subfolders_checkbox, attach_include_subfolders, include_subfolders_slot,
)
from redactor_common.gui.progress import run_busy, run_with_progress
from redactor_common.gui.work_progress import run_work_with_progress
from redactor_common.gui.redact_dialog import (
    RecipeEditorDialog,
    RedactResultsDialog,
)
from redactor_common.gui.redact_dialog import run_redact as run_redact_dialog
from redactor_common.gui.move_runner import run_planned_moves
from redactor_common.gui.quick_pick_dialog import QuickPickDialog
from redactor_common.core.rename_log import RenameLog
from redactor_common.core.duplicates import JsonDismissStore
from redactor_common.core.trash import TrashError, move_to_trash
from redactor_common.gui.duplicates_dialog import run_find_duplicates
from core import mp3_duplicates
from redactor_common.gui.rename_undo import undo_last_rename
from redactor_common.gui.settings_bundle_dialogs import export_settings, import_settings
from core.app_paths import base_dir
from redactor_common.gui.rename_pattern_dialog import RenamePatternDialog
from redactor_common.gui.rename_single_file import rename_single_file as prompt_rename_single_file
from redactor_common.gui.sortable_table import NumericTableWidgetItem, suspend_sorting
from redactor_common.gui import standard_shortcuts as shortcuts
from redactor_common.gui.zoom_toolbar import TableZoomController
from redactor_common.core.version import REDACTOR_COMMON_REPO_URL, REDACTOR_COMMON_VERSION

# Table columns, field-key based -- see redactor_common.core.
# table_settings's own docstring for why (a persisted index-based
# preference silently breaks the moment a column is added/removed/
# reordered in code). "integrity"/"bpm"/"key"/"deep_check"/"loudness"/
# "lyrics" are synthetic (derived check results, or -- for lyrics --
# a field deliberately outside core.fields.FIELDS, see MP3File.lyrics'
# own docstring); everything else is exactly core.fields.FIELDS.
_STATUS_COLUMN_LABELS: dict[str, str] = {
    "integrity": "Integrity", "bpm": "BPM", "key": "Key",
    "deep_check": "Deep Check", "loudness": "Loudness", "lyrics": "Lyrics",
    "cover": "Cover",
}
# Format details from ffprobe (core.scan_service.run_deep_check(), same
# action that populates deep_check above) -- kept as their own group
# since, unlike every other column, these default to HIDDEN (see
# DEFAULT_HIDDEN_COLUMNS below): supplementary technical detail, not a
# check result someone would want in front of them by default.
_PROBE_COLUMN_LABELS: dict[str, str] = {
    "encoder": "Encoder", "sample_rate": "Sample Rate", "channels": "Channels",
}
_COLUMN_LABELS: dict[str, str] = {
    "filename": "Filename",
    "path": "Path",
    **{key: label for key, label, _m in FIELDS},
    **_STATUS_COLUMN_LABELS,
    **_PROBE_COLUMN_LABELS,
}
ALL_COLUMN_KEYS: list[str] = (
    ["filename", "path"]
    + [key for key, _l, _m in FIELDS]
    + list(_STATUS_COLUMN_LABELS)
    + list(_PROBE_COLUMN_LABELS)
)
PROTECTED_COLUMNS = frozenset({"filename"})  # the one column you always need to tell rows apart

# Nothing hidden on a genuinely first run for every column EXCEPT the
# ffprobe format-detail trio above and mp3tag's own "advanced" field
# set (core/fields.py's 2026-09-13 note) -- matches this app's own
# behavior before column management existed for every other column
# (shown by default), but these are a deliberate exception:
# supplementary detail most people won't want cluttering the table
# until they go looking for it (Tools > Columns..., or
# right-click a header, same as any other column). Once the user has
# saved ANY choice, even "show everything" (an empty hidden set), that
# saved choice always wins over this default -- see
# Settings.has_column_preference's own docstring.
DEFAULT_HIDDEN_COLUMNS: frozenset[str] = frozenset({
    "encoder", "sample_rate", "channels",
    "albumsort", "artistsort", "albumartistsort",
    "acoustid_fingerprint", "itunesadvisory",
    "musicbrainz_albumid", "musicbrainz_trackid",
    "publisher", "catalognumber", "releasecountry",
})

# Metadata fields offered as %placeholder% tokens in Rename/Export by
# Pattern and Parse Filename -> Metadata (both redactor_common dialogs,
# see open_rename_dialog()/open_parse_filename_dialog() below) -- the
# exact same set as core.fields.FIELDS, so the table columns, the
# bulk-edit panel, and these filename placeholders never drift out of
# sync with each other.
FILENAME_PLACEHOLDERS: list[tuple[str, str]] = [(key, label) for key, label, _m in FIELDS]
# Fields Parse Filename should extract/coerce as numbers rather than
# leaving as free-text strings, and that Rename/Export's zero-pad
# checkbox can apply to (see redactor_common.core.rename_pattern.
# zero_pad_numeric_value) -- Track/Disc Number ("3" -> "03") are the
# fields that actually benefit; Year is 4 digits already and never
# needs padding, but is still worth parsing as numeric-shaped rather
# than arbitrary text.
NUMERIC_FILENAME_FIELDS: frozenset[str] = frozenset({"track", "discnumber", "year"})
DEFAULT_RENAME_PATTERN = "%track% - %artist% - %title%"

# Shown at the top of the Redact results: Redact isn't on the in-app Undo stack.
REDACT_UNDO_NOTE = (
    "Redact can't be undone with Undo. The original of each changed file was sent to the Recycle Bin: "
    "restore it from there to go back. Where the Recycle Bin is not available (a network share or "
    "removable drive) the original was kept beside the file as <name>.redact-orig.mp3 instead; such files "
    "are named in the notes below. Only when the Integrity step's 'Fix problems' option is on (it is off by "
    "default) and a file was fixed, mp3val's backup is also left beside it as <name>.mp3.bak, unless Tools > "
    "Preferences > Fixing deletes .bak files automatically. Leftover .redact-orig.mp3 and .bak files are plain "
    "copies of the original: delete them in Explorer once you are happy with the result."
)

# Help > About shows this file (bundled by mp3redactor.spec).
ABOUT_FILE = "ABOUT.md"

# Cover column thumbnail size -- same as the sibling apps' table covers.
COVER_ICON_SIZE = QSize(24, 32)
# The side panel's cover is decoded no larger than this (2x a wide
# panel, for high-DPI screens) -- embedded art is often 1000-3000 px.
COVER_PREVIEW_SIZE = QSize(880, 880)

STATUS_COLORS = {
    STATUS_OK: Qt.GlobalColor.darkGreen,
    STATUS_WARNING: Qt.GlobalColor.darkYellow,
    STATUS_ERROR: Qt.GlobalColor.red,
    STATUS_TOOL_MISSING: Qt.GlobalColor.gray,
    # Orange: distinct from a WARNING verdict (yellow) and an ERROR verdict
    # (red) -- the tool failed, the file was never judged.
    STATUS_TOOL_ERROR: QColor("darkorange"),
}

TAG_PANEL_COLLAPSED_WIDTH = 32
TAG_PANEL_DEFAULT_WIDTH = 300



def _drop_keys(items: list, keys: set[str]) -> list:
    """Copies menu `items` without the actions whose key is in `keys`; a
    separator left doubled or dangling is not possible here because the
    dropped action always sits beside another in its group."""
    return [i for i in items if not (isinstance(i, MenuAction) and i.key in keys)]


def file_matches_filter(mp3: MP3File, needle: str) -> bool:
    """Edit > Filter List: does `needle` (already casefolded) occur in the
    file's Filename, Title, Artist, Album or Path?"""
    haystack = (mp3.filename, mp3.title, mp3.artist, mp3.album, str(mp3.path))
    return any(needle in (value or "").casefold() for value in haystack)


def refresh_summary(new_count: int, removed_count: int, loaded: int, wanted: int, finished: bool) -> str:
    """View > Refresh List's status-bar line (a routine result: no box to click away)."""
    if not finished:
        return f"Refresh cancelled: {loaded} of {wanted} file(s) reloaded; the rest left the list until the next refresh."
    found = f"Found {new_count} new file(s) and reloaded the rest." if new_count else "No new files; reloaded everything from disk."
    gone = f" {removed_count} file(s) no longer on disk left the list." if removed_count else ""
    return found + gone


def rename_file_refusal(selected: int, unreadable: bool) -> str:
    """Why File > Rename File (F2) did nothing, for the status bar."""
    if selected == 0:
        return "Rename File: select the file to rename first."
    if selected > 1:
        return f"Rename File renames one file; {selected} are selected. Use Rename / Export / Move (Ctrl+E) for several."
    if unreadable:
        return "Rename File: this file could not be read, so it is not renamed here."
    return ""


def command_line_paths(args: list[str]) -> tuple[list[Path], list[str]]:
    """The files/folders to open from `mp3redactor.exe PATH...` (main.py has already
    sent command names to the command line): (paths that exist, ones that do not).
    Options (anything starting with "-") are Qt's own and left alone."""
    found: list[Path] = []
    missing: list[str] = []
    for arg in args:
        if not arg or arg.startswith("-"):
            continue
        if os.path.exists(arg):
            found.append(Path(arg))
        else:
            missing.append(arg)
    return found, missing


def open_paths_at_start(window, args: list[str]) -> None:
    """main.py: opens the command line's paths once the event loop runs (the
    window is shown first, so a progress dialog has something to sit on)."""
    paths, missing = command_line_paths(args)
    if paths or missing:
        QTimer.singleShot(0, lambda: window.open_paths(paths, missing))


def _rename_log() -> RenameLog:
    """The persistent log behind File > Undo Last Rename (redactor_common's
    core/rename_log.py), next to this app's settings. Its on_move hook lets
    the operations journal's entries follow every renamed or moved file
    (core/history.py record_file_moves; nothing is written while history is off)."""
    return make_rename_log(os.path.join(str(base_dir()), "mp3redactor_rename_log.json"))

def _duplicates_store() -> JsonDismissStore:
    """The "Not duplicates" decisions of Analyze > Find Duplicates, next to
    the settings. Deliberately not part of Export/Import Settings: it is
    state about this library's files, not a preference."""
    return JsonDismissStore(os.path.join(str(base_dir()), "mp3redactor_duplicates_dismissed.json"))


class _ReviewItem:
    """Adapts an MP3File to the shared overwrite review, which reads
    `.path` and a `.metadata` object (cbzredactor's shape); an MP3File
    holds its tag fields directly."""

    def __init__(self, mp3: MP3File):
        self.path = str(mp3.path)
        self.metadata = mp3


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.files: list[MP3File] = []
        self._list_filter = ""  # Edit > Filter List's text; "" = every file shown
        self.notes_fetchers = None  # Create Notes: a callable returning core.notes_flow.Fetchers (None = the real services; tests inject)
        self.settings: Settings = load_settings()
        # The remembered "an unencrypted key file is OK on this computer" answer
        # is applied now, and a new yes is stored through the returned callable
        # (redactor_common's secret_store/secret_prompts); a secret itself is
        # never in the settings.
        self._remember_unencrypted_fallback = secret_store.bind_fallback_preference(
            lambda: self.settings.allow_unencrypted_fallback, self._store_unencrypted_fallback,
        )
        # In-memory-edit undo only (bulk-edit Apply) -- never physical
        # file operations (Save, mp3val Fix). See redactor_common.core.
        # undo's own module docstring for why.
        self.undo_manager: UndoManager[MP3File] = UndoManager()

        self.setWindowTitle(f"{APP_NAME} ({APP_VERSION})")
        self.setWindowIcon(QIcon(str(asset_path("assets/icon.ico"))))
        self.resize(1000, 600)

        self._column_keys = merge_column_order(self.settings.column_order, ALL_COLUMN_KEYS)
        self._col_index = {key: i for i, key in enumerate(self._column_keys)}

        self.table = QTableWidget(0, len(self._column_keys), self)
        self.table.setHorizontalHeaderLabels([_COLUMN_LABELS[key] for key in self._column_keys])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        # Click a header to sort by that column -- safe here because row
        # -> file mapping is Qt.UserRole-based, not list-index-based (see
        # this module's own docstring); _rebuild_table() suspends this
        # while it repopulates, see suspend_sorting()'s own docstring for
        # why that's required, not just tidy.
        self.table.setSortingEnabled(True)
        self.table.setStyleSheet(TABLE_SELECTION_STYLESHEET)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        self.table.itemSelectionChanged.connect(self._on_table_selection_changed)
        self.table.cellDoubleClicked.connect(self._on_cell_double_clicked)
        self._setup_column_persistence()
        self.zoom = TableZoomController(self.table, parent=self)

        # Cover art (see core/cover_art.py). Table thumbnails load lazily
        # -- only rows on screen ever read or decode a cover, in the
        # background (redactor_common's VisibleRowsWatcher +
        # AsyncIconCache, the mechanism epub/cbz use). A cover's version
        # is (path, mtime, cover_version); _cover_source remembers the
        # last one requested so a table rebuild can reuse cached icons
        # without touching the disk.
        self.table.setIconSize(COVER_ICON_SIZE)
        self._cover_icons = AsyncIconCache(COVER_ICON_SIZE, parent=self)
        self._cover_icons.icon_ready.connect(self._on_cover_icon_ready)
        self._cover_source = IdentityWeakDict()
        self._cover_item_by_file = IdentityWeakDict()
        self._visible_rows = VisibleRowsWatcher(self.table, self._load_cover_icons_for_rows)
        # The side panel's cover, read + decoded off the GUI thread.
        self._cover_preview = AsyncPreviewLoader(COVER_PREVIEW_SIZE, parent=self)
        self._cover_preview.image_ready.connect(self._on_cover_preview_ready)

        self.tag_panel = TagPanel()
        self.tag_panel.setMinimumWidth(24)
        self.tag_panel.setMaximumWidth(440)
        self.tag_panel.applyRequested.connect(self._apply_bulk_edit)
        self.tag_panel.selectionCountChanged.connect(self._on_tag_panel_selection_count_changed)
        self.tag_panel.collapseToggleRequested.connect(self._toggle_tag_panel)
        self.tag_panel.quickPickRequested.connect(self._on_quick_pick_requested)
        self.tag_panel.play_btn.clicked.connect(lambda _checked=False: self.action_play.trigger())
        self.tag_panel.coverSetRequested.connect(self.set_cover_from_file)
        self.tag_panel.coverFromFolderRequested.connect(self.set_cover_from_folder_images)
        self.tag_panel.coverRemoveRequested.connect(self.remove_cover)
        self.tag_panel.coverExportRequested.connect(self.export_cover)
        self._sync_panel_visible_fields()

        self.splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.splitter.addWidget(self.tag_panel)
        self.splitter.addWidget(self.table)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([TAG_PANEL_DEFAULT_WIDTH, 700])
        self.splitter.splitterMoved.connect(lambda *_a: self._sync_tag_panel_collapsed_indicator())
        self.setCentralWidget(self.splitter)

        self._panel_collapser = SplitterPaneCollapser(
            self.splitter,
            pane_index=0,
            collapsed_width=TAG_PANEL_COLLAPSED_WIDTH,
            default_width=TAG_PANEL_DEFAULT_WIDTH,
        )

        self._build_menu_and_toolbar()
        self.setAcceptDrops(True)  # files/folders dropped on the window are opened (dropEvent)

    # -- menu/toolbar -----------------------------------------------------

    def _build_menu_and_toolbar(self) -> None:
        # The family's standard menu skeleton (redactor_common's
        # gui/standard_menus.py): File, Edit, View, Metadata, Analyze,
        # Tools, Help. Shared actions come from its standard_*_items()
        # with the canonical labels/shortcuts; only the app-specific
        # Metadata and Analyze menus and the Look Up sources are
        # spelled out here. A slot left None is shown greyed (planned).
        spec = StandardMenuSpec(
            file=_drop_keys(standard_file_items(
                open_files=self.load_files_dialog,
                open_folder=self.load_folder_dialog,
                include_subfolders=include_subfolders_slot(),
                import_and_convert=self.import_and_convert_dialog,
                extra_open=[
                    MenuAction("create_m4b", "Create M4B Audio&book…", self.create_m4b_dialog),
                    # Read-only player window for the one selected file (never Ctrl+Alt: AltGr).
                    MenuAction("play", "Pla&y", self.play_selected_file, shortcut="Ctrl+P"),
                ],
                # One save action: every changed file (what the old
                # "Save File(s)" did). The skeleton's separate "Save" is
                # dropped below. No Ctrl+S alias (owner, 2026-10-14).
                save_all=self.save_changed,
                # Quick, direct rename of the one selected file -- matches
                # Explorer's F2 exactly. Distinct from "Rename / Export /
                # Move" (the pattern-based batch tool): see
                # rename_selected_file().
                rename_file=self.rename_selected_file,
                undo_last_rename=self.undo_last_rename,
                rename_export_move=self.open_rename_dialog,
                export_settings=self.export_settings_file,
                import_settings=self.import_settings_file,
                remove_from_list=self.remove_selected_from_list,
                clear_list=self.clear_list,
                # No explicit shortcut -- Alt+F4 already closes this (or
                # any) plain QMainWindow at the OS level.
                exit_slot=self.close,
            ), {"save"}),
            edit=standard_edit_items(
                undo=self.undo_last_action,
                redo=self.redo_last_action,
                apply=self.tag_panel.apply_bulk_edit,
                # One click: the recipe's checks/fixes/lookups on the selected
                # files (or all loaded, if none selected), saved in place with
                # each original in the Recycle Bin. See redact_files().
                redact=self.redact_files,
                edit_redact_recipe=self.edit_redact_recipe,
                search_replace=self.open_search_replace_dialog,
                change_case=self.open_case_conversion_dialog,
                auto_number=self.open_auto_numbering_dialog,
                filter_list=self.filter_list,
            ),
            view=standard_view_items(
                show_metadata_panel=self._toggle_tag_panel,
                zoom_in=self.zoom.zoom_in,
                zoom_out=self.zoom.zoom_out,
                reset_zoom=self.zoom.zoom_reset,
                refresh_list=self.refresh_list,
            ),
            app_menus=[
                AppMenu(labels.MENU_METADATA, [
                    MenuAction(
                        "parse_filename", labels.PARSE_FILENAME, self.open_parse_filename_dialog,
                        shortcut=shortcuts.PARSE_FILENAME,
                    ),
                    Separator(),
                    look_up_submenu([
                        MenuAction("musicbrainz_lookup", "&MusicBrainz…", self.open_musicbrainz_lookup_dialog),
                        MenuAction(
                            "musicbrainz_local_lookup", "Music&Brainz (Local Database)…",
                            self.open_musicbrainz_local_lookup_dialog,
                        ),
                        MenuAction("discogs_lookup", "&Discogs…", self.open_discogs_lookup_dialog),
                        MenuAction("fetch_lyrics", "&Lyrics", self.run_lyrics_fetch),
                    ]),
                    Separator(),
                    MenuAction("edit_lyrics", "&Edit Lyrics…", self.edit_lyrics_for_selection),
                    Separator(),
                    Submenu("C&over", [
                        MenuAction("cover_set", "&Set from Image File…", self.set_cover_from_file),
                        MenuAction("cover_from_folder", "Set from &Folder Image", self.set_cover_from_folder_images),
                        MenuAction("cover_from_musicbrainz", "Get from &MusicBrainz…", self.open_musicbrainz_cover_dialog),
                        MenuAction("cover_from_discogs", "Get from &Discogs…", self.open_discogs_cover_dialog),
                        MenuAction("cover_remove", "&Remove Cover", self.remove_cover),
                        Separator(),
                        MenuAction("cover_export", "&Export Cover to Image File…", self.export_cover),
                    ]),
                ]),
                AppMenu(labels.MENU_ANALYZE, [
                    MenuAction("check_integrity", "&Check Integrity", self.run_integrity_check),
                    MenuAction("fix_integrity", "&Fix Integrity Issues…", self.run_integrity_fix),
                    MenuAction("deep_check", "&Deep Check Integrity…", self.run_deep_check),
                    Separator(),
                    MenuAction("check_bpm", "Detect &BPM", self.run_bpm_check),
                    MenuAction("check_key", "Detect &Key", self.run_key_detection),
                    MenuAction("measure_loudness", "Measure &Loudness", self.run_loudness_measurement),
                    Separator(),
                    MenuAction("find_duplicates", labels.FIND_DUPLICATES_ALT, self.open_find_duplicates_dialog),
                ]),
            ],
            tools=[
                MenuAction("create_notes", "Cre&ate Notes…", self.create_notes_dialog,
                           tooltip="Draft the artist.md / album.md folder notes for the Pluteus media server"),
                Separator(),
            ] + standard_tools_items(
                preferences=self.open_settings_dialog,
                api_keys=self.open_api_keys_dialog,
                external_tools=self.open_external_tools_dialog,
                app_settings=[
                    MenuAction(
                        "musicbrainz_settings", "&MusicBrainz Database Setup…", self.open_musicbrainz_settings_dialog,
                    ),
                ],
                columns=self.open_column_settings_dialog,
                genres=self.open_genre_settings_dialog,
                languages=self.open_language_settings_dialog,
                history=self.open_history_dialog,
            ),
            help=standard_help_items(
                APP_NAME, self.open_changelog_dialog, self.open_credits_dialog, self.open_about_dialog,
            ),
        )
        build_standard_menu_bar(self, spec)
        actions = self.action_registry

        self.action_include_subfolders = actions["include_subfolders"]
        attach_include_subfolders(
            self.action_include_subfolders, load=lambda: self.settings.include_subfolders, save=self._set_include_subfolders,
        )
        self.action_load_files = actions["open_files"]
        self.action_load_folder = actions["open_folder"]
        self.action_save_all = actions["save_all"]
        self.action_rename_file = actions["rename_file"]
        self.action_rename_files = actions["rename_export_move"]
        self.action_refresh_list = actions["refresh_list"]
        self.action_parse_filename = actions["parse_filename"]
        self.action_apply_bulk_edit = actions["apply"]
        set_apply_count(self.action_apply_bulk_edit, 0)
        self.action_check_integrity = actions["check_integrity"]
        self.action_fix_integrity = actions["fix_integrity"]
        self.action_deep_check = actions["deep_check"]
        self.action_check_bpm = actions["check_bpm"]
        self.action_check_key = actions["check_key"]
        self.action_measure_loudness = actions["measure_loudness"]
        self.action_import_convert = actions["import_convert"]
        self.action_redact = actions["redact"]
        self.action_remove_from_list = actions["remove_from_list"]
        self.action_show_panel = actions["show_metadata_panel"]
        self.action_show_panel.setChecked(True)
        self.action_undo = actions["undo"]
        self.action_undo.setEnabled(False)
        self.action_redo = actions["redo"]
        self.action_play = actions["play"]
        self.action_redo.setEnabled(False)

        # The View menu owns the zoom shortcuts; the zoom controller's own
        # toolbar buttons would bind the same keys and make them ambiguous
        # (so neither would fire).
        self.zoom.zoom_in_action.setShortcuts([])
        self.zoom.zoom_out_action.setShortcuts([])

        # Toolbar carries only the everyday actions -- everything else
        # (Check Integrity, Detect BPM, Detect Key, ...) stays reachable
        # via the Analyze menu and the table's right-click context menu
        # (_show_context_menu), rather than crowding a second copy onto
        # the toolbar too.
        toolbar = QToolBar("Main", self)
        self.addToolBar(toolbar)
        toolbar.addAction(self.action_load_files)
        toolbar.addAction(self.action_load_folder)
        # Mirrors File > Include Subfolders both ways; the saving stays with the action (one saved setting).
        self.include_subfolders_checkbox = add_include_subfolders_checkbox(toolbar, self.action_include_subfolders)
        toolbar.addAction(self.action_play)
        toolbar.addSeparator()
        toolbar.addAction(self.action_save_all)
        toolbar.addSeparator()
        toolbar.addAction(self.action_apply_bulk_edit)
        toolbar.addAction(self.action_redact)
        toolbar.addSeparator()
        toolbar.addAction(self.action_undo)
        toolbar.addAction(self.action_redo)
        toolbar.addSeparator()

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer)

        toggle_panel_act = make_action(self, "Panel", self._toggle_tag_panel)
        toggle_panel_act.setToolTip("Minimize or restore the bulk-edit panel")
        toolbar.addAction(toggle_panel_act)
        toolbar.addSeparator()

        toolbar.addAction(self.zoom.zoom_out_action)
        toolbar.addWidget(self.zoom.label)
        toolbar.addAction(self.zoom.zoom_in_action)

        # Ctrl+K: search every menu action by name (View > Command Palette).
        add_command_palette(self, self.action_registry)

    # -- about/changelog ----------------------------------------------------

    def open_about_dialog(self) -> None:
        # ABOUT.md is the user-facing description (features, the command
        # line), bundled as a data asset like CHANGELOG.md; README.md is
        # the developer's page.
        dialog = AboutDialog(
            app_name=APP_NAME,
            app_version=APP_VERSION,
            release_label=RELEASE_LABEL,
            icon_path=str(asset_path("assets/icon.ico")),
            about_path=str(asset_path(ABOUT_FILE)),
            component_versions={"redactor_common": REDACTOR_COMMON_VERSION},
            repo_url=APP_REPO_URL,
            component_repo_urls={"redactor_common": REDACTOR_COMMON_REPO_URL},
            parent=self,
        )
        dialog.exec()

    def open_changelog_dialog(self) -> None:
        dialog = ChangelogDialog(str(asset_path("CHANGELOG.md")), parent=self)
        dialog.exec()

    def open_credits_dialog(self) -> None:
        dialog = CreditsDialog(str(asset_path("CREDITS.md")), parent=self)
        dialog.exec()

    # -- Tools > History (the operations journal, core/history.py) ----------

    def open_history_dialog(self) -> None:
        """Every tag change this app made, with the old values; a restore
        writes them back. The restore runs on a worker thread, so the
        wrapper only notes the path; loaded rows of restored files are
        re-read here, on the GUI thread, once the dialog is closed."""
        title = "History"
        if not history_enabled():
            if not journal_exists():
                # Opt-in (owner, 2026-10-14): nothing is recorded until asked.
                answer = QMessageBox.question(
                    self, "History",
                    "History is off. Turn it on? From then on, tag changes this program saves are recorded "
                    "here. Nothing has been recorded so far.",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return
                self.settings.history_enabled = True
                save_settings(self.settings)
                set_history_enabled(True)
            else:
                title = "History (recording is off)"
        restored: list[str] = []

        def restore(path: str, values: dict[str, str]) -> str:
            problem = apply_restore(path, values)
            if not problem:
                restored.append(path)
            return problem

        HistoryDialog(journal_for_dialog(), restore, self, title).exec()
        self._reload_restored(restored)

    def _reload_restored(self, paths: list[str]) -> None:
        """Re-reads the loaded rows of files a History restore rewrote
        (pending edits of other fields are kept, as after Fix Integrity)."""
        wanted = {os.path.normcase(os.path.abspath(p)) for p in paths}
        hits = [m for m in self.files if os.path.normcase(os.path.abspath(str(m.path))) in wanted]
        for mp3 in hits:
            reload_from_disk(mp3)
        if hits:
            self._rebuild_table()
            self.statusBar().showMessage(f"Reloaded {len(hits)} restored file(s) from disk.", 8000)

    def _show_journal_problem(self) -> None:
        """A change-history write failure, shown once in the status bar."""
        problem = take_journal_problem()
        if problem:
            self.statusBar().showMessage(problem, 15000)

    # -- loading ------------------------------------------------------------

    def load_files_dialog(self) -> None:
        start_dir = resolve_start_directory(self.settings.last_directory)
        paths, _ = QFileDialog.getOpenFileNames(self, "Open Files", start_dir, "MP3 Files (*.mp3)")
        if paths:
            self._remember_last_directory(paths[0])
            self._load_paths([Path(p) for p in paths])

    def load_folder_dialog(self) -> None:
        start_dir = resolve_start_directory(self.settings.last_directory)
        folder = QFileDialog.getExistingDirectory(self, "Open Folder", start_dir)
        if folder:
            self._remember_last_directory(folder)
            self._load_paths([Path(folder)])

    def open_paths(self, paths: list[Path], missing: list[str] | None = None) -> None:
        """Opens files and folders given on the command line or dropped on the
        window, exactly like File > Open Files / Open Folder (the list is
        replaced; folders follow File > Include Subfolders; a folder with
        only FLAC/WAV/... offers to convert)."""
        if missing:
            self.statusBar().showMessage(f"Not found, not opened: {', '.join(missing)}", 10000)
        if paths:
            self._remember_last_directory(str(paths[0]))
            self._load_paths(paths)

    # Drag and drop: files and folders dropped anywhere on the window are opened.

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        paths = [Path(url.toLocalFile()) for url in event.mimeData().urls() if url.isLocalFile()]
        if not paths:
            return
        event.acceptProposedAction()
        # After the drop has returned: the load can show dialogs, and Explorer
        # waits on the drop until this handler returns.
        QTimer.singleShot(0, lambda: self.open_paths(paths))

    def _set_include_subfolders(self, on: bool) -> None:
        """File > Include Subfolders (redactor_common's shared item): remembered for Open Folder, drops, Refresh."""
        self.settings.include_subfolders = on
        save_settings(self.settings)

    def _remember_last_directory(self, path: str) -> None:
        self.settings.last_directory = directory_for(path)
        save_settings(self.settings)

    def _scan_with_progress(self, label: str, scan):
        """Runs a folder walk (find_mp3_files() and friends -- slow on a big or networked library) on a worker
        thread under an indeterminate progress dialog (redactor_common's run_busy), so the window keeps painting."""
        return run_busy(self, label, scan)

    def _load_paths(self, raw_paths: list[Path]) -> None:
        mp3_paths = self._scan_with_progress(
            "Scanning for MP3 files...", lambda: find_mp3_files(raw_paths, recursive=self.settings.include_subfolders)
        )
        if not mp3_paths:
            # redactor_common's convert offer: the walk under run_busy, then one box listing the formats found
            # (Show Details lists the files).
            importable = find_convertible(
                self, raw_paths, IMPORTABLE_EXTENSIONS, self.settings.include_subfolders, skip=is_app_temp_name,
                label="Looking for audio files to convert...",
            )
            if not importable:
                QMessageBox.information(self, "No MP3 Files", "No .mp3 files found in that selection.")
                return
            answer = offer_conversion(
                self, importable, 0, kind_label="MP3", target_label="MP3", noun="audio file",
                question="Convert them to MP3 (beside the originals, which are kept) and load the new files?",
                title="No MP3 Files", yes_text="Yes", no_text="No",
            )
            if answer == CONVERT_YES:
                self._convert_paths_and_load(list(importable))
            return

        # Open Files/Folder replaces self.files wholesale (unlike the epub
        # tool, which adds to its list) -- always did, but now that files can
        # carry unsaved tag edits, doing that silently would discard them.
        if self._count_dirty() and not self._confirm_discard("open a new selection"):
            return
        self._list_filter = ""  # a new list starts unfiltered (Edit > Filter List)

        loaded: list[MP3File] = []

        def step(mp3_path, _index: int) -> None:
            loaded.extend(load_files([mp3_path]))

        finished = run_with_progress(self, mp3_paths, step, "Loading files...", threshold=3)
        self.files = loaded
        # Old undo snapshots reference now-discarded MP3File objects --
        # restoring into them wouldn't reach anything still on screen.
        self.undo_manager.clear()
        self._update_undo_action()
        self._update_redo_action()
        self._rebuild_table()
        if finished:
            self.statusBar().showMessage(f"Loaded {len(loaded)} file(s).", 5000)
        else:
            # Cancel keeps what was read so far: say so, the list is not the whole selection.
            self.statusBar().showMessage(
                f"Loading cancelled: {len(loaded)} of {len(mp3_paths)} file(s) loaded.", 10000
            )

    def refresh_list(self) -> None:
        """Re-scans the folder(s) your currently-loaded files live in
        (picking up new .mp3 files added there since you loaded), then
        re-reads every file still present from disk. Doesn't discover a
        brand-new subfolder you haven't loaded anything from yet (only
        folders already represented in your current list get scanned,
        non-recursively) -- use Open Folder for that. A file that's
        disappeared from disk is dropped from the list (only files
        still present are shown).

        Discards unsaved in-memory edits (with confirmation first) and
        clears the undo stack, since its entries would reference
        MP3File objects this replaces. The actual "what's new on disk"
        logic is redactor_common.core.folder_refresh's -- this is just
        the mp3-specific wiring: how paths are found in memory, and
        what to do once the new set is known."""
        if not self.files:
            self.statusBar().showMessage("Nothing to refresh: no files are loaded.", 5000)
            return
        if self._count_dirty() and not self._confirm_discard(
            "refresh the list (discarding unsaved changes)"
        ):
            return

        existing_paths = [str(mp3.path) for mp3 in self.files]
        new_paths = self._scan_with_progress(
            "Scanning for new files...",
            lambda: find_new_files_in_loaded_folders(
                existing_paths,
                lambda folder: find_mp3_files([Path(folder)], recursive=self.settings.include_subfolders),
            ),
        )
        present_paths = [p for p in existing_paths if os.path.exists(p)]
        removed_count = len(existing_paths) - len(present_paths)
        all_paths = [Path(p) for p in present_paths + new_paths]

        loaded: list[MP3File] = []

        def step(path, _index: int) -> None:
            loaded.extend(load_files([path]))

        finished = run_with_progress(self, all_paths, step, "Refreshing...", threshold=3)
        self.files = loaded
        self.undo_manager.clear()
        self._update_undo_action()
        self._update_redo_action()
        self._rebuild_table()
        self.statusBar().showMessage(refresh_summary(len(new_paths), removed_count, len(loaded), len(all_paths), finished), 10000)

    def remove_selected_from_list(self) -> None:
        """File > Remove from List (Delete): drops the selected files from
        this list only -- nothing on disk is touched. Like Load, it clears
        the undo stack (its snapshots reference MP3File objects that go
        away) and asks first when a removed file has unsaved edits."""
        targets = self._selected_files()
        if not targets:
            return
        if any(mp3.dirty for mp3 in targets) and not self._confirm_discard(
            "remove files with unsaved changes from the list"
        ):
            return
        gone = {id(mp3) for mp3 in targets}
        self.files = [mp3 for mp3 in self.files if id(mp3) not in gone]
        self._after_list_shrunk()

    def clear_list(self) -> None:
        """File > Clear List: empties the list (nothing on disk is touched)."""
        if not self.files:
            return
        if self._count_dirty() and not self._confirm_discard("clear the entire list"):
            return
        self.files = []
        self._list_filter = ""
        self._after_list_shrunk()

    # -- filter list --------------------------------------------------------

    @property
    def list_filter(self) -> str:
        """The Edit > Filter List text in force ("" = every file shown)."""
        return self._list_filter

    def filter_list(self) -> None:
        """Edit > Filter List (Ctrl+F): asks for a text and shows only the
        files whose Filename, Title, Artist, Album or Path contains it
        (case-insensitive). Empty text shows every file again."""
        text, ok = QInputDialog.getText(
            self, "Filter List",
            "Show only files whose Filename, Title, Artist, Album or Path contains\n"
            "(leave empty to show every file):",
            text=self._list_filter,
        )
        if ok:
            self.set_list_filter(text)

    def set_list_filter(self, text: str) -> None:
        self._list_filter = text.strip()
        self._apply_list_filter()
        if self._list_filter:
            return  # _apply_list_filter() has said "Showing N of M files"
        self.statusBar().showMessage(f"Filter cleared: showing all {len(self.files)} files.", 5000)

    def _apply_list_filter(self) -> None:
        """Hides the rows the filter text does not match and deselects any
        selected row it hides, so a batch action only reaches files the user
        can see. Re-run after every table rebuild; sorting keeps hidden rows
        with their files (QHeaderView keeps hidden sections across a sort)."""
        needle = self._list_filter.casefold()
        col = self._col_index["filename"]
        shown = 0
        selected_before = {id(mp3) for mp3 in self._selected_files()}
        for row in range(self.table.rowCount()):
            item = self.table.item(row, col)
            mp3 = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
            visible = not needle or (mp3 is not None and file_matches_filter(mp3, needle))
            self.table.setRowHidden(row, not visible)
            shown += visible
        selected_after = {id(mp3) for mp3 in self._selected_files()}
        if selected_after != selected_before:
            self._reselect_files(selected_after)
            self.tag_panel.set_selection(self._selected_files())
            self._update_cover_panel()
        if needle:
            self.statusBar().showMessage(
                f"Showing {shown} of {len(self.files)} files (filter: {self._list_filter})"
            )

    # -- create notes ------------------------------------------------------

    def create_notes_dialog(self) -> None:
        """Tools > Create Notes...: ALWAYS asks which folder first, as a short list (the folder you opened, the
        artist / album of the selected files, one level up, the last notes folder, the library folder, Another
        folder...). The selected files are never used silently: their artist folder is only the pre-ticked choice
        until Continue. Include subfolders in the dialog is File > Include Subfolders (one saved setting). The
        review page is gui/notes_dialog.py."""
        choices = scope_choices(
            [mp3.path for mp3 in self.files], [mp3.path for mp3 in self._selected_files()],
            self.settings.last_directory, self.settings.notes_last_folder, self.settings.library_root)
        folder, kinds, outdated = ask_notes_scope(
            self, choices, self.settings.include_subfolders, self.action_include_subfolders.setChecked,
            resolve_start_directory(self.settings.notes_last_folder or self.settings.last_directory),
            self.settings.notes_kinds)
        if not folder:
            return
        self.settings.notes_last_folder = folder
        self.settings.notes_kinds = kinds
        save_settings(self.settings)
        self._run_create_notes([folder], self.settings.include_subfolders, parse_kinds(kinds), outdated)

    def create_notes_for_selection(self) -> None:
        """Right-click "Create Notes for This Folder...": for each selected file its ALBUM folder and the ARTIST
        folder above it (when that is an artist level by the engine's own test; never a collection), whichever
        single album was selected. Only those folders themselves are looked at, whatever Include Subfolders says."""
        files = self._selected_files()
        if not files:
            self.statusBar().showMessage("Select a file first, or use Tools > Create Notes.", 6000)
            return
        folders = [str(f) for f in selection_scope(mp3.path for mp3 in files)]
        self._run_create_notes(folders, False)

    def _run_create_notes(self, paths: list, include_subfolders: bool, kinds: tuple = ALL_KINDS,
                          include_outdated: bool = False) -> None:
        def remember_size(text: str) -> None:
            self.settings.notes_dialog_size = text
            save_settings(self.settings)

        create_notes(
            self, paths, include_subfolders, self.notes_fetchers or live_fetchers,
            size_text=self.settings.notes_dialog_size, on_size=remember_size, kinds=kinds,
            cache=default_cache(self.settings.notes_cache_enabled), include_outdated=include_outdated,
            discogs_mode="always" if self.settings.notes_discogs_always else "fallback",
            singles=not self.settings.notes_skip_singles,
        )

    # -- find duplicates ---------------------------------------------------

    def open_find_duplicates_dialog(self) -> None:
        """Analyze > Find Duplicates: reviews ALL loaded files (not just the
        selection -- a duplicate is a relation between files) for identical
        audio, the same MusicBrainz recording and the same artist/title/
        length, in redactor_common's shared review dialog. A review aid:
        duplicates are not errors, nothing is selected or changed unless
        the user picks an action. See core/mp3_duplicates.py."""
        readable = [mp3 for mp3 in self.files if not mp3.load_error]
        if len(readable) < 2:
            QMessageBox.information(self, "Find Duplicates", "Open at least two readable MP3 files first.")
            return
        intro = f"Compared all {len(readable)} loaded files (not only the selection)."
        if len(readable) < len(self.files):
            intro += f" {len(self.files) - len(readable)} file(s) that could not be read were skipped."
        run_find_duplicates(
            self, readable, mp3_duplicates.find_duplicate_groups, mp3_duplicates.COLUMNS,
            title="Find Duplicates", dismiss_store=_duplicates_store(),
            on_select_in_list=self._select_files_from_duplicates,
            on_trashed=self._remove_trashed_from_list, trash=self._trash_unless_unsaved,
            intro_text=intro, none_found_message="No duplicates found among the loaded files.",
        )

    def _select_files_from_duplicates(self, items: list[MP3File]) -> None:
        self._reselect_files({id(mp3) for mp3 in items})
        self.tag_panel.set_selection(self._selected_files())
        self._update_cover_panel()
        current = self.table.currentIndex()
        if current.isValid():
            self.table.scrollTo(current)

    def _trash_unless_unsaved(self, path: str) -> None:
        """The review dialog's Recycle Bin step, except that a file whose
        edits are not saved yet is refused (the dialog lists it with this
        message): moving it would throw the edits away."""
        for mp3 in self.files:
            if str(mp3.path) == path and mp3.dirty:
                raise TrashError("it has unsaved changes in the list -- save or discard them first")
        move_to_trash(path)

    def _remove_trashed_from_list(self, items: list[MP3File]) -> None:
        """Files the review dialog moved to the Recycle Bin leave the list
        (same path as Remove from List; they cannot be dirty, see above)."""
        gone = {id(mp3) for mp3 in items}
        self.files = [mp3 for mp3 in self.files if id(mp3) not in gone]
        self._after_list_shrunk()

    def _after_list_shrunk(self) -> None:
        self.undo_manager.clear()
        self._update_undo_action()
        self._update_redo_action()
        self._rebuild_table()

    # -- import & convert --------------------------------------------------

    def import_and_convert_dialog(self) -> None:
        """File > "Import and Convert..." -- brings a
        non-MP3 audio file (FLAC/WAV/OGG/M4A/...) into the library by
        converting it to .mp3 via ffmpeg's libmp3lame encoder
        (core.mp3_converter), same directory, same base filename.

        Deliberately ADDITIVE to self.files, unlike Open Files/Folder's
        replace-wholesale semantics -- Import brings something new IN
        alongside whatever's already loaded, it doesn't represent a
        fresh "start over with this selection" the way Open does (see
        this module's own docstring on the Import menu's intent)."""
        extensions_filter = " ".join(f"*{ext}" for ext in sorted(IMPORTABLE_EXTENSIONS))
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Import and Convert",
            resolve_start_directory(self.settings.last_directory),
            f"Audio Files ({extensions_filter})",
        )
        if not paths:
            return
        self._convert_paths_and_load(paths)

    def _convert_paths_and_load(self, paths: list[str]) -> None:
        """Asks the bitrate, converts `paths` to MP3 beside the originals and adds the results to the list."""
        bitrate_labels = [f"{kbps} kbps" for kbps in BITRATE_CHOICES_KBPS]
        default_index = BITRATE_CHOICES_KBPS.index(DEFAULT_BITRATE_KBPS)
        chosen_label, ok = QInputDialog.getItem(
            self, "Convert to MP3", "Bitrate:", bitrate_labels, default_index, editable=False
        )
        if not ok:
            return
        bitrate_kbps = BITRATE_CHOICES_KBPS[bitrate_labels.index(chosen_label)]

        conversions, skipped = plan_conversions([Path(p) for p in paths])

        if skipped:
            names = "\n".join(p.name for p in skipped)
            proceed = QMessageBox.question(
                self,
                "Some Files Already Exist",
                f"{len(skipped)} file(s) already have an .mp3 of the same name in that "
                f"folder and will be skipped (not overwritten):\n\n{names}\n\n"
                f"Convert the remaining {len(conversions)} file(s)?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            )
            if proceed != QMessageBox.StandardButton.Yes:
                return

        if not conversions:
            self.statusBar().showMessage("Nothing to convert: every file already has an .mp3 of the same name.", 8000)
            return

        # ffmpeg runs on a worker thread, so the window keeps painting during
        # a long encode; Cancel kills the running ffmpeg too (nothing half-
        # written is left) and skips the rest.
        def work(progress):
            progress.set_label(f"Converting to MP3: {conversions[0][0].name}")

            def on_progress(done, total):
                progress.on_progress(done, total)
                if done < total:
                    progress.set_label(f"Converting to MP3: {conversions[done][0].name}")

            return run_import_conversion(
                conversions,
                bitrate_kbps=bitrate_kbps,
                progress=on_progress,
                ffmpeg_path=self.settings.ffmpeg_path or None,
                should_cancel=progress.should_cancel,
            )

        results, cancelled = run_work_with_progress(self, len(conversions), "Converting to MP3...", work)

        self._remember_last_directory(paths[0])

        succeeded_dests = [dest for _src, dest, status, _msg in results if status == STATUS_OK]
        failed = [f"{src.name}: {msg}" for src, _dest, status, msg in results if status != STATUS_OK]

        if succeeded_dests:
            newly_loaded: list[MP3File] = []
            run_with_progress(
                self, succeeded_dests, lambda dest, _i: newly_loaded.extend(load_files([dest])),
                "Loading converted files...", threshold=3, cancellable=False,
            )
            existing_by_path = {mp3.path.resolve(): i for i, mp3 in enumerate(self.files)}
            for new_mp3 in newly_loaded:
                resolved = new_mp3.path.resolve()
                if resolved in existing_by_path:
                    # A file already sitting at this exact dest path
                    # (re-importing the same source, or the converted
                    # name happens to match an already-loaded row) is
                    # refreshed in place rather than duplicated.
                    self.files[existing_by_path[resolved]] = new_mp3
                else:
                    self.files.append(new_mp3)
            self._rebuild_table()

        stopped = (
            f" Cancelled: {len(conversions) - len(results)} file(s) were not converted." if cancelled else ""
        )
        if failed:
            QMessageBox.warning(
                self,
                "Some Files Failed to Convert",
                f"{len(failed)} of {len(conversions)} file(s) could not be converted:\n\n"
                f"{summarize_errors(failed)}{stopped}",
            )
        else:
            # A routine result: the status bar, not a box to click away.
            self.statusBar().showMessage(f"Converted and loaded {len(succeeded_dests)} file(s).{stopped}", 8000)

    def create_m4b_dialog(self) -> None:
        """File > Create M4B Audiobook...: the selected files (all loaded files when none is
        selected), one chapter each, joined into chaptered .m4b audiobooks. Files from one folder make
        one book (dialog with the chapter list); files from several folders make one book per folder
        (batch dialog). See gui/m4b_dialog.py, gui/m4b_batch_dialog.py and core/m4b_builder.py.
        Nothing in the list changes: the .m4b files are new, not loaded here."""
        files = [mp3 for mp3 in (self._selected_files() or self.files) if not mp3.load_error]
        if not files:
            QMessageBox.information(self, "Create M4B Audiobook", "Open some MP3 files first.")
            return
        books = group_by_folder(files)
        if len(books) > 1:
            dialog = M4bBatchDialog(
                books, bitrate_kbps=self.settings.m4b_bitrate_kbps, sidecar=self.settings.m4b_sidecar,
                layout=self.settings.m4b_batch_layout, library_root=self.settings.library_root,
                region=self.settings.m4b_audible_region, parent=self,
            )
            if dialog.exec() != dialog.DialogCode.Accepted:
                return
            specs = dialog.specs()
            self.settings.m4b_batch_layout = dialog.layout_choice()
            if dialog.library_root():
                self.settings.library_root = dialog.library_root()
        else:
            dialog = M4bDialog(
                files, bitrate_kbps=self.settings.m4b_bitrate_kbps, sidecar=self.settings.m4b_sidecar,
                region=self.settings.m4b_audible_region, parent=self,
            )
            if dialog.exec() != dialog.DialogCode.Accepted:
                return
            specs = [dialog.spec()]
        self.settings.m4b_bitrate_kbps = dialog.bitrate_kbps()
        self.settings.m4b_audible_region = dialog.region()
        self.settings.m4b_sidecar = specs[0].write_sidecar if specs else self.settings.m4b_sidecar
        self._remember_last_directory(str(specs[0].output))  # saves the settings, the choices above included
        self._build_audiobooks(specs, skip_existing=len(books) > 1)

    def _build_audiobooks(self, specs: list, skip_existing: bool) -> None:
        """Builds each spec under one progress dialog, then reports. With `skip_existing`, a book whose
        .m4b is already there is left alone (the batch never replaces anything)."""
        results: list[tuple] = []
        skipped: list[str] = []
        todo = []
        claimed: set[str] = set()  # two books for one file: the second would replace the first
        for spec in specs:
            key = os.path.normcase(os.path.abspath(spec.output))
            if key in claimed:
                skipped.append(f"{spec.title}: another book in this batch is written to {spec.output.name}, left out")
            elif skip_existing and spec.output.exists():
                skipped.append(f"{spec.output.name}: already exists, left as it is")
            else:
                claimed.add(key)
                spec.trash = move_to_trash  # an audiobook, metadata.opf or cover this replaces goes to the Recycle Bin
                todo.append(spec)
        total = sum(len(spec.chapters) + 1 for spec in todo)
        cancelled = False
        if todo:
            ffmpeg_path, ffprobe_path = self.settings.ffmpeg_path or None, self.settings.ffprobe_path or None

            # The encodes and the join run on a worker thread (the window keeps painting during a join of
            # several minutes); Cancel stops the running ffmpeg at once.
            def work(progress) -> bool:
                offset = 0
                for number, spec in enumerate(todo, start=1):
                    progress.set_label(f"Book {number} of {len(todo)}: {spec.title}" if len(todo) > 1 else f"Creating: {spec.title}")
                    result = build_m4b(
                        spec,
                        ffmpeg_path=ffmpeg_path,
                        ffprobe_path=ffprobe_path,
                        progress=lambda done, _total, base=offset: progress.set_value(base + done),
                        should_cancel=progress.should_cancel,
                    )
                    results.append((spec, result))
                    offset += len(spec.chapters) + 1
                    if result.cancelled:
                        return True
                return False

            cancelled, _pressed = run_work_with_progress(self, total, "Creating audiobook...", work)
        made = [(spec, result) for spec, result in results if result.status == STATUS_OK]
        problems = skipped + [
            f"{spec.title}: {result.message}" for spec, result in results
            if result.status != STATUS_OK and not result.cancelled
        ] + [f"{spec.title}: {note}" for spec, result in made for note in result.notes]
        if len(specs) == 1 and not skipped:
            only = results[0][1] if results else None
            if cancelled:
                self.statusBar().showMessage("Audiobook cancelled; nothing was written.", 6000)
            elif only is not None and only.status != STATUS_OK:
                QMessageBox.warning(self, "Could Not Create the Audiobook", only.message)
            elif only is not None:
                minutes = int(only.duration_seconds // 60)
                extra = f"\n\nNote: {summarize_errors(only.notes)}" if only.notes else ""
                QMessageBox.information(
                    self, "Audiobook Created",
                    f"{only.output.name}: {only.chapter_count} chapters, about {minutes} minutes.\n\n{only.output}{extra}",
                )
            return
        if cancelled:
            self.statusBar().showMessage(f"Cancelled after {len(made)} audiobook(s); the rest were not made.", 8000)
        summary = f"{len(made)} of {len(specs)} audiobook(s) created."
        if problems:
            QMessageBox.warning(self, "Audiobooks", f"{summary}\n\n{summarize_errors(problems)}")
        elif made:
            QMessageBox.information(self, "Audiobooks Created", summary)

    # -- rename/export by pattern, and the reverse: parse filename --------

    def _remember_ascii_filenames(self, enabled: bool) -> None:
        self.settings.ascii_filenames = enabled
        save_settings(self.settings)

    def _remember_rename_zero_pad(self, enabled: bool, width: int) -> None:
        self.settings.rename_zero_pad = enabled
        self.settings.rename_zero_pad_width = width
        save_settings(self.settings)

    def _remember_auto_number_padding(self, width: int) -> None:
        self.settings.auto_number_padding = width
        save_settings(self.settings)

    def _remember_pattern_used(self, pattern: str) -> None:
        self.settings.pattern_history = dedupe_and_trim_pattern_history(
            self.settings.pattern_history, pattern
        )
        save_settings(self.settings)

    def _remember_rename_pattern(self, pattern: str) -> None:
        """The pattern Redact's Rename step starts from (Parse Filename
        doesn't set it, unlike the shared pattern history)."""
        self.settings.rename_pattern = pattern
        save_settings(self.settings)

    def _remember_move_pattern(self, pattern: str) -> None:
        self.settings.move_pattern = pattern
        save_settings(self.settings)

    def _remember_library_root(self, folder: str) -> None:
        self.settings.library_root = folder
        save_settings(self.settings)

    def _move_into_folders(self, planned) -> None:
        """The Rename dialog's "Move into folders" mode: the shared runner
        moves the files (progress, cancel, per-file errors) and records
        the batch -- with the folders it created -- in the rename log, so
        File > Undo Last Rename puts them back. Like a rename it isn't on
        the Undo stack; the rows just take on their new paths."""
        summary = run_planned_moves(self, planned, False, _rename_log(), "Move into Folders")
        for mp3, _old_path, new_path in summary.done:
            mp3.path = Path(new_path)
        self._rebuild_table()

    def _require_targets(self, action_desc: str) -> list[MP3File]:
        """Same "selection required, else a clear message" convention
        every other Operations/File action in this app already uses
        (_run_concurrent_check_with_progress) -- deliberately NOT the
        "selected, or every loaded file if none selected" fallback some
        sibling projects use for their own rename/lookup dialogs: Rename
        physically renames files on disk, so asking for an explicit
        choice here is the safer default, and Parse Filename stays
        consistent with it rather than behaving differently action to
        action within this same app."""
        targets = self._selected_files()
        if not targets:
            if not self.files:
                QMessageBox.information(self, "No Files Loaded", "Open some files first.")
            else:
                QMessageBox.information(
                    self, "No Files Selected", f"Select one or more files in the table to {action_desc}."
                )
        return targets

    def open_rename_dialog(self) -> None:
        targets = self._require_targets("rename")
        if not targets:
            return

        def get_values(mp3: MP3File) -> dict[str, str]:
            return filename_values(mp3)

        dialog = RenamePatternDialog(
            targets, FILENAME_PLACEHOLDERS, get_values, lambda mp3: str(mp3.path),
            pattern_history=self.settings.pattern_history,
            default_pattern=DEFAULT_RENAME_PATTERN,
            title="Rename / Export by Metadata Pattern",
            item_noun="file",
            zero_pad_field="track",
            ascii_only=self.settings.ascii_filenames,
            on_ascii_only_changed=self._remember_ascii_filenames,
            zero_pad_initial=(self.settings.rename_zero_pad, self.settings.rename_zero_pad_width),
            on_zero_pad_changed=self._remember_rename_zero_pad,
            library_root=self.settings.library_root,
            on_library_root_changed=self._remember_library_root,
            parent=self,
        )
        if dialog.exec() != dialog.DialogCode.Accepted:
            return

        self._remember_pattern_used(dialog.pattern_edit.text())
        if dialog.is_move_mode():
            self._remember_move_pattern(dialog.pattern_edit.text())
            self._move_into_folders(dialog.planned_moves())
            return
        self._remember_rename_pattern(dialog.pattern_edit.text())
        export_mode = dialog.is_export_mode()
        errors: list[str] = []
        # Rename is a physical file operation, deliberately not pushed
        # onto self.undo_manager -- same "in-memory edits only" line
        # this project already draws for Save/Fix Integrity (see the
        # Undo section's own docstring above).
        renamed: list[tuple[str, str]] = []

        def do_one(planned, _index: int) -> None:
            mp3, old_path, new_path = planned
            try:
                if export_mode:
                    shutil.copy2(old_path, new_path)
                else:
                    os.rename(old_path, new_path)
                    mp3.path = Path(new_path)
                    renamed.append((str(old_path), str(new_path)))
            except OSError as exc:
                errors.append(f"{Path(old_path).name}: {exc}")

        # Copies can be slow (big files, network shares), so they show
        # progress and can be cancelled between files; renames are
        # near-instant, so they never show a dialog in practice.
        run_with_progress(
            self, list(dialog.planned_renames()), do_one,
            "Copying files..." if export_mode else "Renaming files...",
            threshold=3, cancellable=export_mode,
            label_for=lambda planned: f"{'Copying' if export_mode else 'Renaming'}: {Path(planned[1]).name}",
        )
        _rename_log().record("Rename by Pattern", renamed)

        self._rebuild_table()
        if errors:
            QMessageBox.warning(self, "Some Files Failed", summarize_errors(errors))

    def open_parse_filename_dialog(self) -> None:
        targets = self._require_targets("parse")
        if not targets:
            return

        # Filename patterns first, so a path pattern left by Move into
        # folders doesn't become the starting pattern; the dialog itself
        # switches to path mode for any pattern containing "/".
        names, paths = split_pattern_history(self.settings.pattern_history)
        dialog = ParseFilenameDialog(
            targets, FILENAME_PLACEHOLDERS, lambda mp3: str(mp3.path),
            pattern_history=names + paths,
            default_pattern=DEFAULT_RENAME_PATTERN,
            valid_field_keys={key for key, _label in FILENAME_PLACEHOLDERS},
            numeric_fields=NUMERIC_FILENAME_FIELDS,
            strip_leading_zeros_fields={"track"},
            title="Parse Filename → Metadata",
            item_noun="file",
            library_root=self.settings.library_root,
            on_library_root_changed=self._remember_library_root,
            parent=self,
        )
        if dialog.exec() != dialog.DialogCode.Accepted:
            return

        self._remember_pattern_used(dialog.pattern_edit.text())
        changes = dialog.accepted_changes()  # index into targets -> {field: value}
        if not changes:
            return

        self._push_undo("Parse Filename", targets)
        for index, fields in changes.items():
            targets[index].apply_tags(fields)
        self._rebuild_table()

    def open_musicbrainz_cover_dialog(self) -> None:
        """Metadata > Cover > Get from MusicBrainz...: the MusicBrainz lookup dialog, but only the
        found release's front cover (Cover Art Archive) is applied, never any tag."""
        from gui.musicbrainz_lookup_dialog import MusicBrainzLookupDialog, group_by_folder

        targets = self._require_targets("get a cover for")
        if not targets:
            return
        dialog = MusicBrainzLookupDialog(group_by_folder(targets), self, cover_only=True)
        self._apply_lookup_dialog(dialog, "Cover from MusicBrainz")

    def open_musicbrainz_lookup_dialog(self) -> None:
        """Import > Look Up via MusicBrainz...: the selected files, one
        album per folder, matched to MusicBrainz releases (see
        gui/musicbrainz_lookup_dialog.py). Anything that would overwrite
        an existing, different value goes through the family's per-field
        overwrite review first; the result is one Undo step and is
        written on Save, like every other edit."""
        from gui.musicbrainz_lookup_dialog import MusicBrainzLookupDialog, group_by_folder

        targets = self._require_targets("look up")
        if not targets:
            return
        from core.acoustid_lookup import FPCALC_EXE_NAME
        from core.tool_locator import find_tool

        # fpcalc (if found) adds identification by sound; without it the
        # lookup works from tags and folder names as before.
        fpcalc = find_tool(FPCALC_EXE_NAME, override=self.settings.fpcalc_path or None)
        dialog = MusicBrainzLookupDialog(group_by_folder(targets), self, fpcalc=fpcalc)
        self._apply_lookup_dialog(dialog, "MusicBrainz Lookup")

    def open_musicbrainz_local_lookup_dialog(self) -> None:
        """Metadata > Look Up > MusicBrainz (Local Database)...: the same review dialog
        answered from the offline database (core/musicbrainz_local.py). Without one set up,
        explain and offer Tools > MusicBrainz Database Setup... rather than just failing."""
        from core.acoustid_lookup import FPCALC_EXE_NAME
        from core.musicbrainz_local import MusicBrainzLocalError, open_database
        from core.tool_locator import find_tool
        from gui.musicbrainz_local_dialog import MusicBrainzLocalLookupDialog, group_by_folder

        name = "MusicBrainz (Local Database)"
        targets = self._require_targets("look up")
        if not targets:
            return
        path = self.settings.musicbrainz_database
        if not path or not os.path.isfile(path):
            reply = QMessageBox.question(
                self, name,
                "No local MusicBrainz database is set up yet. It is built from MusicBrainz's free core data "
                "dump (about 7 GB), which you download yourself.\n\n"
                "Open Tools > MusicBrainz Database Setup... "
                "to set it up?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if reply == QMessageBox.StandardButton.Yes:
                self.open_musicbrainz_settings_dialog()
            path = self.settings.musicbrainz_database
            if not path or not os.path.isfile(path):
                return
        try:
            db = open_database(path)  # fail here, with the file's own message, not once per album
        except MusicBrainzLocalError as exc:
            QMessageBox.warning(self, name, f"{exc}\n\nCheck Tools > MusicBrainz Database Setup...")
            return
        fpcalc = find_tool(FPCALC_EXE_NAME, override=self.settings.fpcalc_path or None)
        dialog = MusicBrainzLocalLookupDialog(group_by_folder(targets), db, self, fpcalc=fpcalc)
        self._apply_lookup_dialog(dialog, "MusicBrainz Lookup (Local Database)")

    def _apply_lookup_dialog(self, dialog, undo_name: str) -> None:
        """Runs a MusicBrainz or Discogs review dialog and applies what was ticked: anything that would
        overwrite a different value goes through the per-field overwrite review, the result is
        one Undo step and is written on Save."""
        from redactor_common.gui.overwrite_review_dialog import resolve_overwrite_conflicts

        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        changes = dialog.file_changes()
        covers = dialog.cover_changes()
        if not changes and not covers:
            self.statusBar().showMessage(f"{undo_name}: nothing to apply (no match ticked, or nothing new found).", 8000)
            return
        files = [mp3 for mp3, _fields in changes]
        approved = {}
        if changes:
            proposed = {index: fields for index, (_mp3, fields) in enumerate(changes)}
            labels = {key: label for key, label, _multiline in FIELDS}
            approved = resolve_overwrite_conflicts(
                self, [_ReviewItem(mp3) for mp3 in files], proposed, lambda key: labels.get(key, key),
            )
            if not approved and not covers:
                self.statusBar().showMessage(f"{undo_name}: nothing applied.", 8000)
                return
        touched = list({id(m): m for m in files + [mp3 for mp3, _data, _mime in covers]}.values())
        self._push_undo(undo_name, touched)
        for index, fields in approved.items():
            files[index].apply_tags(fields)
        for mp3, data, mime in covers:
            mp3.set_cover(data, mime)
        self._after_cover_change()

    def open_discogs_cover_dialog(self) -> None:
        """Metadata > Cover > Get from Discogs...: the Discogs lookup dialog, applying only the cover."""
        self.open_discogs_lookup_dialog(cover_only=True)

    def open_discogs_lookup_dialog(self, cover_only: bool = False) -> None:
        """Metadata > Look Up > Discogs...: like the MusicBrainz lookup (one album per
        folder, reviewed in a dialog, anything that would overwrite a different value goes
        through the per-field overwrite review, one Undo step, written on Save), plus label,
        catalogue number, country and genre/style tags. Needs a Discogs token: without one
        the API Keys dialog opens first (Tools > API Keys)."""
        from core.discogs_lookup import DiscogsClient, load_token
        from gui.discogs_lookup_dialog import DiscogsLookupDialog, group_by_folder

        targets = self._require_targets("look up")
        if not targets:
            return
        token = load_token()
        if not token:
            QMessageBox.information(
                self, "Discogs Token Needed",
                "Look Up via Discogs needs your Discogs token. Enter it in the API Keys dialog next "
                "(create one under Discogs Settings > Developers).",
            )
            self.open_api_keys_dialog()
            token = load_token()
            if not token:
                return
        dialog = DiscogsLookupDialog(group_by_folder(targets), DiscogsClient(token), self, cover_only=cover_only)
        self._apply_lookup_dialog(dialog, "Cover from Discogs" if cover_only else "Discogs Lookup")

    def open_api_keys_dialog(self) -> None:
        """Tools > API Keys...: the Discogs token, kept in the secure credential store."""
        from gui.api_keys_dialog import ApiKeysDialog

        ApiKeysDialog(self, remember_fallback=self._remember_unencrypted_fallback).exec()

    def _store_unencrypted_fallback(self, allowed: bool) -> None:
        self.settings.allow_unencrypted_fallback = allowed
        save_settings(self.settings)

    def open_auto_numbering_dialog(self) -> None:
        """Edit > Auto-Number (also right-click > Organize). Its "Number
        Tracks" preset (gui/auto_number_presets.py) is what the old Metadata
        > Number Tracks did: Track 1..N in table order."""
        # Explicit selection required, same "no silent all-files
        # fallback" reasoning as _require_targets's own docstring --
        # doubly true here since this assigns a DIFFERENT value per
        # row based on table order.
        targets = self._require_targets("number")
        if not targets:
            return

        # Only "track" gets numeric (direct-write) treatment -- the
        # rest are prefixed onto their existing value, same
        # conservative default video's own NUMERIC_FIELDS used (year/
        # genre/etc aren't naturally "a number" the way a track
        # position is).
        fields = [(key, label, key == "track") for key, label, _multiline in FIELDS]
        dialog = AutoNumberingDialog(
            targets, fields,
            get_value=lambda mp3, key: getattr(mp3, key, "") or "",
            get_display_name=lambda mp3: mp3.filename,
            item_noun="file",
            padding=self.settings.auto_number_padding,
            on_padding_changed=self._remember_auto_number_padding,
            parent=self,
        )
        if dialog.exec() != dialog.DialogCode.Accepted:
            return

        changes = dialog.accepted_changes()  # index into targets -> new value
        if not changes:
            return

        field_key = dialog.result_field_key()
        self._push_undo("Auto-Numbering", targets)
        for index, new_value in changes.items():
            targets[index].apply_tags({field_key: new_value})
        self._rebuild_table()

    def open_search_replace_dialog(self) -> None:
        """Edit > Search and Replace...: the shared dialog over the selected
        files (explicit selection, same as Auto-Number). Replacements go
        into memory like a bulk edit -- Save writes them, Undo reverts.
        The Filename column is different: it renames the files on disk
        (the name without its extension) right away, see
        _apply_filename_search_replace()."""
        targets = self._require_targets("search and replace in")
        if not targets:
            return
        labels_by_key = [(key, label) for key, label, _multiline in FIELDS]

        def get_value(mp3: MP3File, key: str) -> str:
            if key == FILENAME_FIELD_KEY:
                return mp3.path.stem  # the extension is never part of the search
            return getattr(mp3, key, "") or ""

        dialog = SearchReplaceDialog(
            targets, labels_by_key,
            get_value=get_value,
            get_display_name=lambda mp3: mp3.filename,
            include_filename=True,
            is_excluded=lambda mp3: bool(mp3.load_error),
            item_noun="file",
            parent=self,
        )
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        changes = dialog.accepted_changes()  # index into targets -> new value (one field)
        if not changes:
            return
        field_key = dialog.result_field_key()
        if field_key == FILENAME_FIELD_KEY:
            self._apply_filename_search_replace(targets, changes)
            return
        changed = [targets[index] for index in changes]
        self._push_undo("Search and Replace", changed)
        for index, new_value in changes.items():
            targets[index].apply_tags({field_key: new_value})
        self._rebuild_table()

    def _apply_filename_search_replace(self, targets: list[MP3File], changes: dict[int, str]) -> None:
        """Renames the files on disk to the new names (stems) the Search and
        Replace dialog accepted. A physical operation like Rename by Pattern:
        not on the Undo stack, but recorded in the rename log, so File > Undo
        Last Rename takes it back. A name that is taken (by a file on disk or
        by another file of this batch) is numbered, never overwritten; a name
        Windows would reject is reported and the file left alone. Only
        MP3File.path changes, so a file with unsaved edits keeps them and
        its next Save writes to the new path. Files that could not be read
        are never offered by the dialog and are mentioned afterwards."""
        taken: set[str] = set()
        errors: list[str] = []
        renamed: list[tuple[str, str]] = []

        def do_one(item: tuple[int, str], _index: int) -> None:
            index, new_stem = item
            mp3 = targets[index]
            old_path = str(mp3.path)
            problem = validate_filename_stem(new_stem)
            if problem:
                errors.append(f"{mp3.filename}: {problem}")
                return
            new_path = unique_path(
                os.path.dirname(old_path), new_stem, mp3.path.suffix, taken, own_path=old_path,
            )
            same_file = os.path.normcase(os.path.abspath(new_path)) == os.path.normcase(os.path.abspath(old_path))
            taken.add(os.path.normcase(os.path.abspath(new_path)))
            if same_file and os.path.basename(new_path) == mp3.filename:
                return  # nothing to change
            try:
                if same_file:
                    os.rename(old_path, new_path)  # only the case differs: the same file, not a clash
                else:
                    rename_no_clobber(old_path, new_path)
            except OSError as exc:
                errors.append(f"{mp3.filename}: {exc}")
                return
            mp3.path = Path(new_path)  # a later Save writes here
            renamed.append((old_path, new_path))

        try:
            run_with_progress(
                self, list(changes.items()), do_one, "Renaming files...",
                threshold=3, cancellable=True,
                label_for=lambda item: f"Renaming: {targets[item[0]].filename}",
            )
        finally:
            # Also after a cancel: what was renamed is logged, so it can be undone.
            _rename_log().record("Search/Replace (filename)", renamed)

        self._rebuild_table()
        unreadable = [mp3.filename for mp3 in targets if mp3.load_error]
        notes: list[str] = []
        if errors:
            notes.append(summarize_errors(errors))
        if unreadable:
            notes.append(f"Skipped, the file could not be read: {summarize_errors(unreadable)}")
        if notes:
            QMessageBox.warning(self, "Some Files Not Renamed", "\n\n".join(notes))

    def open_case_conversion_dialog(self) -> None:
        """Edit > Change Case...: same shape as Search and Replace."""
        targets = self._require_targets("change the case of")
        if not targets:
            return
        labels_by_key = [(key, label) for key, label, _multiline in FIELDS]
        dialog = CaseConversionDialog(
            targets, labels_by_key,
            get_value=lambda mp3, key: getattr(mp3, key, "") or "",
            get_display_name=lambda mp3: mp3.filename,
            is_excluded=lambda mp3: bool(mp3.load_error),
            item_noun="file",
            parent=self,
        )
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        changes = dialog.accepted_changes()
        if not changes:
            return
        field_key = dialog.result_field_key()
        changed = [targets[index] for index in changes]
        self._push_undo("Change Case", changed)
        for index, new_value in changes.items():
            targets[index].apply_tags({field_key: new_value})
        self._rebuild_table()

    def _on_cell_double_clicked(self, row: int, col: int) -> None:
        item = self.table.item(row, col)
        mp3 = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if mp3 is None:
            return
        if col == self._col_index["filename"]:
            if not mp3.load_error:
                self.rename_single_file(mp3)
        elif col == self._col_index["lyrics"]:
            self.open_lyrics_dialog(mp3)

    def rename_single_file(self, mp3: MP3File) -> None:
        """Quick, direct rename of a single file on disk -- for fixing a
        typo or small mistake in the filename without going through the
        pattern-based Rename/Export tool (open_rename_dialog()). Acts on
        disk immediately, not staged until Save -- same as that tool's
        own "rename in place" mode -- and, like that, isn't pushed onto
        self.undo_manager, which only ever covers in-memory tag edits,
        never physical file operations. Triggered by double-clicking a
        Filename cell, or via the table's right-click menu.

        The prompt/validate/rename/error-report flow itself lives in
        redactor_common.gui.rename_single_file (promoted from an
        earlier, near-identical version of this exact method, imported
        above as prompt_rename_single_file to avoid shadowing this
        method's own name) -- this is now just the mp3-specific wiring:
        where the path lives on MP3File, and what to do once it's
        changed."""
        if prompt_rename_single_file(self, str(mp3.path), lambda p: setattr(mp3, "path", Path(p)), log=_rename_log()):
            self._rebuild_table()

    def undo_last_rename(self) -> None:
        """File > Undo Last Rename...: renames the newest logged rename back
        (redactor_common's rename log -- renames aren't on the Undo stack,
        which covers metadata edits only)."""
        def restored(new_path: str, old_path: str) -> None:
            wanted = os.path.normcase(os.path.abspath(new_path))
            for item in self.files:
                if os.path.normcase(os.path.abspath(str(item.path))) == wanted:
                    item.path = Path(old_path)

        if undo_last_rename(self, _rename_log(), restored):
            self._rebuild_table()

    def rename_selected_file(self) -> None:
        """F2 entry point (Explorer convention: select one item, press
        F2, rename it directly) -- same guard the right-click "Rename
        File..." item uses (exactly one file selected, no load error),
        since F2 and that menu item are the same action reached two
        ways."""
        files = self._selected_files()
        if len(files) == 1 and not files[0].load_error:
            self.rename_single_file(files[0])
        else:
            self.statusBar().showMessage(rename_file_refusal(len(files), bool(files and files[0].load_error)), 6000)

    def play_selected_file(self) -> None:
        """File > Play (Ctrl+P), the toolbar Play button and the right-click "Play": a separate,
        read-only player window (redactor_common's media_preview) for the one
        selected file. Opens the file for playback only; nothing is written."""
        files = self._selected_files()
        if len(files) != 1:
            self.statusBar().showMessage(
                "Play plays one file; select exactly one." if files else "Select a file to play.", 6000,
            )
            return
        from redactor_common.gui.media_preview import show_media_preview

        show_media_preview(self, str(files[0].path), video=False)

    # -- tag editing ------------------------------------------------------

    def _apply_bulk_edit(self, values: dict) -> None:
        """values: {field_key: value} for whichever fields were checked
        in the tag panel -- see TagPanel.apply_bulk_edit(). Writes them
        into memory for every currently selected file and marks each
        dirty; does not touch disk (that's save_changed())."""
        # A file that failed to load holds blank defaults, not its real
        # tags -- editing/saving it would overwrite them (see _cover_targets).
        targets = [mp3 for mp3 in self._selected_files() if not mp3.load_error]
        if not targets:
            return
        self._push_undo("Bulk Edit", targets)
        for mp3 in targets:
            mp3.apply_tags(values)
        self._rebuild_table()

    # -- undo ---------------------------------------------------------------
    # In-memory edits only (currently just the bulk-edit Apply above) --
    # never physical file operations (Save, mp3val Fix). MP3File is a
    # flat dataclass with no nested sub-objects (unlike e.g. cbzredactor's
    # CbzBook.metadata), so a shallow dataclasses.replace() snapshot is
    # already a complete, independent copy -- no deepcopy needed.

    @staticmethod
    def _snapshot_mp3(mp3: MP3File) -> MP3File:
        return dataclasses.replace(mp3)

    # Fields an undo/redo must NOT roll back: `path` (a rename is a disk
    # operation Undo doesn't cover -- restoring the old path would point
    # at a file that no longer exists) and `tag_baseline` (what's on disk
    # now, which a Save since the snapshot has changed).
    _UNDO_KEEPS = frozenset({"path", "tag_baseline"})

    @staticmethod
    def _restore_mp3(mp3: MP3File, snapshot: MP3File) -> None:
        for f in dataclasses.fields(MP3File):
            if f.name not in MainWindow._UNDO_KEEPS:
                setattr(mp3, f.name, getattr(snapshot, f.name))
        # The restored values may differ from disk (e.g. after a Save
        # since the snapshot), so they must be saved again.
        mp3.dirty = True

    def _push_undo(self, label: str, targets: list[MP3File]) -> None:
        self.undo_manager.push(label, targets, self._snapshot_mp3)
        self._update_undo_action()
        self._update_redo_action()  # push() clears any pending redo

    def _update_undo_action(self) -> None:
        can_undo = self.undo_manager.can_undo()
        self.action_undo.setEnabled(can_undo)
        label = self.undo_manager.peek_label()
        self.action_undo.setText(f"&Undo {label}" if label else "&Undo")

    def _update_redo_action(self) -> None:
        can_redo = self.undo_manager.can_redo()
        self.action_redo.setEnabled(can_redo)
        label = self.undo_manager.peek_redo_label()
        self.action_redo.setText(f"&Redo {label}" if label else "&Redo")

    def undo_last_action(self) -> None:
        # snapshot_fn passed too (not just restore_fn) so the state
        # being overwritten is captured onto the redo stack first --
        # see redactor_common.core.undo's own docstring.
        affected = self.undo_manager.undo(self._restore_mp3, self._snapshot_mp3)
        if affected:
            self._rebuild_table()
        self._update_undo_action()
        self._update_redo_action()

    def redo_last_action(self) -> None:
        affected = self.undo_manager.redo(self._restore_mp3, self._snapshot_mp3)
        if affected:
            self._rebuild_table()
        self._update_undo_action()
        self._update_redo_action()

    def save_changed(self) -> None:
        """File > Save All (Ctrl+Shift+A, Ctrl+S): every file with unsaved tag changes."""
        # Catches a field that's ticked with a value typed in but not
        # yet Applied -- Save should act on it too, not silently drop it.
        self.tag_panel.apply_bulk_edit()

        dirty_files = [mp3 for mp3 in self.files if mp3.dirty and not mp3.load_error]
        if not dirty_files:
            QMessageBox.information(self, "Nothing to Save", "No unsaved tag changes.")
            return

        def step(mp3: MP3File, _index: int) -> None:
            save_dirty_tags([mp3])

        run_with_progress(
            self, dirty_files, step, "Saving tags...", threshold=3,
            label_for=lambda mp3: f"Saving: {mp3.filename}",
        )
        self._rebuild_table()
        self._show_journal_problem()

        failed = [f"{mp3.filename}: {mp3.save_error}" for mp3 in dirty_files if mp3.save_error]
        if failed:
            QMessageBox.warning(
                self, "Some Files Failed to Save",
                f"{len(failed)} of {len(dirty_files)} file(s) could not be saved:\n\n"
                f"{summarize_errors(failed)}",
            )

    def _count_dirty(self) -> int:
        return sum(1 for mp3 in self.files if mp3.dirty)

    def _confirm_discard(self, action_desc: str) -> bool:
        reply = QMessageBox.question(
            self,
            "Unsaved changes",
            f"You have unsaved tag changes. Are you sure you want to {action_desc}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        return reply == QMessageBox.StandardButton.Yes

    def closeEvent(self, event) -> None:
        if self._count_dirty() and not self._confirm_discard("quit without saving"):
            event.ignore()
            return
        super().closeEvent(event)

    # -- quick-pick (Genre/Language "+") -----------------------------------

    def _on_quick_pick_requested(self, key: str) -> None:
        if key == "genre":
            dialog = QuickPickDialog(
                "Pick Genre",
                load_entries_fn=lambda: [(g, g) for g in self._load_genres()],
                multi_select=False,  # replaces the field -- see core/mp3_genres.py's docstring
                add_custom_fn=self._add_custom_genre,
                parent=self,
            )
            if dialog.exec() == QuickPickDialog.DialogCode.Accepted:
                picked = dialog.selected_keys()
                if picked:
                    self.tag_panel.set_picked_value("genre", picked[0])
        elif key == "language":
            dialog = QuickPickDialog(
                "Pick Language",
                load_entries_fn=lambda: [(c, f"{n} ({c})") for c, n in self._load_languages()],
                multi_select=False,  # replaces the field outright
                add_custom_fn=self._add_custom_language,
                parent=self,
            )
            if dialog.exec() == QuickPickDialog.DialogCode.Accepted:
                picked = dialog.selected_keys()
                if picked:
                    self.tag_panel.set_picked_value("language", picked[0])

    def _visible_default_genres(self) -> list[str]:
        return exclude_hidden_genres(COMMON_MP3_GENRES, self.settings.hidden_default_genres)

    def _load_genres(self) -> list[str]:
        """Visible (non-hidden) default genres plus any custom ones
        added previously -- what the quick-pick "+" button shows."""
        return merge_genres(self._visible_default_genres(), self.settings.custom_genres)

    def _add_custom_genre(self, parent_widget) -> None:
        """Shared Add-custom handler for both the quick-pick dialog's
        "Add Custom..." button and Tools > Genres...'s
        "Add..." button -- both hand this the same shape, a widget to
        parent the prompt against."""
        text, ok = QInputDialog.getText(parent_widget, "Add Custom Genre", "New genre name:")
        text = text.strip()
        if ok and text and not any(g.lower() == text.lower() for g in self.settings.custom_genres):
            self.settings.custom_genres.append(text)
            save_settings(self.settings)

    def _remove_custom_genre(self, genre: str) -> None:
        self.settings.custom_genres = [
            g for g in self.settings.custom_genres if g.lower() != genre.lower()
        ]
        save_settings(self.settings)

    def _hide_default_genre(self, genre: str) -> None:
        if not any(g.lower() == genre.lower() for g in self.settings.hidden_default_genres):
            self.settings.hidden_default_genres.append(genre)
            save_settings(self.settings)

    def _restore_default_genres(self) -> None:
        self.settings.hidden_default_genres = []
        save_settings(self.settings)

    def _visible_default_languages(self) -> list[tuple[str, str]]:
        return exclude_hidden_languages(DEFAULT_LANGUAGES, self.settings.hidden_default_languages)

    def _load_languages(self) -> list[tuple[str, str]]:
        return merge_languages(self._visible_default_languages(), self.settings.custom_languages)

    def _add_custom_language(self, parent_widget) -> None:
        code, ok = QInputDialog.getText(
            parent_widget, "Add Custom Language",
            'Language code (ISO 639-2, e.g. "por" for Portuguese):',
        )
        code = code.strip()
        if not (ok and code):
            return
        name, ok = QInputDialog.getText(parent_widget, "Add Custom Language", "Display name for this language:")
        name = name.strip()
        if ok and name and not any(c == code for c, _n in self.settings.custom_languages):
            self.settings.custom_languages.append((code, name))
            save_settings(self.settings)

    def _remove_custom_language(self, code: str) -> None:
        self.settings.custom_languages = [
            (c, n) for c, n in self.settings.custom_languages if c != code
        ]
        save_settings(self.settings)

    def _hide_default_language(self, code: str) -> None:
        if code not in self.settings.hidden_default_languages:
            self.settings.hidden_default_languages.append(code)
            save_settings(self.settings)

    def _restore_default_languages(self) -> None:
        self.settings.hidden_default_languages = []
        save_settings(self.settings)

    def open_genre_settings_dialog(self) -> None:
        dialog = ManageListDialog(
            "Add/Remove Genres",
            load_defaults_fn=lambda: [(g, g) for g in self._visible_default_genres()],
            load_custom_fn=lambda: [(g, g) for g in self.settings.custom_genres],
            add_dialog_fn=self._add_custom_genre,
            remove_custom_fn=self._remove_custom_genre,
            hide_default_fn=self._hide_default_genre,
            restore_defaults_fn=self._restore_default_genres,
            parent=self,
        )
        dialog.exec()

    def open_language_settings_dialog(self) -> None:
        dialog = ManageListDialog(
            "Add/Remove Languages",
            load_defaults_fn=lambda: [(c, f"{n} ({c})") for c, n in self._visible_default_languages()],
            load_custom_fn=lambda: [(c, f"{n} ({c})") for c, n in self.settings.custom_languages],
            add_dialog_fn=self._add_custom_language,
            remove_custom_fn=self._remove_custom_language,
            hide_default_fn=self._hide_default_language,
            restore_defaults_fn=self._restore_default_languages,
            parent=self,
        )
        dialog.exec()

    # -- selection / tag panel --------------------------------------------

    def _on_table_selection_changed(self) -> None:
        self.tag_panel.set_selection(self._selected_files())
        self._update_cover_panel()

    def _on_tag_panel_selection_count_changed(self, count: int) -> None:
        set_apply_count(self.action_apply_bulk_edit, count)

    def _toggle_tag_panel(self) -> None:
        self._panel_collapser.toggle()
        self._sync_tag_panel_collapsed_indicator()

    def _sync_tag_panel_collapsed_indicator(self) -> None:
        collapsed = self._panel_collapser.is_collapsed()
        self.tag_panel.set_collapsed_indicator(collapsed)
        # View > Show Metadata Panel mirrors the panel (setChecked doesn't
        # fire triggered, so this can't loop back into the toggle).
        action = getattr(self, "action_show_panel", None)
        if action is not None:
            action.setChecked(not collapsed)

    # -- columns: order/visibility, persisted by field key -----------------

    def _setup_column_persistence(self) -> None:
        header = self.table.horizontalHeader()
        header.setSectionsMovable(True)  # drag headers to reorder columns
        header.sectionMoved.connect(self._on_columns_reordered)
        header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        header.customContextMenuRequested.connect(self._show_header_context_menu)

        # A genuinely first run (never touched column visibility at all)
        # gets DEFAULT_HIDDEN_COLUMNS (currently: nothing, see its own
        # comment). Once the user has saved ANY choice, that saved
        # choice always wins -- see Settings.has_column_preference.
        raw_hidden = (
            set(self.settings.hidden_columns)
            if self.settings.has_column_preference
            else set(DEFAULT_HIDDEN_COLUMNS)
        )
        hidden = sanitize_hidden_fields(raw_hidden, PROTECTED_COLUMNS)
        for key in hidden:
            if key in self._col_index:
                self.table.setColumnHidden(self._col_index[key], True)

    def _on_columns_reordered(self, *_args) -> None:
        """`*_args` absorbs QHeaderView.sectionMoved's (logical,
        old_visual, new_visual) arguments -- not needed here, we just
        re-read the header's current full visual order and persist it."""
        header = self.table.horizontalHeader()
        visual_order = [self._column_keys[header.logicalIndex(v)] for v in range(header.count())]
        self.settings.column_order = visual_order
        save_settings(self.settings)

    def _on_column_visibility_toggled(self, key: str, visible: bool) -> None:
        if key not in self._col_index:
            return
        self.table.setColumnHidden(self._col_index[key], not visible)
        self._save_hidden_columns()
        self._sync_panel_visible_fields()

    def _save_hidden_columns(self) -> None:
        hidden = {k for k in self._column_keys if self.table.isColumnHidden(self._col_index[k])}
        self.settings.hidden_columns = sorted(sanitize_hidden_fields(hidden, PROTECTED_COLUMNS))
        self.settings.has_column_preference = True
        save_settings(self.settings)

    def _sync_panel_visible_fields(self) -> None:
        """Keeps the bulk-edit panel's visible rows in lock-step with
        which columns are currently shown in the table -- hiding a
        column also stops cluttering the panel with a field you said
        you don't care about, and un-hiding a column brings its row
        straight back. The field's data is untouched either way (see
        TagPanel.set_visible_fields()'s own docstring) -- this only
        ever changes what's drawn on screen."""
        hidden = {k for k in self._column_keys if self.table.isColumnHidden(self._col_index[k])}
        field_keys = {key for key, _l, _m in FIELDS}
        self.tag_panel.set_visible_fields(field_keys - hidden)

    def _show_header_context_menu(self, pos) -> None:
        hidden = {k for k in self._column_keys if self.table.isColumnHidden(self._col_index[k])}
        show_column_header_context_menu(
            self, self.table, pos,
            column_order=self._column_keys,
            label_lookup=_COLUMN_LABELS,
            protected_columns=PROTECTED_COLUMNS,
            hidden_fields=hidden,
            is_visible=lambda key, hidden_set: is_column_visible(key, hidden_set, PROTECTED_COLUMNS),
            on_toggle=self._on_column_visibility_toggled,
            open_column_settings_dialog=self.open_column_settings_dialog,
        )

    def open_column_settings_dialog(self) -> None:
        all_columns = [(key, _COLUMN_LABELS[key]) for key in self._column_keys]
        hidden = {k for k in self._column_keys if self.table.isColumnHidden(self._col_index[k])}
        dialog = ColumnSettingsDialog(all_columns, hidden, PROTECTED_COLUMNS, self)
        dialog.exec()
        new_hidden = dialog.hidden_fields()
        for key in self._column_keys:
            self.table.setColumnHidden(self._col_index[key], key in new_hidden)
        self.settings.hidden_columns = sorted(sanitize_hidden_fields(new_hidden, PROTECTED_COLUMNS))
        self.settings.has_column_preference = True
        save_settings(self.settings)
        self._sync_panel_visible_fields()

    # -- checks ---------------------------------------------------------

    def open_settings_dialog(self) -> None:
        # Shared Preferences dialog (gui/preferences.py): edits and saves self.settings in place.
        build_preferences_dialog(self.settings, save_settings, self).exec()

    # -- export / import settings (redactor_common's shared settings bundle) --

    def _settings_adapter(self) -> Mp3SettingsAdapter:
        # get_settings is a lambda because the Preferences dialog replaces
        # self.settings with a new object.
        return Mp3SettingsAdapter(
            lambda: self.settings, APP_VERSION, redetect_tools=self.open_external_tools_dialog
        )

    def export_settings_file(self) -> None:
        export_settings(self, self._settings_adapter())

    def import_settings_file(self) -> None:
        import_settings(self, self._settings_adapter(), on_applied=self._on_settings_imported)

    def _on_settings_imported(self, result) -> None:
        """Brings the live window in line with imported settings. Patterns,
        the recipe, genre/language lists, field defaults and tool paths are
        read from self.settings each time they're used, so only the column
        layout (and the panel rows that follow it) needs re-applying."""
        if "columns" in result.applied:
            self._apply_column_settings()

    def _apply_column_settings(self) -> None:
        hidden = sanitize_hidden_fields(
            set(self.settings.hidden_columns) if self.settings.has_column_preference
            else set(DEFAULT_HIDDEN_COLUMNS),
            PROTECTED_COLUMNS,
        )
        for key in self._column_keys:
            self.table.setColumnHidden(self._col_index[key], key in hidden)
        # Reorder to the imported visual order; signals blocked so moving
        # one section at a time doesn't persist a half-moved order.
        order = merge_column_order(self.settings.column_order, ALL_COLUMN_KEYS)
        header = self.table.horizontalHeader()
        blocked = header.blockSignals(True)
        try:
            for visual, key in enumerate(order):
                header.moveSection(header.visualIndex(self._col_index[key]), visual)
        finally:
            header.blockSignals(blocked)
        self._sync_panel_visible_fields()

    def open_external_tools_dialog(self) -> None:
        dialog = ExternalToolsDialog(self.settings, self)
        if dialog.exec() == ExternalToolsDialog.DialogCode.Accepted:
            mp3val_path, keyfinder_cli_path, ffmpeg_path, ffprobe_path, fpcalc_path = dialog.result_paths()
            self.settings.mp3val_path = mp3val_path
            self.settings.keyfinder_cli_path = keyfinder_cli_path
            self.settings.ffmpeg_path = ffmpeg_path
            self.settings.ffprobe_path = ffprobe_path
            self.settings.fpcalc_path = fpcalc_path
            save_settings(self.settings)

    def open_musicbrainz_settings_dialog(self) -> None:
        """Tools > MusicBrainz Database Setup...: where the offline MusicBrainz database
        is and how to build it from the core dump (gui/musicbrainz_settings_dialog.py)."""
        from gui.musicbrainz_settings_dialog import MusicBrainzSettingsDialog

        MusicBrainzSettingsDialog(self.settings, self).exec()

    # -- redact ---------------------------------------------------------------

    def _redact_recipe(self):
        return recipe_from_setting(self.settings.redact_recipe, build_catalogue(self.settings))

    def edit_redact_recipe(self) -> None:
        """Edit > Edit Redact Recipe...: the shared recipe editor over
        this app's steps; the result is stored in the settings file."""
        catalogue = build_catalogue(self.settings)
        recipe = self._redact_recipe()
        if not self.settings.redact_recipe.strip():
            # Never saved: pre-fill each pattern with its current value so OK pins it.
            recipe = pin_patterns(recipe, catalogue)
        dialog = RecipeEditorDialog(catalogue, recipe, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.settings.redact_recipe = recipe_to_setting(dialog.recipe())
            save_settings(self.settings)

    def _redact_targets(self) -> list[MP3File]:
        """The selected files, else -- after asking -- every loaded file.
        (Unlike the other batch actions, nothing selected is not an
        error: Redact is meant to be one click on a whole folder.)"""
        targets = self._selected_files()
        if targets:
            return targets
        if not self.files:
            QMessageBox.information(self, "No Files Loaded", "Open some files first.")
            return []
        reply = QMessageBox.question(
            self,
            "Redact all files?",
            f"Nothing is selected. Redact all {len(self.files)} loaded file(s)?\n\n"
            "Each file is fixed and saved in place; the original goes to the Recycle Bin.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        )
        return list(self.files) if reply == QMessageBox.StandardButton.Yes else []

    def redact_files(self) -> None:
        """Edit > Redact (Ctrl+Shift+E): runs the saved recipe on the
        targets through redactor_common's engine (progress, cancel, results
        with Needs review). A file with unsaved edits is skipped and named
        in the report, never silently overwritten; a file that failed to
        load is skipped too. Redact is not on the Undo stack -- the Recycle
        Bin copy of each original is the undo -- so the stack is cleared,
        like Refresh List does, rather than left pointing at old states."""
        targets = self._redact_targets()
        if not targets:
            return
        unsaved = [mp3 for mp3 in targets if mp3.dirty and not mp3.stamp_only_dirty]
        if unsaved and QMessageBox.question(
            self,
            "Unsaved changes",
            f"{len(unsaved)} of the {len(targets)} file(s) have unsaved edits. Redact works on saved "
            "files, so those will be skipped (and listed in the report). Continue with the rest?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        ) != QMessageBox.StandardButton.Yes:
            return

        env = RedactEnv(self.settings, rename_log=_rename_log())
        env.begin(targets)
        recipe = self._redact_recipe()
        report = run_redact_dialog(
            self, targets, recipe, build_catalogue(self.settings),
            make_context=lambda mp3: Mp3Ctx(mp3, env),
            describe=lambda mp3: mp3.filename,
            show_results=False,
            finalize=save_stage,
            finalize_label=FINALIZE_LABEL,
        )
        self.undo_manager.clear()
        self._update_undo_action()
        self._update_redo_action()
        self._after_cover_change()  # table rebuild + lazily reloaded cover thumbnails
        self._show_journal_problem()
        if report is not None:
            cleaned = (
                [f"Removed {env.cleaned} leftover scratch file(s) of an earlier interrupted Redact."]
                if env.cleaned else []
            )
            RedactResultsDialog(
                report, self, title="Redact results", header=REDACT_UNDO_NOTE, extra_notes=cleaned,
            ).exec()

    def run_integrity_check(self) -> None:
        self._run_check_with_progress(
            "Checking file integrity...",
            run_integrity_check,
            self._selected_files(),
            mp3val_path=self.settings.mp3val_path or None,
        )

    def run_integrity_fix(self) -> None:
        targets = self._selected_files()
        if not targets:
            QMessageBox.information(
                self, "No Files Selected", "Select one or more files in the table to fix."
            )
            return

        backup_note = (
            "mp3val will delete the .bak backup once each fix completes "
            "(per your Settings)."
            if self.settings.delete_backup_after_fix
            else "mp3val creates a .bak backup of each original file before "
            "modifying it."
        )
        reply = QMessageBox.question(
            self,
            "Fix File Integrity Issues?",
            f"Attempt to fix {len(targets)} file(s) with mp3val?\n\n"
            f"{backup_note} Not every issue it detects is fixable -- some "
            "files may still show WARNING/ERROR afterward.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._run_fix_with_progress(targets)

    def _run_fix_with_progress(self, targets: list[MP3File]) -> None:
        delete_backup, mp3val_path = self.settings.delete_backup_after_fix, self.settings.mp3val_path or None
        self._run_per_file_in_background(
            "Fixing file integrity issues...", targets,
            lambda target: run_integrity_fix([target], delete_backup=delete_backup, mp3val_path=mp3val_path),
        )

    def run_bpm_check(self) -> None:
        self._run_concurrent_check_with_progress("Detecting BPM...", run_bpm_check)

    def run_key_detection(self) -> None:
        self._run_concurrent_check_with_progress(
            "Detecting key...",
            run_key_detection,
            keyfinder_cli_path=self.settings.keyfinder_cli_path or None,
        )

    def run_deep_check(self) -> None:
        self._run_concurrent_check_with_progress(
            "Deep-checking integrity (full decode)...",
            run_deep_check,
            ffmpeg_path=self.settings.ffmpeg_path or None,
            ffprobe_path=self.settings.ffprobe_path or None,
        )

    def run_loudness_measurement(self) -> None:
        self._run_concurrent_check_with_progress(
            "Measuring loudness...",
            run_loudness_measurement,
            ffmpeg_path=self.settings.ffmpeg_path or None,
        )

    def run_lyrics_fetch(self) -> None:
        self._run_concurrent_check_with_progress("Fetching lyrics...", run_lyrics_fetch)

    def edit_lyrics_for_selection(self) -> None:
        """Metadata menu entry point for Edit Lyrics... -- same
        "exactly one file" convention as rename_selected_file(), since
        this opens a single-file dialog. The table's right-click menu
        reaches the same dialog directly (see _show_context_menu's
        extra_items()), already scoped to the clicked row(s)."""
        files = self._selected_files()
        if len(files) == 1:
            self.open_lyrics_dialog(files[0])
        elif not files:
            QMessageBox.information(self, "No Files Selected", "Select a file in the table.")
        else:
            QMessageBox.information(self, "Select One File", "Select exactly one file to edit its lyrics.")

    def open_lyrics_dialog(self, mp3: MP3File) -> None:
        """Edit Lyrics... -- see gui/lyrics_dialog.py's own docstring
        for why this is a dedicated dialog rather than a
        core.fields.FIELDS row. Triggered by double-clicking a file's
        Lyrics cell, or via the Metadata menu / table's right-click
        menu."""
        if mp3.load_error:
            return  # blank defaults, not its real tags -- see _apply_bulk_edit()
        dialog = LyricsDialog(mp3, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        new_lyrics = dialog.result_lyrics()
        if new_lyrics == mp3.lyrics:
            return
        self._push_undo("Edit Lyrics", [mp3])
        mp3.lyrics = new_lyrics
        mp3.lyrics_status = STATUS_OK if new_lyrics else STATUS_UNCHECKED
        mp3.lyrics_message = ""
        mp3.dirty = True
        self._rebuild_table()

    def _run_concurrent_check_with_progress(self, label: str, scan_fn, **extra_kwargs) -> None:
        """Shared driver for the two checks (run_bpm_check(),
        run_key_detection()) whose core.scan_service function runs a
        thread pool across the whole batch rather than one file at a
        time -- see either one's own docstring for why.

        Deliberately NOT routed through _run_check_with_progress -- that
        helper (and run_with_progress underneath it) iterates targets
        one at a time itself, which would mean scan_fn is never actually
        handed more than a single file and its internal thread pool
        never gets to run more than one file at once, silently defeating
        the whole point. This drives the dialog from scan_fn's own
        progress/should_cancel callback contract instead, so the whole
        selection is dispatched together and genuinely runs concurrently.
        """
        targets = self._selected_files()
        if not targets:
            if not self.files:
                QMessageBox.information(self, "No Files Loaded", "Open some files first.")
            else:
                QMessageBox.information(
                    self, "No Files Selected", "Select one or more files in the table."
                )
            return

        # The whole batch runs on a worker thread: one long file (keyfinder or
        # a loudness pass over an hour-long mix) no longer freezes the window.
        _result, cancelled = run_work_with_progress(
            self, len(targets), label,
            lambda progress: scan_fn(
                targets, progress=progress.on_progress, should_cancel=progress.should_cancel, **extra_kwargs,
            ),
        )
        self._rebuild_table()
        if cancelled:
            self.statusBar().showMessage(f"{label.rstrip('.')} cancelled; files not reached yet were left as they were.", 8000)

    def _selected_files(self) -> list[MP3File]:
        """The selected files the user can see: a row hidden by Edit > Filter
        List is never a target, even if Select All reached it."""
        seen: dict[int, MP3File] = {}
        for item in self.table.selectedItems():
            if self.table.isRowHidden(item.row()):
                continue
            mp3 = item.data(Qt.ItemDataRole.UserRole)
            if mp3 is not None:
                seen[id(mp3)] = mp3
        return list(seen.values())

    def _run_check_with_progress(
        self, label: str, check_fn, targets: list[MP3File], **extra_kwargs
    ) -> None:
        if not targets:
            if not self.files:
                QMessageBox.information(self, "No Files Loaded", "Open some files first.")
            else:
                QMessageBox.information(
                    self, "No Files Selected", "Select one or more files in the table."
                )
            return

        self._run_per_file_in_background(label, targets, lambda target: check_fn([target], **extra_kwargs))

    def _run_per_file_in_background(self, label: str, targets: list[MP3File], one) -> None:
        """Calls `one(file)` for each target on a worker thread under a counted
        progress dialog (mp3val runs per file: the window keeps painting while
        one runs), Cancel stopping between files; then rebuilds the table."""

        def work(progress) -> None:
            for index, target in enumerate(targets):
                if progress.should_cancel():
                    return
                progress.set_label(f"{label.rstrip('.')}: {target.filename}")
                one(target)
                progress.on_progress(index + 1)

        _result, cancelled = run_work_with_progress(self, len(targets), label, work)
        self._rebuild_table()
        if cancelled:
            self.statusBar().showMessage(f"{label.rstrip('.')} cancelled; files not reached yet were left as they were.", 8000)

    # -- table ------------------------------------------------------------

    # -- cover art ------------------------------------------------------------
    # In-memory, undoable edits like a bulk edit: written on Save (see
    # core/tag_writer.py's _write_cover_frame()).

    def _cover_targets(self, action_desc: str, need_cover: bool = False) -> list[MP3File]:
        selected = self._require_targets(action_desc)
        targets = [mp3 for mp3 in selected if not mp3.load_error]
        if need_cover:
            targets = [mp3 for mp3 in targets if mp3.has_cover]
        if selected and not targets:
            # Not silent: say why nothing happened.
            reason = "none of the selected files has a cover" if need_cover else "the selected files could not be read"
            self.statusBar().showMessage(f"Nothing to {action_desc}: {reason}.", 8000)
        return targets

    def _after_cover_change(self) -> None:
        self._rebuild_table()
        self._visible_rows.schedule()

    def set_cover_from_file(self) -> None:
        targets = self._cover_targets("set a cover on")
        if not targets:
            return
        path, _filter = QFileDialog.getOpenFileName(
            self, "Choose Cover Image", resolve_start_directory(self.settings.last_directory),
            IMAGE_FILE_FILTER,
        )
        if not path:
            return
        self._remember_last_directory(path)
        try:
            data, mime = cover_from_file(path)
        except CoverImageError as exc:
            QMessageBox.warning(self, "Cannot Use Image", str(exc))
            return
        self._push_undo("Set Cover", targets)
        for mp3 in targets:
            mp3.set_cover(data, mime)
        self._after_cover_change()

    def set_cover_from_folder_images(self) -> None:
        """For each selected file, embeds the album-art image sitting next
        to it (cover.jpg, folder.jpg, front.jpg, ... -- see
        core.cover_art.FOLDER_IMAGE_NAMES), so a multi-album selection
        gets each album's own art in one go."""
        targets = self._cover_targets("set covers on")
        if not targets:
            return
        found: dict[int, tuple[bytes, str]] = {}
        missing: list[str] = []
        errors: list[str] = []
        by_image: dict[str, tuple[bytes, str]] = {}  # each folder image read once

        def step(mp3: MP3File, index: int) -> None:
            image_path = find_folder_image(mp3.path)
            if image_path is None:
                missing.append(mp3.filename)
                return
            key = str(image_path)
            if key not in by_image:
                try:
                    by_image[key] = cover_from_file(image_path)
                except CoverImageError as exc:
                    errors.append(f"{image_path.name}: {exc}")
                    return
            found[index] = by_image[key]

        run_with_progress(
            self, targets, step, "Looking for folder images...", threshold=3, cancellable=False,
            label_for=lambda mp3: f"Checking: {mp3.filename}",
        )
        if found:
            changed = [targets[i] for i in found]
            self._push_undo("Set Cover from Folder", changed)
            for index, (data, mime) in found.items():
                targets[index].set_cover(data, mime)
            self._after_cover_change()

        # A routine result goes to the status bar; only an unreadable image is a box.
        message = f"Set a cover on {len(found)} of {len(targets)} file(s)."
        if missing:
            message += f" No cover.jpg / folder.jpg / front.jpg next to {len(missing)} file(s)."
        if errors:
            QMessageBox.warning(
                self, "Cover from Folder Image", f"{message}\n\nUnreadable image(s): {summarize_errors(errors)}"
            )
        else:
            self.statusBar().showMessage(message + (" Save All writes them to the files." if found else ""), 10000)

    def remove_cover(self) -> None:
        targets = self._cover_targets("remove the cover from", need_cover=True)
        if not targets:
            return
        self._push_undo("Remove Cover", targets)
        for mp3 in targets:
            mp3.remove_cover()
        self._after_cover_change()

    def export_cover(self) -> None:
        targets = self._cover_targets("export the cover of", need_cover=True)
        if len(targets) != 1:
            if targets:
                self.statusBar().showMessage(
                    f"Export Cover exports one file's cover; {len(targets)} files with a cover are selected.", 8000
                )
            return
        mp3 = targets[0]
        cover = mp3.cover_bytes()
        if cover is None:
            QMessageBox.warning(self, "Export Cover", f"{mp3.filename} has no readable cover.")
            return
        data, mime = cover
        default = str(mp3.path.with_name(mp3.path.stem + " cover" + extension_for(mime)))
        path, _filter = QFileDialog.getSaveFileName(
            self, "Export Cover", default, f"Image (*{extension_for(mime)})",
        )
        if not path:
            return
        try:
            Path(path).write_bytes(data)
        except OSError as exc:
            QMessageBox.warning(self, "Export Cover", f"Could not write the image: {exc}")

    def _update_cover_panel(self) -> None:
        # The panel's big Play button follows the same single-selection rule as File > Play.
        self.tag_panel.play_btn.setEnabled(len(self._selected_files()) == 1)
        selected = [mp3 for mp3 in self._selected_files() if not mp3.load_error]
        self.tag_panel.set_cover_actions_enabled(
            any_selected=bool(selected),
            any_cover=any(mp3.has_cover for mp3 in selected),
            single_with_cover=len(selected) == 1 and bool(selected[0].has_cover),
        )
        if len(selected) != 1:
            self._cover_preview.cancel()
            if not selected:
                self.tag_panel.show_cover_message("No file selected")
            else:
                with_cover = sum(1 for mp3 in selected if mp3.has_cover)
                self.tag_panel.show_cover_message(
                    f"{len(selected)} files selected", f"{with_cover} with a cover"
                )
            return
        mp3 = selected[0]
        note = "Not saved yet" if mp3.cover_change_pending else ""
        if not mp3.has_cover:
            self._cover_preview.cancel()
            self.tag_panel.show_cover_message("No cover", note)
            return
        self.tag_panel.show_cover_loading(note)
        self._cover_preview.request(mp3, mp3.cover_image_bytes)

    def _on_cover_preview_ready(self, mp3: MP3File, image: QImage) -> None:
        self.tag_panel.show_cover_image(image, "Not saved yet" if mp3.cover_change_pending else "")

    def _load_cover_icons_for_rows(self, rows: list[int]) -> None:
        """VisibleRowsWatcher callback: request thumbnails for these (on
        screen) rows only -- and none at all while the Cover column is
        hidden."""
        col = self._col_index["cover"]
        if self.table.isColumnHidden(col):
            return
        for row in rows:
            item = self.table.item(row, col)
            mp3 = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
            if mp3 is None or mp3.load_error or not mp3.has_cover:
                continue
            try:
                mtime = mp3.path.stat().st_mtime
            except OSError:
                continue
            source = (str(mp3.path), mtime, mp3.cover_version)
            self._cover_source[mp3] = source
            cached = self._cover_icons.get_cached_icon(mp3, source)
            if cached is not None:
                item.setIcon(cached)
                continue
            self._cover_icons.request(mp3, source, loader=mp3.cover_image_bytes)

    def _on_cover_icon_ready(self, mp3: MP3File, icon: QIcon) -> None:
        """A background decode finished: set that one cell's icon, via the
        item captured at population time (Qt's sort moves items between
        rows without recreating them)."""
        item = self._cover_item_by_file.get(mp3)
        if item is None or not mp3.has_cover:
            return
        try:
            item.setIcon(icon)
        except RuntimeError:
            pass  # the row was rebuilt/removed meanwhile; the next visible pass catches up

    def _rebuild_table(self) -> None:
        # Rows are refilled in self.files order and then re-sorted, so a
        # selection kept by row number would land on whichever file now
        # sits in that row. Found 2026-09-29: with the (default)
        # descending sort, Apply on a.mp3 left c.mp3 selected, and the
        # NEXT Apply silently edited c.mp3. Reselect by file instead.
        selected_ids = {id(mp3) for mp3 in self._selected_files()}
        with suspend_sorting(self.table):
            self.table.setRowCount(len(self.files))
            for row, mp3 in enumerate(self.files):
                self._populate_row(row, mp3)
        self._reselect_files(selected_ids)
        self._apply_list_filter()  # rows were rebuilt (and re-sorted): hide again by file
        # Rows were just torn down and rebuilt from scratch, so whatever
        # rows Qt now considers "selected" may not match what the tag
        # panel is showing -- keep the two in sync explicitly rather
        # than relying on itemSelectionChanged firing on its own here.
        self.tag_panel.set_selection(self._selected_files())
        self._update_cover_panel()

    def _reselect_files(self, file_ids: set[int]) -> None:
        """Selects exactly the rows holding these MP3Files (by id()),
        signals blocked -- the caller resyncs the panel once."""
        col = self._col_index["filename"]
        selection = QItemSelection()
        first_row = -1
        for row in range(self.table.rowCount()):
            item = self.table.item(row, col)
            if item is not None and id(item.data(Qt.ItemDataRole.UserRole)) in file_ids:
                left = self.table.model().index(row, 0)
                right = self.table.model().index(row, self.table.columnCount() - 1)
                selection.select(left, right)
                if first_row < 0:
                    first_row = row
        model = self.table.selectionModel()
        was_blocked = self.table.blockSignals(True)
        try:
            model.clearSelection()
            if first_row >= 0:
                model.setCurrentIndex(
                    self.table.model().index(first_row, col), QItemSelectionModel.SelectionFlag.NoUpdate,
                )
                model.select(selection, QItemSelectionModel.SelectionFlag.Select)
        finally:
            self.table.blockSignals(was_blocked)

    def _populate_row(self, row: int, mp3: MP3File) -> None:
        filename_item = QTableWidgetItem(mp3.filename)
        filename_item.setData(Qt.ItemDataRole.UserRole, mp3)
        self.table.setItem(row, self._col_index["filename"], filename_item)

        path_item = QTableWidgetItem(str(mp3.path))
        path_item.setData(Qt.ItemDataRole.UserRole, mp3)
        self.table.setItem(row, self._col_index["path"], path_item)

        for key, _label, _m in FIELDS:
            value = getattr(mp3, key, "") or ""
            # Track/Year sort numerically ("2" before "10") -- everything
            # else (Title/Artist/...) is genuinely textual, where a
            # numeric compare would just always fall through to the same
            # text compare anyway.
            item = NumericTableWidgetItem(value) if key in NUMERIC_FILENAME_FIELDS else QTableWidgetItem(value)
            item.setData(Qt.ItemDataRole.UserRole, mp3)
            if mp3.dirty:
                item.setBackground(DIRTY_COLOR)
                item.setForeground(HIGHLIGHT_TEXT_COLOR)
            self.table.setItem(row, self._col_index[key], item)

        integrity_item = QTableWidgetItem(self._integrity_display(mp3))
        integrity_item.setData(Qt.ItemDataRole.UserRole, mp3)
        color = STATUS_COLORS.get(mp3.integrity_status)
        if color is not None:
            integrity_item.setForeground(color)
        tip = scan_tooltip(mp3.integrity_status, mp3.integrity_stamp, mp3.integrity_message)
        if tip:
            integrity_item.setToolTip(tip)
        self.table.setItem(row, self._col_index["integrity"], integrity_item)

        bpm_item = NumericTableWidgetItem(self._bpm_display(mp3))
        bpm_item.setData(Qt.ItemDataRole.UserRole, mp3)
        if mp3.bpm_message:
            bpm_item.setToolTip(mp3.bpm_message)
        # A TOOL ERROR cell keeps its orange text even on a dirty row (the
        # dirty tint would hide it); the row's other cells show the tint.
        if mp3.bpm_status == STATUS_TOOL_ERROR:
            bpm_item.setForeground(STATUS_COLORS[STATUS_TOOL_ERROR])
        # A detected/loaded BPM that hasn't been saved yet is exactly
        # as "dirty" as a manually typed field -- the amber tint below
        # was previously only applied to the FIELDS loop above, so a
        # BPM/key-only change (no text field touched) showed no visual
        # cue at all that Save had anything to do, easy to mistake for
        # "this was already saved" -- see mp3.dirty's docstring.
        elif mp3.dirty:
            bpm_item.setBackground(DIRTY_COLOR)
            bpm_item.setForeground(HIGHLIGHT_TEXT_COLOR)
        self.table.setItem(row, self._col_index["bpm"], bpm_item)

        key_item = QTableWidgetItem(self._key_display(mp3))
        key_item.setData(Qt.ItemDataRole.UserRole, mp3)
        key_color = STATUS_COLORS.get(mp3.key_status)
        if key_color is not None:
            key_item.setForeground(key_color)
        if mp3.key_message:
            key_item.setToolTip(mp3.key_message)
        if mp3.dirty and mp3.key_status != STATUS_TOOL_ERROR:
            key_item.setBackground(DIRTY_COLOR)
            key_item.setForeground(HIGHLIGHT_TEXT_COLOR)
        self.table.setItem(row, self._col_index["key"], key_item)

        deep_check_item = QTableWidgetItem(self._deep_check_display(mp3))
        deep_check_item.setData(Qt.ItemDataRole.UserRole, mp3)
        deep_check_color = STATUS_COLORS.get(mp3.deep_check_status)
        if deep_check_color is not None:
            deep_check_item.setForeground(deep_check_color)
        tip = scan_tooltip(mp3.deep_check_status, mp3.deep_check_stamp, mp3.deep_check_message)
        if tip:
            deep_check_item.setToolTip(tip)
        # No dirty highlight on the scan cells: the status text and
        # colour are what matter, and the row's other cells already
        # show the file as unsaved.
        self.table.setItem(row, self._col_index["deep_check"], deep_check_item)

        loudness_item = NumericTableWidgetItem(self._loudness_display(mp3), sort_value=mp3.loudness_lufs)
        loudness_item.setData(Qt.ItemDataRole.UserRole, mp3)
        if mp3.loudness_status == STATUS_TOOL_ERROR:
            loudness_item.setToolTip(mp3.loudness_message)
            loudness_item.setForeground(STATUS_COLORS[STATUS_TOOL_ERROR])
        elif mp3.loudness_gain_db is not None:
            loudness_item.setToolTip(f"Track gain: {mp3.loudness_gain_db:+.2f} dB (ref. -18 LUFS)")
        elif mp3.loudness_message:
            loudness_item.setToolTip(mp3.loudness_message)
        if mp3.dirty and mp3.loudness_status != STATUS_TOOL_ERROR:
            loudness_item.setBackground(DIRTY_COLOR)
            loudness_item.setForeground(HIGHLIGHT_TEXT_COLOR)
        self.table.setItem(row, self._col_index["loudness"], loudness_item)

        lyrics_item = QTableWidgetItem(self._lyrics_display(mp3))
        lyrics_item.setData(Qt.ItemDataRole.UserRole, mp3)
        lyrics_color = STATUS_COLORS.get(mp3.lyrics_status)
        if lyrics_color is not None and (not mp3.lyrics or mp3.lyrics_status == STATUS_TOOL_ERROR):
            # Unlike bpm/key/integrity, STATUS_OK here just means
            # "there's text in the cell" -- already obvious from the
            # cell itself, so the green tint would be redundant. Only
            # color this cell for the states that AREN'T self-evident
            # from the text: TOOL MISSING (gray) and a fetch that came
            # back with nothing (mp3.lyrics_status == STATUS_ERROR,
            # cell reads blank the same as never-fetched otherwise).
            lyrics_item.setForeground(lyrics_color)
        if mp3.lyrics_message:
            lyrics_item.setToolTip(mp3.lyrics_message)
        elif mp3.lyrics:
            lyrics_item.setToolTip("Double-click to view/edit")
        if mp3.dirty and mp3.lyrics_status != STATUS_TOOL_ERROR:
            lyrics_item.setBackground(DIRTY_COLOR)
            lyrics_item.setForeground(HIGHLIGHT_TEXT_COLOR)
        self.table.setItem(row, self._col_index["lyrics"], lyrics_item)

        cover_item = QTableWidgetItem(self._cover_display(mp3))
        cover_item.setData(Qt.ItemDataRole.UserRole, mp3)
        # Only an already-decoded thumbnail here, never a read/decode --
        # see _load_cover_icons_for_rows().
        cached = self._cover_icons.get_cached_icon(mp3, self._cover_source.get(mp3))
        if cached is not None and mp3.has_cover:
            cover_item.setIcon(cached)
        if mp3.cover_change_pending:
            cover_item.setBackground(DIRTY_COLOR)
            cover_item.setForeground(HIGHLIGHT_TEXT_COLOR)
            cover_item.setToolTip("Cover change not saved yet")
        self.table.setItem(row, self._col_index["cover"], cover_item)
        self._cover_item_by_file[mp3] = cover_item

        encoder_item = QTableWidgetItem(mp3.audio_encoder)
        encoder_item.setData(Qt.ItemDataRole.UserRole, mp3)
        self.table.setItem(row, self._col_index["encoder"], encoder_item)

        sample_rate_sort = float(mp3.sample_rate_hz) if mp3.sample_rate_hz is not None else None
        sample_rate_item = NumericTableWidgetItem(mp3.display_sample_rate(), sort_value=sample_rate_sort)
        sample_rate_item.setData(Qt.ItemDataRole.UserRole, mp3)
        self.table.setItem(row, self._col_index["sample_rate"], sample_rate_item)

        channels_item = NumericTableWidgetItem(str(mp3.channels) if mp3.channels is not None else "")
        channels_item.setData(Qt.ItemDataRole.UserRole, mp3)
        self.table.setItem(row, self._col_index["channels"], channels_item)

    @staticmethod
    def _integrity_display(mp3: MP3File) -> str:
        if mp3.integrity_status in (STATUS_TOOL_MISSING, STATUS_TOOL_ERROR):
            return mp3.integrity_status
        return scan_display(mp3.integrity_status, mp3.integrity_stamp)

    @staticmethod
    def _bpm_display(mp3: MP3File) -> str:
        if mp3.bpm_status in (STATUS_TOOL_MISSING, STATUS_TOOL_ERROR):
            return mp3.bpm_status
        return mp3.display_bpm()

    @staticmethod
    def _key_display(mp3: MP3File) -> str:
        if mp3.key_status in (STATUS_TOOL_MISSING, STATUS_TOOL_ERROR):
            return mp3.key_status
        return mp3.key_value

    @staticmethod
    def _loudness_display(mp3: MP3File) -> str:
        if mp3.loudness_status == STATUS_TOOL_ERROR:
            return mp3.loudness_status
        return mp3.display_loudness()

    @staticmethod
    def _cover_display(mp3: MP3File) -> str:
        if mp3.load_error or mp3.has_cover is None:
            return ""
        return "Yes" if mp3.has_cover else "No"

    @staticmethod
    def _lyrics_display(mp3: MP3File) -> str:
        if mp3.lyrics_status in (STATUS_TOOL_MISSING, STATUS_TOOL_ERROR):
            return mp3.lyrics_status
        if mp3.lyrics:
            line_count = mp3.lyrics.count("\n") + 1
            return f"Yes ({line_count} lines)"
        if mp3.lyrics_status == STATUS_ERROR:
            return "Not found"
        return ""

    @staticmethod
    def _deep_check_display(mp3: MP3File) -> str:
        # Same convention as _integrity_display() above -- the status
        # word plus the stamp's local time, or "UNCHECKED" for a file
        # that's never been deep-checked, not blanked to an empty cell.
        if mp3.deep_check_status in (STATUS_TOOL_MISSING, STATUS_TOOL_ERROR):
            return mp3.deep_check_status
        return scan_display(mp3.deep_check_status, mp3.deep_check_stamp)

    # -- context menu -----------------------------------------------------

    def _show_context_menu(self, pos) -> None:
        # Selection-fix, and the generic Open in Default App / Open
        # Containing Folder / Copy Path rows, are handled by the shared
        # helper -- see its docstring. The rest is a short core plus
        # families (Look Up, Organize, Analyze, Cover); the full set is in
        # the menu bar.
        def extra_items(files: list[MP3File]) -> list:
            items: list = [Separator()]
            # Reuses the actual File-menu QAction (F2) rather than
            # building a fresh one -- same object, so this shows the
            # real shortcut hint and can never drift out of sync with
            # it. Only offered for a single file -- renaming several to
            # the same name doesn't make sense.
            if len(files) == 1:
                items.append(self.action_registry["play"])
            if len(files) == 1 and not files[0].load_error:
                items.extend([self.action_rename_file, Separator()])
            items.extend([
                look_up_submenu([
                    MenuAction("musicbrainz_lookup", "MusicBrainz…", self.open_musicbrainz_lookup_dialog),
                    MenuAction(
                        "musicbrainz_local_lookup", "MusicBrainz (Local Database)…",
                        self.open_musicbrainz_local_lookup_dialog,
                    ),
                    MenuAction("discogs_lookup", "Discogs…", self.open_discogs_lookup_dialog),
                    MenuAction("fetch_lyrics", "Lyrics", self.run_lyrics_fetch),
                ]),
                Submenu("Organize", [
                    self.action_rename_files,
                    # Edit > Auto-Number itself; its "Number Tracks" preset
                    # replaced the old quick Number Tracks (2026-10-14).
                    self.action_registry["auto_number"],
                ]),
                Submenu("Analyze", [
                    MenuAction("check_integrity", "Check Integrity", self.run_integrity_check),
                    MenuAction("fix_integrity", "Fix Integrity Issues…", self.run_integrity_fix),
                    MenuAction("deep_check", "Deep Check Integrity…", self.run_deep_check),
                    Separator(),
                    MenuAction("detect_bpm", "Detect BPM", self.run_bpm_check),
                    MenuAction("detect_key", "Detect Key", self.run_key_detection),
                    MenuAction("measure_loudness", "Measure Loudness", self.run_loudness_measurement),
                ]),
            ])
            cover_items: list = [
                MenuAction("cover_set", "Set from Image File…", self.set_cover_from_file),
                MenuAction("cover_from_folder", "Set from Folder Image", self.set_cover_from_folder_images),
                MenuAction("cover_from_musicbrainz", "Get from MusicBrainz…", self.open_musicbrainz_cover_dialog),
                MenuAction("cover_from_discogs", "Get from Discogs…", self.open_discogs_cover_dialog),
            ]
            if any(mp3.has_cover for mp3 in files):
                cover_items.append(MenuAction("cover_remove", "Remove Cover", self.remove_cover))
            if len(files) == 1 and files[0].has_cover:
                cover_items.append(MenuAction("cover_export", "Export Cover to Image File…", self.export_cover))
            items.append(Submenu("Cover", cover_items))
            if len(files) == 1:
                items.append(
                    MenuAction("edit_lyrics", "Edit Lyrics…", lambda: self.open_lyrics_dialog(files[0]))
                )
            items.append(MenuAction("create_notes_here", "Create Notes for This Folder…", self.create_notes_for_selection))
            items.extend([Separator(), self.action_redact, Separator(), self.action_remove_from_list])
            return items

        show_table_context_menu(
            self, self.table, pos,
            get_selected_items=self._selected_files,
            get_path=lambda mp3: mp3.path,
            extra_items=extra_items,
        )
