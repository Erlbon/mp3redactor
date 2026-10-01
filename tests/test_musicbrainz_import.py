"""core/musicbrainz_import.py: building the offline MusicBrainz database from SMALL
SYNTHETIC archives (tests/mb_fixture.py) -- the real ~7 GB dump is never used."""

import os
import sqlite3
import uuid

import pytest

from core import musicbrainz_import as mi
from core.musicbrainz_import import BuildOptions, build_musicbrainz_database
from redactor_common.core.dump_import import DumpImportError, ImportCancelled
from tests.mb_fixture import GID, default_tables, make_dump, row


def build(tmp_path, options=None, tables=None, dump_name="mbdump.tar.bz2", **dump_kw):
    source = make_dump(tmp_path / dump_name, tables, **dump_kw)
    dest = str(tmp_path / "mb.db")
    summary = build_musicbrainz_database(source, dest, options)
    return dest, summary


def rows(dest, sql, params=()):
    con = sqlite3.connect(dest)
    try:
        return con.execute(sql, params).fetchall()
    finally:
        con.close()


def release(dest, rid):
    con = sqlite3.connect(dest)
    con.row_factory = sqlite3.Row
    try:
        found = con.execute("select * from releases where id = ?", (rid,)).fetchone()
        return dict(found) if found else None
    finally:
        con.close()


def leftovers(tmp_path):
    return sorted(p.name for p in tmp_path.iterdir() if "partial" in p.name or "scratch" in p.name)


def test_default_options_filter_releases(tmp_path):
    dest, summary = build(tmp_path)
    kept = [r[0] for r in rows(dest, "select id from releases order by id")]
    # 3 bootleg, 6 broadcast, 8 interview (secondary type not ticked), 10 untyped (= Other) are out
    assert kept == [1, 2, 4, 5, 7, 9]
    assert (summary.releases_seen, summary.releases) == (10, 6)
    assert (summary.skipped_status, summary.skipped_type) == (1, 3)
    assert leftovers(tmp_path) == []


def test_release_fields_artist_credit_and_sort_name(tmp_path):
    dest, _ = build(tmp_path)
    abbey = release(dest, 1)
    assert abbey["gid"] == GID["abbey_cd"] and abbey["title"] == "Abbey Road"
    assert abbey["artist_credit_text"] == "The Beatles" and abbey["artist_sort"] == "Beatles, The"
    assert abbey["release_group_gid"] == GID["rg_abbey"] and abbey["rg_type"] == "Album"
    assert (abbey["status"], abbey["language"], abbey["script"]) == ("Official", "eng", "Latn")
    assert abbey["barcode"] == "0077774644624"


def test_artist_credit_join_phrases_in_position_order(tmp_path):
    dest, _ = build(tmp_path)
    live = release(dest, 7)
    # the credit's rows were written out of order in the archive
    assert live["artist_credit_text"] == "Paul McCartney & Wings"
    assert live["artist_sort"] == "McCartney, Paul"  # the FIRST credited artist's sort name
    assert release(dest, 5)["artist_credit_text"] == "Various Artists"


def test_earliest_date_and_country(tmp_path):
    dest, _ = build(tmp_path)
    # the undated-country event (1969-09-26) is the earliest: no country
    cd = release(dest, 1)
    assert (cd["year"], cd["month"], cd["day"], cd["country"]) == (1969, 9, 26, "")
    # two country events: GB 1969-09-26 beats US 1969-10-01
    lp = release(dest, 2)
    assert (lp["year"], lp["month"], lp["day"], lp["country"]) == (1969, 9, 26, "GB")
    # partial dates stay partial; a "worldwide" area is its ISO-like code
    hits = release(dest, 5)
    assert (hits["year"], hits["month"], hits["day"], hits["country"]) == (2001, None, None, "XW")
    assert (release(dest, 7)["month"], release(dest, 7)["day"]) == (12, None)
    undated = release(dest, 9)
    assert undated["year"] is None and undated["country"] == ""


