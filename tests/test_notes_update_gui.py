"""Updating old notes on the real review page (offscreen, real MainWindow, a scratch library with notes in all five
states): the checkbox is off by default and never remembered, only outdated / legacy notes are listed, an update has
its badge, the legacy warning and a Before / after view, is never pre-ticked, and goes through the Recycle Bin."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from core.notes_render import CURRENT_NOTE_FORMAT  # noqa: E402
from gui.notes_dialog import LEGACY_WARNING, OUTDATED_CHECK_TEXT  # noqa: E402
from redactor_common.core.notes import STATE_CURRENT, note_status  # noqa: E402
from tests.notes_helpers import make_mp3, ultravox_fetchers, ultravox_library  # noqa: E402
from tests.test_cover_gui import window  # noqa: E402,F401
from tests.test_notes_gui import boxes, click, review, wait  # noqa: E402,F401
from tests.test_notes_scope import drive, scope_dialog  # noqa: E402,F401
from tests.test_notes_update import legacy_note, our_note, put  # noqa: E402


def library(tmp_path):
    """Ultravox: artist.md outdated (an update), Vienna's album.md legacy (an update), Quartet has no note. Beside it
    three artists whose notes are current, edited by a person and not ours: they must never be listed."""
    artist = ultravox_library(tmp_path)
    put(artist / "artist.md", our_note("Ultravox", fmt=1))
    put(artist / "Vienna (1980)" / "album.md", legacy_note("Vienna"))
    notes = {"Cur": our_note("Cur"), "Edit": our_note("Edit").replace("Old text.", "My own words."),
             "Foreign": "---\ntitle: Foreign\n---\n\nWritten by hand.\n"}
    for name, text in notes.items():
        make_mp3(tmp_path / name / f"{name} Album (2000)" / "01 a.mp3", "a", name, f"{name} Album", 1, "2000")
        put(tmp_path / name / "artist.md", text)
        put(tmp_path / name / f"{name} Album (2000)" / "album.md", text)  # the same state one level down
    return artist


def start(window, tmp_path):
    window.settings.last_directory = str(tmp_path)
    window.settings.include_subfolders = True
    window._load_paths([tmp_path / "Ultravox"])
    window.table.clearSelection()
    QApplication.processEvents()
    window.notes_fetchers = lambda: ultravox_fetchers()


def listed(dlg):
    return sorted((i.proposal.kind, i.proposal.folder.name, i.proposal.is_update) for i in dlg.session.items)


def test_the_checkbox_is_off_by_default_and_without_it_no_old_note_is_listed(window, tmp_path, boxes):
    library(tmp_path)
    start(window, tmp_path)
    seen = {}

    def scenario():
        yield from wait(lambda: scope_dialog() is not None, "the scope dialog")
        dlg = scope_dialog()
        seen["text"] = dlg.outdated_box.text()
        seen["checked"] = dlg.outdated_box.isChecked()
        radio = next(r for r, p in dlg.radios if p == str(tmp_path))
        radio.click()
        dlg.kind_radios["both"].click()
        click(dlg.continue_button)
        yield from wait(lambda: review() is not None, "the review page")
        seen["listed"] = listed(review())
        review().done(0)

    drive(scenario, window.action_registry["create_notes"].trigger)
    assert seen["text"] == OUTDATED_CHECK_TEXT == "Also review notes made by an older version" and seen["checked"] is False
    assert seen["listed"] == [("album", "Quartet (1982)", False)]  # the only folder without a note


def test_with_the_checkbox_the_outdated_and_legacy_notes_are_listed_as_updates_never_edited_or_foreign(
        window, monkeypatch, tmp_path, recycle_bin, boxes):
    artist = library(tmp_path)
    start(window, tmp_path)
    asked = []
    monkeypatch.setattr(QMessageBox, "question", staticmethod(
        lambda p, t, text, *a, **k: asked.append(text) or QMessageBox.StandardButton.Yes))
    seen = {}

    def scenario():
        yield from wait(lambda: scope_dialog() is not None, "the scope dialog")
        dlg = scope_dialog()
        next(r for r, p in dlg.radios if p == str(tmp_path)).click()
        dlg.kind_radios["both"].click()
        dlg.outdated_box.setChecked(True)
        click(dlg.continue_button)
        yield from wait(lambda: review() is not None, "the review page")
        page = review()
        seen["listed"] = listed(page)
        seen["banner"] = page.banner_label.text()
        seen["ticked"] = {i.proposal.folder.name: i.ticked for i in page.session.items}
        panel = page.one_panel
        # the first item is the Ultravox artist note: an outdated-untouched update
        seen["first"] = page.session.items[0].proposal.folder.name
        seen["badge"] = panel.update_badge.isVisibleTo(panel)
        seen["legacy_on_artist"] = panel.legacy_label.isVisibleTo(panel)
        seen["diff_tab"] = panel.tabs.isTabVisible(panel.diff_tab)
        seen["diff"] = panel.diff_view.toPlainText()
        page.grab().save(str(tmp_path / "update_review.png"))
        click(page.write_button)  # the artist update: asks first, then replaces
        seen["artist_after"] = (artist / "artist.md").read_text(encoding="utf-8")
        # next item: the album with the legacy note
        panel = page.one_panel
        seen["album_folder"] = page.session.items[page.index].proposal.folder.name
        seen["legacy_on_album"] = panel.legacy_label.isVisibleTo(panel)
        seen["legacy_text"] = panel.legacy_label.text()
        page.done(0)

    drive(scenario, window.action_registry["create_notes"].trigger)
    names = {(k, n) for k, n, _u in seen["listed"]}
    assert ("artist", "Ultravox") in names and ("album", "Vienna (1980)") in names and ("album", "Quartet (1982)") in names
    assert not {n for _k, n in names} & {"Cur", "Edit", "Foreign", "Cur Album (2000)", "Edit Album (2000)", "Foreign Album (2000)"}
    assert [u for k, n, u in seen["listed"] if (k, n) == ("artist", "Ultravox")] == [True]
    assert [u for k, n, u in seen["listed"] if (k, n) == ("album", "Quartet (1982)")] == [False]
    assert "Notes made by an older version: 2 can be updated (never pre-ticked)." in seen["banner"]
    assert "4 notes were left alone: edited by a person or not ours. 2 are already current." in seen["banner"]
    # never pre-ticked: the updates are unticked, the plain new note is ticked
    assert seen["ticked"]["Ultravox"] is False and seen["ticked"]["Vienna (1980)"] is False
    assert seen["ticked"]["Quartet (1982)"] is True
    assert seen["first"] == "Ultravox" and seen["badge"] and not seen["legacy_on_artist"]
    assert seen["diff_tab"] and "Old text." in seen["diff"] and "British new wave" in seen["diff"]
    # the confirmation names the Recycle Bin and the old note is really there, the folder has the new stamped note
    assert asked and "Recycle Bin" in asked[0] and "restore" in asked[0]
    assert "Old text." not in seen["artist_after"] and "British new wave" in seen["artist_after"]
    assert note_status(str(artist / "artist.md"), CURRENT_NOTE_FORMAT).kind == STATE_CURRENT
    assert len(recycle_bin.trashed) == 1
    assert "Old text." in next(recycle_bin.folder.iterdir()).read_text(encoding="utf-8")
    assert [n for n in os.listdir(artist) if n.lower().endswith(".md")] == ["artist.md"]
    # a note edited by a person was never touched
    assert "My own words." in (tmp_path / "Edit" / "artist.md").read_text(encoding="utf-8")
    assert (tmp_path / "Foreign" / "artist.md").read_text(encoding="utf-8").endswith("Written by hand.\n")
    # the legacy note warns, in words
    assert seen["album_folder"] in ("Vienna (1980)", "Quartet (1982)")
    assert LEGACY_WARNING.startswith("Made by the first versions: cannot tell whether you edited it")


def test_the_legacy_warning_shows_on_the_legacy_item_and_a_no_to_the_question_writes_nothing(
        window, monkeypatch, tmp_path, recycle_bin, boxes):
    artist = library(tmp_path)
    start(window, tmp_path)
    seen = {}

    def scenario():
        yield from wait(lambda: scope_dialog() is not None, "the scope dialog")
        dlg = scope_dialog()
        next(r for r, p in dlg.radios if p == str(tmp_path)).click()
        dlg.kind_radios["both"].click()
        dlg.outdated_box.setChecked(True)
        click(dlg.continue_button)
        yield from wait(lambda: review() is not None, "the review page")
        page = review()
        vienna = next(i for i, item in enumerate(page.session.items) if item.proposal.folder.name == "Vienna (1980)")
        page.index = vienna
        page._show_one()
        panel = page.one_panel
        seen["legacy_visible"] = panel.legacy_label.isVisibleTo(panel)
        seen["legacy_text"] = panel.legacy_label.text()
        seen["badge"] = panel.update_badge.isVisibleTo(panel)
        click(page.write_button)  # the boxes fixture answers No to the question
        seen["still_old"] = (artist / "Vienna (1980)" / "album.md").read_text(encoding="utf-8")
        seen["state"] = page.session.items[vienna].state
        page.done(0)

    drive(scenario, window.action_registry["create_notes"].trigger)
    assert seen["legacy_visible"] and seen["badge"] and "cannot tell whether you edited it" in seen["legacy_text"]
    assert "Old text." in seen["still_old"] and seen["state"] == "pending" and recycle_bin.trashed == []


def test_the_checkbox_is_never_remembered(window, tmp_path, boxes):
    library(tmp_path)
    start(window, tmp_path)
    seen = []

    def first():
        yield from wait(lambda: scope_dialog() is not None, "the scope dialog")
        scope_dialog().outdated_box.setChecked(True)
        click(scope_dialog().cancel_button)

    def second():
        yield from wait(lambda: scope_dialog() is not None, "the scope dialog")
        seen.append(scope_dialog().outdated_box.isChecked())
        click(scope_dialog().cancel_button)

    drive(first, window.action_registry["create_notes"].trigger)
    drive(second, window.action_registry["create_notes"].trigger)
    assert seen == [False]
    assert not any("outdated" in key for key in vars(window.settings))
