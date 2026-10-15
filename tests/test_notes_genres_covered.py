"""A genre / collection folder can cover SEVERAL genres ('1.61 - Ska' = ska and ska punk): the note merges the Wikipedia
introductions, one section per genre and one credit; a name is split only when every part verifies as a genre of its own;
the review page edits the genres and rebuilds the row in place; the command line has --genres."""

import json
import os
import re

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from core.notes_flow import Fetchers, NotesEngine, build_proposals, scan, split_genre_name  # noqa: E402
from core.notes_render import wikipedia_credits  # noqa: E402
from redactor_common.core.notes import parse_front_matter  # noqa: E402
from tests.notes_helpers import FakeFetch, make_mp3, set_kinds  # noqa: E402
from tests.test_cover_gui import window  # noqa: E402,F401
from tests.test_notes_genre_only import nothing_else  # noqa: E402
from tests.test_notes_gui import boxes, click, review, wait  # noqa: E402,F401
from tests.test_notes_scope import drive, scope_dialog  # noqa: E402,F401

SKA = ("Ska is a music genre that originated in Jamaica in the late 1950s and was the precursor to rocksteady and reggae. "
       "It combined elements of Caribbean mento with American jazz and rhythm and blues.")
SKA_PUNK = ("Ska punk is a fusion genre that mixes ska with punk rock. It is closely tied to third wave ska, which reached its "
            "zenith in the mid-1990s.")


def page(title, extract, description="Music genre"):
    wiki_title = title.replace(" ", "_")
    return {"type": "standard", "title": title, "description": description, "extract": extract,
            "content_urls": {"desktop": {"page": f"https://en.wikipedia.org/wiki/{wiki_title}"}}}


def hit(title, blurb):
    return {"pages": [{"title": title, "description": "Music genre", "excerpt": blurb}]}


def genre_wiki():
    return FakeFetch([
        (r"(?i)search/title\?q=Ska\+punk\+music", hit("Ska punk", "Ska punk is a fusion genre")),
        (r"(?i)search/title\?q=Ska\+music", hit("Ska", "Ska is a music genre")),
        (r"(?i)search/title\?q=Rock\+and\+Roll\+music", hit("Rock and roll", "Rock and roll is a genre of popular music")),
        (r"(?i)search/title\?q=Rock\+music", hit("Rock music", "Rock is a broad genre of popular music")),
        (r"(?i)search/title\?q=Drum\+and\+Bass\+music", hit("Drum and bass", "Drum and bass is a genre of electronic music")),
        (r"search/title", {"pages": []}),
        (r"page/summary/Ska_punk$", page("Ska punk", SKA_PUNK)),
        (r"page/summary/Ska$", page("Ska", SKA)),
        (r"page/summary/Rock_and_roll$", page("Rock and roll", "Rock and roll is a genre of popular music that originated in the US.")),
        (r"page/summary/Rock_music$", page("Rock music", "Rock is a broad genre of popular music.")),
        (r"page/summary/Drum_and_bass$", page("Drum and bass", "Drum and bass is a genre of electronic music.")),
        (r"page/summary/", None),
    ])


def genre_folder(root, name):
    make_mp3(root / name / "Some Artist" / "Album (2000)" / "01 a.mp3", "a", "Some Artist", "Album", 1, "2000")
    return root / name


def proposal_for(root, name, engine_setup=None):
    wiki = genre_wiki()
    engine = NotesEngine(Fetchers(mb=nothing_else(), wiki=wiki))
    if engine_setup:
        engine_setup(engine)
    found = scan([root], kinds=("genre",))
    [proposal] = build_proposals([t for t in found.targets if t.folder.name == name], engine=engine)
    return proposal, wiki, engine


def without_stamp(text):
    return "".join(line for line in text.splitlines(keepends=True) if not re.match(r"(generator|note-format|body-sha):", line))


EXPECTED = f"""---
title: Ska
genres: [ska, ska punk]
links: [https://en.wikipedia.org/wiki/Ska, https://en.wikipedia.org/wiki/Ska_punk]
status: draft
---

# Ska

## Ska

{SKA}

## Ska punk

{SKA_PUNK}

---
*Draft. Introductions from Wikipedia ([Ska](https://en.wikipedia.org/wiki/Ska), [Ska punk](https://en.wikipedia.org/wiki/Ska_punk)), CC BY-SA 4.0.*
"""


# -- the split ----------------------------------------------------------------------------------------------------

