"""'Seven' and '7' are the same title: one normaliser (core/notes_numbers.compare_key) for album titles, track titles and artist
names, and the search fallbacks ask for both spellings. The normalised text is for comparing only."""

import pytest

from core.notes_flow import Confidence, Fetchers, NotesEngine, build_proposals, scan
from core.notes_level import artist_key, names_agree
from core.notes_match import discography_hits, name_similarity, titles_match, track_overlap
from core.notes_numbers import compare_key, title_variants
from tests.notes_helpers import FakeFetch, make_mp3

MADNESS = "11111111-2222-4333-8444-555555555555"


@pytest.mark.parametrize("a,b", [
    ("Seven", "7"), ("Four", "4"), ("Sju", "7"), ("Syv", "7"), ("Åtte", "8"), ("Fire", "4"), ("Tre", "3"), ("Ett", "1"),
    ("Twenty", "20"), ("Part II", "Part 2"), ("Pt. 2", "Part Two"), ("Part Two", "Part II"), ("Vol. 1", "Volume One"),
    ("Volume 1", "Vol 1"), ("Vol 7", "Volume Seven"), ("No. 1", "Number One"), ("#1", "Number One"), ("Nr. 3", "No. 3"),
    ("First", "1st"), ("Second", "2nd"), ("Third Album", "3rd Album"), ("Ten Years After", "10 Years After"),
    ("Three Dog Night", "3 Dog Night"), ("Maroon Five", "Maroon 5"), ("Two Unlimited", "2 Unlimited"),
    ("Rock & Roll", "Rock and Roll"), ("Rock og Roll", "Rock and Roll"), ("Rocky IV", "Rocky 4"), ("Mk II", "Mk 2"),
    ("Sinfonia XX", "Sinfonia 20"),
])
def test_number_words_numerals_and_romans_compare_equal(a, b):
    assert compare_key(a) == compare_key(b)


@pytest.mark.parametrize("a,b", [
    ("One", "Won"), ("Ten", "Tan"), ("I", "1"), ("V", "5"), ("Malcolm X", "Malcolm 10"), ("No Doubt", "No 1 Doubt"),
    ("Seven", "Eight"), ("Part 2", "Part 3"), ("10cc", "10 cc"), ("Three", "Tree"),
])
def test_things_that_only_look_like_numbers_stay_apart(a, b):
    assert compare_key(a) != compare_key(b)


def test_the_single_letters_i_v_and_x_stay_letters():
    assert compare_key("I") == "i" and compare_key("V") == "v" and compare_key("Malcolm X") == "malcolm x"
    assert compare_key("10cc") == "10cc"


def test_an_ordinary_title_is_unchanged_apart_from_folding():
    assert compare_key("The Return of the Pistol Packin' Mama") == "the return of the pistol packin mama"
    assert compare_key("Дачники") == "дачники"


def test_the_variants_for_a_search():
    assert title_variants("Seven") == ["Seven", "7"]
    assert title_variants("7") == ["7", "Seven"]
    assert title_variants("Part Two") == ["Part Two", "Part 2"]
    assert title_variants("Ten Years After") == ["Ten Years After", "10 Years After"]
    assert title_variants("Sju") == ["Sju", "7"]
    assert title_variants("Absolutely") == ["Absolutely"]
    assert title_variants("I") == ["I"] and title_variants("") == []


def test_the_comparison_helpers_use_it():
    assert titles_match("Seven", "7") and titles_match("Part II (Deluxe Edition)", "Part 2")
    assert not titles_match("One", "Won")
    assert discography_hits([["Seven"], ["Absolutely"]], ["Complete Madness", "7"]) == ["Seven"]
    assert name_similarity(["Three Dog Night"], ["3 Dog Night"]) == 1.0
    assert name_similarity(["Maroon 5"], ["Maroon Five"]) == 1.0
    assert artist_key("2 Unlimited") == artist_key("Two Unlimited") and names_agree("Ten Years After", "10 Years After")
    assert artist_key("10cc") == "10cc"
    assert track_overlap(["Four Seasons", "Intro"], ["4 Seasons", "Intro"]) == 1.0


def madness_fetch():
    return FakeFetch([
        (r"ws/2/artist\?query", {"artists": [{"id": MADNESS, "name": "Madness", "sort-name": "Madness", "score": 100,
                                              "type": "Group", "country": "GB", "life-span": {"begin": "1976"}}]}),
        (r"ws/2/artist/" + MADNESS, {"id": MADNESS, "name": "Madness", "sort-name": "Madness", "type": "Group",
                                     "country": "GB", "life-span": {"begin": "1976"}, "relations": [], "genres": [{"name": "ska", "count": 3}]}),
        (r"ws/2/release-group\?artist=" + MADNESS, {"release-groups": [
            {"id": "rg-1", "title": "One Step Beyond...", "primary-type": "Album", "first-release-date": "1979"},
            {"id": "rg-7", "title": "7", "primary-type": "Album", "first-release-date": "1981"}]}),
        (r"ws/2/release-group\?query", {"release-groups": []}),
        (r"wikidata\.org", {"entities": {}}), (r"search/title", {"pages": []}), (r"page/summary/", None),
    ])


