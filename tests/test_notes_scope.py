"""Create Notes scope: Tools > Create Notes always asks which folder (a leftover selection is only a suggestion),
a file selection proposes the artist note AND the album note, one run never leaks into the next, the list view's
'Tick best guesses', and the CLI's --include-uncertain. Real main window, real tagged MP3s, recorded replies."""

import json
import os
from pathlib import Path

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

def radio_paths(dlg):
    return [path for _radio, path in dlg.radios]


def test_nothing_selected_offers_the_opened_folder_ticked_so_enter_continues(window, tmp_path, boxes):
    """The complaint: with nothing selected the old dialog had only 'Choose another folder...' and Cancel."""
    root = library(tmp_path)
    window.settings.last_directory = str(root / "Ultravox")
    window._load_paths([root / "Ultravox"])
    window.table.clearSelection()
    QApplication.processEvents()
    assert not window._selected_files()
    window.settings.include_subfolders = True
    window.notes_fetchers = lambda: ultravox_fetchers()
    seen = {}

    def scenario():
        yield from wait(lambda: scope_dialog() is not None, "the scope dialog")
        dlg = scope_dialog()
        seen["paths"] = radio_paths(dlg)
        seen["captions"] = [c.text() for c in dlg.captions]
        seen["default"] = dlg.chosen_folder()
        seen["continue_is_default"] = dlg.continue_button.isDefault()
        click(dlg.continue_button)  # what Enter does on the default button
        yield from wait(lambda: review() is not None, "the review page")
        seen["folders"] = sorted(str(i.proposal.folder) for i in review().session.items)
        review().done(0)

    drive(scenario, window.action_registry["create_notes"].trigger)
    assert seen["paths"][:2] == [str(root / "Ultravox"), str(root)]
    assert seen["captions"][0].startswith("The folder you opened") and "loaded" in seen["captions"][0]
    assert seen["captions"][1].startswith("One level up")
    assert seen["default"] == str(root / "Ultravox") and seen["continue_is_default"]
    assert seen["folders"] and all(f.startswith(str(root / "Ultravox")) for f in seen["folders"])
    assert window.settings.notes_last_folder == str(root / "Ultravox")


def test_a_selection_is_only_a_choice_the_artist_folder_is_ticked_and_cancel_looks_nothing_up(window, tmp_path, boxes):
    root = library(tmp_path)
    window.settings.last_directory = str(root)
    load_and_select(window, root, "Ultravox")  # the leftover selection of the previous artist
    window.notes_fetchers = lambda: ultravox_fetchers()
    seen = {}

    def scenario():
        yield from wait(lambda: scope_dialog() is not None, "the scope dialog")
        dlg = scope_dialog()
        seen["title"] = dlg.windowTitle()
        seen["paths"] = radio_paths(dlg)
        seen["captions"] = [c.text() for c in dlg.captions]
        seen["default"] = dlg.chosen_folder()
        assert review() is None  # nothing was looked up from the selection
        click(dlg.cancel_button)

    drive(scenario, window.action_registry["create_notes"].trigger)
    assert "which folder" in seen["title"]
    assert seen["paths"][0] == str(root) and str(root / "Ultravox") in seen["paths"]
    assert str(root / "Ultravox" / "Vienna (1980)") in seen["paths"]
    i = seen["paths"].index(str(root / "Ultravox"))
    assert "artist of the selected files" in seen["captions"][i]
    assert seen["default"] == str(root / "Ultravox")
    assert window.settings.notes_last_folder == ""  # cancelled: nothing remembered


def test_scope_one_level_up_radio_looks_at_the_other_artists_too(window, tmp_path, boxes):
    root = library(tmp_path)
    window.settings.last_directory = str(root / "Ultravox")
    window._load_paths([root / "Ultravox"])
    window.table.clearSelection()
    window.settings.include_subfolders = True
    window.notes_fetchers = lambda: ultravox_fetchers()
    result = {}

    def scenario():
        yield from wait(lambda: scope_dialog() is not None, "the scope dialog")
        dlg = scope_dialog()
        radio = next(r for r, p in dlg.radios if p == str(root))
        radio.click()
        assert dlg.chosen_folder() == str(root)
        click(dlg.continue_button)
        yield from wait(lambda: review() is not None, "the review page")
        result["folders"] = {i.proposal.folder.name for i in review().session.items}
        review().done(0)

    drive(scenario, window.action_registry["create_notes"].trigger)
    assert {"Ultravox", "Artist B"} <= result["folders"]
    assert window.settings.notes_last_folder == str(root)


