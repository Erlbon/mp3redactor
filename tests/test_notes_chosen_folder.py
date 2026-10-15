"""The folder you CHOOSE gets its own note when it is eligible: choosing 'Ska' gives Ska/about.md (genre notes ticked), choosing an
artist folder gives artist.md, an album folder album.md. A generic place name (Music, Bands...) or an alphabetical grouping
gives the clear message, not 'no artist or album folders found'. Real engine, counted requests and tag reads, real dialog."""

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from core import scan_service  # noqa: E402
from core.notes_flow import Fetchers, NotesEngine, build_proposals, scan, write_proposal  # noqa: E402
from core.notes_local import LIBRARY_ROOT_MESSAGE  # noqa: E402
from tests.notes_helpers import make_mp3, ultravox_fetchers, ultravox_library  # noqa: E402
from tests.test_cover_gui import window  # noqa: E402,F401
from tests.test_notes_genre_only import TagReads, genre_wiki, nothing_else, ska_tree  # noqa: E402
from tests.test_notes_gui import boxes, click, review, wait  # noqa: E402,F401
from tests.test_notes_scope import drive, scope_dialog  # noqa: E402,F401


def targets_of(folder, kinds):
    return [(t.kind, t.folder.name) for t in scan([folder], kinds=kinds).targets]


def test_the_ska_folder_itself_gets_its_about_md_with_two_requests_and_no_tag_reads(tmp_path, monkeypatch):
    ska = ska_tree(tmp_path) / "Ska"
    reads = TagReads(monkeypatch)
    wiki = genre_wiki()
    assert targets_of(ska, ("genre",)) == [("genre", "Ska")]  # before: 'no artist or album folders found'
    engine = NotesEngine(Fetchers(mb=nothing_else(), wiki=wiki))
    [proposal] = build_proposals(scan([ska], kinds=("genre",)).targets, engine=engine)
    assert proposal.confidence.value == "confident" and write_proposal(proposal).ok
    assert sorted(p.name for p in ska.iterdir() if p.is_file()) == ["about.md"]  # only Ska/about.md
    assert not (ska / "Specials" / "artist.md").exists()
    assert len(wiki.urls) == 2 and reads.count == 0 and engine.phases.count("tags") == 0


def test_genre_only_on_the_parent_finds_ska_and_leaves_the_grouping_out(tmp_path):
    bands = ska_tree(tmp_path)  # Bands/Ska/..., Bands/D/Dub Band/...
    found = scan([bands], kinds=("genre",))
    assert [(t.kind, t.folder.name) for t in found.targets] == [("genre", "Ska")]
    assert [g.folder.name for g in found.groupings] == ["D"]  # the D-style grouping: no note, no proposal


def test_a_chosen_artist_folder_gets_its_artist_note_and_an_album_folder_its_album_note(tmp_path):
    artist = ultravox_library(tmp_path)
    assert targets_of(artist, ("artist",)) == [("artist", "Ultravox")]
    assert sorted(targets_of(artist, ("artist", "album"))) == [("album", "Quartet (1982)"), ("album", "Vienna (1980)"), ("artist", "Ultravox")]
    assert targets_of(artist / "Vienna (1980)", ("album",)) == [("album", "Vienna (1980)")]
    assert targets_of(artist, ("genre",)) == []  # genre notes only for a folder that holds artist folders


def test_a_chosen_folder_that_holds_artists_gets_a_genre_note_unless_its_name_is_generic(tmp_path):
    for name in ("Rock", "Music", "Bands", "Artists", "Downloads", "780 - Music", "0 - Bands", "Library", "Media"):
        make_mp3(tmp_path / name / "Some Artist" / "Album (2000)" / "01 a.mp3", "a", "Some Artist", "Album", 1, "2000")
    eligible = {name: targets_of(tmp_path / name, ("genre",)) for name in os.listdir(tmp_path)}
    assert eligible["Rock"] == [("genre", "Rock")]
    assert all(v == [] for k, v in eligible.items() if k != "Rock"), eligible


