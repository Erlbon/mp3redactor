"""Create Notes scope: Tools > Create Notes always asks which folder (a leftover selection is only a suggestion),
a file selection proposes the artist note AND the album note, one run never leaks into the next, the list view's
'Tick best guesses', and the CLI's --include-uncertain. Real main window, real tagged MP3s, recorded replies."""

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtWidgets import QApplication, QFileDialog, QLabel  # noqa: E402

from core.notes_flow import build_proposals, scan, selection_scope  # noqa: E402
from core.notes_local import KIND_ALBUM, KIND_ARTIST  # noqa: E402
from core.settings import Settings  # noqa: E402
from gui.notes_dialog import NotesScopeDialog  # noqa: E402
from mp3cli import cmd_notes, files as cli_files  # noqa: E402
from mp3cli.main import main  # noqa: E402
from tests.notes_helpers import cristina_fetchers, make_mp3, ultravox_fetchers  # noqa: E402
from tests.test_cover_gui import window  # noqa: E402,F401
from tests.test_notes_gui import Script, boxes, click, review, vienna, wait  # noqa: E402,F401


def scope_dialog():
    found = [w for w in QApplication.topLevelWidgets() if isinstance(w, NotesScopeDialog) and w.isVisible()]
    return found[0] if found else None


def library(tmp_path):
    vienna(tmp_path / "Ultravox")
    make_mp3(tmp_path / "Artist B" / "Album B (1990)" / "01 b.mp3", "b", "Artist B", "Album B", 1, "1990")
    return tmp_path


def select_files(window, name):
    """Selects exactly the loaded files whose path contains `name` (table rows are not in load order)."""
    window._reselect_files({id(m) for m in window.files if name in str(m.path)})
    QApplication.processEvents()
    selected = window._selected_files()
    assert selected and all(name in str(m.path) for m in selected)


def load_and_select(window, root, name):
    window._load_paths([root / "Ultravox", root / "Artist B"])
    select_files(window, name)


def kinds(dlg):
    return [(i.proposal.kind, i.proposal.folder.name) for i in dlg.session.items]


def drive(scenario, trigger):
    script = Script(scenario())
    trigger()
    script.timer.stop()
    assert script.error == "", script.error
    assert script.done


# -- Tools > Create Notes asks for the scope ---------------------------------------------------------------

def test_tools_menu_never_uses_a_leftover_selection_it_asks_the_scope_first(window, tmp_path, boxes):
    root = library(tmp_path)
    load_and_select(window, root, "Ultravox")  # the leftover selection of the previous artist
    window.notes_fetchers = lambda: ultravox_fetchers()
    seen = {}

    def scenario():
        yield from wait(lambda: scope_dialog() is not None, "the scope dialog")
        dlg = scope_dialog()
        seen["title"] = dlg.windowTitle()
        seen["suggestion"] = dlg.suggestion_button.text()
        assert review() is None  # nothing was looked up from the selection
        click(dlg.cancel_button)

    drive(scenario, window.action_registry["create_notes"].trigger)
    assert "which folder" in seen["title"]
    assert seen["suggestion"].startswith("Use the artist folder of the selected files:")
    assert str(root / "Ultravox") in seen["suggestion"]


def test_scope_choose_another_folder_beats_the_selection(window, monkeypatch, tmp_path, boxes):
    root = library(tmp_path)
    load_and_select(window, root, "Ultravox")
    window.settings.include_subfolders = True
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: str(root / "Artist B")))
    window.notes_fetchers = lambda: cristina_fetchers()
    result = {}

    def scenario():
        yield from wait(lambda: scope_dialog() is not None, "the scope dialog")
        click(scope_dialog().choose_button)
        yield from wait(lambda: review() is not None, "the review page")
        result["folders"] = [str(i.proposal.folder) for i in review().session.items]
        review().done(0)

    drive(scenario, window.action_registry["create_notes"].trigger)
    assert result["folders"] and all("Artist B" in f and "Ultravox" not in f for f in result["folders"])
    assert window.settings.notes_last_folder == str(root / "Artist B")


