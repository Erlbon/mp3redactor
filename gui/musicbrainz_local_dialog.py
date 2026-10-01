"""
gui/musicbrainz_local_dialog.py

Metadata > Look Up > MusicBrainz (Local Database)...: the Look Up via
MusicBrainz review dialog (gui/musicbrainz_lookup_dialog.py -- same rows,
Other Matches, Search Query form, per-file changes) answered from the
offline database built in Tools > MusicBrainz Database
(core/musicbrainz_local.py): no network, no rate limit, instant.

Differences from the online dialog: a file's own MusicBrainz Album Id tag
finds its exact release; the Search Query has a Year hint, and a barcode typed
into Album searches by barcode; label, catalogue number and release country
are filled too; there is no genre (MusicBrainz tags aren't in the CC0 dump)
and no cover (the Cover Art Archive is online -- use Set Cover from Folder
Image or Redact). AcoustID fingerprinting, when fpcalc is set up, still asks
AcoustID online; if that fails (offline) the lookup carries on without it.
"""

from __future__ import annotations

from pathlib import Path

from redactor_common.gui.lookup_dialog import LookupAlternative, LookupResult

from core.acoustid_lookup import AcoustIdError
from core.musicbrainz_local import (
    LocalQuery,
    MusicBrainzLocalDatabase,
    MusicBrainzLocalError,
    find_album_local,
    local_fields_for,
)
from core.musicbrainz_lookup import _most_common, album_query, facts_from_tags
from gui.musicbrainz_lookup_dialog import (
    AlbumFolder,
    MusicBrainzLookupDialog,
    _summary_fields,
    group_by_folder,  # noqa: F401 -- re-exported for callers
)


class MusicBrainzLocalLookupDialog(MusicBrainzLookupDialog):
    window_title = "Look Up via MusicBrainz (Local Database)"
    search_label = "Searching the local MusicBrainz database…"
    query_fields = [("artist", "Artist"), ("album", "Album"), ("year", "Year")]

    def __init__(self, albums: list[AlbumFolder], db: MusicBrainzLocalDatabase, parent=None,
                 fpcalc: Path | None = None, post=None):
        self._db = db
        super().__init__(albums, parent, fpcalc=fpcalc, post=post)

    def _info_text(self, albums: list[AlbumFolder]) -> str:
        by_sound = (
            " Each file is also identified by its sound (AcoustID, which needs the network; without it the "
            "lookup just carries on from tags)." if self._fpcalc else ""
        )
        return (
            f"Finding the MusicBrainz release for {len(albums)} folder(s) -- one album per folder -- in your "
            "local MusicBrainz database (Tools > MusicBrainz Database): by the files' own MusicBrainz Album "
            "Id if they have one, else by Artist + Album (or a barcode typed into Album), then matching each "
            "file to its track by number, title and length. Other editions are listed under Other Matches. "
            "Label, catalogue number and country are filled too; MusicBrainz genres and covers are not in the "
            "offline data. Untick anything you don't trust, then Apply; changes are written on Save." + by_sound
        )

    def _cover(self, release):
        return None  # the Cover Art Archive is online

    def _fields_for(self, release, track) -> dict[str, str]:
        return local_fields_for(release, track)

    def _search_one(self, album: AlbumFolder, override: dict) -> LookupResult:
        default = album_query(
            [m.album for m in album.files], [m.albumartist or m.artist for m in album.files], album.folder,
        )
        year = _most_common([m.year[:4] for m in album.files if m.year])
        override = override or {}
        query = LocalQuery(
            artist=override.get("artist", default.artist), album=override.get("album", default.album),
            year=override.get("year", year),
        )
        used = {"artist": query.artist, "album": query.album, "year": query.year}
        facts = [facts_from_tags(m.title, m.track, m.discnumber, m.duration_seconds) for m in album.files]
        try:
            hits = self._fingerprint(album)
        except AcoustIdError:
            hits = [[] for _ in album.files]  # offline or refused: the local lookup doesn't need it
        for fact, file_hits in zip(facts, hits):
            fact.recordings = {h.recording_id: h.score for h in file_hits}
        try:
            matches = find_album_local(
                self._db, facts, query,
                album_ids=tuple(m.musicbrainz_albumid for m in album.files if m.musicbrainz_albumid),
                recording_ids=[m.musicbrainz_trackid for m in album.files],
                alternatives=True,
            )
        except MusicBrainzLocalError as exc:
            return LookupResult(error=str(exc), used_query=used)
        matches = [m for m in matches if m.matched]
        if not matches:
            self._matches.pop(id(album), None)
            return LookupResult(used_query=used)
        best = matches[0]
        self._matches[id(album)] = best
        return LookupResult(
            fields=_summary_fields(best),
            used_query=used,
            alternatives=[
                LookupAlternative(label=f"{m.release.label()}  -- {m.summary()}", data=m) for m in matches[1:]
            ],
        )
