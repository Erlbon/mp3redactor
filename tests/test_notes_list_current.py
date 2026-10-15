"""Found on the real display: toggling 'Only uncertain or not found' emptied the list's selection (no row highlighted) while the
panel went on showing one folder; row tooltips popped up at the mouse (their last line seemed to follow it) and are now a
stable line under the list; and the opt-in troubleshooting log of the list."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtCore import QPoint, Qt  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402

from core import notes_debug  # noqa: E402
from tests.test_notes_list_stable import click_row, make_dialog, settle  # noqa: E402


def panel_folder(dlg):
    item = dlg.list_panel._item
    return item.proposal.folder.name if item is not None else None


def current_folder(dlg):
    row = dlg.tree.currentItem()
    return dlg.session.items[row.data(0, Qt.ItemDataRole.UserRole)].proposal.folder.name if row is not None else None


def test_toggling_the_filter_keeps_a_row_highlighted_and_the_panel_in_step_with_it():
    dlg = make_dialog(n=60)
    click_row(dlg, 6)  # a nothing-found row
    assert current_folder(dlg) == panel_folder(dlg) is not None
    for on in (True, False, True, False):
        dlg.filter_check.setChecked(on)
        settle()
        assert dlg.tree.currentItem() is not None, f"filter {on}: no row is highlighted"
        assert current_folder(dlg) == panel_folder(dlg), f"filter {on}: the panel shows another folder than the highlighted row"
        assert dlg.index == dlg.tree.currentItem().data(0, Qt.ItemDataRole.UserRole)
    dlg.done(0)


def test_a_row_that_the_filter_hides_hands_the_selection_to_a_visible_row():
    dlg = make_dialog(n=60)
    confident = next(i for i, it in enumerate(dlg.session.items) if it.proposal.confidence.value == "confident")
    click_row(dlg, confident)
    dlg.filter_check.setChecked(True)  # confident rows are hidden now
    settle()
    assert dlg.tree.currentItem() is not None and current_folder(dlg) == panel_folder(dlg)
    assert dlg.session.items[dlg.index].proposal.confidence.value != "confident"
    dlg.done(0)


def test_the_rows_have_no_tooltips_and_the_hint_under_the_list_follows_the_current_row():
    dlg = make_dialog(n=60)
    tree = dlg.tree
    assert all(not tree.topLevelItem(r).toolTip(c) for r in range(tree.topLevelItemCount()) for c in range(4))
    uncertain = next(i for i, it in enumerate(dlg.session.items) if it.proposal.confidence.value == "uncertain")
    click_row(dlg, uncertain)
    height = dlg.row_hint.height()
    hint = dlg.row_hint.text()
    assert dlg.session.items[uncertain].proposal.folder.name in hint and "Not tickable yet: pick a candidate or a level first." in hint
    confident = next(i for i, it in enumerate(dlg.session.items) if it.proposal.confidence.value == "confident")
    click_row(dlg, confident)
    assert "Not tickable" not in dlg.row_hint.text() and dlg.row_hint.height() == height  # the line keeps its size
    dlg.done(0)


# -- the troubleshooting log ----------------------------------------------------------------------------------------

@pytest.fixture
def log_file(tmp_path, monkeypatch):
    path = tmp_path / "mp3redactor_notes_debug.log"
    monkeypatch.setattr(notes_debug, "log_path", lambda: path)
    monkeypatch.delenv("MP3REDACTOR_NOTES_DEBUG", raising=False)
    notes_debug.set_enabled(False)
    yield path
    notes_debug.set_enabled(None)


def test_the_log_is_off_by_default_and_writes_nothing(log_file):
    dlg = make_dialog(n=30)
    click_row(dlg, 4)
    dlg.filter_check.setChecked(True)
    settle()
    assert not log_file.exists() and notes_debug.enabled() is False
    dlg.done(0)


def test_with_the_environment_variable_it_logs_counts_scroll_values_and_folder_names_only(log_file, monkeypatch):
    monkeypatch.setenv("MP3REDACTOR_NOTES_DEBUG", "1")
    dlg = make_dialog(n=30)
    click_row(dlg, 9)
    dlg.filter_check.setChecked(True)
    settle()
    dlg.done(0)
    text = log_file.read_text(encoding="utf-8")
    assert "fill_tree  rows=30" in text and "current  rows=" in text and "filter  rows=" in text
    assert "scroll=[" in text and "current=" in text and "touched=[Artist 009]" in text  # folder NAMES
    assert "line of text" not in text and "title: X" not in text  # no note contents
    assert "C:" not in text and "lib" not in text  # no paths above the folder name


def test_the_preferences_switch_turns_it_on_and_deletes_the_log(log_file, tmp_path):
    from core.settings import Settings, load_settings, save_settings
    from gui.preferences import DELETE_NOTES_LOG_LABEL, NOTES_DEBUG_LABEL, preference_sections

    assert Settings().notes_debug_log is False
    specs = {spec.label: spec for section in preference_sections() for spec in section.specs}
    assert NOTES_DEBUG_LABEL == "Log the Create Notes list for troubleshooting" and specs[NOTES_DEBUG_LABEL].default is False
    actions = [a.label for section in preference_sections() for a in section.actions]
    assert DELETE_NOTES_LOG_LABEL in actions
    s = Settings()
    s.notes_debug_log = True
    save_settings(s, tmp_path)
    assert load_settings(tmp_path).notes_debug_log is True
    notes_debug.set_enabled(True)
    notes_debug.record("probe", rows=1)
    assert log_file.exists()
    assert notes_debug.delete_log() == "" and not log_file.exists()
    notes_debug.set_enabled(False)
    notes_debug.record("probe", rows=1)
    assert not log_file.exists()
