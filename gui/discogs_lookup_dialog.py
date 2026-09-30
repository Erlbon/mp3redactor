"""
gui/discogs_lookup_dialog.py

Metadata > Look Up > Discogs...: finds the Discogs release each selected
folder of MP3s belongs to (core/discogs_lookup.py) -- one row per folder,
since an album is one folder. Built on redactor_common's shared review
dialog (LookupDialogBase), like the MusicBrainz one: the release's cover
next to the files' own, other pressings of the album under Other Matches
(label, catalogue number, year, country), and a Search Query form to
correct the artist/album/year and search again.

Review only: nothing is written by the search itself. A row's "fields" are
a summary of the release; what is applied is per file (title, track,
label, catalogue number...), built by file_changes() from the match
remembered for that folder, and goes through the app's per-field overwrite
review before anything changes. Discogs is paced at its rate limit; if it
answers HTTP 429 the remaining rows report that instead of asking again.

"Add styles to Genre" (on by default) puts Discogs' styles after its
genres in the Genre tag ("Rock; Prog Rock"); off keeps the genres only.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QPushButton

from redactor_common.gui.lookup_dialog import LookupAlternative, LookupDialogBase, LookupResult

from core.cover_art import read_cover
from core.discogs_lookup import (
    DiscogsClient,
    DiscogsError,
    DiscogsMatch,
    DiscogsQuery,
    discogs_query,
    fetch_cover,
    fields_for,
    find_release,
    genre_text,
)
from core.mp3_file import MP3File
from core.musicbrainz_lookup import facts_from_tags
from gui.musicbrainz_lookup_dialog import AlbumFolder, group_by_folder  # noqa: F401 -- re-exported for callers


def _summary_fields(match: DiscogsMatch, include_styles: bool) -> dict[str, str]:
    release = match.release
    fields = {
        "album": release.title,
        "album artist": release.artist,
        "released": " ".join(b for b in (release.year, release.country) if b),
        "label": " ".join(b for b in (release.label, release.catno) if b),
        "genre": genre_text(release, include_styles),
        "tracks": str(release.track_count),
        "match": match.summary(),
    }
    return {k: v for k, v in fields.items() if v}


class DiscogsLookupDialog(LookupDialogBase):
    def __init__(self, albums: list[AlbumFolder], client: DiscogsClient, parent=None):
        self._client = client
        self._include_styles = True
        self._matches: dict[int, DiscogsMatch] = {}  # id(album) -> the chosen match
        self._releases: dict[int, object] = {}  # Discogs release id -> release (fetched once per dialog)
        super().__init__(
            albums,
            parent,
            window_title="Look Up via Discogs",
            info_text=(
                f"Finding the Discogs release for {len(albums)} folder(s) -- one album per folder -- by "
                "Artist + Album (and Year) from the tags, or an 'Artist - Album (Year)' folder name, then "
                "matching each file to its track by number, then by title. Other pressings of the album are "
                "listed under Other Matches. Untick anything you don't trust, then Apply; changes are "
                "written on Save. Discogs allows about one request per second, so this takes a few "
                "seconds per album."
            ),
            search_label="Searching Discogs…",
            item_label=lambda album: f"{album.folder.name}  ({len(album.files)} file(s))",
            search_one=self._search_one,
            query_fields=[("artist", "Artist"), ("album", "Album"), ("year", "Year")],
            get_local_cover=self._local_cover,
            resolve_alternative=self._resolve,
            item_noun="folder",
        )
        self.styles_button = QPushButton("Add Styles to Genre")
        self.styles_button.setCheckable(True)
        self.styles_button.setChecked(True)
        self.styles_button.setToolTip("Put Discogs' styles after its genres in the Genre tag.")
        self.styles_button.toggled.connect(self._on_styles_toggled)
        self.add_toolbar_button(self.styles_button)
        attribution = QLabel('Data from <a href="https://www.discogs.com">Discogs</a>')
        attribution.setTextFormat(Qt.TextFormat.RichText)
        attribution.setOpenExternalLinks(True)
        attribution.setStyleSheet("font-size: 11px;")
        self.add_toolbar_button(attribution)

    @staticmethod
    def _local_cover(album: AlbumFolder):
        for mp3 in album.files:
            cover = read_cover(mp3.path)
            if cover:
                return cover[0]
        return None

    def _default_query(self, album: AlbumFolder) -> DiscogsQuery:
        return discogs_query(
            [m.album for m in album.files], [m.albumartist or m.artist for m in album.files],
            [m.year for m in album.files], album.folder, [m.title for m in album.files],
        )

    def _search_one(self, album: AlbumFolder, override: dict) -> LookupResult:
        default = self._default_query(album)
        query = DiscogsQuery(
            artist=override.get("artist", default.artist) if override else default.artist,
            album=override.get("album", default.album) if override else default.album,
            year=override.get("year", default.year) if override else default.year,
            track=default.track,
        )
        used = {"artist": query.artist, "album": query.album, "year": query.year}
        facts = [facts_from_tags(m.title, m.track, m.discnumber, m.duration_seconds) for m in album.files]
        try:
            matches = find_release(self._client, facts, query, cache=self._releases)
        except DiscogsError as exc:
            return LookupResult(error=str(exc), used_query=used)
        matches = [m for m in matches if m.matched]
        if not matches:
            self._matches.pop(id(album), None)
            return LookupResult(used_query=used)
        best = matches[0]
        self._matches[id(album)] = best
        return LookupResult(
            fields=_summary_fields(best, self._include_styles),
            cover_bytes=self._cover(best),
            used_query=used,
            alternatives=[
                LookupAlternative(label=f"{m.release.label_text()}  -- {m.summary()}", data=m) for m in matches[1:]
            ],
        )

    def _cover(self, match: DiscogsMatch):
        try:
            return fetch_cover(self._client, match.release)
        except DiscogsError:
            return None

    def _resolve(self, album: AlbumFolder, match: DiscogsMatch) -> LookupResult:
        self._matches[id(album)] = match
        return LookupResult(fields=_summary_fields(match, self._include_styles), cover_bytes=self._cover(match))

    def _on_styles_toggled(self, checked: bool) -> None:
        """Re-renders every found row's summary with or without styles;
        the choice is also what file_changes() uses."""
        self._include_styles = checked
        for row, result in self._row_results.items():
            match = self._matches.get(id(self.items[row]))
            if match is not None and result.found:
                result.fields = _summary_fields(match, checked)
                self._update_row_cells(row, self.items[row], result)
        if self._current_detail_row >= 0:
            self._render_detail(self._current_detail_row, self._row_results.get(self._current_detail_row))

    def file_changes(self) -> list[tuple[MP3File, dict[str, str]]]:
        """(file, {field: value}) for every file of every ticked album that
        was matched to a track. Unmatched files are left alone."""
        changes = []
        for row in self.accepted_rows():
            album = self.items[row]
            match = self._matches.get(id(album))
            if match is None:
                continue
            for index, track in match.assignment.items():
                changes.append((album.files[index], fields_for(match.release, track, self._include_styles)))
        return changes
