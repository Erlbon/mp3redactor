"""
core/musicbrainz_local.py

Looks albums up in the OFFLINE MusicBrainz database that
core/musicbrainz_import.py builds from the core dump -- the local twin of
core/musicbrainz_lookup.py, with no network and no rate limit. Qt-free.

It returns the SAME shapes as the online lookup (Release, ReleaseTrack,
ReleaseMatch -- the local ones are subclasses that carry a little more), so
the review dialog, the track pairing (match_release) and fields_for() are
reused unchanged. How a folder is resolved, best evidence first; each stage
runs only if the folder isn't fully placed yet:

1. RELEASE MBID -- the files' own "MusicBrainz Album Id" tag: the exact
   release, resolved locally (via="mbid", ranked above everything else).
2. BARCODE -- a barcode passed in, or an Album search text that is one.
3. RECORDINGS -- the files' recording ids (their "MusicBrainz Track Id"
   tag: via="recording-tag"; or AcoustID fingerprint hits, which stay
   online: via="recording") -> the releases containing them, those holding
   most of the folder's files first.
4. TEXT -- artist + album through the prebuilt full-text index (words, any
   order, "&" = "and", punctuation and accents ignored), scored like the
   online code: album title, artist, official status, track count, year.
   "Beatles, The" and "The Beatles" are the same artist (word sets with the
   article removed, also compared against the artist's sort name); a
   "Various Artists" folder searches by album only.

Each candidate's track list is then paired with the files by the online
code's match_release() (recording id > title/length/track number), so
multi-disc releases and vinyl (positions are sequential per disc; the
printed "A1" is kept in the track's number) behave exactly as online.

A single track without an album can be found too (find_track_local), but only
when the database was built with the optional track search index.

Not available locally: genre (MusicBrainz tags are derived, non-CC0 data) and
the Cover Art Archive covers (network). Everything else maps like the online
lookup, plus label, catalogue number and release country.
"""

from __future__ import annotations

import difflib
import re
import uuid
from collections import defaultdict
from dataclasses import dataclass
from typing import Optional

from redactor_common.core.local_db import (
    LocalDatabase,
    LocalDatabaseError,
    normalize_words,
    open_cached,
    year_gap,
)

from core.musicbrainz_import import MusicBrainzDatabaseError
from core.musicbrainz_lookup import (
    AlbumQuery,
    FileFacts,
    Release,
    ReleaseMatch,
    ReleaseTrack,
    fields_for,
    match_release,
)

CANDIDATES = 4          # releases whose track lists are loaded and matched per folder
TEXT_POOL = 40          # releases the full-text search pulls before ranking
RECORDING_POOL = 60     # releases listed per recording id
LOCAL_SOURCE_NAME = "MusicBrainz (Local Database)"

_RELEASE_COLUMNS = (
    "id", "gid", "title", "artist_credit_text", "artist_sort", "year", "month", "day", "country", "status",
    "label", "catalog_number", "format", "track_count", "barcode", "rg_type", "medium_count", "release_group_gid",
)
_TRACK_COLUMNS = ("medium", "position", "number", "title", "artist_credit_text", "recording_gid", "length_ms")


class MusicBrainzLocalError(MusicBrainzDatabaseError):
    """The local MusicBrainz database is missing, unreadable, or not one this app built."""


class MusicBrainzLocalDatabase(LocalDatabase):
    """The built database, opened read-only, remembering which optional indexes it has."""

    def __init__(self, path: str):
        super().__init__(path, ("releases", "tracks"), "a MusicBrainz lookup database built by this app",
                         MusicBrainzLocalError)
        missing = [c for c in _RELEASE_COLUMNS if c not in self.table_columns("releases")]
        if missing or not set(_TRACK_COLUMNS) <= set(self.table_columns("tracks")):
            self.close()
            raise MusicBrainzLocalError(
                "This MusicBrainz database was built by a different version of the app "
                f"(missing columns{': ' + ', '.join(missing) if missing else ''}) -- rebuild it in "
                "Tools > MusicBrainz Database."
            )
        self.has_release_search = self.has_table("releases_fts")
        self.has_track_search = self.has_table("tracks_fts")
        self.has_track_rows = bool(self.query("select 1 from tracks limit 1"))


