"""
core/musicbrainz_import.py

Builds a compact, offline SQLite lookup database from MusicBrainz's CORE
database dump (`mbdump.tar.bz2`, licence CC0 -- the user downloads it from
https://musicbrainz.org/doc/MusicBrainz_Database/Download; this app never
does, it is about 7 GB). The derived archive (tags, ratings, annotations,
CC BY-NC-SA) is never read: DERIVED_TABLES in redactor_common's
core/musicbrainz_schema.py lists what that would be, and nothing here
uses it -- which is why the lookup has no genre.

Streaming, reading and writing are redactor_common's core/dump_import.py
(iter_tar_members + iter_pgcopy_records + SqliteBuilder); what's here is
the recipe: MusicBrainz's normalised schema (ids, join tables) becomes
two flat app tables.

    releases(id, gid, title, artist_credit_text, artist_sort, artist_credit_id,
             release_group_gid, rg_type, status, language, script, barcode,
             year, month, day, country, label, catalog_number, format,
             medium_count, track_count)
    tracks(release_id, medium, position, number, title, artist_credit_text,
           recording_gid, length_ms)
    releases_fts(title, artist_credit_text)        [prebuilt FTS5, external content]
    tracks_fts(title, artist_credit_text)          [only with "track search index"]
    redactor_import_info(key, value)

- `releases.id` is MusicBrainz's own integer id, `gid` the public MBID (text);
  the artist credit is the credited names with their join phrases ("Paul
  McCartney & Wings"); `artist_sort` is the FIRST credited artist's sort name
  ("Beatles, The"); `rg_type` the release group's primary type; `language` /
  `script` are ISO codes; date and `country` (ISO 3166-1) come from the
  EARLIEST release event (a dated event wins over an undated one; one with
  a country over one without on the same day); `label` / `catalog_number`
  the first release_label row; `format` summarises the mediums ("CD",
  "2×Vinyl", "CD + DVD").
- `tracks.medium` is the disc number (the medium's position), `position` the
  track's sequential number on that disc (vinyl "A1","B2" are 1,2,... here
  and the printed label stays in `number`), `recording_gid` the recording's
  MBID as 16 raw bytes (half the size of text; query with uuid.UUID(..).bytes).
  `artist_credit_text` is NULL when the track's credit equals its release's
  (the usual case; saves ~30% of the table) unless the track search index is
  built, which stores it always. Data tracks are skipped.

What is kept is chosen by BuildOptions: official releases only (default),
which release-group types (default Album, EP, Single, Compilation,
Soundtrack, Live; a release group with secondary types is kept only if ALL
of them are ticked), whether undated releases are kept, whether track
rows are written at all, and the optional track search index.

How it reads (members arrive in archive order, which this does not assume):
one forward pass over the archive; every small table goes into a scratch SQLite
file next to the destination ("<dest>.scratch.tmp", deleted at the end or on
cancel/failure), never into Python dicts -- apart from the tiny reference
tables (formats, languages, scripts, countries, types: a few thousand
entries). When the track table arrives and everything it joins to has
already been read (the real dump is alphabetical, so it has), tracks stream
straight into the destination; otherwise they wait in the scratch file and
are written after the pass. Peak Python memory: ~100-150 MB (three
int arrays indexed by medium id, SQLite page caches, batches); the scratch
file is a few GB and the build needs roughly final size + scratch size free
(see estimates in the settings dialog's text).

THE UNVERIFIED PART: no real dump has been read. The column order (from
CreateTables.sql, in core/musicbrainz_schema.py) and SCHEMA_SEQUENCE_EXPECTED
are checked against documentation only. Everything the recipe assumes about
the dump is in ONE place -- SPEC, SMALL, COLUMN_OVERRIDES and
ACCEPTED_SCHEMA_SEQUENCES below -- so a real 2 MB sample can adjust it fast.
A wrong schema number or column count fails loudly at the start (an
actionable message), never as a silently empty database.
"""

from __future__ import annotations

import datetime
import json
import os
import sqlite3
import uuid
from collections import Counter
from contextlib import closing
from dataclasses import dataclass, field
from functools import lru_cache
from array import array
from typing import Callable, Optional

