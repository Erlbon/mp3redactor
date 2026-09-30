"""The shortcut table after the menu-skeleton migration: every key this app
bound before still works (none had to move, so no with_aliases() alias was
needed), the new standard keys exist, and F1 no longer opens About."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtGui import QAction, QKeySequence  # noqa: E402

from redactor_common.gui.standard_menus import with_aliases  # noqa: E402
from tests.test_cover_gui import _app, window  # noqa: E402,F401

# (action key, shortcuts it must carry) -- old keys first, then new standard ones.
KEPT = [
    ("open_files", ["Ctrl+O"]),
    ("open_folder", ["Ctrl+Shift+O"]),
    ("save", ["Ctrl+S"]),
    ("rename_file", ["F2"]),
    ("rename_export_move", ["Ctrl+E"]),
    ("parse_filename", ["Ctrl+I"]),
    ("refresh_list", ["F5", "Ctrl+R"]),
    ("undo", ["Ctrl+Z"]),
    ("redo", ["Ctrl+Y"]),
    ("redact", ["Ctrl+Shift+E"]),
    ("zoom_in", ["Ctrl++"]),
    ("zoom_out", ["Ctrl+-"]),
]
NEW = [
    ("save_all", ["Ctrl+Shift+A"]),
    ("remove_from_list", ["Delete"]),
    ("search_replace", ["Ctrl+H"]),
    ("apply", ["Ctrl+Return"]),
    ("reset_zoom", ["Ctrl+0"]),
    ("preferences", ["Ctrl+,"]),
    ("command_palette", ["Ctrl+K"]),
]


@pytest.mark.parametrize("key,expected", KEPT + NEW)
def test_action_carries_its_shortcuts(window, key, expected):
    got = window.action_registry[key].shortcuts()
    assert got == [QKeySequence(text) for text in expected]


def test_f1_is_not_bound_to_anything(window):
    f1 = QKeySequence("F1")
    assert not any(f1 in act.shortcuts() for act in window.findChildren(QAction))
    assert window.action_registry["about"].shortcuts() == []


def test_with_aliases_keeps_the_primary_and_adds_the_old_key(window):
    """The mechanism later shortcut moves use (not needed by this app yet)."""
    act = QAction("probe", window)
    act.setShortcut(QKeySequence("Ctrl+Shift+A"))
    with_aliases(act, "Ctrl+Shift+S", "Ctrl+Shift+A")
    assert act.shortcut() == QKeySequence("Ctrl+Shift+A")
    assert act.shortcuts() == [QKeySequence("Ctrl+Shift+A"), QKeySequence("Ctrl+Shift+S")]
