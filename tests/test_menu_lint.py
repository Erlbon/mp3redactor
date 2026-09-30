"""redactor_common's menu lint run against the real main window, plus the
Ctrl+K command palette. The lint must come back empty: there are no known
exceptions for this app (the zoom toolbar's own Ctrl++ / Ctrl+- bindings are
dropped in favour of the View menu's, which the lint cannot see but Qt would
report as an ambiguous shortcut -- see test_zoom_shortcuts_are_not_ambiguous)."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QAction, QKeySequence  # noqa: E402

from redactor_common.gui.command_palette import collect_commands  # noqa: E402
from redactor_common.gui.menu_lint import lint_menu_bar  # noqa: E402
from tests.test_cover_gui import _app, window  # noqa: E402,F401

# Shape problems the lint may report for this app. Keep empty: fix the menu
# rather than listing an exception here.
KNOWN_EXCEPTIONS: list[str] = []


def test_menu_bar_passes_the_skeleton_lint(window):
    problems = [p for p in lint_menu_bar(window) if p not in KNOWN_EXCEPTIONS]
    assert problems == []


def test_no_shortcut_is_bound_to_two_actions_in_the_window(window):
    bound: dict[str, list[str]] = {}
    for act in window.findChildren(QAction):
        for seq in act.shortcuts():
            bound.setdefault(seq.toString(QKeySequence.SequenceFormat.PortableText), []).append(act.text())
    assert {k: v for k, v in bound.items() if len(v) > 1} == {}


def test_zoom_shortcuts_are_not_ambiguous(window):
    assert window.zoom.zoom_in_action.shortcuts() == []
    assert window.zoom.zoom_out_action.shortcuts() == []
    assert window.action_registry["zoom_in"].shortcut() == QKeySequence("Ctrl++")


def test_command_palette_is_installed_on_ctrl_k(window):
    assert window.command_palette is not None
    action = window.action_registry["command_palette"]
    assert action.shortcut() == QKeySequence("Ctrl+K")
    titles = {c.title for c in collect_commands(window, window.action_registry, exclude=action)}
    for expected in ("Open Files", "Redact", "Check Integrity", "Detect BPM", "Preferences"):
        assert expected in titles, expected


def test_command_palette_lists_commands_with_their_menu_path(window):
    action = window.action_registry["command_palette"]
    by_title = {c.title: c for c in collect_commands(window, window.action_registry, exclude=action)}
    assert by_title["Check Integrity"].path == "Analyze"
    assert by_title["MusicBrainz"].path == "Metadata ▸ Look Up"
    assert by_title["Redact"].path == "Edit"