def test_an_engine_run_madness_seven_finds_the_album_called_7_and_is_confident(tmp_path):
    make_mp3(tmp_path / "Madness" / "Seven (1981)" / "01 a.mp3", "Cardiac Arrest", "Madness", "Seven", 1, "1981")
    mb = madness_fetch()
    engine = NotesEngine(Fetchers(mb=mb, wiki=FakeFetch([(r"wikidata\.org", {"entities": {}}), (r"search/title", {"pages": []}),
                                                         (r"page/summary/", None)])))
    target = next(t for t in scan([tmp_path], kinds=("artist",)).targets if t.folder.name == "Madness")
    [proposal] = build_proposals([target], engine=engine)
    assert proposal.confidence == Confidence.CONFIDENT, proposal.reasons
    assert proposal.candidates[0].hits == ["Seven"]
    assert any("found in its discography: Seven" in r for r in proposal.reasons)
    assert "Seven" not in proposal.note_text.split("---")[1]  # the normalised text is never written: the front matter has none
    assert "7" in proposal.note_text  # the album as MusicBrainz calls it


def test_the_reverse_local_7_finds_a_discography_entry_called_seven(tmp_path):
    make_mp3(tmp_path / "Madness" / "7 (1981)" / "01 a.mp3", "Cardiac Arrest", "Madness", "7", 1, "1981")
    mb = madness_fetch()
    mb.routes.insert(0, (r"ws/2/release-group\?artist=" + MADNESS, {"release-groups": [
        {"id": "rg-7", "title": "Seven", "primary-type": "Album", "first-release-date": "1981"}]}))
    engine = NotesEngine(Fetchers(mb=mb))
    target = next(t for t in scan([tmp_path], kinds=("artist",)).targets if t.folder.name == "Madness")
    [proposal] = build_proposals([target], engine=engine)
    assert proposal.confidence == Confidence.CONFIDENT and proposal.candidates[0].hits == ["7"]


def test_the_album_search_asks_for_both_spellings(tmp_path):
    make_mp3(tmp_path / "Madness" / "Seven (1981)" / "01 a.mp3", "Cardiac Arrest", "Madness", "Seven", 1, "1981")
    mb = FakeFetch([
        (r"ws/2/artist\?query", {"artists": []}),
        (r"ws/2/artist/", {"id": MADNESS, "name": "Madness", "sort-name": "Madness", "relations": []}),
        (r"ws/2/release-group\?query=.*releasegroup%3A%22Seven%22.*releasegroup%3A%227%22",
         {"release-groups": [{"id": "rg-7", "title": "7", "primary-type": "Album", "first-release-date": "1981",
                              "artist-credit": [{"name": "Madness", "artist": {"id": MADNESS, "name": "Madness"}}]}]}),
        (r"ws/2/release-group\?query", {"release-groups": []}),
        (r"ws/2/release-group/", {"id": "rg-7", "title": "7", "primary-type": "Album", "first-release-date": "1981",
                                  "releases": [], "relations": [], "genres": []}),
        (r"ws/2/release", {"releases": []}),
    ])
    engine = NotesEngine(Fetchers(mb=mb))
    target = next(t for t in scan([tmp_path / "Madness"], kinds=("album",)).targets)
    [proposal] = build_proposals([target], engine=engine)
    assert mb.count(r"releasegroup%3A%22Seven%22.*OR.*releasegroup%3A%227%22") >= 1
    assert proposal.candidates and proposal.candidates[0].id == "rg-7"


def test_a_title_with_no_number_is_searched_as_before(tmp_path):
    from core import notes_musicbrainz as nmb

    fake = FakeFetch([(r"ws/2/release-group\?query", {"release-groups": []})])
    nmb.search_release_groups("Madness", "Absolutely", fake)
    assert "OR" not in fake.urls[0]


def test_wikipedia_context_terms_find_a_number_in_either_spelling():
    from core.wikipedia_lookup import _mentions

    assert _mentions("Their 1981 album 7 reached the charts.", "Seven")
    assert _mentions("Their 1981 album Seven reached the charts.", "7")
    assert not _mentions("Their album Seventeen reached the charts.", "7")
