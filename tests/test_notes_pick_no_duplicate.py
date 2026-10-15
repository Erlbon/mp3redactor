"""Picking a candidate must REPLACE the row in place. A person saw 'Use selected candidate' leave the uncertain row where it was
AND show a confident row further down ('the last line follows me around'): the list was rebuilt on every pick. Now the rows of
the picked folders are updated in place (same row objects, same positions, same scroll position, same selection), the engine
names one candidate when MusicBrainz and Discogs agree on the artist, and every path that rebuilds a proposal is checked here:
real window, real engine and fake services, the real review dialog in list view with 60 rows, real clicks."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPoint, Qt  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from core.discogs_lookup import DiscogsClient  # noqa: E402
from core.notes_flow import Fetchers  # noqa: E402
from redactor_common.core.lookup_client import Throttle  # noqa: E402
from tests.notes_helpers import set_kinds  # noqa: E402,F401
from tests.notes_helpers import FakeFetch, make_mp3  # noqa: E402
from tests.test_cover_gui import window  # noqa: E402,F401
from tests.test_notes_gui import boxes, click, review, wait  # noqa: E402,F401
from tests.test_notes_scope import drive, scope_dialog  # noqa: E402,F401

TIE = "Czar Chainfist"  # sorts after the 59 others: the row is deep in the list, the list is scrolled
NAMES = [f"Cband {i:02d}" for i in range(59)] + [TIE]


def library(root):
    for name in NAMES:
        make_mp3(root / "C" / name / "Some Album (2001)" / "01 a.mp3", "a", name, "Some Album", 1, "2001")
    return root


def fetchers(both_sources=True):
    mb = FakeFetch([
        (r"ws/2/artist\?query=artist%3A%22Czar", {"artists": [{"id": "mb-czar", "name": TIE, "score": 100, "sort-name": TIE,
            "type": "Group", "country": "DK", "disambiguation": "Danish heavy metal/hard rock group",
            "life-span": {"begin": "2007"}}]}),
        (r"ws/2/artist\?query", {"artists": []}),
        (r"ws/2/release-group\?query=", {"release-groups": []}),
        (r"ws/2/release-group\?artist=mb-czar", {"release-groups": [
            {"id": "rg1", "title": "Another Album", "primary-type": "Album", "first-release-date": "2010"}]}),
        (r"ws/2/artist/mb-czar", {"id": "mb-czar", "name": TIE, "type": "Group", "life-span": {"begin": "2007"},
                                  "relations": [], "genres": [], "tags": []}),
    ])
    wiki = FakeFetch([(r"search/title", {"pages": []}), (r"page/summary/", None)])
    discogs = FakeFetch([
        (r"database/search\?type=artist&q=Czar", {"results": [{"id": 2521047, "title": TIE}]}),
        (r"database/search", {"results": []}),
        (r"artists/2521047/releases", {"releases": [{"title": "Another Album", "year": 2010, "role": "Main", "type": "master"}]}),
        (r"artists/2521047", {"id": 2521047, "name": TIE, "profile": "Danish band.", "members": [],
                              "uri": "https://www.discogs.com/artist/2521047"}),
    ])
    return Fetchers(mb=mb, wiki=wiki, discogs=DiscogsClient("t", fetch=discogs, throttle=Throttle(0)) if both_sources else None)


def row_objects(tree):
    return [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())]


def texts(tree):
    return [tuple(tree.topLevelItem(i).text(c) for c in range(4)) for i in range(tree.topLevelItemCount())]


def open_list(window, tmp_path, kinds="artist", discogs=True):
    root = library(tmp_path)
    window.settings.last_directory = str(root)
    window.settings.include_subfolders = True
    window._load_paths([root / "C" / "Cband 00"])
    window.notes_fetchers = lambda: fetchers(discogs)
    return root


def scenario_in_list(window, root, body, kinds="artist"):
    """Opens Create Notes on the library, waits for the list view, runs body(page)."""
    seen = {}

    def scenario():
        yield from wait(lambda: scope_dialog() is not None, "the scope dialog")
        dlg = scope_dialog()
        next(r for r, p in dlg.radios if p == str(root)).click()
        set_kinds(dlg, kinds)
        click(dlg.continue_button)
        yield from wait(lambda: review() is not None, "the review page")
        page = review()
        assert page.view == "list"
        seen["result"] = body(page)
        page.done(0)

    drive(scenario, window.action_registry["create_notes"].trigger)
    return seen["result"]


def select_tie_row(page):
    """Scrolls the tie artist's row into view and clicks it, like a person."""
    tree = page.tree
    index = next(i for i, it in enumerate(page.session.items) if it.proposal.folder.name == TIE)
    row = next(r for r in row_objects(tree) if r.data(0, Qt.ItemDataRole.UserRole) == index)
    tree.scrollToItem(row)
    QApplication.processEvents()
    rect = tree.visualItemRect(row)
    QTest.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(100, rect.center().y()))
    QApplication.processEvents()
    assert tree.currentItem() is row
    return index, row


