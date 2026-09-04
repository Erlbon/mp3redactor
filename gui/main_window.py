"""
MainWindow: file table on the left/center, menu+toolbar for the v1
actions (Load Files/Folder, Check Integrity, Detect BPM). Bulk-edit tag
panel is intentionally NOT built yet -- v1 scope is integrity + BPM
only, per the agreed roadmap (key/cover/lyrics come later, tag editing
follows once there's something to edit toward beyond what's already
read-only here).

Column layout, row-to-file mapping via Qt.UserRole (not list index -- a
sort or filter must never desync the row from the object it displays,
same lesson as the epub tool), and the progress-dialog pattern for
long-running scans all follow the sibling projects' conventions --
menu bar, progress dialogs, and About/Changelog now built on
redactor_common, the same shared package the epub and video tools use,
rather than reimplementing these independently the way this project
originally did.

Menu shape is File / Import / Operations / Settings / Help, matching
every other Redactor project. Import is present but genuinely empty
for now -- v1 has no external-metadata-source actions yet; it'll gain
entries once cover-art and lyrics fetching (steps 4-5 of the roadmap)
land, exactly the point those were always going to be Import actions
rather than Operations (they bring external data IN, rather than
analyzing the file's own content the way integrity/BPM/key do).
"""

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHeaderView,
    QMainWindow,
    QMenu,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
    QToolBar,
)

from core.app_paths import asset_path
from core.mp3_file import (
    MP3File,
    STATUS_ERROR,
    STATUS_OK,
    STATUS_TOOL_MISSING,
    STATUS_WARNING,
)
from core.scan_service import (
    find_mp3_files,
    load_files,
    run_bpm_check,
    run_integrity_check,
    run_integrity_fix,
)
from core.settings import Settings, load_settings, save_settings
from core.version import APP_NAME, APP_REPO_URL, APP_VERSION, RELEASE_LABEL
from gui.external_tools_dialog import ExternalToolsDialog
from gui.settings_dialog import SettingsDialog
from redactor_common.gui.about_dialog import AboutDialog, ChangelogDialog
from redactor_common.gui.menu_builder import MenuAction, Separator, build_menu_bar
from redactor_common.gui.progress import run_with_progress
from redactor_common.core.version import REDACTOR_COMMON_REPO_URL, REDACTOR_COMMON_VERSION

COL_FILENAME = 0
COL_TITLE = 1
COL_ARTIST = 2
COL_ALBUM = 3
COL_INTEGRITY = 4
COL_BPM = 5
COLUMN_COUNT = 6
COLUMN_HEADERS = ["Filename", "Title", "Artist", "Album", "Integrity", "BPM"]