from redactor_common.core import musicbrainz_schema as mb
from redactor_common.core.dump_import import (
    INFO_TABLE,
    DumpImportError,
    ImportCancelled,
    ReadStats,
    SqliteBuilder,
    check_schema_sequence,
    iter_pgcopy_records,
    iter_tar_members,
    read_archive_info,
)
from redactor_common.core.local_db import LocalDatabase, LocalDatabaseError

SOURCE_NAME = "MusicBrainz"
RECIPE = "musicbrainz-core/1"

# --- everything the recipe assumes about the dump (adjust HERE) ---------------

# Schema sequences this recipe's column lists are valid for. Unverified
# against a real dump: 31 is DB_SCHEMA_SEQUENCE in musicbrainz-server master
# on 2026-10-01. Add the number a real dump reports once its columns are checked.
ACCEPTED_SCHEMA_SEQUENCES: tuple[int, ...] = (mb.SCHEMA_SEQUENCE_EXPECTED,)

# table -> full column order of its dump file, when a real dump differs from
# redactor_common's list (CreateTables.sql order). Empty = trust that list.
COLUMN_OVERRIDES: dict[str, tuple[str, ...]] = {}

# Tables copied into the scratch file: dump table -> (scratch table, primary-key
# first column?, [(source column, kind)]). Kinds: i = integer (NULL stays NULL),
# t = text (NULL -> ""), b = UUID as 16 bytes (NULL if unparseable), n = always NULL.
SPEC: dict[str, tuple[str, bool, list[tuple[str, str]]]] = {
    "artist": ("artist", True, [("id", "i"), ("sort_name", "t")]),
    "artist_credit_name": ("acn", False, [
        ("artist_credit", "i"), ("position", "i"), ("artist", "i"), ("name", "t"), ("join_phrase", "t")]),
    "release_group": ("rg", True, [("id", "i"), ("gid", "t"), ("type", "i")]),
    "release_group_secondary_type_join": ("rgs", False, [("release_group", "i"), ("secondary_type", "i")]),
    "release": ("rel", True, [
        ("id", "i"), ("gid", "t"), ("name", "t"), ("artist_credit", "i"), ("release_group", "i"),
        ("status", "i"), ("language", "i"), ("script", "i"), ("barcode", "t")]),
    "release_country": ("rdate", False, [
        ("release", "i"), ("country", "i"), ("date_year", "i"), ("date_month", "i"), ("date_day", "i")]),
    "release_unknown_country": ("rdate", False, [
        ("release", "i"), (None, "n"), ("date_year", "i"), ("date_month", "i"), ("date_day", "i")]),
    "release_label": ("rl", False, [("id", "i"), ("release", "i"), ("label", "i"), ("catalog_number", "t")]),
    "label": ("label", True, [("id", "i"), ("name", "t")]),
    "medium": ("med", True, [("id", "i"), ("release", "i"), ("position", "i"), ("format", "i"), ("track_count", "i")]),
    "recording": ("rec", True, [("id", "i"), ("gid", "b")]),
}
SCRATCH_COLUMNS = {
    "artist": ["id", "sort_name"], "acn": ["ac", "pos", "artist", "name", "jp"],
    "rg": ["id", "gid", "type"], "rgs": ["rg", "type"],
    "rel": ["id", "gid", "name", "ac", "rg", "status", "lang", "script", "barcode"],
    "rdate": ["release", "area", "y", "m", "d"], "rl": ["id", "release", "label", "catno"],
    "label": ["id", "name"], "med": ["id", "release", "pos", "format", "tc"], "rec": ["id", "gid"],
}
_SCRATCH_KIND = {"id": "integer", "gid": "text", "name": "text", "sort_name": "text", "jp": "text",
                 "barcode": "text", "catno": "text"}

# Tiny reference tables held in dicts: dump table -> (key column, value column(s)).
SMALL: dict[str, tuple[str, tuple[str, ...]]] = {
    "iso_3166_1": ("area", ("code",)),
    "language": ("id", ("iso_code_3", "iso_code_2t")),
    "script": ("id", ("iso_code",)),
    "release_status": ("id", ("name",)),
    "release_group_primary_type": ("id", ("name",)),
    "release_group_secondary_type": ("id", ("name",)),
    "medium_format": ("id", ("name",)),
}

TRACK_TABLE = "track"
TRACK_COLUMNS = ("recording", "medium", "position", "number", "name", "artist_credit", "length", "is_data_track")

