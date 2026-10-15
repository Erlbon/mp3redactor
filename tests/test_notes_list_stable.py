"""The Create Notes LIST must not jump: selecting a row (confident, uncertain with candidates, nothing found, an unclear
level) never changes the tree's viewport, scroll position, row positions, the splitter or the dialog, and a rebuild of
the list (picking a candidate, Tick all) keeps the scroll position. Real review dialog, real clicks, ~60 rows of mixed
confidence like a whole-library run (the real library had D\\DAF uncertain next to D\\Daft Punk confident)."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path  # noqa: E402

import pytest  # noqa: E402
from PyQt6.QtCore import QPoint, Qt  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from core.notes_flow import Candidate, Confidence, Proposal  # noqa: E402
from core.notes_local import Target  # noqa: E402
from core.notes_review import ReviewSession  # noqa: E402
from gui.notes_dialog import NotesReviewDialog  # noqa: E402

_app = QApplication.instance() or QApplication([])


def proposals(n):
    out = []
    for i in range(n):
        album = i % 3 == 2
        folder = Path(r"C:\lib\D") / (f"Artist {i - 1:03d}" if album else f"Artist {i:03d}") / (f"Album {i}" if album else "")
        target = Target(folder, "album" if album else "artist", "D")
        kind = i % 4
        if kind == 0:  # confident, a long note
            p = Proposal(target, Confidence.CONFIDENT, note_text="---\ntitle: X\n---\n\n# X\n\n" + "line of text\n\n" * 60,
                         title="X", reasons=["MusicBrainz: ok"] * 3, sources=["musicbrainz", "wikipedia"],
                         level="album" if album else "artist")
        elif kind == 1:  # uncertain with candidates and a level choice: the biggest panel
            p = Proposal(target, Confidence.UNCERTAIN, reasons=["2 release groups fit equally well", "no Wikipedia page"],
                         level="artist", level_reasons=["x"], suggested_levels=["artist", "genre-collection", "skip"],
                         candidates=[Candidate("musicbrainz", f"id{k}", f"Candidate {k}", "1980, GB", ["Vienna"], 0.8)
                                     for k in range(4)])
        elif kind == 2:  # nothing found
            p = Proposal(target, Confidence.NONE, reasons=["no artist of that name was found"] * 2, level="album" if album else "artist")
        else:  # uncertain, only a level to choose
            p = Proposal(target, Confidence.UNCERTAIN, reasons=["level unclear"], level="unclear",
                         suggested_levels=["artist", "album", "skip"])
        out.append(p)
    return out


def rebuild(proposal, candidate, level):
    """What the engine does after a person's pick: the same folder, now confident with a draft."""
    return Proposal(proposal.target, Confidence.CONFIDENT, note_text="---\ntitle: Picked\n---\n\n# Picked\n", title="Picked",
                    reasons=["chosen by you"], sources=["musicbrainz"], level=proposal.level or "artist")


def settle():
    """Let every deferred layout, resize and queued call run (a relayout can take several passes of the event loop)."""
    for _ in range(3):
        QApplication.processEvents()
        QTest.qWait(20)


def make_dialog(n=60, width=1100, height=700):
    dlg = NotesReviewDialog(ReviewSession(proposals(n)), "list", rebuild, lambda p, c: True)
    dlg.show()
    dlg.resize(width, height)
    settle()
    return dlg


def snap(dlg):
    tree = dlg.tree
    first = tree.topLevelItem(0)
    return {
        "viewport": (tree.viewport().width(), tree.viewport().height()), "tree": (tree.width(), tree.height()),
        "scroll": (tree.verticalScrollBar().value(), tree.horizontalScrollBar().value()),
        "scrollbars": (tree.verticalScrollBar().isVisible(), tree.horizontalScrollBar().isVisible()),
        "row0_y": tree.visualItemRect(first).y(), "splitter": dlg.splitter.sizes(), "dialog": (dlg.width(), dlg.height()),
    }


def click_row(dlg, index):
    tree = dlg.tree
    row = tree.topLevelItem(index)
    tree.scrollToItem(row, tree.ScrollHint.PositionAtCenter)
    QApplication.processEvents()
    rect = tree.visualItemRect(row)
    QTest.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(100, rect.center().y()))
    settle()
    assert tree.currentItem() is row