def test_label_and_catalogue_number(tmp_path):
    dest, _ = build(tmp_path)
    cd = release(dest, 1)
    assert (cd["label"], cd["catalog_number"]) == ("Apple Records", "CDP 7 46446 2")
    # first label row has no catalogue number: the first one that has it is used
    lp = release(dest, 2)
    assert (lp["label"], lp["catalog_number"]) == ("EMI", "PCS 7088")
    assert (release(dest, 4)["label"], release(dest, 4)["catalog_number"]) == ("", "")


def test_format_summary_and_counts(tmp_path):
    dest, _ = build(tmp_path)
    cd, lp = release(dest, 1), release(dest, 2)
    assert (cd["format"], cd["medium_count"], cd["track_count"]) == ("CD", 1, 4)
    assert (lp["format"], lp["medium_count"], lp["track_count"]) == ("2×Vinyl", 2, 4)
    assert release(dest, 9)["format"] == "" and release(dest, 9)["medium_count"] == 0


def test_mixed_formats_are_listed_in_order(tmp_path):
    tables = default_tables()
    tables["medium"].append(row("medium", id=9, release=1, position=2, format=2, track_count=0))
    dest, _ = build(tmp_path, tables=tables)
    assert release(dest, 1)["format"] == "CD + DVD"


def test_tracks_only_for_kept_releases(tmp_path):
    dest, summary = build(tmp_path)
    assert rows(dest, "select count(*) from tracks") == [(11,)]
    assert (summary.tracks_seen, summary.tracks) == (14, 11)
    assert rows(dest, "select count(*) from tracks where release_id in (3, 6, 8)") == [(0,)]
    # data tracks are skipped
    assert rows(dest, "select count(*) from tracks where title = 'Hidden Data'") == [(0,)]


def test_track_columns_vinyl_numbers_and_recording_ids(tmp_path):
    dest, _ = build(tmp_path)
    lp = rows(dest, "select medium, position, number, title, length_ms, recording_gid from tracks "
                    "where release_id = 2 order by medium, position")
    assert [(m, p, n, t) for m, p, n, t, _l, _g in lp] == [
        (1, 1, "A1", "Come Together"), (1, 2, "A2", "Something"), (1, 3, "B1", "Maxwell's Silver Hammer"),
        (2, 1, "C1", "Hey Jude"),
    ]
    assert lp[0][4] == 259000
    assert lp[0][5] == uuid.UUID(GID["rec_come_together"]).bytes
    # the same recording is on several releases
    assert rows(dest, "select count(*) from tracks where recording_gid = ?",
                (uuid.UUID(GID["rec_come_together"]).bytes,)) == [(2,)]


def test_track_artist_credit_only_when_it_differs(tmp_path):
    dest, _ = build(tmp_path)
    assert rows(dest, "select artist_credit_text from tracks where release_id = 1") == [(None,)] * 3
    # a compilation: each track carries its own artist
    assert rows(dest, "select distinct artist_credit_text from tracks where release_id = 5") == [("Paul McCartney & Wings",)]


def test_artist_credit_always_stored_with_the_track_index(tmp_path):
    dest, _ = build(tmp_path, BuildOptions(track_index=True))
    assert rows(dest, "select count(*) from tracks where artist_credit_text is null") == [(0,)]
    assert rows(dest, "select artist_credit_text from tracks where release_id = 1 limit 1") == [("The Beatles",)]


def test_indexes_and_fts(tmp_path):
    dest, _ = build(tmp_path)
    indexes = {r[0] for r in rows(dest, "select name from sqlite_master where type = 'index'")}
    assert {"rel_gid", "rel_barcode", "trk_release", "trk_recording"} <= indexes
    found = rows(dest, "select rowid from releases_fts where releases_fts match '\"beatles\" \"abbey\"'")
    assert sorted(r[0] for r in found) == [1, 2]  # rowid = release id (integer primary key)
    assert rows(dest, "select count(*) from sqlite_master where name = 'tracks_fts'") == [(0,)]