# What the releases need (everything but the track table and its recording ids).
RELEASE_TABLES = frozenset(SMALL) | frozenset(t for t in SPEC if t != "recording")
TRACK_TABLES = frozenset({"recording", TRACK_TABLE})

# --- build options ---------------------------------------------------------------------

PRIMARY_TYPES = ("Album", "EP", "Single", "Broadcast", "Other")  # "Other" also covers releases with no type
SECONDARY_TYPES = (
    "Compilation", "Soundtrack", "Live", "Remix", "DJ-mix", "Mixtape/Street", "Demo", "Spokenword",
    "Audiobook", "Audio drama", "Interview", "Field recording",
)
TYPE_CHOICES = PRIMARY_TYPES + SECONDARY_TYPES
DEFAULT_TYPES = ("Album", "EP", "Single", "Compilation", "Soundtrack", "Live")


@dataclass
class BuildOptions:
    official_only: bool = True
    types: tuple[str, ...] = DEFAULT_TYPES
    skip_undated: bool = False
    include_tracks: bool = True
    track_index: bool = False  # FTS over track titles + artists (a single-track search); big

    def describe(self) -> str:
        bits = ["official releases only" if self.official_only else "all release statuses",
                "types: " + (", ".join(self.types) or "none")]
        if self.skip_undated:
            bits.append("undated releases skipped")
        bits.append(("track titles" + (" + track search index" if self.track_index else ""))
                    if self.include_tracks else "no track titles")
        return "; ".join(bits)

    def to_json(self) -> str:
        return json.dumps({
            "official_only": self.official_only, "types": list(self.types), "skip_undated": self.skip_undated,
            "include_tracks": self.include_tracks, "track_index": self.track_index,
        }, separators=(",", ":"))

    @classmethod
    def from_json(cls, text: str) -> "BuildOptions":
        """Saved options; anything missing or unreadable is the default."""
        try:
            data = json.loads(text) if text else {}
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        defaults = cls()

        def flag(key: str) -> bool:
            value = data.get(key)
            return value if isinstance(value, bool) else getattr(defaults, key)

        types = data.get("types")
        chosen = tuple(t for t in types if t in TYPE_CHOICES) if isinstance(types, list) else defaults.types
        return cls(flag("official_only"), chosen, flag("skip_undated"), flag("include_tracks"), flag("track_index"))


@dataclass
class ImportSummary:
    releases_seen: int = 0
    releases: int = 0
    skipped_status: int = 0
    skipped_type: int = 0
    skipped_undated: int = 0
    tracks_seen: int = 0
    tracks: int = 0
    bad_lines: int = 0
    timestamp: str = ""
    rows: dict = field(default_factory=dict)
    sizes: dict = field(default_factory=dict)

    def describe(self) -> str:
        text = (f"{self.releases:,} releases kept out of {self.releases_seen:,} read "
                f"({self.skipped_status:,} not official, {self.skipped_type:,} of other types"
                f"{f', {self.skipped_undated:,} undated' if self.skipped_undated else ''}); "
                f"{self.tracks:,} tracks.")
        size = self.sizes.get("(file)")
        if size:
            text += f" Database size {size / (1 << 20):,.0f} MB."
        if self.bad_lines:
            text += f" {self.bad_lines:,} unreadable lines were skipped."
        return text


# --- helpers ---------------------------------------------------------------------------

_BATCH = 20000
_POLL = 20000


def dump_columns(table: str) -> tuple[str, ...]:
    return COLUMN_OVERRIDES.get(table) or mb.table_columns(table)


def _int(value) -> Optional[int]:
    try:
        return int(value) if value is not None and value != "" else None
    except ValueError:
        return None


def _blob(value) -> Optional[bytes]:
    try:
        return uuid.UUID(value).bytes if value else None
    except ValueError:
        return None


def _converter(kind: str) -> Callable:
    return {"i": _int, "t": lambda v: v if v is not None else "", "b": _blob, "n": lambda v: None}[kind]


def _scaled(progress, start: float, span: float):
    return (lambda fraction: progress(start + span * min(max(fraction, 0.0), 1.0))) if progress else None


