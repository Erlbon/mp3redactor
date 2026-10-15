"""'What to create' as checkboxes: ticking only 'Genre / collection notes' looks up nothing about artists or albums and reads no
tags (only the folder structure is walked, then about one Wikipedia lookup per genre folder). Real scope dialog (offscreen,
QTest), the real engine, counted fetcher calls and counted tag reads."""

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from core import scan_service  # noqa: E402
from core.notes_flow import Fetchers, NotesEngine, build_proposals, scan  # noqa: E402
from core.notes_kinds import kinds_text, parse_kinds  # noqa: E402
from core.notes_scope import kinds_sentence  # noqa: E402
from core.notes_timing import scope_hint  # noqa: E402
from tests.notes_helpers import FakeFetch, make_mp3, set_kinds  # noqa: E402
from tests.test_cover_gui import window  # noqa: E402,F401
from tests.test_notes_gui import boxes, click, review, wait  # noqa: E402,F401
from tests.test_notes_scope import drive, scope_dialog  # noqa: E402,F401


def ska_tree(root):
    """Bands/Ska/<3 artists>/<2 albums each>, and Bands/D/<an artist>/<an album>: an alphabetical grouping."""
    for artist in ("Specials", "Madness", "Selecter"):
        for album in ("First (1979)", "Second (1980)"):
            make_mp3(root / "Bands" / "Ska" / artist / album / "01 a.mp3", "a", artist, album.split(" (")[0], 1, album[-5:-1])
    make_mp3(root / "Bands" / "D" / "Dub Band" / "Only (1990)" / "01 a.mp3", "a", "Dub Band", "Only", 1, "1990")
    return root / "Bands"


def genre_wiki():
    return FakeFetch([
        (r"search/title\?q=Ska", {"pages": [{"title": "Ska", "description": "Music genre",
                                             "excerpt": "Ska is a music genre that originated in Jamaica"}]}),
        (r"search/title", {"pages": []}),
        (r"page/summary/Ska$", {"type": "standard", "title": "Ska", "description": "Music genre",
                                "extract": "Ska is a music genre that originated in Jamaica in the late 1950s.",
                                "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Ska"}}}),
        (r"page/summary/", None),
    ])


def nothing_else():
    """MusicBrainz and Discogs that fail the test if they are asked anything."""
    def boom(request):
        raise AssertionError(f"an artist / album lookup was made: {request}")

    return boom


class TagReads:
    def __init__(self, monkeypatch):
        self.count = 0
        real = scan_service.load_tags

        def counting(mp3, *a, **k):
            self.count += 1
            return real(mp3, *a, **k)

        monkeypatch.setattr(scan_service, "load_tags", counting)


# -- the words ----------------------------------------------------------------------------------------------------

def test_kinds_are_parsed_and_the_old_words_still_work():
    assert parse_kinds("artist,genre") == ("artist", "genre") and parse_kinds("genre") == ("genre",)
    assert parse_kinds("both") == ("artist", "album") and parse_kinds("all") == ("artist", "album", "genre")
    assert parse_kinds(("genre", "artist")) == ("artist", "genre") and parse_kinds("") == ("artist",)
    assert parse_kinds("nonsense") == ("artist",) and kinds_text(("album", "artist")) == "artist,album"
    assert kinds_sentence(("genre",)).startswith("Looked up: genre / collection notes only (no artist or album lookups")
    assert kinds_sentence(("artist", "genre")) == "Looked up: artist and genre / collection notes."


def test_the_hint_for_genre_only_says_how_many_folders_and_how_long():
    assert scope_hint(500, 900, 4, "genre") == "About 4 genre / collection folders: about 2 s (one Wikipedia lookup each, no tags read)."
    assert scope_hint(500, 900, 1, "genre").startswith("About 1 genre / collection folder: about 1 s")
    assert scope_hint(500, 900, 0, "genre") == ""
    assert "artist notes only is about" in scope_hint(300, 400, 0, "artist,album")  # unchanged for album notes


# -- the engine ---------------------------------------------------------------------------------------------------

def test_genre_only_on_the_parent_makes_only_wikipedia_requests_and_reads_no_tags(tmp_path, monkeypatch):
    bands = ska_tree(tmp_path)
    reads = TagReads(monkeypatch)
    wiki = genre_wiki()
    fetchers = Fetchers(mb=nothing_else(), wiki=wiki)
    found = scan([bands], kinds=("genre",))
    assert [(t.kind, t.folder.name) for t in found.targets] == [("genre", "Ska")]  # D is a grouping, Bands the root
    assert [g.folder.name for g in found.groupings] == ["D"]
    engine = NotesEngine(fetchers)
    [proposal] = build_proposals(found.targets, engine=engine)
    assert proposal.confidence.value == "confident" and proposal.note_kind == "about" and "Jamaica" in proposal.note_text
    assert len(wiki.urls) == 2  # the search and the summary: about one Wikipedia lookup for the folder
    assert reads.count == 0 and engine.phases.count("tags") == 0 and engine.phases.count("musicbrainz") == 0