def open_database(path: str) -> MusicBrainzLocalDatabase:
    """The session's opened database for `path` (opened once, kept)."""
    return open_cached(path, MusicBrainzLocalDatabase)


# --- release / track rows -> the online lookup's shapes ------------------------------------


@dataclass
class LocalRelease(Release):
    """An online-shaped Release plus what only the local database knows.
    (Not named `label`: Release already has a label() method.)"""

    row_id: int = 0
    artist_sort: str = ""
    label_name: str = ""
    catno: str = ""
    barcode: str = ""
    rg_type: str = ""
    release_group_id: str = ""


def _date_text(year, month, day) -> str:
    if not year:
        return ""
    text = f"{int(year):04d}"
    if month:
        text += f"-{int(month):02d}"
        if day:
            text += f"-{int(day):02d}"
    return text


def _release_from_row(row: tuple) -> LocalRelease:
    (rid, gid, title, credit, sort, year, month, day, country, status, label, catno, fmt, count, barcode, rg_type,
     _media, rg_gid) = row
    return LocalRelease(
        id=gid, title=title or "", artist=credit or "", date=_date_text(year, month, day), country=country or "",
        status=status or "",
        # what tells editions of one album apart in the dialog's Other Matches list
        disambiguation=", ".join(b for b in (label, catno) if b),
        media_formats=fmt or "", track_count=count or 0,
        row_id=rid, artist_sort=sort or "", label_name=label or "", catno=catno or "", barcode=barcode or "",
        rg_type=rg_type or "", release_group_id=rg_gid or "",
    )


_SELECT_RELEASE = "select " + ", ".join(_RELEASE_COLUMNS) + " from releases"


def _uuid_text(blob) -> str:
    try:
        return str(uuid.UUID(bytes=bytes(blob))) if blob else ""
    except ValueError:
        return ""


def load_tracks(db: MusicBrainzLocalDatabase, release: LocalRelease) -> list[ReleaseTrack]:
    rows = db.query(
        "select medium, position, number, title, artist_credit_text, recording_gid, length_ms from tracks "
        "where release_id = ? order by medium, position", (release.row_id,),
    )
    return [
        ReleaseTrack(
            disc=medium or 1, position=position or 0, title=title or "",
            seconds=length / 1000 if length else None, recording_id=_uuid_text(gid),
            artist=credit or release.artist,
        )
        for medium, position, _number, title, credit, gid, length in rows
    ]


def _with_tracks(db: MusicBrainzLocalDatabase, release: LocalRelease) -> LocalRelease:
    release.tracks = load_tracks(db, release)
    release.track_count = release.track_count or len(release.tracks)
    return release


def release_by_mbid(db: MusicBrainzLocalDatabase, mbid: str) -> Optional[LocalRelease]:
    """The release with this MusicBrainz id, tracks loaded; None if not in the database."""
    rows = db.query(f"{_SELECT_RELEASE} where gid = ?", ((mbid or "").strip().lower(),))
    return _with_tracks(db, _release_from_row(rows[0])) if rows else None


def releases_by_barcode(db: MusicBrainzLocalDatabase, barcode: str) -> list[LocalRelease]:
    """Releases with this barcode (UPC-A and EAN-13 spellings of one code are the same)."""
    digits = re.sub(r"\D", "", barcode or "")
    if len(digits) < 8:
        return []
    variants = {digits, digits.lstrip("0"), digits.zfill(12), digits.zfill(13)} - {""}
    marks = ",".join("?" * len(variants))
    return [_release_from_row(r) for r in db.query(f"{_SELECT_RELEASE} where barcode in ({marks})", sorted(variants))]


def releases_by_recording(db: MusicBrainzLocalDatabase, recording_id: str, limit: int = RECORDING_POOL) -> list[LocalRelease]:
    """The releases that contain this recording (MusicBrainz recording id)."""
    try:
        blob = uuid.UUID((recording_id or "").strip()).bytes
    except ValueError:
        return []
    ids = [r[0] for r in db.query(
        "select distinct release_id from tracks where recording_gid = ? limit ?", (blob, int(limit)))]
    return _releases_by_row_id(db, ids)