def _remove_scratch(path: str) -> None:
    for leftover in (path, path + "-journal"):
        try:
            os.remove(leftover)
        except OSError:
            pass


def schema_help(error: DumpImportError) -> DumpImportError:
    """The schema-sequence error with what to do about it."""
    return DumpImportError(
        f"{error} This app was written for MusicBrainz schema "
        f"{' or '.join(str(n) for n in ACCEPTED_SCHEMA_SEQUENCES)}: use the dump of that era "
        "(data.metabrainz.org/pub/musicbrainz/data/fullexport/ keeps older ones), or have the app updated "
        "(its column lists live in core/musicbrainz_import.py and redactor_common's core/musicbrainz_schema.py)."
    )


def check_archive(source: str, cancelled=None) -> tuple[Optional[str], Optional[int], Optional[int]]:
    """Reads the archive's bookkeeping files and checks the schema number.
    Returns (timestamp, schema sequence, replication sequence)."""
    info = read_archive_info(source, cancelled=cancelled)
    try:
        check_schema_sequence(info, ACCEPTED_SCHEMA_SEQUENCES)
    except DumpImportError as exc:
        raise schema_help(exc) from None
    return info.timestamp, info.schema_sequence, info.replication_sequence


TABLES = {
    "releases": [
        "id integer primary key", "gid text", "title text", "artist_credit_text text", "artist_sort text",
        "artist_credit_id integer", "release_group_gid text", "rg_type text", "status text", "language text",
        "script text", "barcode text", "year integer", "month integer", "day integer", "country text",
        "label text", "catalog_number text", "format text", "medium_count integer", "track_count integer",
    ],
    "tracks": [
        "release_id integer", "medium integer", "position integer", "number text", "title text",
        "artist_credit_text text", "recording_gid blob", "length_ms integer",
    ],
}
INDEXES = [
    "create index rel_gid on releases(gid)",
    "create index rel_barcode on releases(barcode)",
    "create index trk_release on tracks(release_id)",
    "create index trk_recording on tracks(recording_gid)",
]


