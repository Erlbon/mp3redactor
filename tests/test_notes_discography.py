"""The shape of the notes after the Pluteus contract (docs/notes-format.md, "Discographies"): plain
'- Title (Year)' lines grouped by release type, no library lists, no [[links]], no 'in this library'."""

import re
from dataclasses import dataclass, field
from html import unescape

import pytest

from core import notes_render
from core.notes_flow import build_proposals, scan
from core.notes_local import KIND_ALBUM, KIND_ARTIST, KIND_GENRE
from core.notes_render import discography_heading, group_discography, release_line, render_artist_note
from tests.notes_helpers import make_mp3, ultravox_fetchers, ultravox_library


@dataclass
class G:
    title: str
    year: str = ""
    primary: str = "Album"
    secondary: list = field(default_factory=list)


def release_key(text: str) -> str:
    """A copy of pluteus.markdown.release_key (pluteus main 2b56e25), the matcher the badges use."""
    text = re.sub(r"[(\[][^)\]]*[)\]]", " ", unescape(text).lower().replace("&", " and "))
    text = re.sub(r"^\W*\d{4}\W+(?=\w)", "", text.strip())
    return re.sub(r"[\W_]+", " ", text).strip()


def list_key(line: str) -> str:
    """What pluteus does with one list line: the item text, cut at ' - ' (list_item), then release_key."""
    title = line[2:].strip()
    return release_key(re.sub(r"\s+[-–—]\s+.*$", "", title))


@pytest.mark.parametrize("primary, secondary, heading", [
    ("Album", [], "Studio albums"), ("Album", ["Live"], "Live albums"), ("EP", ["Live"], "Live albums"),
    ("Album", ["Compilation"], "Compilations"), ("EP", [], "EPs"), ("Single", [], "Singles"),
    ("Single", ["Live"], "Singles"), ("Album", ["Remix"], "Remixes"), ("Album", ["Soundtrack"], "Soundtracks"),
    ("Album", ["Compilation", "Live"], "Compilations"), ("Broadcast", [], "Releases"), ("Other", [], "Releases"),
    ("", [], "Releases"), ("Album", ["DJ-mix"], "Releases"),
])
def test_the_heading_comes_from_the_release_type(primary, secondary, heading):
    assert discography_heading(primary, secondary) == heading


def test_groups_are_ordered_by_heading_and_year_and_skip_demos_and_empty_headings():
    groups = [G("Zed", "1990"), G("Alpha", "1990"), G("Early", "1980"), G("Undated"), G("Live It", "1985", secondary=["Live"]),
              G("Hits", "1991", secondary=["Compilation"]), G("Tape", "1979", secondary=["Demo"]),
              G("Mix", "1992", secondary=["Mixtape/Street"]), G("Early", "1980"), G("Ep One", "1981", "EP"),
              G("Hit", "1982", "Single")]
    assert group_discography(groups) == [
        ("Studio albums", [("Early", "1980"), ("Alpha", "1990"), ("Zed", "1990"), ("Undated", "")]),
        ("Live albums", [("Live It", "1985")]), ("Compilations", [("Hits", "1991")]),
        ("EPs", [("Ep One", "1981")]), ("Singles", [("Hit", "1982")])]


def test_when_there_is_nothing_but_demos_they_are_better_than_no_discography():
    assert group_discography([G("Tape", "1979", secondary=["Demo"])]) == [("Releases", [("Tape", "1979")])]


def test_release_lines_change_only_what_the_matcher_needs():
    assert release_line("Ha! Ha! Ha!", "1977") == "- Ha! Ha! Ha! (1977)"
    assert release_line("U‐Vox", "1986") == "- U‐Vox (1986)"  # a Unicode hyphen stays
    assert release_line("No Year") == "- No Year"
    assert release_line("Hits - Live", "1990") == "- Hits: Live (1990)"  # the matcher would cut ' - ...' off the list line
    assert release_line("Live – Rock  Vol. 1", "1990") == "- Live: Rock Vol. 1 (1990)"
    assert release_line("*Star* [x] <b> `c` a_b", "") == "- " + chr(92) + "*Star" + chr(92) + "* " + chr(92) + "[x" + chr(92) + "] " \
        + chr(92) + "<b> " + chr(92) + "`c" + chr(92) + "` a" + chr(92) + "_b"


def test_every_line_round_trips_through_the_pluteus_matcher_with_the_folder_names():
    groups = [G("Ha! Ha! Ha!", "1977"), G("U‐Vox", "1986"), G("Speak & Spell", "1981"), G("Hits - Live", "1990", "EP"),
              G("Don’t Look Back", "1999"), G("1984", "1984"), G("Systems of Romance", "1978")]
    folders = {"Ha! Ha! Ha! (1977)": "ha ha ha", "U-Vox": "u vox", "[1981] - Speak And Spell": "speak and spell",
               "Hits - Live (1990)": "hits live", "Don't Look Back": "don t look back", "1984": "1984",
               "Systems of Romance (1978)": "systems of romance"}
    lines = [line for _h, items in group_discography(groups) for line in (release_line(t, y) for t, y in items)]
    assert len(lines) == len(groups)
    for line in lines:
        key = list_key(line)
        assert key in folders.values(), (line, key)
        assert release_key(next(f for f, k in folders.items() if k == key)) == key


def test_the_artist_note_has_the_discography_and_nothing_baked_in():
    text = render_artist_note(
        name="Band", discography=[("Studio albums", [("One", "1980"), ("Two", "")]), ("EPs", [("Short", "1981")])],
        facts=[("Country", "UK")], footer=["Facts from X"])
    assert "## Studio albums\n\n- One (1980)\n- Two\n\n## EPs\n\n- Short (1981)" in text
    assert "In this library" not in text and "[[" not in text and "(all)" not in text


def test_a_real_artist_note_from_the_engine_has_no_links_to_navigate_and_no_library_state(tmp_path):
    ultravox_library(tmp_path)
    found = scan([tmp_path], kinds=[KIND_ARTIST, KIND_ALBUM, KIND_GENRE])
    for proposal in build_proposals(found.targets, ultravox_fetchers()):
        assert proposal.writable
        assert "[[" not in proposal.note_text and "in this library" not in proposal.note_text.lower()
        if proposal.kind == KIND_ARTIST:
            assert "## Studio albums" in proposal.note_text and "Loose tracks" not in proposal.note_text


def test_the_folder_comparison_of_an_album_note_can_be_switched_off(tmp_path, monkeypatch):
    ultravox_library(tmp_path)
    found = scan([tmp_path], kinds=[KIND_ALBUM])
    [vienna] = [t for t in found.targets if t.folder.name == "Vienna (1980)"]
    assert "## In this folder" in build_proposals([vienna], ultravox_fetchers())[0].note_text
    monkeypatch.setattr(notes_render, "INCLUDE_FOLDER_COMPARISON", False)
    off = build_proposals([vienna], ultravox_fetchers())[0].note_text
    assert "## In this folder" not in off and "## Tracklist" in off
