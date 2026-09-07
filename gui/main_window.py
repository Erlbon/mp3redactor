"""
MainWindow: bulk-edit tag panel (left) + file table (right) in a
collapsible QSplitter, menu+toolbar for the v1 actions (Load
Files/Folder, bulk tag editing, Check Integrity, Detect BPM, Detect
Key). Tag editing is deliberately "basic" -- plain text fields only,
via core.fields.FIELDS -- no covers or external lookups the way the
epub tool's fuller tag panel has; those don't have an obvious MP3-tag
equivalent yet. Genre and Language do get the family's quick-pick "+"
button (core.fields.QUICK_PICK_FIELDS) plus Settings > Add/Remove
Genres.../Add/Remove Languages... management dialogs.

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

Menu shape is File / Import / Operations / Settings / Help, matching
every other Redactor project. Import is present but genuinely empty
for now -- v1 has no external-metadata-source actions yet; it'll gain
entries once cover-art and lyrics fetching (steps 4-5 of the roadmap)
land, exactly the point those were always going to be Import actions
rather than Operations (they bring external data IN, rather than
analyzing the file's own content the way integrity/BPM/key do).
"""

import dataclasses
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFileDialog,
    QHeaderView,
    QInputDialog,
    QMainWindow,
    QMessageBox,
    QProgressDialog,
    QSizePolicy,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QToolBar,
    QWidget,
)

from core.app_paths import asset_path
from core.fields import FIELDS
from core.mp3_file import (
    MP3File,
    STATUS_ERROR,
    STATUS_OK,
    STATUS_TOOL_MISSING,
    STATUS_WARNING,
)
from core.mp3_genres import COMMON_MP3_GENRES
from core.mp3_genres import exclude_hidden as exclude_hidden_genres
from core.mp3_genres import merge_genres
from core.mp3_languages import DEFAULT_LANGUAGES
from core.mp3_languages import exclude_hidden as exclude_hidden_languages
from core.mp3_languages import merge_languages
from core.scan_service import (
    find_mp3_files,
    load_files,
    run_bpm_check,
    run_integrity_check,
    run_integrity_fix,
    run_key_detection,
    save_dirty_tags,
)
from core.settings import Settings, directory_for, load_settings, resolve_start_directory, save_settings
from core.version import APP_NAME, APP_REPO_URL, APP_VERSION, RELEASE_LABEL
from gui.external_tools_dialog import ExternalToolsDialog
from gui.settings_dialog import SettingsDialog
from gui.tag_panel import TagPanel
from redactor_common.core.error_summary import summarize_errors
from redactor_common.core.table_settings import is_column_visible, merge_column_order, sanitize_hidden_fields
from redactor_common.core.undo import UndoManager
from redactor_common.gui.about_dialog import AboutDialog, ChangelogDialog, CreditsDialog
from redactor_common.gui.action_factory import make_action
from redactor_common.gui.collapsible_splitter import SplitterPaneCollapser
from redactor_common.gui.colors import DIRTY_COLOR, HIGHLIGHT_TEXT_COLOR, TABLE_SELECTION_STYLESHEET
from redactor_common.gui.column_menu import show_column_header_context_menu
from redactor_common.gui.column_settings_dialog import ColumnSettingsDialog
from redactor_common.gui.manage_list_dialog import ManageListDialog
from redactor_common.gui.menu_builder import MenuAction, Separator, build_menu_bar
from redactor_common.gui.context_menu import show_table_context_menu
from redactor_common.gui.progress import run_with_progress
from redactor_common.gui.quick_pick_dialog import QuickPickDialog
from redactor_common.gui.zoom_toolbar import TableZoomController
from redactor_common.core.version import REDACTOR_COMMON_REPO_URL, REDACTOR_COMMON_VERSION

# Table columns, field-key based -- see redactor_common.core.
# table_settings's own docstring for why (a persisted index-based
# preference silently breaks the moment a column is added/removed/
# reordered in code). "integrity"/"bpm"/"key" are synthetic (derived
# check results, not a tag field); everything else is exactly
# core.fields.FIELDS.
_STATUS_COLUMN_LABELS: dict[str, str] = {"integrity": "Integrity", "bpm": "BPM", "key": "Key"}
_COLUMN_LABELS: dict[str, str] = {
    "filename": "Filename",
    "path": "Path",
    **{key: label for key, label, _m in FIELDS},
    **_STATUS_COLUMN_LABELS,
}
ALL_COLUMN_KEYS: list[str] = ["filename", "path"] + [key for key, _l, _m in FIELDS] + list(_STATUS_COLUMN_LABELS)
PROTECTED_COLUMNS = frozenset({"filename"})  # the one column you always need to tell rows apart

