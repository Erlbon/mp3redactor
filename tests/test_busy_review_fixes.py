"""2026-10-16 independent review, 'shared progress dialogs and threading': the mp3 review page's lookups.
(1) the result of a lookup lands on the row it was started for, even when the selection moves meanwhile;
(2) a second start while one is running (double click, Enter) is ignored; (3) the dialogs of the Create Notes flow
do not pile up on the parent. Real widgets, real progress dialogs, offscreen."""

import os
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, QCoreApplication, QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication, QDialog, QFileDialog, QWidget  # noqa: E402

from core.notes_flow import Candidate, Confidence, Proposal  # noqa: E402
from core.notes_local import Target  # noqa: E402
from core.notes_review import ReviewSession  # noqa: E402
from gui.notes_dialog import NotesReviewDialog  # noqa: E402
from tests.test_notes_list_stable import click_row, settle  # noqa: E402

_app = QApplication.instance() or QApplication([])
_KEEP: list = []


def proposal(name, with_candidate=False):
    target = Target(Path(f"C:/lib/Genres/{name}"), "genre", "Genres")
    cands = [Candidate("musicbrainz", "1", name, "", score=0.9)] if with_candidate else []
    return Proposal(target, Confidence.UNCERTAIN, level="genre", suggested_levels=["genre-collection", "skip"],
                    candidates=cands, reasons=[f"{name}: nothing verified"])


def dialog(proposals, rebuild, **kw):
    dlg = NotesReviewDialog(ReviewSession(proposals), "list", rebuild, lambda p, c: True, **kw)
    dlg.show()
    dlg.resize(1100, 700)
    settle()
    _KEEP.append(dlg)
    return dlg


def rebuilt_from(p, title="REBUILT"):
    return Proposal(p.target, Confidence.UNCERTAIN, level="genre", reasons=[title])


def test_a_lookup_lands_on_the_row_it_was_started_for_even_if_the_selection_moves():
    calls = []

    def rebuild(p, candidate, level, **extra):
        calls.append(p.target.folder.name)
        time.sleep(0.6)
        return rebuilt_from(p)

    a, b = proposal("A"), proposal("B")
    dlg = dialog([a, b], rebuild)
    click_row(dlg, 0)
    item_b = dlg.session.items[1]
    QTimer.singleShot(200, lambda: dlg.tree.setCurrentItem(dlg.tree.topLevelItem(1)))  # the selection moves to B
    dlg._choose_level("genre-collection")
    assert calls == ["A"]
    assert dlg.session.items[0].proposal.reasons == ["REBUILT"]
    assert dlg.session.items[1] is item_b and item_b.proposal is b and b.reasons == ["B: nothing verified"]
    assert [i.proposal.reasons[0] for i in dlg.session.items].count("REBUILT") == 1
    dlg.close()


def test_a_second_start_while_a_lookup_runs_is_ignored():
    calls = []

    def rebuild(p, candidate, level, **extra):
        calls.append(1)
        time.sleep(0.5)
        return rebuilt_from(p)

    dlg = dialog([proposal("A")], rebuild)
    click_row(dlg, 0)
    QTimer.singleShot(100, lambda: dlg._choose_level("genre-collection"))  # a double click / Enter
    QTimer.singleShot(150, lambda: dlg._choose_link("https://en.wikipedia.org/wiki/Ska"))
    dlg._choose_level("genre-collection")
    assert calls == [1]
    assert dlg.isEnabled()
    dlg.close()


def test_the_best_guess_run_is_not_started_twice():
    calls = []

    def rebuild(p, candidate, level, **extra):
        calls.append(p.target.folder.name)
        time.sleep(0.3)
        return rebuilt_from(p)

    guess = Candidate("musicbrainz", "1", "x", "", score=0.9)
    dlg = dialog([proposal("A", True), proposal("B", True)], rebuild, best_guess=lambda p: guess)
    assert dlg.guess_indices() == [0, 1]
    QTimer.singleShot(100, dlg._tick_best_guesses)  # Enter / a second click while the first run is still going
    dlg._tick_best_guesses()
    assert sorted(calls) == ["A", "B"], calls  # each folder once, not twice
    assert dlg.isEnabled()
    dlg.close()


def test_the_scope_question_does_not_leave_a_dialog_behind(monkeypatch):
    from gui.notes_dialog import NotesScopeDialog, ask_scope

    parent = QWidget()
    _KEEP.append(parent)
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: ""))
    monkeypatch.setattr(NotesScopeDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
    for _ in range(5):
        ask_scope(parent, [], True)
    for _ in range(3):
        _app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert parent.findChildren(NotesScopeDialog) == []
