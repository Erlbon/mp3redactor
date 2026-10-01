"""
gui/musicbrainz_settings_dialog.py

Tools > MusicBrainz Database... -- points the app at an offline MusicBrainz
lookup database and builds it. MusicBrainz's core dump (mbdump.tar.bz2) is
about 7 GB, so the app never downloads it: the user fetches it from
musicbrainz.org/doc/MusicBrainz_Database/Download, picks it here, chooses
what to keep, and Build Database... converts it
(core/musicbrainz_import.py) behind a cancellable progress dialog.

redactor_common's LocalDatabaseSettingsDialog supplies the database path,
Check File and the Build button; this adds the dump picker, the build
options, a free-disk-space note and a status line (what the file was built
from). The paths and options are saved in the app's Settings
(core/settings.py), which the caller passes in.
"""

from __future__ import annotations

import os
import shutil
from typing import Callable, Optional

from PyQt6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)
from redactor_common.core.dump_import import DumpImportError
from redactor_common.core.local_db import LocalDatabaseError, forget_cached
from redactor_common.gui.dump_import_runner import run_dump_import
from redactor_common.gui.local_db_settings_dialog import LocalDatabaseSettingsDialog

from core.app_paths import base_dir
from core.musicbrainz_import import (
    PRIMARY_TYPES,
    SECONDARY_TYPES,
    BuildOptions,
    build_musicbrainz_database,
    describe_database,
)
from core.settings import Settings, save_settings

DOWNLOAD_URL = "https://musicbrainz.org/doc/MusicBrainz_Database/Download"
DUMP_FILTER = "MusicBrainz core dump (*.tar.bz2 *.tar.xz *.tar.gz *.tar);;All files (*)"
# Rough guide (an ESTIMATE from the dump's published size and row counts, not measured on
# the real file): the default options end near 5-6 GB, and the scratch file needed while
# building is about as big again.
FREE_SPACE_WARNING_BYTES = 15 * (1 << 30)

INSTRUCTIONS = (
    "Look up albums offline in <b>MusicBrainz</b>: by release or recording id, barcode, or artist and "
    "album, with no network and no rate limits.<br><br><b>Building it:</b><ol>"
    f"<li>From <a href=\"{DOWNLOAD_URL}\">musicbrainz.org/doc/MusicBrainz_Database/Download</a> download "
    "<code>mbdump.tar.bz2</code> from the newest <code>fullexport</code> folder (about 7 GB; keep it "
    "compressed, do not unpack it).</li>"
    "<li>Choose it below, pick what to keep, and click <b>Build Database...</b>. Reading the dump takes "
    "a long while (an hour or more); Cancel is safe, and an existing database is replaced only when the "
    "new one is complete.</li></ol>"
    "The core data is <b>CC0</b> (public domain). The derived data (tags and genres, ratings, annotations) is "
    "not CC0 and is never used, so there is no genre. Expect a database of several GB, and about as much "
    "again free disk space while it builds."
)


def default_database_path() -> str:
    return os.path.join(str(base_dir()), "musicbrainz.db")


