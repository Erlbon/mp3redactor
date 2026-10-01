"""A SMALL SYNTHETIC MusicBrainz core dump for the tests: a tar.bz2 (or .xz/.gz/plain)
with TIMESTAMP / SCHEMA_SEQUENCE / REPLICATION_SEQUENCE and `mbdump/<table>` members
in PostgreSQL COPY text format, columns in the verified order of
redactor_common's core/musicbrainz_schema.py. No real MusicBrainz data is used
(the real core archive is ~7 GB and is never downloaded by tests)."""

import io
import tarfile
import time

from redactor_common.core import musicbrainz_schema as mb

# Fixed ids -- the tests refer to these.
GID = {
    "abbey_cd": "11111111-1111-4111-8111-111111111111",
    "abbey_lp": "22222222-2222-4222-8222-222222222222",
    "bootleg": "33333333-3333-4333-8333-333333333333",
    "hey_jude": "44444444-4444-4444-8444-444444444444",
    "hits": "55555555-5555-4555-8555-555555555555",
    "broadcast": "66666666-6666-4666-8666-666666666666",
    "live": "77777777-7777-4777-8777-777777777777",
    "interview": "88888888-8888-4888-8888-888888888888",
    "undated": "99999999-9999-4999-8999-999999999999",
    "untyped": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    "rec_come_together": "c0000001-0000-4000-8000-000000000001",
    "rec_something": "c0000002-0000-4000-8000-000000000002",
    "rec_maxwell": "c0000003-0000-4000-8000-000000000003",
    "rec_hey_jude": "c0000004-0000-4000-8000-000000000004",
    "rec_live_and_let_die": "c0000005-0000-4000-8000-000000000005",
    "rec_jet": "c0000006-0000-4000-8000-000000000006",
    "rec_undated": "c0000007-0000-4000-8000-000000000007",
    "rg_abbey": "d0000001-0000-4000-8000-000000000001",
}


def row(table: str, **values):
    """A dict with every column of `table` (None where not given)."""
    columns = mb.table_columns(table)
    unknown = set(values) - set(columns)
    assert not unknown, f"{table}: not columns: {unknown}"
    return {c: values.get(c) for c in columns}