def test_the_split_is_only_a_candidate():
    assert split_genre_name("Ska & Ska Punk") == ["Ska", "Ska Punk"]
    assert split_genre_name("Ska and Ska Punk") == ["Ska", "Ska Punk"]
    assert split_genre_name("Ska, Ska punk / Reggae + Dub") == ["Ska", "Ska punk", "Reggae", "Dub"]
    assert split_genre_name("Ska") == ["Ska"] and split_genre_name("Rock and Roll") == ["Rock", "Roll"]  # (then not verified)
    assert split_genre_name("") == [""]


def test_the_credit_for_several_pages_is_one_sentence():
    assert wikipedia_credits([("Ska", "https://en.wikipedia.org/wiki/Ska"), ("Ska punk", "https://en.wikipedia.org/wiki/Ska_punk")]) == (
        "Introductions from Wikipedia ([Ska](https://en.wikipedia.org/wiki/Ska), [Ska punk](https://en.wikipedia.org/wiki/Ska_punk)), "
        "CC BY-SA 4.0")
    assert wikipedia_credits([("Ska", "https://en.wikipedia.org/wiki/Ska")]).startswith("Introduction from Wikipedia ([Ska]")


# -- the engine ---------------------------------------------------------------------------------------------------

def test_a_name_that_names_two_genres_is_split_when_both_verify_and_merged(tmp_path):
    genre_folder(tmp_path, "Ska & Ska Punk")
    proposal, wiki, _engine = proposal_for(tmp_path, "Ska & Ska Punk")
    assert proposal.confidence.value == "confident" and proposal.genres == ["Ska", "Ska Punk"]
    meta, body = parse_front_matter(proposal.note_text)
    assert meta["title"] == "Ska & Ska Punk" and meta["genres"] == ["ska", "ska punk"]
    assert "## Ska\n\n" in body and "## Ska punk\n\n" in body and body.count("Introductions from Wikipedia") == 1


def test_the_merged_note_has_exactly_the_shape_of_the_hand_made_sample(tmp_path):
    genre_folder(tmp_path, "1.61 - Ska")
    proposal, _wiki, _engine = proposal_for(tmp_path, "1.61 - Ska",
                                            lambda e: e.set_genres("1.61 - Ska", ["Ska", "Ska punk"]))
    assert proposal.confidence.value == "confident"
    assert without_stamp(proposal.note_text) == EXPECTED  # byte for byte, the stamp lines aside
    meta = parse_front_matter(proposal.note_text)[0]
    assert meta["generator"].startswith("mp3redactor") and meta["note-format"] == "2" and len(meta["body-sha"]) == 16


@pytest.mark.parametrize("key", ["1.61 - Ska", "Ska", "ska"])
def test_the_genres_can_be_given_by_the_raw_or_the_cleaned_name(tmp_path, key):
    genre_folder(tmp_path, "1.61 - Ska")
    proposal, _w, _e = proposal_for(tmp_path, "1.61 - Ska", lambda e: e.set_genres(key, ["Ska", "Ska punk"]))
    assert without_stamp(proposal.note_text) == EXPECTED


def test_the_genres_can_be_given_by_path(tmp_path):
    folder = genre_folder(tmp_path, "1.61 - Ska")
    proposal, _w, _e = proposal_for(tmp_path, "1.61 - Ska", lambda e: e.set_genres(str(folder), ["Ska", "Ska punk"]))
    assert without_stamp(proposal.note_text) == EXPECTED


def test_a_single_genre_keeps_todays_shape(tmp_path):
    genre_folder(tmp_path, "1.61 - Ska")
    proposal, wiki, _e = proposal_for(tmp_path, "1.61 - Ska")
    meta, body = parse_front_matter(proposal.note_text)
    assert meta["title"] == "Ska" and "genres" not in meta and meta["links"] == ["https://en.wikipedia.org/wiki/Ska"]
    assert "## " not in body and body.startswith("# Ska\n\nSka is a music genre") and proposal.genres == ["Ska"]
    assert body.rstrip().endswith("*Draft. Introduction from Wikipedia ([Ska](https://en.wikipedia.org/wiki/Ska), CC BY-SA 4.0).*")
    assert not any("Ska+punk" in u for u in wiki.urls)


@pytest.mark.parametrize("name,title", [("Rock and Roll", "Rock and roll"), ("Drum and Bass", "Drum and bass")])
def test_a_genre_whose_name_has_an_and_stays_whole_when_the_parts_are_no_genres(tmp_path, name, title):
    genre_folder(tmp_path, name)
    proposal, _w, _e = proposal_for(tmp_path, name)
    assert proposal.confidence.value == "confident" and proposal.genres == [name]
    body = parse_front_matter(proposal.note_text)[1]
    assert "## " not in body and f"[{title}](" in body  # one section, the whole name's own page