@pytest.mark.parametrize("subfolders", [True, False])
def test_scope_suggestion_states_and_honours_include_subfolders(window, tmp_path, boxes, subfolders):
    root = library(tmp_path)
    load_and_select(window, root, "Ultravox")
    window.settings.include_subfolders = subfolders
    window.notes_fetchers = lambda: ultravox_fetchers()
    result = {}

    def scenario():
        yield from wait(lambda: scope_dialog() is not None, "the scope dialog")
        result["text"] = " ".join(label.text() for label in scope_dialog().findChildren(QLabel))
        click(scope_dialog().suggestion_button)
        yield from wait(lambda: review() is not None, "the review page")
        result["kinds"] = kinds(review())
        review().done(0)

    drive(scenario, window.action_registry["create_notes"].trigger)
    if subfolders:
        assert "including subfolders" in result["text"]
        assert result["kinds"] == [("artist", "Ultravox"), ("album", "Vienna (1980)")]
    else:
        assert "only the folder itself" in result["text"]
        assert result["kinds"] == [("artist", "Ultravox")]


# -- the right-click path ----------------------------------------------------------------------------------

def test_file_selection_proposes_the_artist_note_and_the_album_note(window, tmp_path, boxes):
    root = library(tmp_path)
    load_and_select(window, root, "01 Astradyne")  # ONE file of ONE album
    window.notes_fetchers = lambda: ultravox_fetchers()
    result = {}

    def scenario():
        yield from wait(lambda: review() is not None, "the review page")
        result["kinds"] = kinds(review())
        artist = next(i for i in review().session.items if i.proposal.kind == "artist")
        result["artist_text"] = artist.proposal.note_text
        review().done(0)

    drive(scenario, window.create_notes_for_selection)
    assert result["kinds"] == [("artist", "Ultravox"), ("album", "Vienna (1980)")]
    assert result["artist_text"].startswith("---\ntitle: Ultravox")


def test_two_runs_in_one_window_do_not_leak_the_first_artist_into_the_second(window, tmp_path, boxes):
    root = library(tmp_path)
    make_mp3(root / "Cristina" / "Cristina (1980)" / "01 a.mp3", "a", "Cristina", "Cristina", 1, "1980")
    window._load_paths([root / "Ultravox", root / "Cristina"])
    outcomes = []

    def run_for(name, fetchers):
        select_files(window, name)
        window.notes_fetchers = lambda: fetchers

        def scenario():
            yield from wait(lambda: review() is not None, "the review page")
            outcomes.append([(i.proposal.folder.name, i.proposal.note_text, [c.name for c in i.proposal.candidates],
                              i.proposal.reasons, i.proposal.problems) for i in review().session.items])
            review().done(0)

        drive(scenario, window.create_notes_for_selection)

    run_for("Ultravox", ultravox_fetchers())
    cristina = cristina_fetchers(with_discogs=False)
    cristina.mb.routes.append((r"ws/2/release-group\?query=", {"release-groups": []}))
    run_for("Cristina", cristina)
    first, second = outcomes
    assert any("Ultravox" in text for _f, text, *_rest in first)
    blob = repr(second).lower()
    assert "ultravox" not in blob and "vienna" not in blob
    assert {name for name, *_rest in second} == {"Cristina", "Cristina (1980)"}


def test_two_engines_in_one_process_are_independent(tmp_path):
    vienna(tmp_path / "Ultravox")
    make_mp3(tmp_path / "Cristina" / "Cristina (1980)" / "01 a.mp3", "a", "Cristina", "Cristina", 1, "1980")
    first = build_proposals(scan([tmp_path / "Ultravox"], False, kinds=[KIND_ARTIST]).targets, ultravox_fetchers())
    second = build_proposals(scan([tmp_path / "Cristina"], False, kinds=[KIND_ARTIST]).targets,
                             cristina_fetchers(with_discogs=False))
    assert "Ultravox" in first[0].note_text
    assert "ultravox" not in repr([p.to_dict() for p in second]).lower()