def test_track_search_index_is_optional(tmp_path):
    dest, _ = build(tmp_path, BuildOptions(track_index=True))
    found = rows(dest, "select count(*) from tracks_fts where tracks_fts match '\"come\" \"together\"'")
    assert found == [(2,)]  # releases 1 and 2 (3 is a bootleg)
    info = dict(rows(dest, "select key, value from redactor_import_info"))
    assert "fts.tracks_fts" in info and "fts.releases_fts" in info


def test_official_only_off_keeps_bootlegs(tmp_path):
    dest, _ = build(tmp_path, BuildOptions(official_only=False))
    assert 3 in [r[0] for r in rows(dest, "select id from releases order by id")]
    assert release(dest, 3)["status"] == "Bootleg"


def test_type_choices(tmp_path):
    dest, _ = build(tmp_path, BuildOptions(types=("Broadcast",)))
    assert [r[0] for r in rows(dest, "select id from releases order by id")] == [6]
    # Other also covers a release group with no type at all
    dest2, _ = build(tmp_path, BuildOptions(types=("Other",)), dump_name="two.tar.bz2")
    assert [r[0] for r in rows(dest2, "select id from releases order by id")] == [10]


def test_a_secondary_type_must_be_ticked_too(tmp_path):
    # Album + Interview release group: kept only with Interview ticked as well
    dest, _ = build(tmp_path, BuildOptions(types=("Album", "Interview")))
    kept = [r[0] for r in rows(dest, "select id from releases order by id")]
    assert 8 in kept and 5 not in kept and 7 not in kept  # compilation and live are not ticked


def test_skip_undated(tmp_path):
    dest, summary = build(tmp_path, BuildOptions(skip_undated=True))
    assert [r[0] for r in rows(dest, "select id from releases order by id")] == [1, 2, 4, 5, 7]
    assert summary.skipped_undated == 1


def test_without_track_titles(tmp_path):
    dest, summary = build(tmp_path, BuildOptions(include_tracks=False))
    assert rows(dest, "select count(*) from tracks") == [(0,)]
    assert summary.releases == 6


def test_member_order_does_not_matter(tmp_path):
    """The real dump is alphabetical (track after everything it joins to), but a
    track table that comes FIRST is parked and joined afterwards, same result."""
    first, _ = build(tmp_path)
    tables = default_tables()
    order = ["track"] + [t for t in sorted(tables) if t != "track"]
    (tmp_path / "other").mkdir()
    second, _ = build(tmp_path / "other", dump_name="rev.tar.bz2", order=order)
    query = "select release_id, medium, position, number, title, artist_credit_text, recording_gid, length_ms from tracks order by 1,2,3"
    assert rows(first, query) == rows(second, query) and len(rows(second, query)) == 11
    assert rows(first, "select * from releases order by id") == rows(second, "select * from releases order by id")


@pytest.mark.parametrize("compression", ["xz", "gz", ""])
def test_other_archive_compressions(tmp_path, compression):
    dest, summary = build(tmp_path, compression=compression, dump_name="dump.tar" + ("." + compression if compression else ""))
    assert summary.releases == 6


def test_import_info_table(tmp_path):
    dest, summary = build(tmp_path)
    info = dict(rows(dest, "select key, value from redactor_import_info"))
    assert info["recipe"] == mi.RECIPE and info["source"] == "MusicBrainz"
    assert info["dump_timestamp"].startswith("2026-09-30") and info["schema_sequence"] == "31"
    assert info["replication_sequence"] == "123456" and info["dump_file"] == "mbdump.tar.bz2"
    assert info["rows.releases"] == "6" and info["rows.tracks"] == "11"
    assert info["skipped_status"] == "1" and info["bad_lines"] == "0"
    assert int(info["size.file"]) > 0 and summary.sizes["(file)"] == int(info["size.file"])
    assert BuildOptions.from_json(info["options"]) == BuildOptions()
    text = mi.describe_database(dest)
    assert "6 releases, 11 tracks" in text and "official releases only" in text and "2026-09-30" in text