class _Build:
    """One build: the scratch file, the reference dicts and the passes."""

    def __init__(self, source, dest, options, progress, cancelled, scratch_path):
        self.source, self.dest, self.options = source, dest, options
        self.progress, self.cancelled = progress, cancelled
        self.summary = ImportSummary()
        self.scratch = sqlite3.connect(scratch_path)
        for pragma in ("journal_mode = OFF", "synchronous = OFF", "cache_size = -131072"):
            self.scratch.execute(f"pragma {pragma}")
        self.small: dict[str, dict] = {name: {} for name in SMALL}
        self.seen: set[str] = set()
        self.bad = 0
        self.out: Optional[SqliteBuilder] = None
        self.releases_done = False
        self.track_arrays: Optional[tuple[array, array, array]] = None
        self.deferred = 0
        self.always_artist = options.track_index
        self._counter = 0
        for table, columns in SCRATCH_COLUMNS.items():
            defs = [f"{c} {_SCRATCH_KIND.get(c, 'integer')}" for c in columns]
            if table in ("artist", "rg", "rel", "label", "med", "rec"):
                defs[0] += " primary key"
            self.scratch.execute(f"create table {table} ({', '.join(defs)})")
        self.scratch.execute("create table raw_track (rec integer, medium integer, pos integer, number text, "
                             "name text, ac integer, length integer, data text)")

    def check_cancel(self, step: int = 1) -> None:
        self._counter += step
        if self._counter >= _POLL:
            self._counter = 0
            if self.cancelled and self.cancelled():
                raise ImportCancelled()

    # -- pass over the archive ---------------------------------------------------------

    def wanted_tables(self) -> list[str]:
        tables = set(RELEASE_TABLES)
        if self.options.include_tracks:
            tables |= TRACK_TABLES
        return sorted(tables)

    def run_pass(self, span: float) -> None:
        wanted = self.wanted_tables()
        members = iter_tar_members(
            self.source, [mb.table_member(t) for t in wanted],
            progress=_scaled(self.progress, 0.0, span), cancelled=self.cancelled,
        )
        with closing(members) as tar:
            for name, member in tar:
                table = name.rsplit("/", 1)[-1]
                if table not in wanted or table in self.seen:
                    continue
                try:
                    if table in SMALL:
                        self.load_small(table, member)
                    elif table == TRACK_TABLE:
                        self.load_tracks(member)
                    else:
                        self.load_scratch(table, member)
                except DumpImportError as exc:
                    raise DumpImportError(f"{mb.table_member(table)}: {exc}") from None
                self.seen.add(table)
        missing = [t for t in wanted if t not in self.seen]
        if missing:
            raise DumpImportError(
                f"The archive has no {', '.join(mb.table_member(t) for t in missing)}. Is this the MusicBrainz "
                f"core dump ({mb.CORE_ARCHIVE})? The derived archive and the other dumps don't contain these tables."
            )

    def _records(self, table: str, member):
        stats = ReadStats()
        try:
            yield from iter_pgcopy_records(
                member, list(dump_columns(table)), exact=True, cancelled=self.cancelled, stats=stats)
        finally:
            self.bad += stats.bad

    def load_small(self, table: str, member) -> None:
        key, values = SMALL[table]
        target = self.small[table]
        for rec in self._records(table, member):
            k = _int(rec[key])
            if k is None:
                continue
            value = next((rec[c] for c in values if rec.get(c)), "")
            target[k] = value or ""

    def load_scratch(self, table: str, member) -> None:
        scratch, is_pk, columns = SPEC[table]
        converters = [(src, _converter(kind)) for src, kind in columns]
        marks = ",".join("?" * len(columns))
        verb = "insert or replace" if is_pk else "insert"
        sql = f"{verb} into {scratch} values ({marks})"
        batch: list[tuple] = []
        for rec in self._records(table, member):
            batch.append(tuple(conv(rec[src] if src else None) for src, conv in converters))
            if len(batch) >= _BATCH:
                self.scratch.executemany(sql, batch)
                batch.clear()
        if batch:
            self.scratch.executemany(sql, batch)
        self.scratch.commit()

    def load_tracks(self, member) -> None:
        """The big table: straight into the database when everything it joins
        to has been read, otherwise parked in the scratch file."""
        self.track_batch: list[tuple] = []
        direct = None
        for rec in self._records(TRACK_TABLE, member):
            if direct is None:
                direct = (RELEASE_TABLES | {"recording"}) <= self.seen
                if direct:
                    self.finalize_releases()
            row = (_int(rec["recording"]), _int(rec["medium"]), _int(rec["position"]), rec["number"] or "",
                   rec["name"] or "", _int(rec["artist_credit"]), _int(rec["length"]), rec["is_data_track"])
            if direct:
                self.put_track(row)
            else:
                self.deferred += 1
                self.track_batch.append(row)
                if len(self.track_batch) >= _BATCH:
                    self.scratch.executemany("insert into raw_track values (?,?,?,?,?,?,?,?)", self.track_batch)
                    self.track_batch.clear()
        if self.track_batch:
            self.scratch.executemany("insert into raw_track values (?,?,?,?,?,?,?,?)", self.track_batch)
            self.track_batch.clear()
        self.scratch.commit()

    # -- joins in SQL, once everything is read ------------------------------------------

    def finalize_releases(self) -> None:
        if self.releases_done:
            return
        con, out, options, summary = self.scratch, self.out, self.options, self.summary
        self.check_cancel(_POLL)

        # Artist credits: names + join phrases, first artist's sort name.
        con.execute("create table credit (id integer primary key, text text, sort text)")
        rows, current, parts, sort = [], None, [], ""
        query = ("select a.ac, a.name, a.jp, s.sort_name from acn a left join artist s on s.id = a.artist "
                 "order by a.ac, a.pos")
        for ac, name, jp, sort_name in con.execute(query):
            if ac != current:
                if current is not None:
                    rows.append((current, "".join(parts), sort))
                current, parts, sort = ac, [], sort_name or ""
            parts.append((name or "") + (jp or ""))
            self.check_cancel()
            if len(rows) >= _BATCH:
                con.executemany("insert into credit values (?,?,?)", rows)
                rows.clear()
        if current is not None:
            rows.append((current, "".join(parts), sort))
        con.executemany("insert into credit values (?,?,?)", rows)

        self.check_cancel(_POLL)
        # Which releases are kept: status, then the release group's types.
        status_names = {k: v.casefold() for k, v in self.small["release_status"].items()}
        primary = {k: v.casefold() for k, v in self.small["release_group_primary_type"].items()}
        chosen = {t.casefold() for t in options.types}
        primary_ok = {k for k, v in primary.items() if v in chosen}
        other_ok = "other" in chosen  # untyped release groups count as Other
        secondary_bad = [k for k, v in self.small["release_group_secondary_type"].items() if v.casefold() not in chosen]
        con.execute("create table rg_bad (rg integer primary key)")
        if secondary_bad:
            marks = ",".join("?" * len(secondary_bad))
            con.execute(f"insert or ignore into rg_bad select rg from rgs where type in ({marks})", secondary_bad)
        con.execute("create table keep (id integer primary key)")
        batch = []
        query = ("select r.id, r.status, g.type, b.rg from rel r left join rg g on g.id = r.rg "
                 "left join rg_bad b on b.rg = r.rg")
        for rid, status, rgtype, bad in con.execute(query):
            summary.releases_seen += 1
            self.check_cancel()
            if options.official_only and status_names.get(status) != "official":
                summary.skipped_status += 1
                continue
            if bad is not None or not (rgtype in primary_ok if rgtype is not None else other_ok):
                summary.skipped_type += 1
                continue
            batch.append((rid,))
            if len(batch) >= _BATCH:
                con.executemany("insert into keep values (?)", batch)
                batch.clear()
        con.executemany("insert into keep values (?)", batch)

        self.check_cancel(_POLL)
        # Earliest release event per kept release.
        iso = self.small["iso_3166_1"]
        con.execute("create table r_date (release integer primary key, y integer, m integer, d integer, country text)")
        batch, last = [], None
        query = ("select d.release, d.area, d.y, d.m, d.d from rdate d join keep k on k.id = d.release "
                 "order by d.release, coalesce(d.y, 9999), coalesce(d.m, 13), coalesce(d.d, 32), d.area is null")
        for release, area, y, m, d in con.execute(query):
            if release == last:
                continue
            last = release
            batch.append((release, y, m, d, iso.get(area, "") if area is not None else ""))
            self.check_cancel()
            if len(batch) >= _BATCH:
                con.executemany("insert into r_date values (?,?,?,?,?)", batch)
                batch.clear()
        con.executemany("insert into r_date values (?,?,?,?,?)", batch)
        if options.skip_undated:
            before = con.execute("select count(*) from keep").fetchone()[0]
            con.execute("delete from keep where id not in (select release from r_date where y is not null)")
            summary.skipped_undated = before - con.execute("select count(*) from keep").fetchone()[0]

        self.check_cancel(_POLL)
        # First label / catalogue number per kept release.
        con.execute("create table r_label (release integer primary key, label text, catno text)")
        batch, last, label, catno = [], None, "", ""
        query = ("select l.release, b.name, l.catno from rl l join keep k on k.id = l.release "
                 "left join label b on b.id = l.label order by l.release, l.id")
        for release, name, number in con.execute(query):
            if release != last:
                if last is not None:
                    batch.append((last, label, catno))
                last, label, catno = release, name or "", number or ""
            elif not catno and number:
                catno = number
            self.check_cancel()
            if len(batch) >= _BATCH:
                con.executemany("insert into r_label values (?,?,?)", batch)
                batch.clear()
        if last is not None:
            batch.append((last, label, catno))
        con.executemany("insert into r_label values (?,?,?)", batch)

        self.check_cancel(_POLL)
        # Mediums per kept release: count, tracks, format summary.
        formats = self.small["medium_format"]
        con.execute("create table r_med (release integer primary key, n integer, tracks integer, fmt text)")
        batch, last, names, tracks, count = [], None, Counter(), 0, 0

        def summary_of(counts: Counter) -> str:
            return " + ".join(name if n == 1 else f"{n}×{name}" for name, n in counts.items())

        query = ("select m.release, m.format, m.tc from med m join keep k on k.id = m.release "
                 "order by m.release, m.pos")
        for release, fmt, tc in con.execute(query):
            if release != last:
                if last is not None:
                    batch.append((last, count, tracks, summary_of(names)))
                last, names, tracks, count = release, Counter(), 0, 0
            count += 1
            tracks += tc or 0
            if fmt is not None and formats.get(fmt):
                names[formats[fmt]] += 1
            self.check_cancel()
            if len(batch) >= _BATCH:
                con.executemany("insert into r_med values (?,?,?,?)", batch)
                batch.clear()
        if last is not None:
            batch.append((last, count, tracks, summary_of(names)))
        con.executemany("insert into r_med values (?,?,?,?)", batch)
        con.commit()

        self.check_cancel(_POLL)
        # The releases table.
        languages, scripts = self.small["language"], self.small["script"]
        rg_names = {k: v for k, v in self.small["release_group_primary_type"].items()}
        query = (
            "select r.id, r.gid, r.name, c.text, c.sort, r.ac, g.gid, g.type, r.status, r.lang, r.script, r.barcode, "
            "d.y, d.m, d.d, d.country, l.label, l.catno, m.fmt, m.n, m.tracks "
            "from keep k join rel r on r.id = k.id left join credit c on c.id = r.ac left join rg g on g.id = r.rg "
            "left join r_date d on d.release = r.id left join r_label l on l.release = r.id "
            "left join r_med m on m.release = r.id order by r.id"
        )
        for row in con.execute(query):
            (rid, gid, name, text, sort, ac, rggid, rgtype, status, lang, script, barcode, y, mo, day, country,
             label, catno, fmt, n, tracks) = row
            out.add("releases", (
                rid, gid, name, text or "", sort or "", ac, rggid or "", rg_names.get(rgtype, "") if rgtype else "",
                self.small["release_status"].get(status, ""), languages.get(lang, ""), scripts.get(script, ""),
                barcode or "", y, mo, day, country or "", label or "", catno or "", fmt or "", n or 0, tracks or 0,
            ))
            summary.releases += 1
            self.check_cancel()
        self.releases_done = True

        if options.include_tracks:
            # medium id -> (release, disc number, release's artist credit), as flat int arrays.
            top = con.execute("select max(m.id) from med m join keep k on k.id = m.release").fetchone()[0] or 0
            m_rel, m_pos, m_ac = (array("i", bytes(4 * (top + 1))) for _ in range(3))
            for mid, release, pos, ac in con.execute(
                "select m.id, m.release, m.pos, r.ac from med m join keep k on k.id = m.release "
                "join rel r on r.id = m.release"
            ):
                m_rel[mid], m_pos[mid], m_ac[mid] = release, pos or 0, ac or 0
                self.check_cancel()
            self.track_arrays = (m_rel, m_pos, m_ac)

    @lru_cache(maxsize=200_000)
    def credit_text(self, ac) -> Optional[str]:
        row = self.scratch.execute("select text from credit where id = ?", (ac,)).fetchone()
        return row[0] if row else None

    def put_track(self, row: tuple) -> None:
        recording, medium, position, number, name, ac, length, is_data = row
        self.summary.tracks_seen += 1
        self.check_cancel()
        m_rel, m_pos, m_ac = self.track_arrays
        if medium is None or medium >= len(m_rel) or not m_rel[medium] or is_data == "t":
            return
        gid = None
        if recording is not None:
            found = self.scratch.execute("select gid from rec where id = ?", (recording,)).fetchone()
            gid = found[0] if found else None
        if self.always_artist:
            text = self.credit_text(ac or m_ac[medium])
        else:
            text = None if (ac == m_ac[medium] or not ac) else self.credit_text(ac)
        self.out.add("tracks", (m_rel[medium], m_pos[medium], position, number, name, text, gid, length))
        self.summary.tracks += 1

    def write_deferred_tracks(self) -> None:
        if not self.deferred:
            return
        for row in self.scratch.execute("select * from raw_track"):
            self.put_track(row)

    def close(self) -> None:
        self.scratch.close()


