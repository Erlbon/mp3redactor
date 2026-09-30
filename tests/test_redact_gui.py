"""Redact in the main window: menu/toolbar entries, target choice, the
unsaved-edits guard, and an end-to-end run through redactor_common's
run_redact on copies of tiny.mp3 (fake Recycle Bin, mocked tools)."""

import functools
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtWidgets import QMessageBox  # noqa: E402

import core.redact_steps as rs  # noqa: E402
import gui.main_window as mw  # noqa: E402
from core.mp3_file import MP3File, STATUS_OK  # noqa: E402
from core.tag_reader import load_tags  # noqa: E402
from redactor_common.gui.redact_dialog import RedactResultsDialog  # noqa: E402
from tests.test_cover_gui import _app, _load, _select_all, window  # noqa: E402,F401


@pytest.fixture
def shown(monkeypatch, recycle_bin):
    """Captures the results dialogs instead of exec()-ing them, and gives
    Redact the fake Recycle Bin."""
    dialogs = []
    monkeypatch.setattr(RedactResultsDialog, "exec", lambda self: dialogs.append(self) or 0)
    monkeypatch.setattr(mw, "RedactEnv", functools.partial(rs.RedactEnv, trash=recycle_bin))
    return dialogs


@pytest.fixture
def tools(monkeypatch):
    monkeypatch.setattr(rs, "check_integrity", lambda p, override_path=None: (STATUS_OK, ""))
    monkeypatch.setattr(rs, "detect_bpm", lambda p: (120.0, STATUS_OK, ""))
    monkeypatch.setattr(rs, "detect_key", lambda p, override_path=None: ("C", STATUS_OK, ""))
    monkeypatch.setattr(rs, "measure_loudness", lambda p, override_path=None: (-16.0, -2.0, STATUS_OK, ""))
    monkeypatch.setattr(rs, "find_tool", lambda *a, **k: None)
    monkeypatch.setattr(rs, "find_album", lambda facts, query: [])


def test_menu_and_toolbar_entries(window):
    from PyQt6.QtWidgets import QMenu, QToolBar

    assert window.action_redact.shortcut().toString() == "Ctrl+Shift+E"
    assert window.action_redact.toolTip()
    toolbar_actions = [a for bar in window.findChildren(QToolBar) for a in bar.actions()]
    assert window.action_redact in toolbar_actions
    texts = [a.text() for menu in window.findChildren(QMenu) for a in menu.actions()]
    assert "Edit Redact Reci&pe..." in texts and "Re&dact" in texts


def test_end_to_end_redact_selected_files(window, tmp_path, shown, tools, recycle_bin):
    a, b = _load(window, tmp_path, ["a.mp3", "b.mp3"])
    untouched = _load(window, tmp_path, ["c.mp3"])[0]
    window.files = [a, b, untouched]
    window._rebuild_table()
    window.table.clearSelection()
    for row in range(window.table.rowCount()):
        if window.table.item(row, 0).data(mw.Qt.ItemDataRole.UserRole) is not untouched:
            window.table.selectionModel().select(
                window.table.model().index(row, 0),
                mw.QItemSelectionModel.SelectionFlag.Select | mw.QItemSelectionModel.SelectionFlag.Rows,
            )
    before_c = untouched.path.read_bytes()
    window.undo_manager.push("x", [a], window._snapshot_mp3)

    window.redact_files()

    assert len(recycle_bin.trashed) == 2
    assert untouched.path.read_bytes() == before_c
    for mp3 in (a, b):
        fresh = MP3File(path=mp3.path)
        load_tags(fresh)
        assert not fresh.load_error and fresh.duration_seconds
        assert fresh.integrity_stamp.startswith("OK;")
        assert not mp3.dirty and mp3.bpm == 120.0 and mp3.integrity_status == STATUS_OK
    assert not window.undo_manager.can_undo()
    (dialog,) = shown
    text = dialog.report.to_text()
    assert "Redact report" in text and "saved in place; the original is in the Recycle Bin" in text
    assert dialog.header_label.text() == mw.REDACT_UNDO_NOTE and dialog.notes_label is None
    assert "c.mp3" not in text
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".mp3redactor-redact-")] == []


def test_nothing_selected_asks_then_redacts_everything(window, tmp_path, shown, tools, recycle_bin, monkeypatch):
    _load(window, tmp_path, ["a.mp3", "b.mp3"])
    window.table.clearSelection()
    asked = []
    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(lambda *a, **k: asked.append(a[2]) or QMessageBox.StandardButton.Yes),
    )
    window.redact_files()
    assert "Redact all 2" in asked[0] and len(recycle_bin.trashed) == 2

    asked.clear()
    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(lambda *a, **k: asked.append(a[2]) or QMessageBox.StandardButton.Cancel),
    )
    window.redact_files()
    assert len(recycle_bin.trashed) == 2  # declined: nothing more happened