def default_tables() -> dict[str, list[dict]]:
    t: dict[str, list[dict]] = {}
    t["iso_3166_1"] = [row("iso_3166_1", area=221, code="GB"), row("iso_3166_1", area=222, code="US"),
                       row("iso_3166_1", area=240, code="XW")]
    t["language"] = [row("language", id=120, iso_code_3="eng", iso_code_2t="eng", name="English")]
    t["script"] = [row("script", id=28, iso_code="Latn", name="Latin")]
    t["release_status"] = [row("release_status", id=1, name="Official"), row("release_status", id=3, name="Bootleg")]
    t["release_group_primary_type"] = [
        row("release_group_primary_type", id=1, name="Album"), row("release_group_primary_type", id=2, name="Single"),
        row("release_group_primary_type", id=3, name="EP"), row("release_group_primary_type", id=12, name="Broadcast"),
        row("release_group_primary_type", id=11, name="Other"),
    ]
    t["release_group_secondary_type"] = [
        row("release_group_secondary_type", id=1, name="Compilation"), row("release_group_secondary_type", id=6, name="Live"),
        row("release_group_secondary_type", id=5, name="Interview"),
    ]
    t["medium_format"] = [row("medium_format", id=1, name="CD"), row("medium_format", id=7, name="Vinyl"),
                          row("medium_format", id=2, name="DVD")]
    t["artist"] = [
        row("artist", id=1, gid="a0000001-0000-4000-8000-000000000001", name="The Beatles", sort_name="Beatles, The"),
        row("artist", id=2, gid="a0000002-0000-4000-8000-000000000002", name="Paul McCartney", sort_name="McCartney, Paul"),
        row("artist", id=3, gid="a0000003-0000-4000-8000-000000000003", name="Wings", sort_name="Wings"),
        row("artist", id=4, gid="a0000004-0000-4000-8000-000000000004", name="Various Artists", sort_name="Various Artists"),
    ]
    t["artist_credit_name"] = [
        # written out of order on purpose: the build must sort by position
        row("artist_credit_name", artist_credit=2, position=1, artist=3, name="Wings", join_phrase=""),
        row("artist_credit_name", artist_credit=1, position=0, artist=1, name="The Beatles", join_phrase=""),
        row("artist_credit_name", artist_credit=2, position=0, artist=2, name="Paul McCartney", join_phrase=" & "),
        row("artist_credit_name", artist_credit=3, position=0, artist=4, name="Various Artists", join_phrase=""),
    ]
    t["release_group"] = [
        row("release_group", id=1, gid=GID["rg_abbey"], name="Abbey Road", artist_credit=1, type=1),
        row("release_group", id=2, gid="d0000002-0000-4000-8000-000000000002", name="Hey Jude", artist_credit=1, type=2),
        row("release_group", id=3, gid="d0000003-0000-4000-8000-000000000003", name="Greatest Hits", artist_credit=3, type=1),
        row("release_group", id=4, gid="d0000004-0000-4000-8000-000000000004", name="On Air", artist_credit=1, type=12),
        row("release_group", id=5, gid="d0000005-0000-4000-8000-000000000005", name="Wings Live", artist_credit=2, type=1),
        row("release_group", id=6, gid="d0000006-0000-4000-8000-000000000006", name="Talk", artist_credit=1, type=1),
        row("release_group", id=7, gid="d0000007-0000-4000-8000-000000000007", name="Mystery", artist_credit=2, type=None),
    ]
    t["release_group_secondary_type_join"] = [
        row("release_group_secondary_type_join", release_group=3, secondary_type=1),
        row("release_group_secondary_type_join", release_group=5, secondary_type=6),
        row("release_group_secondary_type_join", release_group=6, secondary_type=5),
    ]

    def rel(id_, key, name, ac, rg, status=1, barcode=None):
        return row("release", id=id_, gid=GID[key], name=name, artist_credit=ac, release_group=rg,
                   status=status, language=120, script=28, barcode=barcode)

    t["release"] = [
        rel(1, "abbey_cd", "Abbey Road", 1, 1, barcode="0077774644624"),
        rel(2, "abbey_lp", "Abbey Road", 1, 1),
        rel(3, "bootleg", "Abbey Road (bootleg)", 1, 1, status=3),
        rel(4, "hey_jude", "Hey Jude", 1, 2),
        rel(5, "hits", "Greatest Hits", 3, 3),
        rel(6, "broadcast", "On Air", 1, 4),
        rel(7, "live", "Wings Live", 2, 5),
        rel(8, "interview", "Talk", 1, 6),
        rel(9, "undated", "No Date Album", 1, 1),
        rel(10, "untyped", "Mystery", 2, 7),
    ]
    t["release_country"] = [
        row("release_country", release=1, country=221, date_year=1987, date_month=10, date_day=19),
        row("release_country", release=2, country=222, date_year=1969, date_month=10, date_day=1),
        row("release_country", release=2, country=221, date_year=1969, date_month=9, date_day=26),
        row("release_country", release=4, country=221, date_year=1968, date_month=8, date_day=26),
        row("release_country", release=5, country=240, date_year=2001),
        row("release_country", release=6, country=221, date_year=1990),
        row("release_country", release=7, country=222, date_year=1976, date_month=12),
        row("release_country", release=8, country=221, date_year=1980),
        row("release_country", release=10, country=221, date_year=1970),
    ]
    t["release_unknown_country"] = [
        row("release_unknown_country", release=1, date_year=1969, date_month=9, date_day=26),
    ]
    t["label"] = [row("label", id=1, gid="b0000001-0000-4000-8000-000000000001", name="Apple Records"),
                  row("label", id=2, gid="b0000002-0000-4000-8000-000000000002", name="EMI")]
    t["release_label"] = [
        row("release_label", id=1, release=1, label=1, catalog_number="CDP 7 46446 2"),
        row("release_label", id=2, release=1, label=2, catalog_number="EMI 123"),
        row("release_label", id=3, release=2, label=2, catalog_number=None),
        row("release_label", id=4, release=2, label=2, catalog_number="PCS 7088"),
    ]
    t["medium"] = [
        row("medium", id=1, release=1, position=1, format=1, track_count=4),
        row("medium", id=2, release=2, position=1, format=7, track_count=3),
        row("medium", id=3, release=2, position=2, format=7, track_count=1),
        row("medium", id=4, release=5, position=1, format=1, track_count=2),
        row("medium", id=5, release=3, position=1, format=1, track_count=1),
        row("medium", id=6, release=4, position=1, format=1, track_count=1),
        row("medium", id=7, release=7, position=1, format=1, track_count=1),
        row("medium", id=8, release=8, position=1, format=2, track_count=1),
    ]
    t["recording"] = [
        row("recording", id=1, gid=GID["rec_come_together"], name="Come Together"),
        row("recording", id=2, gid=GID["rec_something"], name="Something"),
        row("recording", id=3, gid=GID["rec_maxwell"], name="Maxwell's Silver Hammer"),
        row("recording", id=4, gid=GID["rec_hey_jude"], name="Hey Jude"),
        row("recording", id=5, gid=GID["rec_live_and_let_die"], name="Live and Let Die"),
        row("recording", id=6, gid=GID["rec_jet"], name="Jet"),
    ]

    def trk(id_, recording, medium, position, number, name, ac, length, data="f"):
        return row("track", id=id_, recording=recording, medium=medium, position=position, number=number,
                   name=name, artist_credit=ac, length=length, is_data_track=data)

    t["track"] = [
        trk(1, 1, 1, 1, "1", "Come Together", 1, 259000),
        trk(2, 2, 1, 2, "2", "Something", 1, 182000),
        trk(3, 3, 1, 3, "3", "Maxwell's Silver Hammer", 1, 207000),
        trk(4, 3, 1, 4, "4", "Hidden Data", 1, None, "t"),
        trk(5, 1, 2, 1, "A1", "Come Together", 1, 259000),
        trk(6, 2, 2, 2, "A2", "Something", 1, 182000),
        trk(7, 3, 2, 3, "B1", "Maxwell's Silver Hammer", 1, 207000),
        trk(8, 4, 3, 1, "C1", "Hey Jude", 1, 431000),
        trk(9, 5, 4, 1, "1", "Live and Let Die", 2, 191000),
        trk(10, 6, 4, 2, "2", "Jet", 2, 247000),
        trk(11, 1, 5, 1, "1", "Come Together", 1, 259000),
        trk(12, 4, 6, 1, "1", "Hey Jude", 1, 431000),
        trk(13, 5, 7, 1, "1", "Live and Let Die", 2, 191000),
        trk(14, 4, 8, 1, "1", "Hey Jude", 1, 431000),
    ]
    return t