def _releases_by_row_id(db: MusicBrainzLocalDatabase, ids: list[int]) -> list[LocalRelease]:
    if not ids:
        return []
    rows = db.query_in(f"{_SELECT_RELEASE} where id in ({{ids}})", ids)
    by_id = {row[0]: _release_from_row(row) for row in rows}
    return [by_id[i] for i in ids if i in by_id]


# --- artist / album text matching -----------------------------------------------------------

_VARIOUS = {"various", "various artists", "va", "v a", "varios artistas", "diverse", "diverse artister"}


def is_various(artist: str) -> bool:
    return normalize_words(artist) in _VARIOUS


def _artist_words(text: str) -> frozenset:
    """The words that identify an artist, order and a leading/trailing "the" aside:
    "The Beatles" and "Beatles, The" are the same set, as are "Paul McCartney" and
    "McCartney, Paul"."""
    words = normalize_words(text).split()
    kept = [w for w in words if w != "the"]
    return frozenset(kept or words)


def artist_equivalent(tag: str, *names: str) -> bool:
    """True when the artist text `tag` names the same artist as any of `names`
    (the credit as printed and/or the artist's sort name)."""
    if not (tag or "").strip():
        return False
    if is_various(tag):
        return any(is_various(n) for n in names)
    wanted = _artist_words(tag)
    return any(n and _artist_words(n) == wanted for n in names)


def _similar(a: str, b: str) -> float:
    a, b = normalize_words(a), normalize_words(b)
    if not a or not b:
        return 0.0
    return 1.0 if a == b else difflib.SequenceMatcher(None, a, b).ratio()


def agreement(album: str, artist: str, release: Release, track: ReleaseTrack) -> float:
    """How far a file's own tags back up a match (1.0 = album and artist both agree);
    the local twin of redact_steps._agreement, but with "Beatles, The" = "The Beatles"."""
    sort = getattr(release, "artist_sort", "")
    factor = 1.0 if normalize_words(album) == normalize_words(release.title) else 0.6
    if not (artist or "").strip():
        factor *= 0.85
    elif not artist_equivalent(artist, track.artist, release.artist, sort):
        factor *= 0.5
    return factor


def _match_tokens(text: str, drop: tuple[str, ...] = ("and",)) -> str:
    """FTS5 tokens (quoted, AND-ed) for the words of `text`. "and" is dropped because
    the index holds the original text ("Simon & Garfunkel" has no "and" in it); if
    that leaves nothing, the words are kept as they are."""
    words = normalize_words(text).split()
    kept = [w for w in words if w not in drop] or words
    return " ".join('"' + w + '"' for w in kept)


@dataclass
class LocalQuery(AlbumQuery):
    """The folder's search: artist, album, plus a year hint and an optional barcode."""

    year: str = ""
    barcode: str = ""


_BARCODE_TEXT = re.compile(r"^\s*\d[\d \-]{6,}\d\s*$")


def search_releases_local(db: MusicBrainzLocalDatabase, query: AlbumQuery, limit: int = TEXT_POOL) -> list[LocalRelease]:
    """Release summaries (no track lists) for the artist + album text, then, if
    that finds nothing, by album alone -- the local twin of search_releases()."""
    if not db.has_release_search:
        raise MusicBrainzLocalError("This MusicBrainz database has no release search index -- rebuild it.")
    if not (query.album or "").strip():
        raise MusicBrainzLocalError("needs an album name (Album tag or an 'Artist - Album' folder name)")
    album = _match_tokens(query.album, drop=())
    attempts = []
    if (query.artist or "").strip() and not is_various(query.artist):
        artist = _match_tokens(query.artist, drop=("and", "the"))
        attempts.append(f"title : ({album}) AND artist_credit_text : ({artist})")
    attempts.append(f"title : ({album})")
    for match in attempts:
        ids = [r[0] for r in db.query(
            "select rowid from releases_fts where releases_fts match ? order by rank limit ?", (match, int(limit)))]
        found = _releases_by_row_id(db, ids)
        if found:
            return found
    return []