def test_unsaved_edits_are_skipped_not_overwritten(window, tmp_path, shown, tools, recycle_bin, monkeypatch):
    a, b = _load(window, tmp_path, ["a.mp3", "b.mp3"])
    a.apply_tags({"title": "My edit"})
    before = a.path.read_bytes()
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    _select_all(window)
    window.redact_files()
    assert a.title == "My edit" and a.dirty and a.path.read_bytes() == before
    assert len(recycle_bin.trashed) == 1  # only b
    (dialog,) = shown
    assert "a.mp3" in dialog.report.to_text() and "unsaved edits" in dialog.report.to_text()
    a.dirty = False


def test_declining_the_unsaved_prompt_cancels_the_run(window, tmp_path, shown, tools, recycle_bin, monkeypatch):
    a, b = _load(window, tmp_path, ["a.mp3", "b.mp3"])
    a.apply_tags({"title": "My edit"})
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Cancel))
    _select_all(window)
    window.redact_files()
    assert recycle_bin.trashed == [] and shown == []
    a.dirty = False


def test_load_error_file_is_skipped_with_a_report_line(window, tmp_path, shown, tools, recycle_bin):
    a, b = _load(window, tmp_path, ["a.mp3", "b.mp3"])
    b.load_error = "failed to read tags: boom"
    _select_all(window)
    window.redact_files()
    assert len(recycle_bin.trashed) == 1
    assert "b.mp3" in shown[0].report.to_text() and "could not be read" in shown[0].report.to_text()


def test_needs_review_shows_in_the_results_dialog(window, tmp_path, shown, tools, monkeypatch, recycle_bin):
    from core.musicbrainz_lookup import Release, ReleaseTrack, match_release

    release = Release(id="r", title="Album", artist="Artist", date="1999",
                      tracks=[ReleaseTrack(1, 1, "Song", 0.4, "rec", "Artist")], track_count=1)
    monkeypatch.setattr(rs, "find_album", lambda facts, query: [match_release(facts, release)])
    (a,) = _load(window, tmp_path, ["a.mp3"])
    a.apply_tags({"title": "Song", "album": "Album"})  # no artist: ~85%, below the default 90%
    from core.tag_writer import save_tags
    assert save_tags(a)
    window.settings.redact_recipe = rs.recipe_to_setting(_only("tags", window.settings))
    _select_all(window)
    window.redact_files()
    (dialog,) = shown
    assert dialog.review_tree.topLevelItemCount() == 1 and a.track == ""
    assert "NEEDS REVIEW" in dialog.report.to_text() and recycle_bin.trashed == []


def _only(key, settings, threshold=0.9):
    catalogue = rs.build_catalogue(settings)
    recipe = rs.Recipe.default_for(catalogue)
    recipe.enabled = {s.key: s.key == key for s in catalogue}
    recipe.confidence_threshold = threshold
    return recipe


def test_edit_recipe_saves_it_in_the_settings(window, monkeypatch):
    saved = []
    monkeypatch.setattr(mw, "save_settings", lambda s, *a, **k: saved.append(s.redact_recipe))

    class FakeDialog:
        def __init__(self, catalogue, recipe, parent):
            assert [s.key for s in catalogue][0] == "integrity" and "save" not in [s.key for s in catalogue]

        def exec(self):
            return mw.QDialog.DialogCode.Accepted

        def recipe(self):
            return _only("bpm", window.settings, 0.6)

    monkeypatch.setattr(mw, "RecipeEditorDialog", FakeDialog)
    window.edit_redact_recipe()
    assert saved and '"confidence_threshold":0.6' in saved[0]
    recipe = window._redact_recipe()
    assert recipe.confidence_threshold == 0.6 and recipe.enabled["bpm"] and not recipe.enabled["integrity"]


def test_leftover_scratch_files_are_cleaned_and_mentioned(window, tmp_path, shown, tools, recycle_bin):
    (a,) = _load(window, tmp_path, ["a.mp3"])
    stale = tmp_path / ".mp3redactor-redact-crashed.mp3"
    stale.write_bytes(b"left behind")
    _select_all(window)
    window.redact_files()
    assert not stale.exists()
    assert "1 leftover scratch file" in shown[0].notes_label.text()