def test_one_genre_that_cannot_be_verified_makes_the_row_uncertain_and_the_reasons_name_it(tmp_path):
    genre_folder(tmp_path, "1.61 - Ska")
    proposal, _w, _e = proposal_for(tmp_path, "1.61 - Ska", lambda e: e.set_genres("Ska", ["Ska", "Nonsense genre"]))
    assert proposal.confidence.value == "uncertain" and not proposal.note_text
    reasons = " | ".join(proposal.reasons)
    assert "no Wikipedia page about a music genre called 'Nonsense genre' could be verified" in reasons
    assert "verified: 'Ska'" in reasons and "called 'Ska' could not" not in reasons


# -- the review page ----------------------------------------------------------------------------------------------

def test_the_review_page_edits_the_genres_and_rebuilds_the_row_in_place(window, tmp_path, boxes):
    root = tmp_path / "Music"
    genre_folder(root, "1.61 - Ska")
    genre_folder(root, "Reggae")
    window.settings.last_directory = str(root)
    window.settings.include_subfolders = True
    window._load_paths([root / "1.61 - Ska" / "Some Artist"])
    window.notes_fetchers = lambda: Fetchers(mb=nothing_else(), wiki=genre_wiki())
    seen = {}

    def scenario():
        yield from wait(lambda: scope_dialog() is not None, "the scope dialog")
        dlg = scope_dialog()
        next(r for r, p in dlg.radios if p == str(root)).click()
        set_kinds(dlg, "genre")
        click(dlg.continue_button)
        yield from wait(lambda: review() is not None, "the review page")
        page = review()
        tree = page.tree if page.view == "list" else None
        index = next(i for i, it in enumerate(page.session.items) if it.proposal.folder.name == "1.61 - Ska")
        page.index = index
        if tree is not None:
            from PyQt6.QtCore import Qt

            row = next(tree.topLevelItem(r) for r in range(tree.topLevelItemCount())
                       if tree.topLevelItem(r).data(0, Qt.ItemDataRole.UserRole) == index)
            tree.setCurrentItem(row)
            seen["rows_before"] = [tree.topLevelItem(r) for r in range(tree.topLevelItemCount())]
        panel = page.list_panel if page.view == "list" else page.one_panel
        if page.view != "list":
            page._show_one()
        seen["view"] = page.view
        seen["prefilled"] = panel.genres_edit.text()
        seen["visible"] = panel.genres_box.isVisibleTo(panel)
        panel.genres_edit.setText("Ska, Ska punk")
        calls = []
        real = page._fill_tree
        page._fill_tree = lambda: (calls.append(1), real())[1]
        click(panel.genres_button)
        QApplication.processEvents()
        item = page.session.items[index]
        seen["genres"] = item.proposal.genres
        seen["text"] = item.proposal.note_text
        seen["after_edit"] = panel.genres_edit.text()
        seen["calls"] = calls
        if tree is not None:
            seen["rows_after"] = [tree.topLevelItem(r) for r in range(tree.topLevelItemCount())]
        seen["count"] = len(page.session.items)
        page.done(0)

    drive(scenario, window.action_registry["create_notes"].trigger)
    assert seen["visible"] and seen["prefilled"] == "Ska"
    assert seen["genres"] == ["Ska", "Ska punk"] and "## Ska punk" in seen["text"] and seen["after_edit"] == "Ska, Ska punk"
    assert seen["count"] == 2 and seen["calls"] == []
    if "rows_before" in seen:
        assert seen["rows_after"] == seen["rows_before"]  # in place: the same rows, nothing added


# -- the command line ---------------------------------------------------------------------------------------------------

def test_cli_genres_writes_the_merged_note_and_checks_its_argument(tmp_path, monkeypatch, capsys):
    from core.settings import Settings
    from mp3cli import cmd_notes, files as cli_files
    from mp3cli.main import main
    from redactor_common.cli import CliError

    monkeypatch.setattr(cli_files, "load_settings", lambda *a, **k: Settings())
    monkeypatch.setattr(cmd_notes, "make_fetchers", lambda use: Fetchers(mb=nothing_else(), wiki=genre_wiki()))
    music = tmp_path / "780 - Music"
    genre_folder(music, "1.61 - Ska")
    code = main(["notes", str(music), "--kind", "genre", "--write", "--no-cache", "--genres", "1.61 - Ska=Ska,Ska punk"])
    assert code == 0
    written = (music / "1.61 - Ska" / "about.md").read_text(encoding="utf-8")
    assert without_stamp(written) == EXPECTED
    with pytest.raises(CliError):
        main(["notes", str(music), "--kind", "genre", "--genres", "no equals sign"])
