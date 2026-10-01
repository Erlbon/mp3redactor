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

With fpcalc available (Tools > External Tools), each folder is
also identified by SOUND (core/acoustid_lookup.py, stage 2): every file
is fingerprinted once, its AcoustID recordings make its track pairing
certain on any release, and when the text search doesn't place every
file -- junk tags, no usable names -- the releases AcoustID found for
the folder are tried too.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from redactor_common.gui.lookup_dialog import LookupAlternative, LookupDialogBase, LookupResult

from core.acoustid_lookup import AcoustIdError, candidate_releases, identify_files
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
    find_album_by_recordings,
    rank_matches,
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
        # only the local database's releases know their label (core/musicbrainz_local.py)
        "label": " ".join(b for b in (getattr(release, "label_name", ""), getattr(release, "catno", "")) if b),
        "format": release.media_formats,
        "tracks": str(release.track_count),
        "match": match.summary(),
    }
    return {k: v for k, v in fields.items() if v}


class MusicBrainzLookupDialog(LookupDialogBase):
    # What the offline twin (gui/musicbrainz_local_dialog.py) changes: its texts, the search form,
    # the cover (the Cover Art Archive is online) and the field mapping.
    window_title = "Look Up via MusicBrainz"
    search_label = "Searching MusicBrainz…"
    query_fields = [("artist", "Artist"), ("album", "Album")]

    def __init__(self, albums: list[AlbumFolder], parent=None, fetch=None, fpcalc: Path | None = None, post=None):
        self._fetch = fetch
        self._fpcalc = fpcalc  # None: identify by tags/names only
        self._post = post  # AcoustID transport (tests)
        self._matches: dict[int, ReleaseMatch] = {}  # id(album) -> the chosen match
        self._fingerprints: dict[int, list] = {}  # id(album) -> AcoustID hits per file (computed once)
        super().__init__(
            albums,
            parent,
            window_title=self.window_title,
            info_text=self._info_text(albums),
            search_label=self.search_label,
            item_label=lambda album: f"{album.folder.name}  ({len(album.files)} file(s))",
            search_one=self._search_one,
            query_fields=self.query_fields,
            get_local_cover=self._local_cover,
            resolve_alternative=self._resolve,
            item_noun="folder",
        )

    def _info_text(self, albums: list[AlbumFolder]) -> str:
        by_sound = (
            " Each file is also identified by its sound (AcoustID fingerprints), which finds the album "
            "even when tags and names are missing."
            if self._fpcalc else
            " Tip: with fpcalc set up (Tools > External Tools) files are also identified "
            "by their sound, even when tags and names are missing."
        )
        return (
            f"Finding the MusicBrainz release for {len(albums)} folder(s) -- one album per folder -- "
            "by Artist + Album (from the tags, or an 'Artist - Album' folder name), then matching "
            "each file to its track by number, title and length. Other editions of the album are "
            "listed under Other Matches. Untick anything you don't trust, then Apply; changes are "
            "written on Save. MusicBrainz is queried at most once per second, so this takes a few "
            "seconds per album." + by_sound
        )

    def _cover(self, release):
        return fetch_front_cover(release.id)

    def _fields_for(self, release, track) -> dict[str, str]:
        return fields_for(release, track)

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
            hits = self._fingerprint(album)
        except AcoustIdError as exc:
            return LookupResult(error=str(exc), used_query=used)
        for fact, file_hits in zip(facts, hits):
            fact.recordings = {h.recording_id: h.score for h in file_hits}
        try:
            matches = []
            if query.artist or query.album:
                matches = find_album(facts, query, self._fetch) if self._fetch else find_album(facts, query)
            if any(hits) and (not matches or matches[0].matched < len(facts)):
                seen = {m.release.id for m in matches}
                ids = [rid for rid in candidate_releases(hits, limit=5) if rid not in seen]
                if ids:
                    matches = rank_matches(matches + find_album_by_recordings(facts, ids, self._fetch), len(facts))
        except MusicBrainzError as exc:
            return LookupResult(error=str(exc), used_query=used)
        if not matches or matches[0].matched == 0:
            self._matches.pop(id(album), None)
            return LookupResult(used_query=used)
        best = matches[0]
        self._matches[id(album)] = best
        return LookupResult(
            fields=_summary_fields(best),
            cover_bytes=self._cover(best.release),
            used_query=used,
            alternatives=[
                LookupAlternative(label=f"{m.release.label()}  -- {m.summary()}", data=m) for m in matches[1:]
            ],
        )

    def _fingerprint(self, album: AlbumFolder) -> list:
        """AcoustID hits for each of the folder's files (fingerprinted
        once, kept for Search This Item); [[]...] without fpcalc."""
        if not self._fpcalc:
            return [[] for _ in album.files]
        key = id(album)
        if key not in self._fingerprints:
            hits, _problems = identify_files([m.path for m in album.files], self._fpcalc, self._post)
            self._fingerprints[key] = hits
        return self._fingerprints[key]

    def _resolve(self, album: AlbumFolder, match: ReleaseMatch) -> LookupResult:
        self._matches[id(album)] = match
        return LookupResult(fields=_summary_fields(match), cover_bytes=self._cover(match.release))

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
                changes.append((album.files[index], self._fields_for(match.release, track)))
        return changes
