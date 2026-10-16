"""Dewey-style numbered folders ('780 - Music\\1.61 - Ska'): every lookup and every note title uses the cleaned name, the folder
keeps its own name for paths. A band called '1975', '10cc', '311', '2 Unlimited' or '3 Doors Down' is never touched."""

import pytest

from core.notes_flow import Fetchers, NotesEngine, build_proposals, scan
from core.notes_local import (
    is_alphabetical_grouping, is_collection_name, is_generic_name, lookup_name, read_artist, read_genre, split_year,
)
from redactor_common.core.notes import parse_front_matter
from tests.notes_helpers import FakeFetch, make_mp3, ultravox_fetchers, ultravox_library


@pytest.mark.parametrize("raw,cleaned", [
    ("1.61 - Ska", "Ska"), ("780 - Music", "Music"), ("0 - Bands", "Bands"), ("3 - Rock", "Rock"), ("02. Name", "Name"),
    ("[1981] - Name", "Name"), ("[1981] Name", "Name"), ("#12 - Name", "Name"), ("#12. Name", "Name"), ("1.61. Ska", "Ska"),
    ("1.61 – Ska", "Ska"), ("Marvel (1961-2012)", "Marvel"), ("Foo (1963-)", "Foo"),
    ("2 - Various Artists", "Various Artists"), ("Ska", "Ska"),
])
def test_numbering_and_year_ranges_are_taken_off(raw, cleaned):
    assert lookup_name(raw) == cleaned


@pytest.mark.parametrize("name", ["1975", "10cc", "311", "2 Unlimited", "3 Doors Down", "5 Seconds of Summer", "A. R. Rahman",
                                  "Blink-182", "Sum 41", "U2", "1", "2012", "1.61", "", "(1961-2012)"])
def test_real_names_and_bare_numbers_are_never_changed(name):
    assert lookup_name(name) == name


@pytest.mark.parametrize("name", ["#1 Record", "1984: For the Love of Big Brother", "5: Name", "1984 - of Big Brother",
                                  "#2 Radio City"])
def test_a_number_that_is_part_of_the_title_is_kept_review_2026_10_16(name):
    """The numbering cleaner ate the '#1' of '#1 Record' and the '1984' of '1984: For the Love of Big Brother'."""
    assert lookup_name(name) == name


def test_a_year_followed_by_a_colon_is_a_title_not_a_year_prefix():
    assert split_year("1984: For the Love of Big Brother") == ("1984: For the Love of Big Brother", "")
    assert split_year("1980 - Vienna") == ("Vienna", "1980")


def test_a_prefix_needs_a_letter_after_it():
    assert lookup_name("7 - 11") == "7 - 11" and lookup_name("1.61 -") == "1.61 -" and lookup_name("12 - 34 - 56") == "12 - 34 - 56"


def test_generic_and_grouping_names_are_checked_raw_and_cleaned():
    assert is_generic_name("780 - Music") and is_generic_name("0 - Bands") and is_generic_name("Music") and is_generic_name("Bands")
    assert not is_generic_name("1.61 - Ska") and not is_generic_name("Depeche Mode")
    assert is_alphabetical_grouping("D") and is_alphabetical_grouping("0 - D") and is_alphabetical_grouping("1 - A-C")
    assert not is_alphabetical_grouping("1.61 - Ska") and not is_alphabetical_grouping("311")
    assert is_collection_name("2 - Various Artists") and is_collection_name("Various Artists")


def test_the_folder_keeps_its_name_the_reading_uses_the_cleaned_one(tmp_path):
    make_mp3(tmp_path / "1.61 - Ska" / "02. The Specials" / "1979 - Specials" / "01 a.mp3", "a", "The Specials", "Specials", 1, "1979")
    genre = read_genre(tmp_path / "1.61 - Ska")
    assert genre.name == "Ska" and genre.folder.name == "1.61 - Ska"
    artist = read_artist(tmp_path / "1.61 - Ska" / "02. The Specials")
    assert artist.name == "The Specials" and artist.folder.name == "02. The Specials"
    assert [(a.title, a.year) for a in artist.albums] == [("Specials", "1979")]
    assert split_year("1980 - Vienna") == ("Vienna", "1980") and split_year("Vienna (1980)") == ("Vienna", "1980")


