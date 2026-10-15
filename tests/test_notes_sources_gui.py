"""The picking flow of the other sources on the real review page (offscreen, real MainWindow, injected fetchers): a
Discogs-only album candidate becomes writable and the written file has the Discogs content; a Wikipedia-only candidate
is a thin draft; 'Create a minimal note from the tags' is explicit and writes the tags-only draft."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt  # noqa: E402

from core.notes_flow import Fetchers  # noqa: E402
from tests.test_cover_gui import window  # noqa: E402,F401
from tests.test_notes_gui import boxes, click, review, run, wait  # noqa: E402,F401
from tests.test_notes_sources import (  # noqa: E402
    album_routes, discogs, no_mb, no_wiki, vienna_folder, wiki_only_fetchers,
)


def pick_row(panel, source):
    table = panel.candidate_table
    for row in range(table.rowCount()):
        if table.item(row, 3).text() == source:
            table.selectRow(row)
            return
    raise AssertionError(f"no {source} candidate listed")


def test_a_discogs_only_album_candidate_is_enabled_chosen_and_written(window, monkeypatch, tmp_path, boxes):
    vienna_folder(tmp_path)
    window.settings.include_subfolders = True
    fetchers = Fetchers(mb=no_mb(), wiki=no_wiki(), discogs=discogs(*album_routes()))
    seen = {}

    def scenario():
        yield from wait(lambda: review() is not None, "the review page")
        dlg = review()
        panel = dlg.one_panel
        pick_row(panel, "discogs")
        seen["enabled"] = panel.candidate_button.isEnabled()
        seen["message"] = panel.candidate_note.text()
        seen["before"] = dlg.session.items[0].proposal.writable
        click(panel.candidate_button)
        item = dlg.session.items[0]
        seen["after"] = item.proposal.writable
        seen["badge"] = panel.badge.text()
        seen["preview"] = panel.raw.toPlainText()
        seen["local_button_hidden"] = not panel.local_box.isVisibleTo(panel)
        click(dlg.write_button)
        dlg.done(0)

    run(window, monkeypatch, tmp_path / "Ultravox" / "Vienna (1980)", fetchers, scenario)
    assert seen["enabled"] and seen["message"] == "" and not seen["before"] and seen["after"]
    assert seen["local_button_hidden"]
    written = (tmp_path / "Ultravox" / "Vienna (1980)" / "album.md").read_text(encoding="utf-8")
    assert "Chrysalis (CDL 1296)" in written and "1. Astradyne 6:09" in written and "status: draft" in written
    assert "Facts from [Discogs](https://www.discogs.com/release/1111)" in written


def test_wikipedia_only_candidate_is_a_thin_draft_written_only_after_the_pick(window, monkeypatch, tmp_path, boxes):
    vienna_folder(tmp_path)
    window.settings.include_subfolders = True
    seen = {}

    def scenario():
        yield from wait(lambda: review() is not None, "the review page")
        dlg = review()
        panel = dlg.one_panel
        seen["written_before"] = (tmp_path / "Ultravox" / "Vienna (1980)" / "album.md").exists()
        pick_row(panel, "wikipedia")
        click(panel.candidate_button)
        click(dlg.write_button)
        dlg.done(0)

    run(window, monkeypatch, tmp_path / "Ultravox" / "Vienna (1980)", wiki_only_fetchers(), scenario)
    assert not seen["written_before"]
    written = (tmp_path / "Ultravox" / "Vienna (1980)" / "album.md").read_text(encoding="utf-8")
    assert "Vienna is the fourth studio album" in written and "status: draft" in written


def test_nothing_found_offers_the_explicit_minimal_note_and_writes_it(window, monkeypatch, tmp_path, boxes):
    vienna_folder(tmp_path)
    window.settings.include_subfolders = True
    seen = {}

    def scenario():
        yield from wait(lambda: review() is not None, "the review page")
        dlg = review()
        panel = dlg.one_panel
        seen["visible"] = panel.local_box.isVisibleTo(panel)
        seen["hint"] = panel.preview.toPlainText()
        seen["reasons"] = panel.reasons_label.text()
        seen["written_before"] = (tmp_path / "Ultravox" / "Vienna (1980)" / "album.md").exists()
        click(panel.local_button)
        seen["writable"] = dlg.session.items[0].proposal.writable
        click(dlg.write_button)
        dlg.done(0)

    run(window, monkeypatch, tmp_path / "Ultravox" / "Vienna (1980)", Fetchers(mb=no_mb(), wiki=no_wiki()), scenario)
    assert seen["visible"] and not seen["written_before"] and seen["writable"]
    assert "minimal note from the tags" in seen["hint"]
    assert "Discogs was not searched" in seen["reasons"] and "Tools &gt; API Keys" in seen["reasons"]
    written = (tmp_path / "Ultravox" / "Vienna (1980)" / "album.md").read_text(encoding="utf-8")
    assert "Built from the file tags only, no online source" in written and "status: draft" in written
