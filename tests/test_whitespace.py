"""
Stray whitespace in tags (2026-11-25, whitespace decisions 1A-4A): the
tidy rules (core/whitespace.py), the Redact step that applies them, and
Edit > Tidy Whitespace... (gui/whitespace_dialog.py) in the main window.
"""

import os
import shutil
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox  # noqa: E402

import core.redact_steps as rs  # noqa: E402
from core.mp3_file import MP3File  # noqa: E402
from core.settings import Settings  # noqa: E402
from core.tag_reader import load_tags  # noqa: E402
from core.tag_writer import save_tags  # noqa: E402
from core.whitespace import (  # noqa: E402
    TIDY_KEYS,
    find_untidy,
    fixes_for,
    show_whitespace,
    tidy_field,
    tidy_value,
)
from redactor_common.core.pipeline import FileStatus, Recipe, run_recipe_on_item  # noqa: E402

_app = QApplication.instance() or QApplication([])
FIXTURE = Path(__file__).parent / "fixtures" / "tiny.mp3"
NBSP = " "
ZWSP = "​"
BOM = "﻿"


# --- the rules -------------------------------------------------------------------


@pytest.mark.parametrize(
    "before, after",
    [
        ("  Switzerland", "Switzerland"),  # the Bloodstains Across Switzerland case
        ("Switzerland  ", "Switzerland"),
        ("Iron  Maiden", "Iron Maiden"),
        ("Iron\tMaiden", "Iron Maiden"),
        ("Iron\r\nMaiden\n", "Iron Maiden"),
        (f"Vol.{NBSP}1", "Vol. 1"),
        (f"Vol. 1", "Vol. 1"),
        (f"{BOM}Title{ZWSP}", "Title"),
        (f"Ti{ZWSP}tle", "Title"),
        ("   ", ""),
        ("", ""),
        ("Already tidy", "Already tidy"),
    ],
)
def test_one_line_values(before, after):
    assert tidy_value(before) == after
    assert tidy_field("album", before) == after


def test_comment_and_lyrics_keep_their_line_breaks_and_inner_spaces():
    text = f"\n  First line  \n    indented  line\t\r\nlast{ZWSP} line {NBSP}\n\n"
    expected = "First line\n    indented  line\r\nlast line"
    assert tidy_value(text, multiline=True) == expected
    assert tidy_field("comment", text) == expected
    assert tidy_field("lyrics", text) == expected
    assert tidy_field("comment", "Two\n\nparagraphs") == "Two\n\nparagraphs"


def test_every_editable_text_tag_is_checked_and_scan_stamps_are_not():
    assert {"title", "artist", "album", "albumartist", "comment", "lyrics", "track", "publisher"} <= set(TIDY_KEYS)
    assert "integrity_stamp" not in TIDY_KEYS and "deep_check_stamp" not in TIDY_KEYS
    assert TIDY_KEYS[0] == "title" and TIDY_KEYS[-1] == "lyrics"


def test_finder_lists_file_and_field_and_skips_unreadable_files(tmp_path):
    good = MP3File(path=tmp_path / "a.mp3", album="  Switzerland", title="Fine", comment="ok ")
    broken = MP3File(path=tmp_path / "b.mp3", album="  x", load_error="unreadable")
    fixes = find_untidy([good, broken])
    assert [(f.item, f.key, f.label, f.old, f.new) for f in fixes] == [
        (good, "album", "Album", "  Switzerland", "Switzerland"),
        (good, "comment", "Comment", "ok ", "ok"),
    ]


def test_show_whitespace_marks_only_the_untidy_spaces():
    assert show_whitespace("  Switzerland") == "··Switzerland"
    assert show_whitespace("Iron  Maiden ") == "Iron··Maiden·"
    assert show_whitespace("Iron Maiden") == "Iron Maiden"
    assert show_whitespace(f"a\tb{NBSP}c{ZWSP}\nd") == "a→b⍽c¤↵d"
    # In a comment the indentation is kept, so it is not marked; a space before a line break is.
    assert show_whitespace("one \n    two", multiline=True) == "one·↵    two"


# --- the Redact step --------------------------------------------------------------


def _song(tmp_path, name="a.mp3", **tags):
    path = tmp_path / name
    shutil.copyfile(FIXTURE, path)
    mp3 = MP3File(path=path)
    load_tags(mp3)
    for key, value in tags.items():
        setattr(mp3, key, value)
    assert save_tags(mp3)
    return mp3


def _run(mp3, only):
    env = rs.RedactEnv(Settings())
    env.begin([mp3])
    catalogue = rs.build_catalogue(Settings())
    recipe = Recipe.default_for(catalogue)
    recipe.enabled = {s.key: s.key in only for s in catalogue}
    return run_recipe_on_item(
        mp3, recipe.resolve(catalogue), 0.9, lambda m: rs.Mp3Ctx(m, env), lambda m: m.filename,
        finalize=rs.save_stage, finalize_label=rs.FINALIZE_LABEL,
    )


