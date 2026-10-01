"""The Redact step `tags` with a LOCAL MusicBrainz database configured (core/redact_steps.py):
the local database is consulted first, the network only for what is still missing, nothing
is ever overwritten, and with no database configured the step is exactly what it was.
Driven through the real engine on copies of tests/fixtures/tiny.mp3, with a SMALL SYNTHETIC
database (tests/mb_fixture.py) and mocked online/AcoustID calls -- no network, no real dump."""

from pathlib import Path

import pytest

import core.redact_steps as rs
from core.acoustid_lookup import AcoustIdError, RecordingHit
from core.musicbrainz_import import BuildOptions, build_musicbrainz_database
from core.musicbrainz_lookup import MusicBrainzError, Release, ReleaseTrack, match_release
from core.settings import Settings
from redactor_common.core.local_db import forget_cached
from redactor_common.core.pipeline import FileStatus
from tests.mb_fixture import GID, default_tables, make_dump, row
from tests.test_redact_steps import make_file, notes_of, run


def redact_tables():
    """The synthetic database with every track 0.4 s long (like tests/fixtures/tiny.mp3), a
    deterministic winner among the two Abbey Road editions, and an undated release with tracks."""
    tables = default_tables()
    for track in tables["track"]:
        if track["length"] is not None:
            track["length"] = 400
    tables["release_country"] = [r for r in tables["release_country"] if r["release"] != 2]
    tables["release_country"].append(row("release_country", release=2, country=221, date_year=1970))
    tables["medium"].append(row("medium", id=9, release=9, position=1, format=1, track_count=1))
    tables["recording"].append(row("recording", id=7, gid=GID["rec_undated"], name="Undated Song"))
    tables["track"].append(row("track", id=15, recording=7, medium=9, position=1, number="1", name="Undated Song",
                               artist_credit=1, length=400, is_data_track="f"))
    return tables


@pytest.fixture(scope="module")
def db_path(tmp_path_factory):
    folder = tmp_path_factory.mktemp("redact_mb")
    source = make_dump(folder / "mbdump.tar.bz2", redact_tables())
    dest = str(folder / "mb.db")
    build_musicbrainz_database(source, dest, BuildOptions(track_index=True))
    yield dest
    forget_cached(dest)


@pytest.fixture
def settings(db_path):
    return Settings(musicbrainz_database=db_path)


@pytest.fixture(autouse=True)
def no_fpcalc_and_no_network(monkeypatch):
    """Tests opt in to fingerprints; the online lookup fails the test unless it is patched."""
    monkeypatch.setattr(rs, "find_tool", lambda *a, **k: None)
    monkeypatch.setattr(rs, "find_album", lambda facts, query: pytest.fail("the network was asked"))


def abbey_folder(tmp_path, count=3, **extra):
    folder = tmp_path / "Abbey Road"
    folder.mkdir(parents=True, exist_ok=True)
    titles = ["Come Together", "Something", "Maxwell's Silver Hammer"]
    return [
        make_file(folder, f"{n + 1:02d}.mp3", title=titles[n], track=str(n + 1), **extra) for n in range(count)
    ]


def run_all(files, settings, threshold=0.9, options=None):
    """One shared env over `files` (as a real run); returns the report entries in order."""
    env = rs.RedactEnv(settings)
    env.begin(files)
    from redactor_common.core.pipeline import Recipe, run_recipe_on_item

    catalogue = rs.build_catalogue(settings)
    recipe = Recipe.default_for(catalogue)
    recipe.enabled = {s.key: s.key == "tags" for s in catalogue}
    recipe.options = dict(options or {})
    recipe.confidence_threshold = threshold
    resolved = recipe.resolve(catalogue)
    return [
        run_recipe_on_item(m, resolved, threshold, lambda x: rs.Mp3Ctx(x, env), lambda x: x.filename,
                           finalize=rs.save_stage, finalize_label=rs.FINALIZE_LABEL)
        for m in files
    ], env


def reload(mp3):
    from core.mp3_file import MP3File
    from core.tag_reader import load_tags

    fresh = MP3File(path=mp3.path)
    load_tags(fresh)
    return fresh


# --- catalogue / no database -------------------------------------------------------------


def test_step_description_explains_the_local_database():
    step = next(s for s in rs.build_catalogue(Settings()) if s.key == "tags")
    text = step.description
    assert "local MusicBrainz database" in text and "97%" in text and "85%" in text and "FIRST" in text