# -- selection_scope -------------------------------------------------------------------------------------

def folders(tmp_path, *files):
    return [str(p.relative_to(tmp_path)).replace("\\", "/") for p in selection_scope([tmp_path / f for f in files])]


def test_selection_scope_album_plus_artist_disc_folders_and_loose_tracks(tmp_path):
    vienna(tmp_path / "Ultravox")
    make_mp3(tmp_path / "Ultravox" / "Quartet (1982)" / "Disc 2" / "01 a.mp3", "a", "Ultravox", "Quartet", 1, "1982")
    make_mp3(tmp_path / "Ultravox" / "Quartet (1982)" / "Disc 1" / "01 a.mp3", "a", "Ultravox", "Quartet", 1, "1982")
    make_mp3(tmp_path / "Ultravox" / "Single.mp3", "Single", "Ultravox", "", 0, "")
    assert folders(tmp_path, "Ultravox/Vienna (1980)/01 Astradyne.mp3") == ["Ultravox/Vienna (1980)", "Ultravox"]
    assert folders(tmp_path, "Ultravox/Quartet (1982)/Disc 2/01 a.mp3") == ["Ultravox/Quartet (1982)", "Ultravox"]
    assert folders(tmp_path, "Ultravox/Single.mp3") == ["Ultravox"]
    # two albums of the artist: the artist once
    assert folders(tmp_path, "Ultravox/Vienna (1980)/01 Astradyne.mp3", "Ultravox/Quartet (1982)/Disc 1/01 a.mp3") == [
        "Ultravox/Vienna (1980)", "Ultravox", "Ultravox/Quartet (1982)"]


def test_selection_scope_never_proposes_an_artist_note_for_a_collection_or_a_genre_folder(tmp_path):
    for name in ("Various Artists", "Soundtracks"):
        make_mp3(tmp_path / name / "Hits (1999)" / "01 a.mp3", "a", "Someone", "Hits", 1, "1999")
        assert folders(tmp_path, f"{name}/Hits (1999)/01 a.mp3") == [f"{name}/Hits (1999)"]
    # a genre folder holds artist folders, not albums: the artist is above the album, the genre is not offered
    make_mp3(tmp_path / "Rock" / "Ultravox" / "Vienna (1980)" / "01 a.mp3", "a", "Ultravox", "Vienna", 1, "1980")
    assert folders(tmp_path, "Rock/Ultravox/Vienna (1980)/01 a.mp3") == ["Rock/Ultravox/Vienna (1980)", "Rock/Ultravox"]
    # albums tagged by someone else than the folder name: the level is not clear, no artist note
    make_mp3(tmp_path / "Mystery" / "Unknown Thing" / "01 a.mp3", "a", "Nobody", "Unknown Thing")
    assert folders(tmp_path, "Mystery/Unknown Thing/01 a.mp3") == ["Mystery/Unknown Thing"]


def test_selection_scope_leaves_out_folders_that_already_have_a_note(tmp_path):
    vienna(tmp_path / "Ultravox")
    (tmp_path / "Ultravox" / "Artist.md").write_text("mine\n", encoding="utf-8")
    found = scan(selection_scope([tmp_path / "Ultravox" / "Vienna (1980)" / "01 Astradyne.mp3"]),
                 include_subfolders=False, kinds=[KIND_ARTIST, KIND_ALBUM])
    assert [t.kind for t in found.targets] == ["album"] and [s.kind for s in found.skipped] == ["artist"]


# -- Tick best guesses ---------------------------------------------------------------------------------------

def friends_library(tmp_path):
    friends = tmp_path / "Rock" / "Ultravox & Friends"
    for album, year in (("Vienna", "1980"), ("Quartet", "1982")):
        make_mp3(friends / f"{album} ({year})" / "01 a.mp3", "a", "Ultravox & Friends", album, 1, year)
    vienna(tmp_path / "Rock" / "Ultravox")
    make_mp3(tmp_path / "Rock" / "Mystery" / "Unknown Thing" / "01 a.mp3", "a", "Nobody", "Unknown Thing")
    return friends


