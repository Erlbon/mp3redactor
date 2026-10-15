"""'What to create' in the real scope dialog (offscreen, QTest, real MainWindow): artist notes only is the default and
makes no album lookups, the choice is remembered, and the review page of an artist-only run offers the album notes of the
artists just written."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QLabel  # noqa: E402

from core.notes_scope import kinds_sentence  # noqa: E402
from tests.notes_helpers import set_kinds  # noqa: E402,F401
from tests.notes_helpers import ultravox_fetchers, ultravox_library  # noqa: E402
from tests.test_cover_gui import window  # noqa: E402,F401
from tests.test_notes_gui import Script, boxes, click, review, wait  # noqa: E402,F401
from tests.test_notes_scope import drive, scope_dialog  # noqa: E402,F401


def open_library(window, tmp_path):
    ultravox_library(tmp_path)
    window.settings.last_directory = str(tmp_path)
    window.settings.include_subfolders = True
    window._load_paths([tmp_path / "Ultravox"])
    window.table.clearSelection()
    QApplication.processEvents()


def item_kinds(dlg):
    return sorted(i.proposal.kind for i in dlg.session.items)


def test_artist_notes_only_is_the_default_and_looks_no_albums_up(window, tmp_path, boxes):
    open_library(window, tmp_path)
    fetchers = ultravox_fetchers()
    window.notes_fetchers = lambda: fetchers
    seen = {}

    def scenario():
        yield from wait(lambda: scope_dialog() is not None, "the scope dialog")
        dlg = scope_dialog()
        seen["labels"] = [b.text() for b in dlg.kind_boxes.values()]
        seen["ticked"] = [k for k, b in dlg.kind_boxes.items() if b.isChecked()]
        seen["checked"] = dlg.chosen_kinds()
        click(dlg.continue_button)
        yield from wait(lambda: review() is not None, "the review page")
        seen["kinds"] = item_kinds(review())
        seen["banner"] = review().banner_label.text()
        review().done(0)

    drive(scenario, window.action_registry["create_notes"].trigger)
    assert seen["labels"] == ["Genre / collection notes (fast: about one Wikipedia lookup per folder, no tags read)",
                              "Artist notes (about 5 requests each)", "Album notes (slower: about 4 requests each)"]
    assert seen["ticked"] == ["artist"]  # artist notes on, album notes and genre notes off
    assert seen["checked"] == "artist" and seen["kinds"] == ["artist"]
    assert kinds_sentence(("artist",)) in seen["banner"] and "5 requests" in seen["banner"] and "Looked up 1 folder in" in seen["banner"]
    assert not [u for u in fetchers.mb.urls if "release-group/rg-" in u or "ws/2/release/" in u]
    assert window.settings.notes_kinds == "artist"


def test_the_choice_is_used_and_remembered_for_the_next_time(window, tmp_path, boxes):
    open_library(window, tmp_path)
    window.notes_fetchers = lambda: ultravox_fetchers()
    seen = {}

    def first():
        yield from wait(lambda: scope_dialog() is not None, "the scope dialog")
        set_kinds(scope_dialog(), "both")
        click(scope_dialog().continue_button)
        yield from wait(lambda: review() is not None, "the review page")
        seen["kinds"] = item_kinds(review())
        review().done(0)

    drive(first, window.action_registry["create_notes"].trigger)
    assert seen["kinds"] == ["album", "album", "artist"] and window.settings.notes_kinds == "artist,album"

    def second():
        yield from wait(lambda: scope_dialog() is not None, "the scope dialog")
        seen["default_now"] = scope_dialog().chosen_kinds()
        click(scope_dialog().cancel_button)

    drive(second, window.action_registry["create_notes"].trigger)
    assert seen["default_now"] == "artist,album"


def test_everything_includes_the_genre_level(window, tmp_path, boxes):
    from tests.notes_helpers import make_mp3

    ultravox_library(tmp_path / "Rock")
    window.settings.last_directory = str(tmp_path)
    window.settings.include_subfolders = True
    window._load_paths([tmp_path / "Rock"])
    window.notes_fetchers = lambda: ultravox_fetchers()
    seen = {}

    def scenario():
        yield from wait(lambda: scope_dialog() is not None, "the scope dialog")
        dlg = scope_dialog()
        radio = next(r for r, p in dlg.radios if p == str(tmp_path))
        radio.click()
        set_kinds(dlg, "all")
        click(dlg.continue_button)
        yield from wait(lambda: review() is not None, "the review page")
        seen["kinds"] = item_kinds(review())
        review().done(0)

    drive(scenario, window.action_registry["create_notes"].trigger)
    assert "genre" in seen["kinds"] and "artist" in seen["kinds"] and "album" in seen["kinds"]


def test_after_an_artist_only_run_a_yes_creates_the_albums_of_those_artists(window, monkeypatch, tmp_path, boxes):
    from PyQt6.QtWidgets import QMessageBox

    asked = []
    monkeypatch.setattr(QMessageBox, "question", staticmethod(
        lambda p, t, text, *a, **k: asked.append(text) or QMessageBox.StandardButton.Yes))
    open_library(window, tmp_path)
    window.notes_fetchers = lambda: ultravox_fetchers()
    seen = {}

    def scenario():
        yield from wait(lambda: scope_dialog() is not None, "the scope dialog")
        click(scope_dialog().continue_button)  # artist notes only
        yield from wait(lambda: review() is not None, "the review page")
        first = review()
        seen["album_note_before"] = (tmp_path / "Ultravox" / "Vienna (1980)" / "album.md").exists()
        click(first.write_button)  # the last one: the page closes and asks
        yield from wait(lambda: review() is not None and review() is not first, "the album review page")
        second = review()
        seen["second_kinds"] = item_kinds(second)
        seen["second_banner"] = second.banner_label.text()
        seen["second_has_followup"] = second.followup_button.isVisibleTo(second)
        click(second.write_all_button)
        second.done(0)

    drive(scenario, window.action_registry["create_notes"].trigger)
    assert (tmp_path / "Ultravox" / "artist.md").exists() and not seen["album_note_before"]
    assert len(asked) == 1 and "1 artist note written" in asked[0] and "album notes" in asked[0]
    assert seen["second_kinds"] == ["album", "album"] and "album notes only" in seen["second_banner"]
    assert not seen["second_has_followup"]
    assert (tmp_path / "Ultravox" / "Vienna (1980)" / "album.md").exists()


def test_after_an_artist_only_run_a_no_leaves_it_at_that(window, tmp_path, boxes):
    open_library(window, tmp_path)
    window.notes_fetchers = lambda: ultravox_fetchers()

    def scenario():
        yield from wait(lambda: scope_dialog() is not None, "the scope dialog")
        click(scope_dialog().continue_button)
        yield from wait(lambda: review() is not None, "the review page")
        click(review().write_button)

    drive(scenario, window.action_registry["create_notes"].trigger)
    assert (tmp_path / "Ultravox" / "artist.md").exists() and not (tmp_path / "Ultravox" / "Vienna (1980)" / "album.md").exists()


def test_the_header_button_in_the_list_view_queues_only_the_albums_of_the_written_artists(window, tmp_path, boxes):
    from tests.test_notes_scope import library

    root = library(tmp_path)  # Ultravox (2 artists in all with Artist B)
    window.settings.last_directory = str(root)
    window.settings.include_subfolders = True
    window._load_paths([root / "Ultravox", root / "Artist B"])
    window.notes_fetchers = lambda: ultravox_fetchers()
    seen = {}

    def scenario():
        yield from wait(lambda: scope_dialog() is not None, "the scope dialog")
        click(scope_dialog().continue_button)
        yield from wait(lambda: review() is not None, "the review page")
        first = review()
        seen["view"] = first.view
        seen["hidden_before"] = not first.followup_button.isVisibleTo(first)
        click(first.write_ticked_button)
        seen["visible_after"] = first.followup_button.isVisibleTo(first)
        click(first.followup_button)
        yield from wait(lambda: review() is not None and review() is not first, "the album review page")
        seen["second_folders"] = sorted(str(i.proposal.folder) for i in review().session.items)
        review().done(0)

    drive(scenario, window.action_registry["create_notes"].trigger)
    assert seen["view"] == "list" and seen["hidden_before"] and seen["visible_after"]
    assert seen["second_folders"] and all("Ultravox" in f for f in seen["second_folders"])  # Artist B was not written


def test_scope_dialog_text_names_all_three_choices(window, tmp_path, boxes):
    open_library(window, tmp_path)
    seen = {}

    def scenario():
        yield from wait(lambda: scope_dialog() is not None, "the scope dialog")
        seen["labels"] = " ".join(label.text() for label in scope_dialog().findChildren(QLabel))
        click(scope_dialog().cancel_button)

    drive(scenario, window.action_registry["create_notes"].trigger)
    assert "What to create" in seen["labels"]