def build_musicbrainz_database(
    source: str,
    dest: str,
    options: Optional[BuildOptions] = None,
    progress: Optional[Callable[[float], None]] = None,
    cancelled: Optional[Callable[[], bool]] = None,
) -> ImportSummary:
    """Reads the core dump `source` (.tar.bz2 / .xz / .gz / .tar) and writes the
    lookup database to `dest`, replacing a previous build only once this one
    has succeeded. Raises DumpImportError for anything wrong with the dump
    (not an archive, wrong schema number, wrong columns, missing tables)."""
    options = options or BuildOptions()
    if not source or not os.path.isfile(source):
        raise DumpImportError(f"MusicBrainz dump not found: {source or '(not set)'}")
    if not options.types:
        raise DumpImportError("No release types chosen -- tick at least one.")
    timestamp, schema, replication = check_archive(source, cancelled)
    scratch_path = dest + ".scratch.tmp"
    _remove_scratch(scratch_path)
    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    build = _Build(source, dest, options, progress, cancelled, scratch_path)
    try:
        with SqliteBuilder(dest, TABLES, INDEXES) as out:
            build.out = out
            build.run_pass(0.80)
            if progress:
                progress(0.80)
            build.finalize_releases()
            if progress:
                progress(0.86)
            if options.include_tracks:
                build.write_deferred_tracks()
            summary = build.summary
            summary.bad_lines = build.bad
            summary.timestamp = timestamp or ""
            if summary.releases == 0:
                raise DumpImportError(
                    f"No releases were kept -- none of the {summary.releases_seen:,} releases matches the options "
                    f"({options.describe()})."
                )
            out.create_fts_index("releases", ["title", "artist_credit_text"], progress=_scaled(progress, 0.90, 0.05),
                                 cancelled=cancelled)
            if options.include_tracks and options.track_index:
                out.create_fts_index("tracks", ["title", "artist_credit_text"], progress=_scaled(progress, 0.95, 0.04),
                                     cancelled=cancelled)
            summary.rows = out.finish({
                "source": SOURCE_NAME, "recipe": RECIPE, "dump_timestamp": timestamp or "",
                "schema_sequence": schema, "replication_sequence": replication if replication is not None else "",
                "dump_file": os.path.basename(source), "options": options.to_json(),
                "options_text": options.describe(), "releases_seen": summary.releases_seen,
                "skipped_status": summary.skipped_status, "skipped_type": summary.skipped_type,
                "skipped_undated": summary.skipped_undated, "tracks_seen": summary.tracks_seen,
                "bad_lines": summary.bad_lines,
            })
            summary.sizes = dict(out.sizes)
        _record_sizes(dest, summary.sizes)
    finally:
        build.close()
        _remove_scratch(scratch_path)
    if progress:
        progress(1.0)
    return summary