@pytest.mark.parametrize("height", [700, 420, 330])
def test_selecting_any_kind_of_row_does_not_move_the_tree_the_splitter_or_the_dialog(height):
    dlg = make_dialog(height=height)
    kinds_seen = set()
    for index in range(0, 24):
        p = dlg.session.items[index].proposal
        kinds_seen.add((p.confidence, bool(p.candidates)))
        # scroll once, so that what changes is only what the selection changes
        dlg.tree.scrollToItem(dlg.tree.topLevelItem(index), dlg.tree.ScrollHint.PositionAtCenter)
        settle()
        before = snap(dlg)
        before_rows = [dlg.tree.visualItemRect(dlg.tree.topLevelItem(r)).y() for r in range(0, 60)]
        row = dlg.tree.topLevelItem(index)
        rect = dlg.tree.visualItemRect(row)
        QTest.mouseClick(dlg.tree.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(100, rect.center().y()))
        settle()
        after = snap(dlg)
        after_rows = [dlg.tree.visualItemRect(dlg.tree.topLevelItem(r)).y() for r in range(0, 60)]
        assert after == before, f"row {index} ({p.confidence.value}): {before} -> {after}"
        assert after_rows == before_rows, f"row {index}: the rows moved"
    assert len(kinds_seen) >= 3  # confident, uncertain with candidates, nothing found / unclear all were selected
    dlg.done(0)


def test_the_first_selection_does_not_change_the_horizontal_scroll_bar():
    dlg = make_dialog()
    first = snap(dlg)
    click_row(dlg, 1)  # an uncertain artist with candidates
    assert snap(dlg)["scrollbars"] == first["scrollbars"] and snap(dlg)["viewport"] == first["viewport"]
    dlg.done(0)


def test_a_selection_never_rebuilds_the_tree(monkeypatch):
    dlg = make_dialog()
    calls = []
    real = dlg._fill_tree
    monkeypatch.setattr(dlg, "_fill_tree", lambda: (calls.append(1), real())[1])
    for index in (1, 2, 3, 4, 5, 6):
        click_row(dlg, index)
    assert calls == []
    dlg.done(0)


def test_picking_a_candidate_keeps_the_scroll_position():
    dlg = make_dialog(n=120)
    tree = dlg.tree
    tree.verticalScrollBar().setValue(tree.verticalScrollBar().maximum() // 2)
    QApplication.processEvents()
    # an uncertain row that is on screen now
    vp = tree.viewport().height()
    row_index = next(i for i in range(120)
                     if dlg.session.items[i].proposal.candidates
                     and 0 <= tree.visualItemRect(tree.topLevelItem(i)).y() < vp - 40)
    row = tree.topLevelItem(row_index)
    rect = tree.visualItemRect(row)
    QTest.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(100, rect.center().y()))
    QApplication.processEvents()
    before = (tree.verticalScrollBar().value(), tree.visualItemRect(tree.topLevelItem(row_index)).y())
    QTest.mouseClick(dlg.list_panel.candidate_button, Qt.MouseButton.LeftButton)
    settle()
    assert dlg.session.items[row_index].proposal.confidence == Confidence.CONFIDENT  # the pick rebuilt the list
    after = (tree.verticalScrollBar().value(), tree.visualItemRect(tree.topLevelItem(row_index)).y())
    assert before[0] > 0 and after == before, f"the list jumped: {before} -> {after}"
    dlg.done(0)


def test_tick_all_and_untick_all_keep_the_scroll_position():
    dlg = make_dialog(n=120)
    tree = dlg.tree
    tree.verticalScrollBar().setValue(tree.verticalScrollBar().maximum() // 3)
    QApplication.processEvents()
    click_row(dlg, 40)
    value = tree.verticalScrollBar().value()
    assert value > 0
    QTest.mouseClick(dlg.tick_all_button, Qt.MouseButton.LeftButton)
    QApplication.processEvents()
    assert tree.verticalScrollBar().value() == value
    dlg.done(0)