class MusicBrainzSettingsDialog(LocalDatabaseSettingsDialog):
    def __init__(self, settings: Settings, parent=None, save: Optional[Callable[[Settings], None]] = None):
        self._settings = settings
        self._persist = save or save_settings
        super().__init__(
            title="MusicBrainz Database",
            instructions_html=INSTRUCTIONS,
            path=settings.musicbrainz_database,
            check=describe_database,
            save=self._save_all,
            error_types=(LocalDatabaseError,),
            build=lambda dialog: self._build_database(),
            build_label="Build Database…",
            parent=parent,
        )
        self.setMinimumWidth(640)
        self.path_edit.setPlaceholderText(default_database_path())
        options = BuildOptions.from_json(settings.musicbrainz_options)

        box = QGroupBox("Build from the MusicBrainz core dump")
        layout = QVBoxLayout(box)
        row = QHBoxLayout()
        row.addWidget(QLabel("Core dump:"))
        self.dump_edit = QLineEdit(settings.musicbrainz_dump)
        self.dump_edit.setPlaceholderText("Path to mbdump.tar.bz2")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse_dump)
        row.addWidget(self.dump_edit, 1)
        row.addWidget(browse)
        layout.addLayout(row)

        self.official_only = QCheckBox("Only official releases (leaves out bootlegs, promos and pseudo-releases)")
        self.official_only.setChecked(options.official_only)
        layout.addWidget(self.official_only)

        layout.addWidget(QLabel("Keep these release types (a release with several types needs all of them ticked):"))
        grid = QGridLayout()
        self.type_boxes: dict[str, QCheckBox] = {}
        for index, name in enumerate(PRIMARY_TYPES + SECONDARY_TYPES):
            type_box = QCheckBox(name)
            type_box.setChecked(name in options.types)
            if name == "Other":
                type_box.setToolTip("Also releases whose release group has no type at all.")
            self.type_boxes[name] = type_box
            grid.addWidget(type_box, index // 4, index % 4)
        layout.addLayout(grid)

        self.skip_undated = QCheckBox("Skip releases without a date")
        self.skip_undated.setChecked(options.skip_undated)
        layout.addWidget(self.skip_undated)
        self.include_tracks = QCheckBox("Include track titles (needed to match files to tracks; the biggest part)")
        self.include_tracks.setChecked(options.include_tracks)
        self.include_tracks.toggled.connect(self._sync_track_boxes)
        layout.addWidget(self.include_tracks)
        self.track_index = QCheckBox("Include a track search index (finds single tracks without an album; much bigger)")
        self.track_index.setChecked(options.track_index)
        layout.addWidget(self.track_index)
        self._sync_track_boxes()

        self.space_label = QLabel()
        self.space_label.setWordWrap(True)
        layout.addWidget(self.space_label)
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        # Between the path row and the Check File / Build row.
        self.layout().insertWidget(2, box)
        self.layout().insertWidget(2, self.status_label)
        self.path_edit.textChanged.connect(self._refresh_status)
        self._refresh_status()

    # -- widgets -------------------------------------------------------------------------

    def _sync_track_boxes(self) -> None:
        self.track_index.setEnabled(self.include_tracks.isChecked())

    def _browse_dump(self) -> None:
        start = self.dump_edit.text() or os.path.dirname(self.path_edit.text() or "")
        path, _ = QFileDialog.getOpenFileName(self, "Choose the MusicBrainz core dump", start, DUMP_FILTER)
        if path:
            self.dump_edit.setText(path)

    def _browse(self) -> None:
        """The database may not exist yet, so this is a Save dialog that doesn't
        ask to overwrite (picking an existing file just selects it)."""
        path, _ = QFileDialog.getSaveFileName(
            self, "Choose where the database is (or will be) stored",
            self.path_edit.text().strip() or default_database_path(),
            self._file_filter, options=QFileDialog.Option.DontConfirmOverwrite,
        )
        if path:
            self.path_edit.setText(path)

    def free_space_text(self) -> str:
        """How much room the database's drive has, with a warning when that is
        less than a default build needs; "" when it can't be told."""
        folder = os.path.dirname(os.path.abspath(self.path_edit.text().strip() or default_database_path()))
        while folder and not os.path.isdir(folder):
            parent = os.path.dirname(folder)
            if parent == folder:
                return ""
            folder = parent
        try:
            free = shutil.disk_usage(folder).free
        except OSError:
            return ""
        text = f"Free space where the database goes: {free / (1 << 30):,.1f} GB."
        if free < FREE_SPACE_WARNING_BYTES:
            text += (" That may not be enough: a build with the default options needs roughly 12 GB while it runs "
                     "(about 5-6 GB stays afterwards). Choose another location or keep less.")
        return text

    def _refresh_status(self) -> None:
        path = self.path_edit.text().strip()
        if not path:
            self.status_label.setText("No database yet.")
        elif not os.path.isfile(path):
            self.status_label.setText("Not built yet -- the file doesn't exist.")
        else:
            ok, message = self.check_result()
            self.status_label.setText(message if ok else f"Problem: {message}")
        self.space_label.setText(self.free_space_text())

    # -- options / build -----------------------------------------------------------------

    def build_options(self) -> BuildOptions:
        return BuildOptions(
            official_only=self.official_only.isChecked(),
            types=tuple(name for name, box in self.type_boxes.items() if box.isChecked()),
            skip_undated=self.skip_undated.isChecked(),
            include_tracks=self.include_tracks.isChecked(),
            track_index=self.include_tracks.isChecked() and self.track_index.isChecked(),
        )

    def _save_all(self, path: str) -> None:
        self._settings.musicbrainz_database = path
        self._settings.musicbrainz_dump = self.dump_edit.text().strip()
        self._settings.musicbrainz_options = self.build_options().to_json()
        self._persist(self._settings)

    def _build_database(self) -> Optional[str]:
        """Converts the chosen dump; returns the new database's path (None
        if abandoned, cancelled or failed)."""
        source = self.dump_edit.text().strip()
        if not source:
            QMessageBox.information(self, "Build Database", "Choose the MusicBrainz core dump first.")
            return None
        options = self.build_options()
        if not options.types:
            QMessageBox.information(self, "Build Database", "Tick at least one release type.")
            return None
        dest = self.path_edit.text().strip() or default_database_path()
        replacing = (
            "\n\nThe existing database is replaced only when the new one is complete." if os.path.exists(dest) else ""
        )
        free = self.free_space_text()
        answer = QMessageBox.question(
            self, "Build Database",
            f"Build {os.path.basename(dest)} from the dump?\n\nIt reads the whole dump, which can take an hour or "
            f"more, and needs several GB of free disk space.\n{free}\nKeeping: {options.describe()}.{replacing}",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return None
        self._save_all(dest)  # remember the source even if the build is cancelled
        forget_cached(dest)  # a lookup may still hold the old build open
        try:
            summary = run_dump_import(
                self, "Build MusicBrainz Database", "Reading the MusicBrainz dump…",
                lambda progress, cancelled: build_musicbrainz_database(source, dest, options, progress, cancelled),
            )
        except DumpImportError as exc:
            QMessageBox.warning(self, "Build MusicBrainz Database", str(exc))
            return None
        if summary is None:
            return None
        QMessageBox.information(self, "Build MusicBrainz Database", summary.describe())
        self.path_edit.setText(dest)
        self._refresh_status()
        return dest