def test_nothing_changes_when_no_database_is_configured(tmp_path, monkeypatch):
    calls = []
    release = Release(id="rel1", title="Album", artist="Artist", date="1999",
                      tracks=[ReleaseTrack(1, 1, "Song", 0.4, "rec1", "Artist")], track_count=1)
    monkeypatch.setattr(rs, "find_album", lambda facts, query: calls.append(1) or [match_release(facts, release)])
    mp3 = make_file(tmp_path, title="Song", album="Album", artist="Artist")
    entry, env = run(mp3, {"tags"})
    assert calls == [1] and entry.status is FileStatus.CHANGED and mp3.year == "1999"
    assert rs._local_database(env) == (None, "")
    assert mp3.publisher == "" and mp3.releasecountry == ""


def test_a_missing_database_is_a_note_not_a_failure(tmp_path, monkeypatch):
    release = Release(id="rel1", title="Album", artist="Artist", date="1999",
                      tracks=[ReleaseTrack(1, 1, "Song", 0.4, "rec1", "Artist")], track_count=1)
    monkeypatch.setattr(rs, "find_album", lambda facts, query: [match_release(facts, release)])
    mp3 = make_file(tmp_path, title="Song", album="Album", artist="Artist")
    entry, _ = run(mp3, {"tags"}, settings=Settings(musicbrainz_database=str(tmp_path / "gone.db")))
    assert entry.status is FileStatus.CHANGED and not entry.failures and mp3.year == "1999"  # online carried on
    assert "Local MusicBrainz database not used" in notes_of(entry) and "not found" in notes_of(entry)


# --- confidence rules ---------------------------------------------------------------------


def test_exact_release_mbid_is_97_percent_and_fills_label_catalogue_number(tmp_path, settings):
    files = abbey_folder(tmp_path, musicbrainz_albumid=GID["abbey_cd"])
    entries, _ = run_all(files, settings, threshold=0.9)
    assert all(e.status is FileStatus.CHANGED for e in entries)
    first = reload(files[0])
    assert (first.album, first.artist, first.albumartist, first.year) == ("Abbey Road", "The Beatles", "The Beatles", "1969")
    assert (first.publisher, first.catalognumber) == ("Apple Records", "CDP 7 46446 2")
    assert first.musicbrainz_trackid == GID["rec_come_together"] and first.track == "1"
    assert first.genre == ""  # MusicBrainz tags are not in the CC0 dump
    # and at a threshold above 97% it is a review item with that confidence
    other = abbey_folder(tmp_path / "again", musicbrainz_albumid=GID["abbey_cd"])
    entries, _ = run_all(other, settings, threshold=0.98)
    assert entries[0].status is FileStatus.NEEDS_REVIEW
    assert entries[0].review[0].confidence == pytest.approx(0.97) and "Album Id" in entries[0].review[0].reason


def test_the_local_answer_is_used_without_asking_the_network(tmp_path, settings):
    # the guard fixture fails the test if rs.find_album is called
    files = abbey_folder(tmp_path, musicbrainz_albumid=GID["abbey_cd"])
    entries, _ = run_all(files, settings)
    assert entries[0].status is FileStatus.CHANGED


def test_nothing_is_overwritten_and_only_empty_fields_are_filled(tmp_path, settings):
    files = abbey_folder(tmp_path, musicbrainz_albumid=GID["abbey_cd"], year="1985", publisher="My Label",
                         artist="Someone Else")
    run_all(files, settings)
    first = reload(files[0])
    assert (first.year, first.publisher, first.artist) == ("1985", "My Label", "Someone Else")
    assert first.album == "Abbey Road" and first.catalognumber == "CDP 7 46446 2"  # the empty ones were filled


def test_overwrite_option_replaces_different_values(tmp_path, settings):
    files = abbey_folder(tmp_path, musicbrainz_albumid=GID["abbey_cd"], year="1985")
    run_all(files, settings, options={"tags": {"overwrite": True}})
    assert reload(files[0]).year == "1969"


def test_tag_match_with_exact_artist_album_and_equal_track_count_is_applied(tmp_path, settings):
    files = abbey_folder(tmp_path, album="Abbey Road", artist="Beatles, The")  # not the printed spelling
    entries, _ = run_all(files, settings)
    assert all(e.status is FileStatus.CHANGED for e in entries)
    first = reload(files[0])
    assert first.year == "1969" and first.publisher == "Apple Records" and first.albumartist == "The Beatles"


def test_tag_match_with_a_different_track_count_goes_to_review(tmp_path, settings):
    # two files of a three-track album: artist and album are exact, the count is not -> at most 85%
    files = abbey_folder(tmp_path, count=2, album="Abbey Road", artist="The Beatles")
    entries, _ = run_all(files, settings, threshold=0.9)
    assert entries[0].status is FileStatus.NEEDS_REVIEW
    confidence = entries[0].review[0].confidence
    assert confidence == pytest.approx(rs.LOCAL_FUZZY_CAP) and confidence < 0.9
    assert "equal track count" in entries[0].review[0].reason
    assert reload(files[0]).year == ""  # nothing was written
    applied, _ = run_all(abbey_folder(tmp_path / "low", count=2, album="Abbey Road", artist="The Beatles"),
                         settings, threshold=0.8)
    assert applied[0].status is FileStatus.CHANGED