def genre_wiki():
    return FakeFetch([
        (r"search/title\?q=Ska\+music", {"pages": [{"title": "Ska", "description": "Music genre",
                                                    "excerpt": "Ska is a music genre that originated in Jamaica"}]}),
        (r"search/title", {"pages": []}),
        (r"page/summary/Ska$", {"type": "standard", "title": "Ska", "description": "Music genre",
                                "extract": "Ska is a music genre that originated in Jamaica in the late 1950s.",
                                "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Ska"}}}),
        (r"page/summary/", None),
    ])


def dewey_tree(root):
    make_mp3(root / "780 - Music" / "1.61 - Ska" / "The Specials" / "Specials (1979)" / "01 a.mp3", "a", "The Specials", "Specials", 1, "1979")
    make_mp3(root / "780 - Music" / "0 - Bands" / "D" / "Depeche Mode" / "Violator (1990)" / "01 a.mp3", "a", "Depeche Mode", "Violator", 1, "1990")
    return root / "780 - Music"


def test_a_dewey_numbered_genre_folder_is_looked_up_and_titled_by_its_real_name(tmp_path):
    music = dewey_tree(tmp_path)
    wiki = genre_wiki()
    found = scan([music], kinds=("genre",))
    assert [(t.kind, t.folder.name) for t in found.targets] == [("genre", "1.61 - Ska")]  # '0 - Bands' is a placeholder, D a grouping
    [proposal] = build_proposals(found.targets, Fetchers(mb=lambda r: 1 / 0, wiki=wiki))
    assert proposal.confidence.value == "confident"  # was: no Wikipedia page about a genre called '1.61 - Ska'
    meta, body = parse_front_matter(proposal.note_text)
    assert meta["title"] == "Ska" and "# Ska" in body and "Introduction from Wikipedia ([Ska]" in body
    assert not any("1.61" in u for u in wiki.urls) and any("q=Ska" in u for u in wiki.urls)
    assert proposal.path.endswith("1.61 - Ska\\about.md") or proposal.path.endswith("1.61 - Ska/about.md")


def test_a_numbered_artist_folder_is_searched_and_titled_by_its_real_name(tmp_path):
    artist = ultravox_library(tmp_path)
    numbered = artist.rename(tmp_path / "02. Ultravox (1974-)")
    fetchers = ultravox_fetchers()
    [proposal] = build_proposals([next(t for t in scan([tmp_path], kinds=("artist",)).targets if t.folder == numbered)], fetchers)
    assert proposal.confidence.value == "confident" and proposal.title == "Ultravox"
    assert parse_front_matter(proposal.note_text)[0]["title"] == "Ultravox"
    assert any("artist%3A%22Ultravox%22" in u for u in fetchers.mb.urls) and not any("02" in u and "artist%3A" in u for u in fetchers.mb.urls)
    assert numbered.name == "02. Ultravox (1974-)"  # the folder keeps its name


def test_an_album_folder_with_a_year_in_front_is_a_normal_album(tmp_path):
    base = tmp_path / "Ultravox"
    make_mp3(base / "1980 - Vienna" / "01 Astradyne.mp3", "Astradyne", "Ultravox", "Vienna", 1, "1980")
    make_mp3(base / "1982 - Quartet" / "01 Hymn.mp3", "Hymn", "Ultravox", "Quartet", 1, "1982")
    fetchers = ultravox_fetchers()
    [proposal] = build_proposals([next(t for t in scan([tmp_path], kinds=("artist",)).targets)], fetchers)
    assert proposal.confidence.value == "confident"  # the albums were found as 'Vienna' and 'Quartet', not '1980 - Vienna'
    assert "Vienna" in " ".join(proposal.reasons) and "Quartet" in " ".join(proposal.reasons)
