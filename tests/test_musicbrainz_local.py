"""core/musicbrainz_local.py: lookups in the offline database, built here from the SMALL
SYNTHETIC archive in tests/mb_fixture.py (nothing real, nothing downloaded)."""

import uuid

import pytest

from core import musicbrainz_local as ml
from core.musicbrainz_import import BuildOptions, build_musicbrainz_database
from core.musicbrainz_local import LocalQuery, artist_equivalent
from core.musicbrainz_lookup import FileFacts
from tests.mb_fixture import GID, default_tables, make_dump, row


def _build(folder, options=None, tables=None):
    folder.mkdir(exist_ok=True)
    source = make_dump(folder / "mbdump.tar.bz2", tables)
    dest = str(folder / "mb.db")
    build_musicbrainz_database(source, dest, options)
    return dest


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    """The synthetic database with the optional track search index."""
    path = _build(tmp_path_factory.mktemp("mb_with_tracks"), BuildOptions(track_index=True))
    database = ml.MusicBrainzLocalDatabase(path)
    yield database
    database.close()


@pytest.fixture(scope="module")
def plain_db(tmp_path_factory):
    path = _build(tmp_path_factory.mktemp("mb_plain"))
    database = ml.MusicBrainzLocalDatabase(path)
    yield database
    database.close()


ABBEY = [
    FileFacts(title="Come Together", track=1, seconds=259),
    FileFacts(title="Something", track=2, seconds=182),
    FileFacts(title="Maxwell's Silver Hammer", track=3, seconds=207),
]


def titles(match):
    return {i: t.title for i, t in match.assignment.items()}


# --- opening ---------------------------------------------------------------------------


def test_open_errors_are_one_error_type(tmp_path):
    with pytest.raises(ml.MusicBrainzLocalError, match="not found"):
        ml.MusicBrainzLocalDatabase(str(tmp_path / "missing.db"))
    other = tmp_path / "x.db"
    other.write_bytes(b"not sqlite" * 100)
    with pytest.raises(ml.MusicBrainzLocalError):
        ml.MusicBrainzLocalDatabase(str(other))
    assert "no local MusicBrainz database" in ml.describe_missing("")
    assert "not found" in ml.describe_missing(str(tmp_path / "missing.db"))