# Nothing hidden on a genuinely first run -- matches this app's own
# behavior before column management existed (every column always
# shown), unlike a project with a large field set that wants a curated
# default. Once the user has saved ANY choice, even "show everything"
# (an empty hidden set), that saved choice always wins -- see
# Settings.has_column_preference's own docstring.
DEFAULT_HIDDEN_COLUMNS: frozenset[str] = frozenset()

STATUS_COLORS = {
    STATUS_OK: Qt.GlobalColor.darkGreen,
    STATUS_WARNING: Qt.GlobalColor.darkYellow,
    STATUS_ERROR: Qt.GlobalColor.red,
    STATUS_TOOL_MISSING: Qt.GlobalColor.gray,
}

TAG_PANEL_COLLAPSED_WIDTH = 32
TAG_PANEL_DEFAULT_WIDTH = 300


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.files: list[MP3File] = []
        self.settings: Settings = load_settings()
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
        self.table.setStyleSheet(TABLE_SELECTION_STYLESHEET)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        self.table.itemSelectionChanged.connect(self._on_table_selection_changed)
        self._setup_column_persistence()
        self.zoom = TableZoomController(self.table, parent=self)

        self.tag_panel = TagPanel()
        self.tag_panel.setMinimumWidth(24)
        self.tag_panel.setMaximumWidth(440)
        self.tag_panel.applyRequested.connect(self._apply_bulk_edit)
        self.tag_panel.selectionCountChanged.connect(self._on_tag_panel_selection_count_changed)
        self.tag_panel.collapseToggleRequested.connect(self._toggle_tag_panel)
        self.tag_panel.quickPickRequested.connect(self._on_quick_pick_requested)
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

    # -- menu/toolbar -----------------------------------------------------

    def _build_menu_and_toolbar(self) -> None:
        specs = {
            "File": [
                MenuAction("load_files", "&Load Files...", self.load_files_dialog),
                MenuAction("load_folder", "Load &Folder...", self.load_folder_dialog),
                Separator(),
                # "Save File(s)", not "Save Tags" -- this writes to the
                # actual file on disk (mutagen open+modify+re-save), not
                # some separate sidecar/tag store, and user feedback was
                # that "Tags" read as a smaller, less concrete action
                # than what it actually does.
                MenuAction("save", "&Save File(s)", self.save_changed, shortcut="Ctrl+S"),
                Separator(),
                MenuAction("exit", "E&xit", self.close),
            ],
            # Empty for now -- see module docstring. Still present so
            # the menu shape matches every other Redactor project even
            # before there's anything to put in it.
            "Import": [],
            "Operations": [
                MenuAction(
                    "apply_bulk_edit", "&Apply to 0 selected file(s)", self.tag_panel.apply_bulk_edit
                ),
                Separator(),
                MenuAction(
                    "check_integrity", "&Check Selected Files' Integrity", self.run_integrity_check
                ),
                MenuAction(
                    "fix_integrity", "&Fix Selected Files' Integrity Issues...", self.run_integrity_fix
                ),
                MenuAction("check_bpm", "Detect &BPM for Selected Files", self.run_bpm_check),
                MenuAction("check_key", "Detect &Key for Selected Files", self.run_key_detection),
                Separator(),
                MenuAction("undo", "&Undo", self.undo_last_action, shortcut="Ctrl+Z"),
            ],
            "Settings": [
                MenuAction("preferences", "&Preferences...", self.open_settings_dialog),
                MenuAction(
                    "locate_tools", "&Locate External Tools...", self.open_external_tools_dialog
                ),
                Separator(),
                MenuAction("manage_columns", "Add/&Remove Columns...", self.open_column_settings_dialog),
                MenuAction("manage_genres", "Add/Remove &Genres...", self.open_genre_settings_dialog),
                MenuAction(
                    "manage_languages", "Add/Remove &Languages...", self.open_language_settings_dialog
                ),
            ],
            "Help": [
                MenuAction("about", f"&About {APP_NAME}...", self.open_about_dialog),
                MenuAction("changelog", "View &Changelog...", self.open_changelog_dialog),
                MenuAction("credits", "&Credits...", self.open_credits_dialog),
            ],
        }
        actions = build_menu_bar(self, specs)

        self.action_load_files = actions["load_files"]
        self.action_load_folder = actions["load_folder"]
        self.action_save = actions["save"]
        self.action_apply_bulk_edit = actions["apply_bulk_edit"]
        self.action_apply_bulk_edit.setEnabled(False)
        self.action_check_integrity = actions["check_integrity"]
        self.action_fix_integrity = actions["fix_integrity"]
        self.action_check_bpm = actions["check_bpm"]
        self.action_check_key = actions["check_key"]
        self.action_undo = actions["undo"]
        self.action_undo.setEnabled(False)

        # Toolbar carries only the everyday five (Load Files, Load
        # Folder, Save, Apply, Undo) -- everything else (Check
        # Integrity, Detect BPM, Detect Key) stays reachable only via
        # the Operations menu and the table's right-click context menu
        # (_show_context_menu), both of which already have them, rather
        # than crowding a second copy onto the toolbar too.
        toolbar = QToolBar("Main", self)
        self.addToolBar(toolbar)
        toolbar.addAction(self.action_load_files)
        toolbar.addAction(self.action_load_folder)
        toolbar.addSeparator()
        toolbar.addAction(self.action_save)
        toolbar.addSeparator()
        toolbar.addAction(self.action_apply_bulk_edit)
        toolbar.addSeparator()
        toolbar.addAction(self.action_undo)
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

    # -- about/changelog ----------------------------------------------------

    def open_about_dialog(self) -> None:
        # No dedicated ABOUT.md exists yet for this project (unlike the
        # epub tool) -- README.md is the closest equivalent today and
        # is already bundled as a data asset, same PyInstaller path
        # resolution rules apply via asset_path().
        dialog = AboutDialog(
            app_name=APP_NAME,
            app_version=APP_VERSION,
            release_label=RELEASE_LABEL,
            icon_path=str(asset_path("assets/icon.ico")),
            about_path=str(asset_path("README.md")),
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

    # -- loading ------------------------------------------------------------

    def load_files_dialog(self) -> None:
        start_dir = resolve_start_directory(self.settings.last_directory)
        paths, _ = QFileDialog.getOpenFileNames(self, "Load Files", start_dir, "MP3 Files (*.mp3)")
        if paths:
            self._remember_last_directory(paths[0])
            self._load_paths([Path(p) for p in paths])

    def load_folder_dialog(self) -> None:
        start_dir = resolve_start_directory(self.settings.last_directory)
        folder = QFileDialog.getExistingDirectory(self, "Load Folder", start_dir)
        if folder:
            self._remember_last_directory(folder)
            self._load_paths([Path(folder)])

    def _remember_last_directory(self, path: str) -> None:
        self.settings.last_directory = directory_for(path)
        save_settings(self.settings)

    def _load_paths(self, raw_paths: list[Path]) -> None:
        mp3_paths = find_mp3_files(raw_paths)
        if not mp3_paths:
            QMessageBox.information(self, "No MP3 Files", "No .mp3 files found in that selection.")
            return

        # Load Files/Folder replaces self.files wholesale (unlike the epub
        # tool's additive Add Files) -- always did, but now that files can
        # carry unsaved tag edits, doing that silently would discard them.
        if self._count_dirty() and not self._confirm_discard("load a new selection"):
            return

        loaded: list[MP3File] = []

        def step(mp3_path, _index: int) -> None:
            loaded.extend(load_files([mp3_path]))

        run_with_progress(self, mp3_paths, step, "Loading files...", threshold=3)
        self.files = loaded
        # Old undo snapshots reference now-discarded MP3File objects --
        # restoring into them wouldn't reach anything still on screen.
        self.undo_manager.clear()
        self._update_undo_action()
        self._rebuild_table()

    # -- tag editing ------------------------------------------------------

    def _apply_bulk_edit(self, values: dict) -> None:
        """values: {field_key: value} for whichever fields were checked
        in the tag panel -- see TagPanel.apply_bulk_edit(). Writes them
        into memory for every currently selected file and marks each
        dirty; does not touch disk (that's save_changed())."""
        targets = self._selected_files()
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

    @staticmethod
    def _restore_mp3(mp3: MP3File, snapshot: MP3File) -> None:
        for f in dataclasses.fields(MP3File):
            setattr(mp3, f.name, getattr(snapshot, f.name))

    def _push_undo(self, label: str, targets: list[MP3File]) -> None:
        self.undo_manager.push(label, targets, self._snapshot_mp3)
        self._update_undo_action()

    def _update_undo_action(self) -> None:
        can_undo = self.undo_manager.can_undo()
        self.action_undo.setEnabled(can_undo)
        label = self.undo_manager.peek_label()
        self.action_undo.setText(f"&Undo {label}" if label else "&Undo")

    def undo_last_action(self) -> None:
        affected = self.undo_manager.undo(self._restore_mp3)
        if affected:
            self._rebuild_table()
        self._update_undo_action()

    def save_changed(self) -> None:
        # Catches a field that's ticked with a value typed in but not
        # yet Applied -- Save should act on it too, not silently drop it.
        self.tag_panel.apply_bulk_edit()

        dirty_files = [mp3 for mp3 in self.files if mp3.dirty]
        if not dirty_files:
            QMessageBox.information(self, "Nothing to Save", "No unsaved tag changes.")
            return

        def step(mp3: MP3File, _index: int) -> None:
            save_dirty_tags([mp3])

        run_with_progress(self, dirty_files, step, "Saving tags...", threshold=3)
        self._rebuild_table()

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
        "Add Custom..." button and Settings > Add/Remove Genres...'s
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

    def _on_tag_panel_selection_count_changed(self, count: int) -> None:
        self.action_apply_bulk_edit.setText(f"Apply to {count} selected file(s)")
        self.action_apply_bulk_edit.setEnabled(count > 0)

    def _toggle_tag_panel(self) -> None:
        self._panel_collapser.toggle()
        self._sync_tag_panel_collapsed_indicator()

    def _sync_tag_panel_collapsed_indicator(self) -> None:
        self.tag_panel.set_collapsed_indicator(self._panel_collapser.is_collapsed())

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
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec() == SettingsDialog.DialogCode.Accepted:
            self.settings = dialog.result_settings()
            save_settings(self.settings)

    def open_external_tools_dialog(self) -> None:
        dialog = ExternalToolsDialog(self.settings, self)
        if dialog.exec() == ExternalToolsDialog.DialogCode.Accepted:
            mp3val_path, keyfinder_cli_path = dialog.result_paths()
            self.settings.mp3val_path = mp3val_path
            self.settings.keyfinder_cli_path = keyfinder_cli_path
            save_settings(self.settings)

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
        def step(target: MP3File, _index: int) -> None:
            run_integrity_fix(
                [target],
                delete_backup=self.settings.delete_backup_after_fix,
                mp3val_path=self.settings.mp3val_path or None,
            )

        run_with_progress(self, targets, step, "Fixing file integrity issues...", threshold=3)
        self._rebuild_table()

    def run_bpm_check(self) -> None:
        self._run_concurrent_check_with_progress("Detecting BPM...", run_bpm_check)

    def run_key_detection(self) -> None:
        self._run_concurrent_check_with_progress(
            "Detecting key...",
            run_key_detection,
            keyfinder_cli_path=self.settings.keyfinder_cli_path or None,
        )

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
                QMessageBox.information(self, "No Files Loaded", "Load some files first.")
            else:
                QMessageBox.information(
                    self, "No Files Selected", "Select one or more files in the table."
                )
            return

        dialog = None
        if len(targets) >= 3:
            dialog = QProgressDialog(label, "Cancel", 0, len(targets), self)
            dialog.setWindowModality(Qt.WindowModality.WindowModal)
            dialog.setMinimumDuration(0)
            dialog.show()

        def on_progress(done: int, _total: int) -> None:
            if dialog is not None:
                dialog.setValue(done)
                QApplication.processEvents()

        def should_cancel() -> bool:
            return dialog is not None and dialog.wasCanceled()

        scan_fn(targets, progress=on_progress, should_cancel=should_cancel, **extra_kwargs)

        if dialog is not None:
            dialog.close()
        self._rebuild_table()

    def _selected_files(self) -> list[MP3File]:
        seen: dict[int, MP3File] = {}
        for item in self.table.selectedItems():
            mp3 = item.data(Qt.ItemDataRole.UserRole)
            if mp3 is not None:
                seen[id(mp3)] = mp3
        return list(seen.values())

    def _run_check_with_progress(
        self, label: str, check_fn, targets: list[MP3File], **extra_kwargs
    ) -> None:
        if not targets:
            if not self.files:
                QMessageBox.information(self, "No Files Loaded", "Load some files first.")
            else:
                QMessageBox.information(
                    self, "No Files Selected", "Select one or more files in the table."
                )
            return

        def step(target: MP3File, _index: int) -> None:
            check_fn([target], **extra_kwargs)

        run_with_progress(self, targets, step, label, threshold=3)
        self._rebuild_table()

    # -- table ------------------------------------------------------------

    def _rebuild_table(self) -> None:
        self.table.setRowCount(len(self.files))
        for row, mp3 in enumerate(self.files):
            self._populate_row(row, mp3)
        # Rows were just torn down and rebuilt from scratch, so whatever
        # rows Qt now considers "selected" may not match what the tag
        # panel is showing -- keep the two in sync explicitly rather
        # than relying on itemSelectionChanged firing on its own here.
        self.tag_panel.set_selection(self._selected_files())

    def _populate_row(self, row: int, mp3: MP3File) -> None:
        filename_item = QTableWidgetItem(mp3.filename)
        filename_item.setData(Qt.ItemDataRole.UserRole, mp3)
        self.table.setItem(row, self._col_index["filename"], filename_item)

        path_item = QTableWidgetItem(str(mp3.path))
        path_item.setData(Qt.ItemDataRole.UserRole, mp3)
        self.table.setItem(row, self._col_index["path"], path_item)

        for key, _label, _m in FIELDS:
            item = QTableWidgetItem(getattr(mp3, key, "") or "")
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
        if mp3.integrity_message:
            integrity_item.setToolTip(mp3.integrity_message)
        self.table.setItem(row, self._col_index["integrity"], integrity_item)

        bpm_item = QTableWidgetItem(self._bpm_display(mp3))
        bpm_item.setData(Qt.ItemDataRole.UserRole, mp3)
        if mp3.bpm_message:
            bpm_item.setToolTip(mp3.bpm_message)
        # A detected/loaded BPM that hasn't been saved yet is exactly
        # as "dirty" as a manually typed field -- the amber tint below
        # was previously only applied to the FIELDS loop above, so a
        # BPM/key-only change (no text field touched) showed no visual
        # cue at all that Save had anything to do, easy to mistake for
        # "this was already saved" -- see mp3.dirty's docstring.
        if mp3.dirty:
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
        if mp3.dirty:
            key_item.setBackground(DIRTY_COLOR)
            key_item.setForeground(HIGHLIGHT_TEXT_COLOR)
        self.table.setItem(row, self._col_index["key"], key_item)

    @staticmethod
    def _integrity_display(mp3: MP3File) -> str:
        if mp3.integrity_status == STATUS_TOOL_MISSING:
            return "TOOL MISSING"
        return mp3.integrity_status

    @staticmethod
    def _bpm_display(mp3: MP3File) -> str:
        if mp3.bpm_status == STATUS_TOOL_MISSING:
            return "TOOL MISSING"
        return mp3.display_bpm()

    @staticmethod
    def _key_display(mp3: MP3File) -> str:
        if mp3.key_status == STATUS_TOOL_MISSING:
            return "TOOL MISSING"
        return mp3.key_value

    # -- context menu -----------------------------------------------------

    def _show_context_menu(self, pos) -> None:
        # Selection-fix, and the generic Open Containing Folder/Copy Path
        # actions, are handled by the shared helper -- see its docstring.
        show_table_context_menu(
            self, self.table, pos,
            get_selected_items=self._selected_files,
            get_path=lambda mp3: mp3.path,
            extra_items=lambda files: [
                Separator(),
                MenuAction(
                    "check_integrity", "Check Selected Files' Integrity", self.run_integrity_check
                ),
                MenuAction(
                    "fix_integrity", "Fix Selected Files' Integrity Issues...", self.run_integrity_fix
                ),
                MenuAction("detect_bpm", "Detect BPM for Selected Files", self.run_bpm_check),
                MenuAction("detect_key", "Detect Key for Selected Files", self.run_key_detection),
            ],
        )