def _record_sizes(dest: str, sizes: dict) -> None:
    """Adds the measured sizes (known only once the file is in place) to the
    info table; a failure here is harmless."""
    try:
        con = sqlite3.connect(dest)
        try:
            con.executemany(
                f"insert or replace into {INFO_TABLE} values (?, ?)",
                [("size." + name.strip("()"), str(size)) for name, size in sizes.items()],
            )
            con.commit()
        finally:
            con.close()
    except sqlite3.DatabaseError:
        pass


class MusicBrainzDatabaseError(LocalDatabaseError):
    """The local MusicBrainz database is missing or not one this app built."""


def database_info(path: str) -> dict[str, str]:
    """The import-info rows of a database built here. Raises
    MusicBrainzDatabaseError for a missing file, a non-SQLite file, or a
    database that isn't a MusicBrainz lookup database built by this app."""
    db = LocalDatabase(path, ("releases", "tracks", INFO_TABLE), "a MusicBrainz lookup database built by this app",
                       MusicBrainzDatabaseError)
    try:
        info = dict(db.query(f"select key, value from {INFO_TABLE}"))
    finally:
        db.close()
    if not info.get("recipe", "").startswith("musicbrainz-core/"):
        raise MusicBrainzDatabaseError("This database wasn't built from the MusicBrainz dump by this app.")
    return info


def describe_database(path: str) -> str:
    """A status text for the settings dialog: what it was built from, when, how big."""
    info = database_info(path)
    stamp = (info.get("dump_timestamp") or "")[:10]
    text = (f"Built from {info.get('dump_file') or 'a MusicBrainz dump'}"
            f"{f' (data of {stamp})' if stamp else ''} on {info.get('built', '')[:10] or 'an unknown date'}: "
            f"{int(info.get('rows.releases', '0') or 0):,} releases, {int(info.get('rows.tracks', '0') or 0):,} tracks")
    if info.get("options_text"):
        text += f"; {info['options_text']}"
    size = info.get("size.file")
    if size and size.isdigit():
        text += f". {int(size) / (1 << 20):,.0f} MB."
    return text
