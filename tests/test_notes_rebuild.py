"""Folder notes: rebuilding a proposal after a PERSON chose a candidate or a level (NotesEngine.rebuild). Recorded
replies and real tagged MP3 files; an unexpected URL fails the test."""

from core.notes_flow import Confidence, Fetchers, NotesEngine, build_proposals, scan, write_proposal
from core.notes_local import ALL_KINDS
from redactor_common.core.notes import parse_front_matter
from tests.notes_helpers import FakeFetch, make_mp3, ultravox_fetchers, ultravox_library

NO_WIKI = FakeFetch([(r"search/title", {"pages": []}), (r"page/summary/", None)])


def by_key(root):
    return {(t.kind, t.folder.name): t for t in scan([root], kinds=ALL_KINDS).targets}


def collab_library(tmp_path):
    base = tmp_path / "Ultravox & Friends"
    for album, year in (("Vienna", "1980"), ("Quartet", "1982")):
        make_mp3(base / f"{album} ({year})" / "01 a.mp3", "a", "Ultravox & Friends", album, 1, year)
    return base


def test_choosing_the_candidate_makes_an_uncertain_artist_confident_and_says_who_chose(tmp_path):
    base = collab_library(tmp_path)
    engine = NotesEngine(ultravox_fetchers())
    [uncertain] = build_proposals([by_key(tmp_path)[("artist", "Ultravox & Friends")]], engine=engine)
    assert uncertain.confidence == Confidence.UNCERTAIN and not uncertain.note_text
    candidate = uncertain.candidates[0]
    assert engine.can_choose(uncertain, candidate)
    chosen = engine.rebuild(uncertain, candidate=candidate)
    assert chosen.confidence == Confidence.CONFIDENT and chosen.writable
    assert any(r.startswith("chosen by you: Ultravox") for r in chosen.reasons)
    assert not any("loose match" in r for r in chosen.reasons)
    meta, _body = parse_front_matter(chosen.note_text)
    assert meta["status"] == "draft"
    assert write_proposal(chosen).ok and (base / "artist.md").is_file()


def test_the_rebuild_reuses_the_cached_discography(tmp_path):
    collab_library(tmp_path)
    fetchers = ultravox_fetchers()
    engine = NotesEngine(fetchers)
    [uncertain] = build_proposals([by_key(tmp_path)[("artist", "Ultravox & Friends")]], engine=engine)
    before = fetchers.mb.count(r"ws/2/release-group\?artist=")
    engine.rebuild(uncertain, candidate=uncertain.candidates[0])
    assert fetchers.mb.count(r"ws/2/release-group\?artist=") == before  # the browse of that artist is already in memory: no new request


def test_choosing_a_release_group_for_a_folder_that_does_not_match_the_tracklist(tmp_path):
    folder = tmp_path / "Ultravox" / "Vienna (1980)"
    for i, title in enumerate(["Alpha", "Beta", "Gamma", "Delta"], start=1):
        make_mp3(folder / f"{i:02d} {title}.mp3", title, "Ultravox", "Vienna", i, "1980")
    engine = NotesEngine(ultravox_fetchers())
    [uncertain] = build_proposals([by_key(tmp_path)[("album", "Vienna (1980)")]], engine=engine)
    assert uncertain.confidence == Confidence.UNCERTAIN and uncertain.candidates
    chosen = engine.rebuild(uncertain, candidate=uncertain.candidates[0])
    assert chosen.confidence == Confidence.CONFIDENT and chosen.note_text
    assert any(r.startswith("chosen by you") for r in chosen.reasons)
    assert "Astradyne" in chosen.note_text  # the tracklist of the chosen release is there, the folder's tracks are "extra"


def test_a_discogs_only_album_candidate_cannot_be_chosen(tmp_path):
    from core.notes_flow import Candidate, Proposal

    engine = NotesEngine(Fetchers())
    ultravox_library(tmp_path)
    proposal = Proposal(by_key(tmp_path)[("album", "Vienna (1980)")])
    assert not engine.can_choose(proposal, Candidate("discogs", "1", "X - Vienna"))
    assert engine.can_choose(proposal, Candidate("musicbrainz", "1", "X - Vienna"))


def test_an_unclear_level_can_be_rebuilt_as_an_artist_folder(tmp_path):
    base = tmp_path / "Compilation Box"
    make_mp3(base / "One (1980)" / "01 a.mp3", "a", "Queen", "One", 1, "1980")
    make_mp3(base / "Two (1981)" / "01 a.mp3", "a", "Queen", "Two", 1, "1981")
    engine = NotesEngine(Fetchers(mb=FakeFetch([(r"ws/2/artist\?query", {"artists": []})]), wiki=NO_WIKI))
    [unclear] = build_proposals([by_key(tmp_path)[("artist", "Compilation Box")]], engine=engine)
    assert unclear.level == "unclear" and "artist" in unclear.suggested_levels
    rebuilt = engine.rebuild(unclear, level="artist")
    assert rebuilt.level == "artist" and rebuilt.note_kind == "artist"
    assert any("level chosen by you: artist" in r for r in rebuilt.level_reasons)
    assert rebuilt.confidence != Confidence.CONFIDENT  # nothing found for it: still for a person to look at
    as_collection = engine.rebuild(unclear, level="genre-collection")
    assert as_collection.level == "collection" and as_collection.note_kind == "about"


def test_loose_tracks_of_one_artist_rebuilt_as_an_artist_note_or_as_an_album(tmp_path):
    folder = tmp_path / "Ultravox"
    for i, album in enumerate(["A", "B", "C"], start=1):
        make_mp3(folder / f"0{i} song.mp3", f"song {i}", "Ultravox", album, i)
    [target] = [t for t in scan([tmp_path]).targets if t.kind == "album"]
    engine = NotesEngine(Fetchers(mb=FakeFetch([(r"ws/2/", {"artists": [], "release-groups": []})]), wiki=NO_WIKI))
    [first] = build_proposals([target], engine=engine)
    assert first.level == "artist" and first.confidence == Confidence.UNCERTAIN and first.kind == "album"
    as_artist = engine.rebuild(first, level="artist")
    assert as_artist.kind == "artist" and as_artist.note_kind == "artist" and as_artist.level == "artist"
    as_album = engine.rebuild(first, level="album")
    assert as_album.kind == "album" and as_album.level == "album"
    assert not any("loose tracks" in r for r in as_album.reasons)
