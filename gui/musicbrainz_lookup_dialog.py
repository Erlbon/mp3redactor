"""
gui/musicbrainz_lookup_dialog.py

Import > Look Up via MusicBrainz...: finds the release each selected
folder of MP3s belongs to (core/musicbrainz_lookup.py) -- one row per
folder, since an album is one folder. Built on redactor_common's shared
review dialog (LookupDialogBase): the release's cover (Cover Art
Archive) next to the files' own, other editions of the album under
Other Matches, and a Search Query form to correct the artist/album and
search again. Searches run in the background, paced at MusicBrainz's
one request per second.

A row's "fields" are a summary of the release for review; what's
actually applied is per file (title, track, recording id...), built by
file_changes() from the match remembered for that folder.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from redactor_common.gui.lookup_dialog import LookupAlternative, LookupDialogBase, LookupResult

from core.cover_art import read_cover
from core.mp3_file import MP3File
from core.musicbrainz_lookup import (
    AlbumQuery,
    MusicBrainzError,
    ReleaseMatch,
    album_query,
    facts_from_tags,
    fetch_front_cover,
    fields_for,
    find_album,
)


@dataclass
class AlbumFolder:
    folder: Path
    files: list[MP3File] = field(default_factory=list)


def group_by_folder(files: list[MP3File]) -> list[AlbumFolder]:
    albums: dict[Path, AlbumFolder] = {}
    for mp3 in files:
        folder = Path(mp3.path).parent
        albums.setdefault(folder, AlbumFolder(folder)).files.append(mp3)
    for album in albums.values():
        album.files.sort(key=lambda m: Path(m.path).name.casefold())
    return list(albums.values())


def _summary_fields(match: ReleaseMatch) -> dict[str, str]:
    release = match.release
    fields = {
        "album": release.title,
        "album artist": release.artist,
        "released": " ".join(b for b in (release.date, release.country) if b),
        "format": release.media_formats,
        "tracks": str(release.track_count),
        "match": match.summary(),
    }
    return {k: v for k, v in fields.items() if v}


class MusicBrainzLookupDialog(LookupDialogBase):
    def __init__(self, albums: list[AlbumFolder], parent=None, fetch=None):
        self._fetch = fetch
        self._matches: dict[int, ReleaseMatch] = {}  # id(album) -> the chosen match
        super().__init__(
            albums,
            parent,
            window_title="Look Up via MusicBrainz",
            info_text=(
                f"Finding the MusicBrainz release for {len(albums)} folder(s) -- one album per folder -- "
                "by Artist + Album (from the tags, or an 'Artist - Album' folder name), then matching "
                "each file to its track by number, title and length. Other editions of the album are "
                "listed under Other Matches. Untick anything you don't trust, then Apply; changes are "
                "written on Save. MusicBrainz is queried at most once per second, so this takes a few "
                "seconds per album."
            ),
            search_label="Searching MusicBrainz…",
            item_label=lambda album: f"{album.folder.name}  ({len(album.files)} file(s))",
            search_one=self._search_one,
            query_fields=[("artist", "Artist"), ("album", "Album")],
            get_local_cover=self._local_cover,
            resolve_alternative=self._resolve,
        )

    @staticmethod
    def _local_cover(album: AlbumFolder):
        for mp3 in album.files:
            cover = read_cover(mp3.path)
            if cover:
                return cover[0]
        return None

    def _search_one(self, album: AlbumFolder, override: dict) -> LookupResult:
        default = album_query(
            [m.album for m in album.files], [m.albumartist or m.artist for m in album.files], album.folder,
        )
        query = AlbumQuery(
            artist=override.get("artist", default.artist) if override else default.artist,
            album=override.get("album", default.album) if override else default.album,
        )
        used = {"artist": query.artist, "album": query.album}
        facts = [facts_from_tags(m.title, m.track, m.discnumber, m.duration_seconds) for m in album.files]
        try:
            matches = find_album(facts, query, self._fetch) if self._fetch else find_album(facts, query)
        except MusicBrainzError as exc:
            return LookupResult(error=str(exc), used_query=used)
        if not matches or matches[0].matched == 0:
            self._matches.pop(id(album), None)
            return LookupResult(used_query=used)
        best = matches[0]
        self._matches[id(album)] = best
        return LookupResult(
            fields=_summary_fields(best),
            cover_bytes=fetch_front_cover(best.release.id),
            used_query=used,
            alternatives=[
                LookupAlternative(label=f"{m.release.label()}  -- {m.summary()}", data=m) for m in matches[1:]
            ],
        )

    def _resolve(self, album: AlbumFolder, match: ReleaseMatch) -> LookupResult:
        self._matches[id(album)] = match
        return LookupResult(fields=_summary_fields(match), cover_bytes=fetch_front_cover(match.release.id))

    def file_changes(self) -> list[tuple[MP3File, dict[str, str]]]:
        """(file, {field: value}) for every file of every ticked album
        that was matched to a track. Unmatched files are left alone."""
        changes = []
        for row in self.accepted_rows():
            album = self.items[row]
            match = self._matches.get(id(album))
            if match is None:
                continue
            for index, track in match.assignment.items():
                changes.append((album.files[index], fields_for(match.release, track)))
        return changes