def watch_rebuilds(page):
    calls = []
    real = page._fill_tree
    page._fill_tree = lambda: (calls.append(1), real())[1]
    return calls


def snapshot(page):
    tree = page.tree
    return {"objects": row_objects(tree), "texts": texts(tree), "scroll": tree.verticalScrollBar().value(),
            "folders": [str(i.proposal.folder) for i in page.session.items], "count": tree.topLevelItemCount()}


# -- two services, one name: separate unless a fact agrees -------------------------------------------------------------

def test_two_services_naming_the_same_name_without_agreeing_evidence_stay_two_candidates_and_uncertain(
        window, tmp_path, boxes):
    root = open_list(window, tmp_path)

    def body(page):
        item = next(i for i in page.session.items if i.proposal.folder.name == TIE)
        return item.proposal.candidates, item.proposal.reasons, item.proposal.confidence.value

    candidates, reasons, confidence = scenario_in_list(window, root, body)
    assert confidence == "uncertain"  # rightly: nothing verifies that the two are the same artist
    assert [(c.source, c.also, c.hits, c.score) for c in candidates] == [("musicbrainz", "", [], 1.0), ("discogs", "", [], 1.0)]
    assert any(r.startswith("why it is uncertain: no local album ('Some Album'") and "either discography" in r
               and "may be different artists" in r for r in reasons), reasons


def test_the_candidate_table_lists_both_services_when_nothing_agrees(window, tmp_path, boxes):
    root = open_list(window, tmp_path)

    def body(page):
        select_tie_row(page)
        table = page.list_panel.candidate_table
        return [table.item(r, 3).text() for r in range(table.rowCount())]

    assert scenario_in_list(window, root, body) == ["musicbrainz", "discogs"]


def test_candidates_with_a_local_album_in_both_discographies_are_merged_and_the_note_says_why():
    from core.notes_flow import Candidate, NotesEngine

    a = Candidate("musicbrainz", "m", "Same Name", "Danish group", ["Album One", "Album Two"], 1.0)
    b = Candidate("discogs", "d", "Same Name", "Discogs d", ["Album Two"], 1.0)
    [merged] = NotesEngine._merge_same_artist([a, b])
    assert merged.source == "musicbrainz" and merged.also == "discogs" and merged.also_id == "d"
    assert merged.note.startswith("merged with Discogs d: both discographies have 'Album Two'")
    assert "Discogs d" in merged.detail
    # the same name and no shared album, or different names: never merged
    assert len(NotesEngine._merge_same_artist([a, Candidate("discogs", "d", "Same Name", "", ["Other"], 1.0)])) == 2
    assert len(NotesEngine._merge_same_artist([a, Candidate("discogs", "d", "Other Name", "", ["Album Two"], 1.0)])) == 2


def test_picking_a_merged_candidate_builds_the_note_from_both_sources(tmp_path):
    from core.notes_flow import Candidate, NotesEngine, scan
    from core.notes_local import KIND_ARTIST

    library(tmp_path)
    engine = NotesEngine(fetchers())
    target = next(t for t in scan([tmp_path], kinds=(KIND_ARTIST,)).targets if t.folder.name == TIE)
    proposal = engine.build_proposal(target)
    merged = Candidate("musicbrainz", "mb-czar", TIE, "", ["Another Album"], 1.0, also="discogs", also_id="2521047")
    built = engine.rebuild(proposal, merged)
    assert built.confidence.value == "confident" and "musicbrainz" in built.sources and "discogs" in built.sources
    assert "chosen by you" in " ".join(built.reasons)