def test_wrong_artist_tag_is_review_even_with_the_right_album(tmp_path, settings):
    files = abbey_folder(tmp_path, album="Abbey Road", artist="Beetles")
    entries, _ = run_all(files, settings)
    assert all(e.status is FileStatus.NEEDS_REVIEW for e in entries)
    assert entries[0].review[0].confidence < 0.9


def test_missing_artist_tag_lowers_the_confidence(tmp_path, settings):
    files = abbey_folder(tmp_path, album="Abbey Road")
    entries, _ = run_all(files, settings)
    assert entries[0].status is FileStatus.NEEDS_REVIEW
    assert 0.8 < entries[0].review[0].confidence < 0.9


def test_recording_id_tag_is_95_percent(tmp_path, settings):
    folder = tmp_path / "Loose"
    folder.mkdir()
    mp3 = make_file(folder, "x.mp3", title="Jet", musicbrainz_trackid=GID["rec_jet"], album="Greatest Hits",
                    artist="Paul McCartney & Wings")
    entries, _ = run_all([mp3], settings, threshold=0.99)
    assert entries[0].status is FileStatus.NEEDS_REVIEW
    assert entries[0].review[0].confidence == pytest.approx(0.95)


# --- fingerprints ---------------------------------------------------------------------------


def test_acoustid_hit_resolved_locally_keeps_the_fingerprint_score(tmp_path, settings, monkeypatch):
    folder = tmp_path / "unknown"
    folder.mkdir()
    mp3 = make_file(folder, "junk.mp3", title="junk", album="junk")
    monkeypatch.setattr(rs, "find_tool", lambda *a, **k: Path("fpcalc"))
    monkeypatch.setattr(rs, "identify_files",
                        lambda paths, fpcalc: ([[RecordingHit(GID["rec_jet"], 0.96, set())]], []))
    entries, _ = run_all([mp3], settings, threshold=0.9)
    assert entries[0].status is FileStatus.CHANGED
    fresh = reload(mp3)
    assert fresh.musicbrainz_trackid == GID["rec_jet"] and fresh.album == "junk" and fresh.title == "junk"
    assert fresh.albumartist == "Various Artists" and fresh.artist == "Paul McCartney & Wings"
    assert fresh.publisher == "" and fresh.releasecountry == "XW"  # the compilation has no label, only a country
    entries, _ = run_all([make_file(folder, "j2.mp3", title="junk", album="junk")], settings, threshold=0.99)
    assert entries[0].review[0].confidence == pytest.approx(0.96) and "AcoustID" in entries[0].review[0].reason


def test_offline_acoustid_is_skipped_with_a_note_and_the_tags_still_work(tmp_path, settings, monkeypatch):
    files = abbey_folder(tmp_path, album="Abbey Road", artist="The Beatles")
    monkeypatch.setattr(rs, "find_tool", lambda *a, **k: Path("fpcalc"))

    def offline(paths, fpcalc):
        raise AcoustIdError("AcoustID couldn't be reached")

    monkeypatch.setattr(rs, "identify_files", offline)
    entries, _ = run_all(files, settings)
    assert all(e.status is FileStatus.CHANGED for e in entries) and not entries[0].failures
    assert "fingerprints skipped" in notes_of(entries[0]) and "couldn't be reached" in notes_of(entries[0])


# --- multi-disc, compilations, single tracks ---------------------------------------------------


def test_multi_disc_fills_the_disc_number(tmp_path, settings):
    folder = tmp_path / "LP"
    folder.mkdir()
    mp3 = make_file(folder, "d2.mp3", title="Hey Jude", track="1", album="Abbey Road", artist="The Beatles",
                    musicbrainz_albumid=GID["abbey_lp"])
    entries, _ = run_all([mp3], settings)
    assert entries[0].status is FileStatus.CHANGED
    fresh = reload(mp3)
    assert fresh.discnumber == "2" and fresh.track == "1" and fresh.publisher == "EMI"
    assert (fresh.catalognumber, fresh.releasecountry) == ("PCS 7088", "GB")


def test_compilation_fills_album_artist_and_each_tracks_own_artist(tmp_path, settings):
    folder = tmp_path / "Greatest Hits"
    folder.mkdir()
    files = [make_file(folder, "1.mp3", title="Live and Let Die", track="1", album="Greatest Hits"),
             make_file(folder, "2.mp3", title="Jet", track="2", album="Greatest Hits")]
    entries, _ = run_all(files, settings, threshold=0.5)
    assert all(e.status is FileStatus.CHANGED for e in entries)
    one, two = reload(files[0]), reload(files[1])
    assert one.albumartist == two.albumartist == "Various Artists"
    assert one.artist == two.artist == "Paul McCartney & Wings"


