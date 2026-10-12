"""
What the MusicBrainz and Discogs lookup dialogs share for covers: a Save Cover button and the
list of (file, image bytes, mime) to embed for the ticked albums. A dialog opened in cover-only
mode (Metadata > Cover > Get from ...) changes no tags, only covers.

The host class provides `items` (albums with `.files`), `_matches` (id(album) -> a match with
`.assignment`), `accepted_rows()` and `add_toolbar_button()`.
"""

from __future__ import annotations

from PyQt6.QtWidgets import QPushButton

from core.cover_art import sniff_mime
from core.mp3_file import MP3File


class CoverSaveMixin:
    cover_only = False

    def _setup_cover_save(self, cover_only: bool) -> None:
        self.cover_only = cover_only
        self.cover_button = QPushButton("Save Cover")
        self.cover_button.setCheckable(True)
        self.cover_button.setChecked(True)
        self.cover_button.setToolTip(
            "Also embed the found cover in the matched files (replacing a cover they already have)."
        )
        if cover_only:
            self.setWindowTitle(f"{self.windowTitle()} -- Cover Only")
            self.cover_button.setVisible(False)  # always on: covers are all this dialog does
        else:
            self.add_toolbar_button(self.cover_button)

    def cover_changes(self) -> list[tuple[MP3File, bytes, str]]:
        """(file, image bytes, mime) for every matched file of every ticked album whose found
        cover is a JPEG or PNG; empty when Save Cover is off."""
        if not self.cover_button.isChecked():
            return []
        changes = []
        for row, result in self.accepted_rows().items():
            data = result.cover_bytes
            mime = sniff_mime(data)
            album = self.items[row]
            match = self._matches.get(id(album))
            if not data or not mime or match is None:
                continue
            changes.extend((album.files[index], data, mime) for index in match.assignment)
        return changes