def test_options_json_round_trip_and_garbage():
    options = BuildOptions(official_only=False, types=("Album", "Live"), skip_undated=True, include_tracks=False,
                           track_index=True)
    assert BuildOptions.from_json(options.to_json()) == options
    assert BuildOptions.from_json("") == BuildOptions()
    assert BuildOptions.from_json("not json") == BuildOptions()
    assert BuildOptions.from_json('{"types": ["Album", "Nonsense"], "official_only": "yes"}') == BuildOptions(types=("Album",))
    assert "official" in BuildOptions().describe()


# --- failing loudly ---------------------------------------------------------------------


def test_schema_mismatch_is_an_actionable_error(tmp_path):
    source = make_dump(tmp_path / "d.tar.bz2", schema=99)
    with pytest.raises(DumpImportError) as caught:
        build_musicbrainz_database(source, str(tmp_path / "mb.db"))
    message = str(caught.value)
    assert "schema 99" in message and "31" in message and "fullexport" in message and "musicbrainz_import.py" in message
    assert not (tmp_path / "mb.db").exists() and leftovers(tmp_path) == []


def test_archive_without_a_schema_number_is_refused(tmp_path):
    source = make_dump(tmp_path / "d.tar.bz2", info=False)
    with pytest.raises(DumpImportError, match="SCHEMA_SEQUENCE"):
        build_musicbrainz_database(source, str(tmp_path / "mb.db"))


def test_other_accepted_schema_sequence(tmp_path, monkeypatch):
    monkeypatch.setattr(mi, "ACCEPTED_SCHEMA_SEQUENCES", (31, 32))
    dest, summary = build(tmp_path, schema=32)
    assert summary.releases == 6


def test_missing_tables_name_themselves(tmp_path):
    source = make_dump(tmp_path / "d.tar.bz2", skip=("medium", "label"))
    with pytest.raises(DumpImportError) as caught:
        build_musicbrainz_database(source, str(tmp_path / "mb.db"))
    assert "mbdump/label" in str(caught.value) and "mbdump/medium" in str(caught.value)
    assert "core dump" in str(caught.value)
    assert not (tmp_path / "mb.db").exists() and leftovers(tmp_path) == []


def test_derived_archive_is_not_the_core_dump(tmp_path):
    source = make_dump(tmp_path / "d.tar.bz2", tables={}, extra_members=[("mbdump/tag", b"1\trock\t5\n")])
    with pytest.raises(DumpImportError, match="core dump"):
        build_musicbrainz_database(source, str(tmp_path / "mb.db"))


def test_not_an_archive_fails_loudly(tmp_path):
    path = tmp_path / "mbdump.tar.bz2"
    path.write_bytes(b"this is not a tar archive at all" * 50)
    with pytest.raises(DumpImportError):
        build_musicbrainz_database(str(path), str(tmp_path / "mb.db"))
    with pytest.raises(DumpImportError, match="not found"):
        build_musicbrainz_database(str(tmp_path / "missing.tar.bz2"), str(tmp_path / "mb.db"))
    assert leftovers(tmp_path) == []


def test_changed_column_layout_fails_loudly(tmp_path, monkeypatch):
    """A table with more columns than the recipe knows (exact=True) is a systematic
    mismatch, not a database full of shifted columns."""
    source = make_dump(tmp_path / "d.tar.bz2")
    monkeypatch.setitem(mi.COLUMN_OVERRIDES, "release", mi.mb.table_columns("release")[:-3])
    with pytest.raises(DumpImportError, match="mbdump/release"):
        build_musicbrainz_database(source, str(tmp_path / "mb.db"))
    assert not (tmp_path / "mb.db").exists() and leftovers(tmp_path) == []