STATUS_COLORS = {
    STATUS_OK: Qt.GlobalColor.darkGreen,
    STATUS_WARNING: Qt.GlobalColor.darkYellow,
    STATUS_ERROR: Qt.GlobalColor.red,
    STATUS_TOOL_MISSING: Qt.GlobalColor.gray,
}


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.files: list[MP3File] = []
        self.settings: Settings = load_settings()

        self.setWindowTitle(f"{APP_NAME} ({APP_VERSION})")
        self.setWindowIcon(QIcon(str(asset_path("assets/icon.ico"))))
        self.resize(1000, 600)

        self.table = QTableWidget(0, COLUMN_COUNT, self)
        self.table.setHorizontalHeaderLabels(COLUMN_HEADERS)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        self.setCentralWidget(self.table)

        self._build_menu_and_toolbar()

    # -- menu/toolbar -----------------------------------------------------

    def _build_menu_and_toolbar(self) -> None:
        specs = {
            "File": [
                MenuAction("load_files", "&Load Files...", self.load_files_dialog),
                MenuAction("load_folder", "Load &Folder...", self.load_folder_dialog),
                Separator(),
                MenuAction("exit", "E&xit", self.close),
            ],
            # Empty for now -- see module docstring. Still present so
            # the menu shape matches every other Redactor project even
            # before there's anything to put in it.
            "Import": [],
            "Operations": [
                MenuAction("check_integrity", "&Check File Integrity", self.run_integrity_check),
                MenuAction(
                    "fix_integrity", "&Fix Selected Files' Integrity Issues...", self.run_integrity_fix
                ),
                MenuAction("check_bpm", "Detect &BPM", self.run_bpm_check),
            ],
            "Settings": [
                MenuAction("preferences", "&Preferences...", self.open_settings_dialog),
                MenuAction(
                    "locate_tools", "&Locate External Tools...", self.open_external_tools_dialog
                ),
            ],
            "Help": [
                MenuAction("about", f"&About {APP_NAME}...", self.open_about_dialog),
                MenuAction("changelog", "View &Changelog...", self.open_changelog_dialog),
            ],
        }
        actions = build_menu_bar(self, specs)

        self.action_load_files = actions["load_files"]
        self.action_load_folder = actions["load_folder"]
        self.action_check_integrity = actions["check_integrity"]
        self.action_fix_integrity = actions["fix_integrity"]
        self.action_check_bpm = actions["check_bpm"]

        toolbar = QToolBar("Main", self)
        self.addToolBar(toolbar)
        toolbar.addAction(self.action_load_folder)
        toolbar.addAction(self.action_check_integrity)
        toolbar.addAction(self.action_check_bpm)

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

    # -- loading ------------------------------------------------------------

    def load_files_dialog(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Load Files", "", "MP3 Files (*.mp3)")
        if paths:
            self._load_paths([Path(p) for p in paths])

    def load_folder_dialog(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Load Folder")
        if folder:
            self._load_paths([Path(folder)])

    def _load_paths(self, raw_paths: list[Path]) -> None:
        mp3_paths = find_mp3_files(raw_paths)
        if not mp3_paths:
            QMessageBox.information(self, "No MP3 Files", "No .mp3 files found in that selection.")
            return

        loaded: list[MP3File] = []

        def step(mp3_path, _index: int) -> None:
            loaded.extend(load_files([mp3_path]))

        run_with_progress(self, mp3_paths, step, "Loading files...", threshold=3)
        self.files = loaded
        self._rebuild_table()

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
            self.files,
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
        self._run_check_with_progress("Detecting BPM...", run_bpm_check, self.files)

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
            QMessageBox.information(self, "No Files Loaded", "Load some files first.")
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

    def _populate_row(self, row: int, mp3: MP3File) -> None:
        values = [mp3.filename, mp3.title, mp3.artist, mp3.album]
        for col, value in enumerate(values):
            item = QTableWidgetItem(value)
            item.setData(Qt.ItemDataRole.UserRole, mp3)
            self.table.setItem(row, col, item)

        integrity_item = QTableWidgetItem(self._integrity_display(mp3))
        integrity_item.setData(Qt.ItemDataRole.UserRole, mp3)
        color = STATUS_COLORS.get(mp3.integrity_status)
        if color is not None:
            integrity_item.setForeground(color)
        if mp3.integrity_message:
            integrity_item.setToolTip(mp3.integrity_message)
        self.table.setItem(row, COL_INTEGRITY, integrity_item)

        bpm_item = QTableWidgetItem(self._bpm_display(mp3))
        bpm_item.setData(Qt.ItemDataRole.UserRole, mp3)
        if mp3.bpm_message:
            bpm_item.setToolTip(mp3.bpm_message)
        self.table.setItem(row, COL_BPM, bpm_item)

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

    # -- context menu -----------------------------------------------------

    def _show_context_menu(self, pos) -> None:
        if not self.table.itemAt(pos):
            return
        menu = QMenu(self)
        menu.addAction("Check File Integrity", self.run_integrity_check)
        menu.addAction("Fix File Integrity Issues...", self.run_integrity_fix)
        menu.addAction("Detect BPM", self.run_bpm_check)
        menu.exec(self.table.viewport().mapToGlobal(pos))