# -- the rebuilds: in place ------------------------------------------------------------------------------------------

def test_use_selected_candidate_updates_the_row_in_place_nothing_is_added_nothing_moves(window, tmp_path, boxes):
    root = open_list(window, tmp_path)

    def body(page):
        index, row = select_tie_row(page)
        calls = watch_rebuilds(page)
        before = snapshot(page)
        assert before["scroll"] > 0 and before["count"] == 60
        click(page.list_panel.candidate_button)
        QApplication.processEvents()
        after = snapshot(page)
        return before, after, calls, page.tree.currentItem() is row, row.text(2), row.checkState(0), index

    before, after, calls, still_current, confidence_text, check, index = scenario_in_list(window, root, body)
    assert calls == []  # the list was not rebuilt
    assert after["count"] == before["count"] == 60 and after["folders"] == before["folders"]
    assert after["objects"] == before["objects"]  # the very same row objects, in the same order
    assert after["scroll"] == before["scroll"]
    assert still_current and confidence_text == "Confident" and check == Qt.CheckState.Checked
    changed = [i for i, (a, b) in enumerate(zip(before["texts"], after["texts"])) if a != b]
    assert changed == [index]  # exactly one row changed: the picked one, where it was


def test_the_level_choice_updates_the_row_in_place(window, tmp_path, boxes):
    root = open_list(window, tmp_path)

    def body(page):
        index, row = select_tie_row(page)
        calls = watch_rebuilds(page)
        before = snapshot(page)
        page.list_panel.level_combo.setCurrentIndex(1)  # index 0 is "Choose..."
        page.list_panel.level_combo.activated.emit(1)  # picking a level builds at once: no Build button
        QApplication.processEvents()
        return before, snapshot(page), calls, page.tree.currentItem() is row, index

    before, after, calls, still_current, index = scenario_in_list(window, root, body)
    assert calls == [] and after["objects"] == before["objects"] and after["folders"] == before["folders"]
    assert after["scroll"] == before["scroll"] and still_current


def test_tick_best_guesses_updates_the_rows_in_place(window, tmp_path, boxes):
    root = open_list(window, tmp_path)

    def body(page):
        index, row = select_tie_row(page)
        calls = watch_rebuilds(page)
        before = snapshot(page)
        click(page.tick_guesses_button)
        QApplication.processEvents()
        after = snapshot(page)
        return before, after, calls, page.session.items[index].proposal.confidence.value, index

    before, after, calls, confidence, index = scenario_in_list(window, root, body)
    assert confidence == "confident"
    assert calls == [] and after["objects"] == before["objects"] and after["folders"] == before["folders"]
    assert after["count"] == 60 and after["scroll"] == before["scroll"]


def test_with_the_only_uncertain_filter_the_picked_row_leaves_and_nothing_else_moves(window, tmp_path, boxes):
    root = open_list(window, tmp_path)

    def body(page):
        page.filter_check.setChecked(True)  # only the uncertain / not found rows
        QApplication.processEvents()
        index, row = select_tie_row(page)
        before_objects = row_objects(page.tree)
        count = len(before_objects)
        click(page.list_panel.candidate_button)
        QApplication.processEvents()
        after_objects = row_objects(page.tree)
        return count, before_objects, after_objects, row in after_objects, len(page.session.items)

    count, before_objects, after_objects, still_there, items = scenario_in_list(window, root, body)
    assert items == 60 and count == len(before_objects)
    assert not still_there and [o for o in before_objects if o in after_objects] == after_objects  # one left, order kept
    assert len(after_objects) == count - 1


def test_a_rebuild_from_the_one_at_a_time_view_still_shows_one_row_per_folder(window, tmp_path, boxes):
    root = open_list(window, tmp_path)

    def body(page):
        index, _row = select_tie_row(page)
        page.toggle_view()  # one at a time
        click(page.one_panel.candidate_button)
        page.toggle_view()  # back to the list
        QApplication.processEvents()
        return [str(i.proposal.folder) for i in page.session.items], texts(page.tree), page.tree.topLevelItemCount()

    folders, rows_, count = scenario_in_list(window, root, body)
    assert count == 60 and len(set(folders)) == 60
    assert sum(1 for r in rows_ if r[0].endswith(TIE)) == 1