def _preranked(candidates: list[LocalRelease], query: LocalQuery, file_count: int) -> list[LocalRelease]:
    """Best summaries first: album title, artist, official, room for the whole
    folder, year -- the cheap ranking before any track list is loaded."""
    def key(r: LocalRelease):
        artist_score = 1.0 if artist_equivalent(query.artist, r.artist, r.artist_sort) or (
            is_various(query.artist) and is_various(r.artist)) else _similar(query.artist, r.artist) * 0.8
        return (
            -round(_similar(query.album, r.title), 2), -round(artist_score, 2),
            r.status.casefold() != "official", r.track_count < file_count,
            year_gap(query.year, r.date[:4]), r.date or "9999",
        )
    return sorted(candidates, key=key)


# --- matching a folder ----------------------------------------------------------------------


@dataclass
class LocalMatch(ReleaseMatch):
    """A ReleaseMatch that also says HOW the release was found and how well the
    folder's own tags agree with it (what the Redact step turns into a confidence)."""

    via: str = "text"          # mbid | barcode | recording | recording-tag | text
    exact: bool = False        # the folder's artist and album tags equal the release's
    count_equal: bool = False  # the release has as many tracks as the folder has files


def _is_exact(query: AlbumQuery, release: LocalRelease) -> bool:
    return (
        bool(query.album) and normalize_words(query.album) == normalize_words(release.title)
        and artist_equivalent(query.artist, release.artist, release.artist_sort)
    )


def _rank(matches: list[LocalMatch], file_count: int) -> list[LocalMatch]:
    """An exact release id first, then overall score, official releases, a track
    count equal to the folder's, and the oldest."""
    def key(m: LocalMatch):
        return (
            m.via != "mbid", -round(m.score, 3), m.release.status.casefold() != "official",
            m.release.track_count != file_count, m.release.date or "9999",
        )
    return sorted(matches, key=key)


def _match(files: list[FileFacts], release: LocalRelease, query: AlbumQuery, via: str) -> LocalMatch:
    base = match_release(files, release)
    return LocalMatch(
        release=release, score=base.score, assignment=base.assignment, matched=base.matched, via=via,
        exact=_is_exact(query, release), count_equal=(len(release.tracks) or release.track_count) == len(files),
    )


def _recording_candidates(db, files: list[FileFacts], tag_recordings: list[str]) -> list[tuple[LocalRelease, str]]:
    """Releases holding the folder's recordings, those with most files first."""
    covered: dict[int, int] = defaultdict(int)
    strength: dict[int, float] = defaultdict(float)
    releases: dict[int, LocalRelease] = {}
    via: dict[int, str] = {}
    known = [(f.recordings, tag) for f, tag in zip(files, tag_recordings)] if tag_recordings else [(f.recordings, "") for f in files]
    for scores, tag in known:
        wanted = dict(scores)
        if tag:
            wanted.setdefault(tag.lower(), 0.95)
        best: dict[int, float] = {}
        for rec, score in wanted.items():
            for release in releases_by_recording(db, rec):
                releases.setdefault(release.row_id, release)
                best[release.row_id] = max(best.get(release.row_id, 0.0), score)
                if rec in scores:
                    via[release.row_id] = "recording"
                else:
                    via.setdefault(release.row_id, "recording-tag")
        for rid, score in best.items():
            covered[rid] += 1
            strength[rid] += score
    ranked = sorted(covered, key=lambda rid: (-covered[rid], -strength[rid], releases[rid].date or "9999", rid))
    return [(releases[rid], via.get(rid, "recording")) for rid in ranked[:CANDIDATES + 1]]