def test_the_step_is_first_and_on_by_default():
    catalogue = rs.build_catalogue(Settings())
    assert catalogue[0].key == "tidy_whitespace"
    assert catalogue[0].default_enabled and catalogue[0].dry_run_safe
    assert Recipe.default_for(catalogue).resolve(catalogue)[0][0].key == "tidy_whitespace"


def test_a_saved_recipe_from_before_gets_the_step_first():
    catalogue = rs.build_catalogue(Settings())
    old = Recipe.from_dict({"order": [s.key for s in catalogue if s.key != "tidy_whitespace"],
                            "enabled": {"integrity": True}, "options": {}})
    assert old.resolve(catalogue)[0][0].key == "tidy_whitespace"


def test_redact_tidies_the_tags_in_the_file(tmp_path, recycle_bin):
    mp3 = _song(tmp_path, album="  Switzerland", artist="Krokus", comment="line one  \nline two ")
    entry = _run(mp3, {"tidy_whitespace"})
    assert entry.status is FileStatus.CHANGED
    assert any("album" in change and "'Switzerland'" in change for change in entry.applied)
    fresh = MP3File(path=mp3.path)
    load_tags(fresh)
    assert fresh.album == "Switzerland" and fresh.artist == "Krokus"
    assert fresh.comment == "line one\nline two"
    assert mp3.album == "Switzerland" and not mp3.dirty
    assert len(recycle_bin.trashed) == 1


def test_redact_leaves_a_tidy_file_alone(tmp_path, recycle_bin):
    mp3 = _song(tmp_path, album="Switzerland", artist="Krokus")
    before = mp3.path.read_bytes()
    entry = _run(mp3, {"tidy_whitespace"})
    assert entry.status is not FileStatus.CHANGED
    assert mp3.path.read_bytes() == before
    assert recycle_bin.trashed == []


# --- Edit > Tidy Whitespace... -----------------------------------------------------


@pytest.fixture
def window(monkeypatch):
    import gui.main_window as mw

    monkeypatch.setattr(mw, "save_settings", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))
    w = mw.MainWindow()
    w.show()
    yield w
    for mp3 in w.files:
        mp3.dirty = False
    w.close()


def test_the_menu_item_is_in_the_edit_menu(window):
    action = window.action_registry["tidy_whitespace"]
    assert action.text().replace("&", "") == "Tidy Whitespace…"
    edit = next(a.menu() for a in window.menuBar().actions() if a.text().replace("&", "") == "Edit")
    assert action in edit.actions()


def test_the_window_lists_ticks_and_tidies_in_memory_with_undo(window, tmp_path, monkeypatch):
    import gui.main_window as mw

    a = _song(tmp_path, "a.mp3", album="  Switzerland", title="One")
    b = _song(tmp_path, "b.mp3", album="  Switzerland", title=f"Two{NBSP}Words")
    c = _song(tmp_path, "c.mp3", album="Tidy", title="Three")
    window.files = [a, b, c]
    window._rebuild_table()
    seen = {}

    def fake_exec(dialog):
        seen["rows"] = dialog.table.rowCount()
        seen["now"] = sorted(dialog.table.item(r, 2).text() for r in range(dialog.table.rowCount()))
        # Untick b's title: only the two albums are tidied.
        for row in range(dialog.table.rowCount()):
            if dialog.table.item(row, 1).text() == "Title":
                dialog.table.item(row, 0).setCheckState(Qt.CheckState.Unchecked)
        seen["button"] = dialog.tidy_button.text()
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(mw.TidyWhitespaceDialog, "exec", fake_exec)
    window.open_tidy_whitespace_dialog()  # nothing selected: every loaded file
    assert seen["rows"] == 3
    assert seen["now"] == ["Two⍽Words", "··Switzerland", "··Switzerland"]
    assert seen["button"] == "Tidy Ticked (2)"
    assert a.album == b.album == "Switzerland" and a.dirty and b.dirty
    assert b.title == f"Two{NBSP}Words" and not c.dirty
    fresh = MP3File(path=a.path)
    load_tags(fresh)
    assert fresh.album == "  Switzerland"  # nothing written until Save
    window.undo_last_action()
    assert a.album == b.album == "  Switzerland"


def test_nothing_untidy_shows_a_message_and_no_window(window, tmp_path, monkeypatch):
    import gui.main_window as mw

    window.files = [_song(tmp_path, album="Tidy")]
    window._rebuild_table()
    messages = []
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: messages.append(a[2])))
    monkeypatch.setattr(mw.TidyWhitespaceDialog, "exec", lambda d: pytest.fail("no window expected"))
    window.open_tidy_whitespace_dialog()
    assert messages and "No stray whitespace" in messages[0]


def test_fixes_for_reads_lyrics_too(tmp_path):
    mp3 = MP3File(path=tmp_path / "a.mp3", lyrics="la la  \n")
    assert [(f.key, f.new) for f in fixes_for(mp3)] == [("lyrics", "la la")]