def test_scope_another_folder_opens_the_picker_and_beats_the_selection(window, monkeypatch, tmp_path, boxes):
    root = library(tmp_path)
    load_and_select(window, root, "Ultravox")
    window.settings.include_subfolders = True
    calls = []
    monkeypatch.setattr(QFileDialog, "getExistingDirectory",
                        staticmethod(lambda *a, **k: calls.append(1) or str(root / "Artist B")))
    window.notes_fetchers = lambda: cristina_fetchers()
    result = {}

    def scenario():
        yield from wait(lambda: scope_dialog() is not None, "the scope dialog")
        dlg = scope_dialog()
        dlg.other_radio.click()  # choosing the radio opens the picker
        result["picker_calls"] = len(calls)
        result["label"] = dlg.other_label.text()
        result["checked"] = dlg.chosen_folder()
        click(dlg.continue_button)
        yield from wait(lambda: review() is not None, "the review page")
        result["folders"] = [str(i.proposal.folder) for i in review().session.items]
        review().done(0)

    drive(scenario, window.action_registry["create_notes"].trigger)
    assert result["picker_calls"] == 1 and result["label"] == str(root / "Artist B")
    assert result["checked"] == str(root / "Artist B")
    assert result["folders"] and all("Artist B" in f and "Ultravox" not in f for f in result["folders"])
    assert window.settings.notes_last_folder == str(root / "Artist B")


def test_scope_cancelling_the_picker_goes_back_to_the_previous_choice(window, monkeypatch, tmp_path, boxes):
    root = library(tmp_path)
    window.settings.last_directory = str(root / "Ultravox")
    window._load_paths([root / "Ultravox"])
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: ""))
    seen = {}

    def scenario():
        yield from wait(lambda: scope_dialog() is not None, "the scope dialog")
        dlg = scope_dialog()
        dlg.other_radio.click()
        seen["checked"] = dlg.chosen_folder()
        seen["other"] = dlg.other_radio.isChecked()
        click(dlg.cancel_button)

    drive(scenario, window.action_registry["create_notes"].trigger)
    assert seen == {"checked": str(root / "Ultravox"), "other": False}


def test_scope_include_subfolders_is_the_same_setting_as_the_file_menu(window, tmp_path, boxes):
    root = library(tmp_path)
    window.settings.last_directory = str(root)
    window._load_paths([root / "Ultravox"])
    window.action_include_subfolders.setChecked(False)
    seen = {}

    def scenario():
        yield from wait(lambda: scope_dialog() is not None, "the scope dialog")
        dlg = scope_dialog()
        seen["off_note"] = dlg.include_note.text()
        dlg.include_box.setChecked(True)
        seen["on_note"] = dlg.include_note.text()
        seen["setting"] = window.settings.include_subfolders
        seen["action"] = window.action_include_subfolders.isChecked()
        seen["toolbar"] = window.include_subfolders_checkbox.isChecked()
        click(dlg.cancel_button)

    drive(scenario, window.action_registry["create_notes"].trigger)
    assert seen == {"off_note": "Only this folder is looked at.", "on_note": "Everything below the folder is looked at.",
                    "setting": True, "action": True, "toolbar": True}


def test_scope_choices_dedupe_order_and_limits(tmp_path):
    from core.notes_scope import scope_choices

    root = library(tmp_path)
    vienna_file = str(root / "Ultravox" / "Vienna (1980)" / "01 Astradyne.mp3")
    last = root / "Artist B"
    choices = scope_choices([vienna_file], [vienna_file], str(root / "Ultravox"), str(last), str(root))
    paths = [c.path for c in choices]
    assert paths[0] == str(root / "Ultravox") and len(paths) == len({p.lower() for p in paths}) <= 5
    assert "artist of the selected files" in choices[0].caption and choices[0].default  # joined, not listed twice
    assert str(root) in paths and str(last) in paths
    # folders that do not exist are left out; no files and no settings: no choices
    assert scope_choices([], [], "", str(tmp_path / "gone"), "") == []
    # a drive root is never offered as 'one level up'
    anchor = str(Path(tmp_path).anchor)
    assert all(c.path != anchor for c in scope_choices([], [], str(root.parent.parent), "", ""))


def test_scope_without_choices_is_just_another_folder_with_the_picker_opened(window, monkeypatch, tmp_path, boxes):
    window.settings.last_directory = ""
    window.settings.notes_last_folder = ""
    window.settings.library_root = ""
    window.files.clear()
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: str(tmp_path)))
    seen = {}

    def scenario():
        yield from wait(lambda: scope_dialog() is not None, "the scope dialog")
        dlg = scope_dialog()
        yield from wait(lambda: dlg.picked != "", "the picker")
        seen["radios"] = len(dlg.radios)
        seen["other"] = dlg.other_radio.isChecked()
        seen["folder"] = dlg.chosen_folder()
        click(dlg.cancel_button)

    drive(scenario, window.action_registry["create_notes"].trigger)
    assert seen == {"radios": 0, "other": True, "folder": str(tmp_path)}


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
        scope_dialog().other_radio.click()
        click(scope_dialog().continue_button)
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