def find_album_local(
    db: MusicBrainzLocalDatabase,
    files: list[FileFacts],
    query: LocalQuery,
    *,
    album_ids: tuple[str, ...] = (),
    recording_ids: Optional[list[str]] = None,
    candidates: int = CANDIDATES,
    alternatives: bool = False,
) -> list[LocalMatch]:
    """The folder's releases, best first. `album_ids`: the files' own MusicBrainz
    release ids; `recording_ids`: each file's own recording id tag ("" if none,
    one entry per file); the files' AcoustID hits are in FileFacts.recordings.
    Stages run until one places every file (see the module docstring), unless
    `alternatives`: then every stage runs, so the review dialog can list other
    editions of the album even after an exact release id."""
    matches: dict[str, LocalMatch] = {}

    def add(release: Optional[LocalRelease], via: str) -> None:
        if release is None or release.id in matches:
            return
        if not release.tracks:
            _with_tracks(db, release)
        matches[release.id] = _match(files, release, query, via)

    def placed() -> bool:
        return not alternatives and any(m.matched >= len(files) for m in matches.values())

    for mbid in dict.fromkeys(i.strip().lower() for i in album_ids if i and i.strip()):
        add(release_by_mbid(db, mbid), "mbid")
    barcode = query.barcode or (query.album if _BARCODE_TEXT.match(query.album or "") else "")
    if barcode and not placed():
        for release in releases_by_barcode(db, barcode)[:candidates]:
            add(release, "barcode")
    if not placed() and (any(f.recordings for f in files) or any(recording_ids or [])):
        for release, via in _recording_candidates(db, files, list(recording_ids or [])):
            add(release, via)
    text_query = query if not _BARCODE_TEXT.match(query.album or "") else LocalQuery(query.artist, "", query.year)
    if not placed() and (text_query.album or "").strip():
        pool = _preranked(search_releases_local(db, text_query), text_query, len(files))
        for release in pool[:candidates]:
            add(release, "text")
    return _rank(list(matches.values()), len(files))


def find_track_local(
    db: MusicBrainzLocalDatabase, fact: FileFacts, artist: str = "", year: str = "", candidates: int = CANDIDATES,
) -> list[LocalMatch]:
    """Releases containing a single track found by title (+ artist), when the database has
    the track search index: [] otherwise. Each match pairs the one file with its best
    track; ranked like find_album_local."""
    if not db.has_track_search or not (fact.title or "").strip():
        return []
    title = _match_tokens(fact.title, drop=())
    attempts = []
    if (artist or "").strip() and not is_various(artist):
        attempts.append(f"title : ({title}) AND artist_credit_text : ({_match_tokens(artist, drop=('and', 'the'))})")
    attempts.append(f"title : ({title})")
    query = LocalQuery(artist=artist, album="", year=year)
    for match in attempts:
        ids = list(dict.fromkeys(r[0] for r in db.query(
            "select t.release_id from tracks_fts f join tracks t on t.rowid = f.rowid "
            "where tracks_fts match ? order by f.rank limit ?", (match, TEXT_POOL * 5))))
        releases = _releases_by_row_id(db, ids[:TEXT_POOL])
        if not releases:
            continue
        pool = _preranked(releases, LocalQuery(artist=artist, album="", year=year), 1)
        found = []
        for release in pool[:candidates * 2]:
            _with_tracks(db, release)
            match_ = _match([fact], release, query, "track")
            if match_.matched:
                found.append(match_)
        if found:
            return _rank(found, 1)[:candidates]
    return []


# --- mapping to the app's fields -----------------------------------------------------------


def local_fields_for(release: Release, track: ReleaseTrack) -> dict[str, str]:
    """MP3File field values for a matched file: everything the online lookup maps, plus the
    label (Publisher), Catalog Number and Release Country the local database holds. No genre."""
    fields = fields_for(release, track)
    extra = {
        "publisher": getattr(release, "label_name", ""),
        "catalognumber": getattr(release, "catno", ""),
        "releasecountry": release.country,
    }
    fields.update({k: v for k, v in extra.items() if v})
    return fields


def describe_missing(path: str) -> str:
    """Why `path` can't be used, as one line for a Redact note / message box."""
    if not path:
        return "no local MusicBrainz database is set up (Tools > MusicBrainz Database)"
    try:
        open_database(path)
    except LocalDatabaseError as exc:
        return str(exc)
    return ""