def test_a_database_without_the_expected_columns_asks_for_a_rebuild(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("create table releases (id integer, gid text)")
    con.execute("create table tracks (release_id integer)")
    con.commit()
    con.close()
    with pytest.raises(ml.MusicBrainzLocalError, match="rebuild"):
        ml.MusicBrainzLocalDatabase(str(path))


def test_optional_indexes_are_detected(db, plain_db):
    assert db.has_release_search and db.has_track_search and db.has_track_rows
    assert plain_db.has_release_search and not plain_db.has_track_search


# --- by id -----------------------------------------------------------------------------


def test_release_by_mbid_has_the_online_shape(db):
    release = ml.release_by_mbid(db, GID["abbey_cd"].upper())  # case is irrelevant
    assert (release.id, release.title, release.artist) == (GID["abbey_cd"], "Abbey Road", "The Beatles")
    assert (release.date, release.country, release.status) == ("1969-09-26", "", "Official")
    assert release.media_formats == "CD" and release.track_count == 4
    assert (release.label_name, release.catno) == ("Apple Records", "CDP 7 46446 2")
    assert [(t.disc, t.position, t.title) for t in release.tracks] == [
        (1, 1, "Come Together"), (1, 2, "Something"), (1, 3, "Maxwell's Silver Hammer")]
    assert release.tracks[0].seconds == 259.0
    assert release.tracks[0].recording_id == GID["rec_come_together"]
    assert release.tracks[0].artist == "The Beatles"  # a track with no credit of its own: the release's
    assert "Apple Records" in release.label() and "Abbey Road" in release.label()
    assert ml.release_by_mbid(db, "00000000-0000-4000-8000-000000000000") is None
    assert ml.release_by_mbid(db, "garbage") is None


def test_recording_mbid_finds_the_releases_containing_it(db):
    found = ml.releases_by_recording(db, GID["rec_come_together"])
    assert sorted(r.id for r in found) == sorted([GID["abbey_cd"], GID["abbey_lp"]])  # the bootleg is not in the database
    assert ml.releases_by_recording(db, "not-a-uuid") == []
    assert ml.releases_by_recording(db, str(uuid.uuid4())) == []


def test_barcode_lookup_tolerates_upc_and_ean_spellings(db):
    assert [r.id for r in ml.releases_by_barcode(db, "0077774644624")] == [GID["abbey_cd"]]
    assert [r.id for r in ml.releases_by_barcode(db, "077774644624")] == [GID["abbey_cd"]]  # UPC-A: no leading 0
    assert [r.id for r in ml.releases_by_barcode(db, "0077-7746 44624")] == [GID["abbey_cd"]]
    assert ml.releases_by_barcode(db, "123") == [] and ml.releases_by_barcode(db, "") == []


# --- finding a folder's release --------------------------------------------------------


def test_release_mbid_is_exact_and_ranked_first(db):
    # the text says something else entirely; the file's own release id wins
    query = LocalQuery(artist="Wings", album="Wings Live")
    matches = ml.find_album_local(db, ABBEY, query, album_ids=(GID["abbey_cd"],))
    assert matches[0].via == "mbid" and matches[0].release.id == GID["abbey_cd"]
    assert matches[0].matched == 3
    assert [m.via for m in matches] == ["mbid"]  # every file is placed: nothing else is consulted
    # the review dialog also wants the other editions
    wider = ml.find_album_local(db, ABBEY, query, album_ids=(GID["abbey_cd"],), alternatives=True)
    assert wider[0].via == "mbid" and any(m.via == "text" and m.release.title == "Wings Live" for m in wider[1:])


def test_recording_ids_resolve_locally_without_any_text(db):
    """What an AcoustID fingerprint hit looks like: recording id -> score. AcoustID itself is
    online; only the resolution to releases is local."""
    facts = [FileFacts(title="", track=None, recordings={GID["rec_live_and_let_die"]: 0.93}),
             FileFacts(title="", track=None, recordings={GID["rec_jet"]: 0.88})]
    matches = ml.find_album_local(db, facts, LocalQuery())
    best = {m.release.title: m for m in matches}["Greatest Hits"]
    assert best.via == "recording" and best.matched == 2
    assert {t.recording_id for t in best.assignment.values()} == {GID["rec_live_and_let_die"], GID["rec_jet"]}


def test_recording_id_tags_are_a_candidate_source_too(db):
    facts = [FileFacts(title="x"), FileFacts(title="y")]
    matches = ml.find_album_local(db, facts, LocalQuery(), recording_ids=[GID["rec_live_and_let_die"], GID["rec_jet"]])
    assert matches and matches[0].via == "recording-tag" and matches[0].release.title == "Greatest Hits"


def test_barcode_in_the_album_box_works_like_a_barcode_search(db):
    matches = ml.find_album_local(db, ABBEY, LocalQuery(artist="", album="0077774644624"))
    assert matches[0].release.id == GID["abbey_cd"] and matches[0].via == "barcode"
    matches = ml.find_album_local(db, ABBEY, LocalQuery(barcode="077774644624"))
    assert matches[0].release.id == GID["abbey_cd"]


def test_text_search_exact_artist_and_album(db):
    matches = ml.find_album_local(db, ABBEY, LocalQuery(artist="The Beatles", album="Abbey Road", year="1969"))
    best = matches[0]
    assert best.via == "text" and best.exact and best.matched == 3
    assert {m.release.id for m in matches} == {GID["abbey_cd"], GID["abbey_lp"]}


def test_fuzzy_artist_is_not_exact(db):
    # the artist tag is wrong: still found by album alone, but not "exact"
    matches = ml.find_album_local(db, ABBEY, LocalQuery(artist="Beetles", album="Abbey Road"))
    assert matches and not matches[0].exact
    # a partly right album title is never exact either
    other = ml.find_album_local(db, ABBEY, LocalQuery(artist="The Beatles", album="Abbey"))
    assert other and not other[0].exact


def test_the_beatles_and_beatles_the_are_the_same_artist(db):
    for artist in ("Beatles, The", "The Beatles", "BEATLES", "beatles the"):
        matches = ml.find_album_local(db, ABBEY, LocalQuery(artist=artist, album="Abbey Road"))
        assert matches[0].exact and matches[0].release.artist == "The Beatles", artist
    assert artist_equivalent("Beatles, The", "The Beatles")
    assert artist_equivalent("Beatles, The", "something else", "Beatles, The")
    assert artist_equivalent("McCartney, Paul", "Paul McCartney")
    assert artist_equivalent("The The", "The The")  # all words are "the": kept
    assert not artist_equivalent("Wings", "Paul McCartney & Wings")
    assert not artist_equivalent("", "The Beatles")
    assert artist_equivalent("Various", "Various Artists") and artist_equivalent("VA", "Various Artists")
    assert not artist_equivalent("Various", "The Beatles")


def test_compilation_various_artists(db):
    files = [FileFacts(title="Live and Let Die", track=1, seconds=191), FileFacts(title="Jet", track=2, seconds=247)]
    for artist in ("Various Artists", "Various", "VA", ""):
        matches = ml.find_album_local(db, files, LocalQuery(artist=artist, album="Greatest Hits"))
        assert matches[0].release.title == "Greatest Hits" and matches[0].matched == 2, artist
    best = matches[0]
    release = best.release
    track = best.assignment[0]
    assert release.artist == "Various Artists" and track.artist == "Paul McCartney & Wings"
    fields = ml.local_fields_for(release, track)
    assert fields["albumartist"] == "Various Artists" and fields["artist"] == "Paul McCartney & Wings"


def test_multi_disc_and_vinyl_positions(db):
    """The LP has two discs; position is sequential per disc ("A1","A2","B1" are 1,2,3, the
    printed number is only a label), exactly as the online lookup's tracks."""
    lp = ml.release_by_mbid(db, GID["abbey_lp"])
    assert [(t.disc, t.position) for t in lp.tracks] == [(1, 1), (1, 2), (1, 3), (2, 1)]
    files = [FileFacts(title="Maxwell's Silver Hammer", track=3, disc=1), FileFacts(title="Hey Jude", track=1, disc=2)]
    matches = ml.find_album_local(db, files, LocalQuery(artist="The Beatles", album="Abbey Road"),
                                  album_ids=(GID["abbey_lp"],))
    best = matches[0]
    assert best.release.id == GID["abbey_lp"] and best.matched == 2
    assert [best.assignment[i].disc for i in (0, 1)] == [1, 2]
    fields = ml.local_fields_for(best.release, best.assignment[1])
    assert fields["discnumber"] == "2" and fields["track"] == "1" and fields["title"] == "Hey Jude"
    # a single-disc release writes no disc number
    cd = ml.release_by_mbid(db, GID["abbey_cd"])
    assert "discnumber" not in ml.local_fields_for(cd, cd.tracks[0])


def test_fields_map_like_the_online_lookup_plus_label_catalogue_and_country(db):
    cd = ml.release_by_mbid(db, GID["abbey_lp"])
    track = cd.tracks[0]
    fields = ml.local_fields_for(cd, track)
    assert fields == {
        "title": "Come Together", "artist": "The Beatles", "albumartist": "The Beatles", "album": "Abbey Road",
        "track": "1", "year": "1969", "musicbrainz_albumid": GID["abbey_lp"],
        "musicbrainz_trackid": GID["rec_come_together"], "discnumber": "1",
        "publisher": "EMI", "catalognumber": "PCS 7088", "releasecountry": "GB",
    }
    assert "genre" not in fields  # MusicBrainz tags are derived (non-CC0) data: never used


def test_text_search_ignores_punctuation_and_ampersands(tmp_path):
    tables = default_tables()
    tables["artist"].append(row("artist", id=5, gid="a0000005-0000-4000-8000-000000000005", name="Hall & Oates",
                                sort_name="Hall & Oates"))
    tables["artist_credit_name"].append(
        row("artist_credit_name", artist_credit=4, position=0, artist=5, name="Hall & Oates", join_phrase=""))
    tables["release_group"].append(row("release_group", id=8, gid="d0000008-0000-4000-8000-000000000008",
                                       name="Voices", artist_credit=4, type=1))
    tables["release"].append(row("release", id=11, gid="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", name="Voices!",
                                 artist_credit=4, release_group=8, status=1))
    path = _build(tmp_path / "amp", tables=tables)
    database = ml.MusicBrainzLocalDatabase(path)
    try:
        found = ml.search_releases_local(database, LocalQuery(artist="Hall and Oates", album="Voices"))
        assert [r.title for r in found] == ["Voices!"]
    finally:
        database.close()


def test_no_match_is_empty_not_an_error(db):
    assert ml.find_album_local(db, ABBEY, LocalQuery(artist="Nobody", album="Nothing Here")) == []
    assert ml.find_album_local(db, ABBEY, LocalQuery()) == []
    with pytest.raises(ml.MusicBrainzLocalError, match="album name"):
        ml.search_releases_local(db, LocalQuery(artist="x"))


# --- one track without an album ---------------------------------------------------------


def test_single_track_search_needs_the_track_index(db, plain_db):
    fact = FileFacts(title="Jet", seconds=247)
    found = ml.find_track_local(db, fact, "Wings")
    assert found and found[0].release.title == "Greatest Hits" and found[0].matched == 1
    assert found[0].assignment[0].recording_id == GID["rec_jet"]
    assert ml.find_track_local(plain_db, fact, "Wings") == []  # no track index in this database
    assert ml.find_track_local(db, FileFacts(title=""), "Wings") == []
    assert ml.find_track_local(db, FileFacts(title="Not A Song At All")) == []
    # title only (no artist): "Hey Jude" is on three kept releases
    hey = ml.find_track_local(db, FileFacts(title="Hey Jude", seconds=431))
    assert {m.release.title for m in hey} >= {"Hey Jude", "Abbey Road"}


def test_the_year_hint_orders_editions_of_one_album(tmp_path):
    tables = default_tables()
    tables["release"].append(row("release", id=12, gid="cccccccc-cccc-4ccc-8ccc-cccccccccccc",
                                 name="Abbey Road", artist_credit=1, release_group=1, status=1))
    tables["release_country"].append(row("release_country", release=12, country=221, date_year=2019))
    tables["medium"].append(row("medium", id=9, release=12, position=1, format=1, track_count=4))
    database = ml.MusicBrainzLocalDatabase(_build(tmp_path / "years", tables=tables))
    try:
        found = ml.search_releases_local(database, LocalQuery(artist="The Beatles", album="Abbey Road"))
        for year, expected in (("2019", "2019"), ("1969", "1969")):
            ranked = ml._preranked(found, LocalQuery(artist="The Beatles", album="Abbey Road", year=year), 3)
            assert ranked[0].date.startswith(expected), year
    finally:
        database.close()