def _escape(value) -> str:
    if value is None:
        return "\\N"
    return str(value).replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n").replace("\r", "\\r")


def copy_text(table: str, rows: list[dict]) -> bytes:
    columns = mb.table_columns(table)
    # a str item is written as it is (a deliberately broken line)
    lines = [r if isinstance(r, str) else "\t".join(_escape(r[c]) for c in columns) for r in rows]
    return ("\n".join(lines) + ("\n" if lines else "") + "\\.\n").encode("utf-8")


def make_dump(path, tables=None, *, schema=31, order=None, extra_members=(), timestamp="2026-09-30 00:22:22.1+00",
              compression="bz2", skip=(), info=True):
    """Writes the archive and returns its path. `tables`: {table: [row dicts]} (default_tables());
    `order`: member order (default alphabetical, like the real dump); `skip`: tables left out;
    `extra_members`: [(name, bytes)] appended before the tables."""
    tables = default_tables() if tables is None else tables
    mode = {"bz2": "w:bz2", "xz": "w:xz", "gz": "w:gz", "": "w"}[compression]
    members = []
    if info:
        members += [("TIMESTAMP", timestamp.encode() + b"\n"), ("COPYING", b"CC0\n"), ("README", b"synthetic\n"),
                    ("REPLICATION_SEQUENCE", b"123456\n"), ("SCHEMA_SEQUENCE", f"{schema}\n".encode())]
    members += list(extra_members)
    for table in (order or sorted(tables)):
        if table in tables and table not in skip:
            members.append((mb.table_member(table), copy_text(table, tables[table])))
    with tarfile.open(str(path), mode) as archive:
        for name, data in members:
            entry = tarfile.TarInfo(name)
            entry.size = len(data)
            entry.mtime = int(time.time())
            archive.addfile(entry, io.BytesIO(data))
    return str(path)