def test_bad_lines_are_counted_not_fatal(tmp_path):
    tables = default_tables()
    tables["release"].append("123\tonly three\tcolumns")
    dest, summary = build(tmp_path, tables=tables)
    assert summary.bad_lines == 1 and summary.releases == 6
    assert dict(rows(dest, "select key, value from redactor_import_info"))["bad_lines"] == "1"


def test_no_release_types_chosen(tmp_path):
    source = make_dump(tmp_path / "d.tar.bz2")
    with pytest.raises(DumpImportError, match="release types"):
        build_musicbrainz_database(source, str(tmp_path / "mb.db"), BuildOptions(types=()))


def test_options_that_keep_nothing_fail(tmp_path):
    source = make_dump(tmp_path / "d.tar.bz2")
    with pytest.raises(DumpImportError, match="No releases were kept"):
        build_musicbrainz_database(source, str(tmp_path / "mb.db"), BuildOptions(types=("EP",)))
    assert not (tmp_path / "mb.db").exists() and leftovers(tmp_path) == []


def test_cancel_removes_every_partial_file(tmp_path):
    source = make_dump(tmp_path / "d.tar.bz2")
    dest = tmp_path / "mb.db"
    dest.write_bytes(b"previous build")
    state = {"stop": False}

    def progress(fraction):
        if fraction >= 0.8:
            state["stop"] = True

    with pytest.raises(ImportCancelled):
        build_musicbrainz_database(source, str(dest), None, progress, lambda: state["stop"])
    assert dest.read_bytes() == b"previous build"  # the old database survives a cancelled rebuild
    assert leftovers(tmp_path) == []


def test_cancel_at_once(tmp_path):
    source = make_dump(tmp_path / "d.tar.bz2")
    with pytest.raises(ImportCancelled):
        build_musicbrainz_database(source, str(tmp_path / "mb.db"), None, None, lambda: True)
    assert leftovers(tmp_path) == [] and not (tmp_path / "mb.db").exists()


def test_a_rebuild_replaces_the_old_database(tmp_path):
    dest, _ = build(tmp_path, BuildOptions(types=("Broadcast",)))
    assert rows(dest, "select count(*) from releases") == [(1,)]
    dest, _ = build(tmp_path)
    assert rows(dest, "select count(*) from releases") == [(6,)]


def test_database_info_rejects_foreign_files(tmp_path):
    plain = tmp_path / "other.db"
    con = sqlite3.connect(plain)
    con.execute("create table releases (id)")
    con.execute("create table tracks (id)")
    con.execute("create table redactor_import_info (key text, value text)")
    con.execute("insert into redactor_import_info values ('recipe', 'openlibrary-editions/1')")
    con.commit()
    con.close()
    with pytest.raises(mi.MusicBrainzDatabaseError, match="wasn't built"):
        mi.database_info(str(plain))
    with pytest.raises(mi.MusicBrainzDatabaseError):
        mi.database_info(str(tmp_path / "nope.db"))
    garbage = tmp_path / "garbage.db"
    garbage.write_bytes(b"x" * 5000)
    with pytest.raises(mi.MusicBrainzDatabaseError):
        mi.database_info(str(garbage))


def test_recipe_columns_exist_in_the_verified_schema():
    """The recipe's single source of truth (SPEC/SMALL) only names columns the
    verified column lists have -- the check to rerun when a real sample adjusts either."""
    for table, (_scratch, _pk, columns) in mi.SPEC.items():
        have = set(mi.dump_columns(table))
        assert {c for c, kind in columns if c} <= have, table
    for table, (key, values) in mi.SMALL.items():
        assert {key, *values} <= set(mi.dump_columns(table)), table
    assert set(mi.TRACK_COLUMNS) <= set(mi.dump_columns("track"))
    assert not (set(mi.SPEC) | set(mi.SMALL) | {"track"}) & set(mi.mb.DERIVED_TABLES)  # CC0 tables only