def test_a_library_root_says_so_instead_of_the_cryptic_message(tmp_path, capsys, monkeypatch):
    from core.settings import Settings
    from mp3cli import cmd_notes, files as cli_files
    from mp3cli.main import main
    from redactor_common.cli import CliError

    monkeypatch.setattr(cli_files, "load_settings", lambda *a, **k: Settings())
    monkeypatch.setattr(cmd_notes, "make_fetchers", lambda use: Fetchers(mb=nothing_else(), wiki=genre_wiki()))
    empty = tmp_path / "Empty"
    empty.mkdir()
    with pytest.raises(CliError) as why:
        main(["notes", str(empty), "--kind", "genre"])
    assert str(why.value) == LIBRARY_ROOT_MESSAGE and "no artist or album folders found" not in str(why.value)
    assert LIBRARY_ROOT_MESSAGE == ("This folder looks like a library root, not a genre: nothing to write here. "
                                    "Choose the genre folder itself or one level lower.")
    # the generic folder 'Music' holding artist folders, genre notes only: the same message (it is the root, not a genre)
    music = tmp_path / "Music"
    make_mp3(music / "Specials" / "First (1979)" / "01 a.mp3", "a", "Specials", "First", 1, "1979")
    with pytest.raises(CliError) as again:
        main(["notes", str(music), "--kind", "genre"])
    assert str(again.value) == LIBRARY_ROOT_MESSAGE
    # with a genre folder below it: that one is proposed, the root itself is not
    make_mp3(music / "Ska" / "Specials" / "First (1979)" / "01 a.mp3", "a", "Specials", "First", 1, "1979")
    assert main(["notes", str(music), "--kind", "genre", "--json", "--no-cache"]) == 0
    document = json.loads(capsys.readouterr().out)
    assert [os.path.basename(r["folder"]) for r in document["results"] if r["status"] == "would-write"] == ["Ska"]
    assert [r["status"] for r in document["results"] if os.path.basename(r["folder"]) == "Music"] == ["skipped"]  # said, not written


def test_the_dialog_says_what_each_folder_would_get_and_choosing_ska_writes_its_note(window, tmp_path, monkeypatch, boxes):
    bands = ska_tree(tmp_path)
    ska = bands / "Ska"
    window.settings.last_directory = str(ska)
    window.settings.include_subfolders = True
    window._load_paths([ska / "Specials"])
    reads = TagReads(monkeypatch)
    wiki = genre_wiki()
    window.notes_fetchers = lambda: Fetchers(mb=nothing_else(), wiki=wiki)
    seen = {}

    def scenario():
        yield from wait(lambda: scope_dialog() is not None, "the scope dialog")
        dlg = scope_dialog()
        yield from wait(lambda: all(path in dlg.infos for _r, path in dlg.radios), "the captions", 30)
        for box in dlg.kind_boxes.values():
            box.setChecked(False)
        dlg.kind_boxes["genre"].setChecked(True)
        by_path = {path: label.text() for (_r, path), label in zip(dlg.radios, dlg.captions)}
        seen["captions"] = by_path
        radio = next(r for r, p in dlg.radios if p == str(ska))
        radio.click()
        click(dlg.continue_button)
        yield from wait(lambda: review() is not None, "the review page")
        page = review()
        seen["items"] = [(i.proposal.kind, i.proposal.folder.name) for i in page.session.items]
        click(page.write_button)

    drive(scenario, window.action_registry["create_notes"].trigger)
    assert "would get a genre note: Ska" in seen["captions"][str(ska)]  # the folder you opened
    assert any("a library root" in text for path, text in seen["captions"].items() if path == str(bands))
    assert seen["items"] == [("genre", "Ska")]
    assert (ska / "about.md").is_file() and reads.count == 0 and len(wiki.urls) == 2


def test_the_dialog_message_for_a_library_root(window, tmp_path, boxes):
    music = tmp_path / "Music"
    make_mp3(music / "A" / "Album (2000)" / "01 a.mp3", "a", "A", "Album", 1, "2000")
    window.settings.last_directory = str(music)
    window.settings.include_subfolders = True
    window._load_paths([music / "A"])
    window.notes_fetchers = lambda: Fetchers()

    def scenario():
        yield from wait(lambda: scope_dialog() is not None, "the scope dialog")
        dlg = scope_dialog()
        next(r for r, p in dlg.radios if p == str(music)).click()
        for box in dlg.kind_boxes.values():
            box.setChecked(False)
        dlg.kind_boxes["genre"].setChecked(True)  # genre notes only, and 'Music' is a library root, not a genre
        click(dlg.continue_button)

    drive(scenario, window.action_registry["create_notes"].trigger)
    assert any(LIBRARY_ROOT_MESSAGE in text for _t, text in boxes)