def test_tick_best_guesses_ticks_uncertain_with_candidate_and_clear_level_only_and_writes_nothing(
        window, monkeypatch, tmp_path, boxes):
    friends = friends_library(tmp_path)
    window.notes_fetchers = lambda: ultravox_fetchers()
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: str(tmp_path / "Rock")))
    window.settings.include_subfolders = True
    result = {}

    def scenario():
        yield from wait(lambda: scope_dialog() is not None, "the scope dialog")
        click(scope_dialog().choose_button)
        yield from wait(lambda: review() is not None, "the review page")
        dlg = review()
        by = {i.proposal.folder.name: i for i in dlg.session.items if i.proposal.kind == "artist"}
        assert not by["Ultravox & Friends"].ticked and not by["Mystery"].proposal.writable
        assert "RISK" in dlg.tick_guesses_button.toolTip()
        click(dlg.tick_guesses_button)
        yield from wait(lambda: by["Ultravox & Friends"].proposal.writable, "the guess was built", 20)
        result["friends_ticked"] = by["Ultravox & Friends"].ticked
        result["mystery_ticked"] = by["Mystery"].ticked
        result["written_before_click"] = (friends / "artist.md").exists()
        assert "draft" in by["Ultravox & Friends"].proposal.note_text.split("---")[1]
        click(dlg.write_ticked_button)
        dlg.done(0)

    drive(scenario, window.action_registry["create_notes"].trigger)
    assert result == {"friends_ticked": True, "mystery_ticked": False, "written_before_click": False}
    assert (friends / "artist.md").is_file() and not (tmp_path / "Rock" / "Mystery" / "artist.md").exists()


# -- the CLI --include-uncertain ---------------------------------------------------------------------------

@pytest.fixture
def cli(monkeypatch, capsys):
    monkeypatch.setattr(cli_files, "load_settings", lambda *a, **k: Settings())
    monkeypatch.setattr(cmd_notes, "make_fetchers", lambda use: ultravox_fetchers())

    def run(*argv):
        code = main(list(argv))
        return code, capsys.readouterr().out

    return run


def test_include_uncertain_writes_only_uncertain_with_a_candidate_and_never_over_a_note(tmp_path, cli):
    friends = friends_library(tmp_path)
    (tmp_path / "Rock" / "Ultravox" / "ARTIST.md").write_text("mine\n", encoding="utf-8")
    cli("notes", str(tmp_path), "--kind", "artist", "--write")
    assert not (friends / "artist.md").exists()  # without the flag: only the confident ones
    code, out = cli("notes", str(tmp_path), "--kind", "artist", "--write", "--include-uncertain")
    assert code == 0
    text = (friends / "artist.md").read_text(encoding="utf-8")
    assert "status: draft" in text.split("---")[1]
    assert not (tmp_path / "Rock" / "Mystery" / "artist.md").exists()  # unclear level: listed, not written
    assert "the level is unclear" in out and "from an uncertain match" in out
    assert (tmp_path / "Rock" / "Ultravox" / "ARTIST.md").read_text(encoding="utf-8") == "mine\n"
    assert sorted(p.name.lower() for p in (tmp_path / "Rock" / "Ultravox").glob("*.md")) == ["artist.md"]


def test_include_uncertain_json_marks_the_guesses_and_a_dry_run_writes_nothing(tmp_path, cli):
    friends = friends_library(tmp_path)
    code, out = cli("notes", str(tmp_path), "--kind", "artist", "--write", "--include-uncertain", "--dry-run", "--json")
    document = json.loads(out)
    rows = {os.path.basename(r["folder"]): r for r in document["results"] if "folder" in r}
    assert not (friends / "artist.md").exists()
    assert rows["Ultravox & Friends"]["from_uncertain"] is True and rows["Ultravox & Friends"]["status"] == "would-write"
    assert rows["Mystery"]["status"] == "review" and rows["Mystery"]["from_uncertain"] is False
    assert document["from_uncertain"] == 1