def test_the_command_line_takes_the_same_in_combination(tmp_path, monkeypatch, capsys):
    from core.settings import Settings
    from mp3cli import cmd_notes, files as cli_files
    from mp3cli.main import main

    monkeypatch.setattr(cli_files, "load_settings", lambda *a, **k: Settings())
    bands = ska_tree(tmp_path)
    reads = TagReads(monkeypatch)
    wiki = genre_wiki()
    monkeypatch.setattr(cmd_notes, "make_fetchers", lambda use: Fetchers(mb=nothing_else(), wiki=wiki))
    code = main(["notes", str(bands), "--kind", "genre", "--json", "--no-cache"])
    document = json.loads(capsys.readouterr().out)
    assert code == 0 and [r["kind"] for r in document["results"] if r["status"] != "grouping"] == ["genre"]
    assert document["requests"] == 2 and reads.count == 0
    with pytest.raises(SystemExit) as usage:  # argparse: an unknown word is a usage error
        main(["notes", str(bands), "--kind", "bogus"])
    assert usage.value.code == 2
    capsys.readouterr()
    parsed = cmd_notes.kind_argument("artist,genre")
    assert parsed == ("artist", "genre") and cmd_notes.kind_argument("both") == ("artist", "album")


# -- the real scope dialog ----------------------------------------------------------------------------------------

def test_ticking_only_genre_notes_in_the_dialog_does_no_artist_or_album_work(window, tmp_path, monkeypatch, boxes):
    bands = ska_tree(tmp_path)
    window.settings.last_directory = str(bands)
    window.settings.include_subfolders = True
    window._load_paths([bands / "Ska" / "Specials"])
    reads = TagReads(monkeypatch)  # counted from here on: the window's own loading is done
    wiki = genre_wiki()
    window.notes_fetchers = lambda: Fetchers(mb=nothing_else(), wiki=wiki)
    seen = {}

    def scenario():
        yield from wait(lambda: scope_dialog() is not None, "the scope dialog")
        dlg = scope_dialog()
        next(r for r, p in dlg.radios if p == str(bands)).click()
        seen["default"] = [k for k, b in dlg.kind_boxes.items() if b.isChecked()]
        for box in dlg.kind_boxes.values():
            box.setChecked(False)
        seen["nothing_ticked_continue"] = dlg.continue_button.isEnabled()
        dlg.kind_boxes["genre"].setChecked(True)
        yield from wait(lambda: dlg.counts is not None, "the folder count", 30)
        seen["hint"] = (dlg.hint_label.isVisibleTo(dlg), dlg.hint_label.text())
        click(dlg.continue_button)
        yield from wait(lambda: review() is not None, "the review page")
        page = review()
        seen["items"] = [(i.proposal.kind, i.proposal.folder.name) for i in page.session.items]
        seen["banner"] = page.banner_label.text()
        page.done(0)

    drive(scenario, window.action_registry["create_notes"].trigger)
    assert seen["default"] == ["artist"] and seen["nothing_ticked_continue"] is False
    assert seen["hint"] == (True, "About 1 genre / collection folder: about 1 s (one Wikipedia lookup each, no tags read).")
    assert seen["items"] == [("genre", "Ska")]
    assert "genre / collection notes only" in seen["banner"] and "Looked up 1 folder" in seen["banner"]
    assert reads.count == 0 and len(wiki.urls) == 2  # no tag read, two Wikipedia requests, no MusicBrainz / Discogs call
    assert window.settings.notes_kinds == "genre"

    def again():
        yield from wait(lambda: scope_dialog() is not None, "the scope dialog")
        seen["remembered"] = [k for k, b in scope_dialog().kind_boxes.items() if b.isChecked()]
        click(scope_dialog().cancel_button)

    drive(again, window.action_registry["create_notes"].trigger)
    assert seen["remembered"] == ["genre"]  # the last choice is remembered


def test_the_combinations_reach_the_scan(window, tmp_path, boxes):
    bands = ska_tree(tmp_path)
    window.settings.last_directory = str(bands)
    window.settings.include_subfolders = True
    window._load_paths([bands / "Ska" / "Specials"])
    window.notes_fetchers = lambda: Fetchers(mb=FakeFetch([(r".", {"artists": [], "release-groups": []})]), wiki=genre_wiki())
    seen = {}

    def scenario():
        yield from wait(lambda: scope_dialog() is not None, "the scope dialog")
        dlg = scope_dialog()
        next(r for r, p in dlg.radios if p == str(bands)).click()
        set_kinds(dlg, "artist,genre")
        click(dlg.continue_button)
        yield from wait(lambda: review() is not None, "the review page")
        seen["kinds"] = sorted({i.proposal.kind for i in review().session.items})
        review().done(0)

    drive(scenario, window.action_registry["create_notes"].trigger)
    assert seen["kinds"] == ["artist", "genre"] and window.settings.notes_kinds == "artist,genre"