def test_a_single_track_is_found_by_title_when_the_database_has_the_track_index(tmp_path, settings):
    folder = tmp_path / "misc"
    folder.mkdir()
    mp3 = make_file(folder, "jet.mp3", title="Jet", artist="Paul McCartney & Wings", album="Nowhere Near A Real Album")
    entries, _ = run_all([mp3], settings, threshold=0.9)
    assert entries[0].status is FileStatus.NEEDS_REVIEW  # no album to confirm it: at most 80%
    review = entries[0].review[0]
    assert review.confidence <= rs.LOCAL_TRACK_ONLY_CAP and "single track" in review.reason
    assert "year=" in str(review.value)
    applied, _ = run_all([make_file(folder, "jet2.mp3", title="Jet", artist="Paul McCartney & Wings", album="Nowhere Near A Real Album")],
                         settings, threshold=0.5)
    assert applied[0].status is FileStatus.CHANGED


# --- local first, online only for what is missing -------------------------------------------------


def _undated_files(tmp_path):
    folder = tmp_path / "No Date Album"
    folder.mkdir()
    return [make_file(folder, "1.mp3", title="Undated Song", track="1", album="No Date Album", artist="The Beatles")]


def test_online_is_asked_only_for_what_the_local_database_lacks(tmp_path, settings, monkeypatch):
    calls = []
    online = Release(id="relX", title="No Date Album", artist="The Beatles", date="1971-01-01",
                     tracks=[ReleaseTrack(1, 1, "Undated Song", 0.4, "recX", "The Beatles")], track_count=1)
    monkeypatch.setattr(rs, "find_album", lambda facts, query: calls.append(1) or [match_release(facts, online)])
    files = _undated_files(tmp_path)
    entries, _ = run_all(files, settings)
    assert calls == [1]  # the local release has no date, so the year is still missing
    assert entries[0].status is FileStatus.CHANGED
    fresh = reload(files[0])
    assert fresh.year == "1971"  # from online
    assert fresh.musicbrainz_albumid == GID["undated"]  # from the local database: online adds nothing it had
    assert fresh.musicbrainz_trackid == GID["rec_undated"]


def test_offline_the_local_answer_is_used_as_it_is(tmp_path, settings, monkeypatch):
    def offline(facts, query):
        raise MusicBrainzError("Could not reach MusicBrainz")

    monkeypatch.setattr(rs, "find_album", offline)
    files = _undated_files(tmp_path)
    entries, _ = run_all(files, settings)
    assert entries[0].status is FileStatus.CHANGED and not entries[0].failures
    fresh = reload(files[0])
    assert fresh.year == "" and fresh.musicbrainz_albumid == GID["undated"]
    assert "online MusicBrainz skipped" in notes_of(entries[0]) and "Could not reach" in notes_of(entries[0])


def test_when_neither_knows_the_album_nothing_happens_and_the_note_is_the_online_one(tmp_path, settings, monkeypatch):
    def offline(facts, query):
        raise MusicBrainzError("Could not reach MusicBrainz")

    monkeypatch.setattr(rs, "find_album", offline)
    folder = tmp_path / "Mystery"
    folder.mkdir()
    mp3 = make_file(folder, "m.mp3", title="Unknown Song", album="Unknown Album", artist="Nobody")
    entries, _ = run_all([mp3], settings)
    assert entries[0].status is FileStatus.UNCHANGED and "Could not reach MusicBrainz" in notes_of(entries[0])


def test_complete_tags_need_no_lookup_at_all(tmp_path, settings):
    folder = tmp_path / "done"
    folder.mkdir()
    mp3 = make_file(folder, "d.mp3", title="Song", album="Album", artist="Artist", albumartist="Artist", track="1",
                    year="1999", publisher="L", catalognumber="C", releasecountry="GB")
    entries, _ = run_all([mp3], settings)
    assert entries[0].status is FileStatus.UNCHANGED  # neither database nor network consulted (the guard would fail)


def test_local_lookup_is_done_once_per_folder(tmp_path, settings, monkeypatch):
    import core.musicbrainz_local as ml

    calls = []
    original = rs.find_album_local
    monkeypatch.setattr(rs, "find_album_local", lambda *a, **k: calls.append(1) or original(*a, **k))
    files = abbey_folder(tmp_path, album="Abbey Road", artist="The Beatles")
    run_all(files, settings)
    assert calls == [1] and ml  # three files, one folder, one search
