"""
The operations journal's entries follow a renamed or moved file (redactor_common 2026-10-14-04:
Journal.record_moves, RenameLog's on_move hook): File > Rename File, Search/Replace (filename),
Rename / Export / Move, Undo Last Rename, Redact's rename and move steps, and the command line's
rename, move and redact. Entry.path is where the file is now, Entry.original_path where the change
was made. With history off (the opt-in default) nothing is written.
"""

import json
import os
import shutil
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtWidgets import QMessageBox  # noqa: E402

import gui.main_window as mw  # noqa: E402
from core import history  # noqa: E402
from core.mp3_file import MP3File  # noqa: E402
from core.settings import Settings  # noqa: E402
from core.tag_reader import load_tags  # noqa: E402
from core.tag_writer import save_tags  # noqa: E402
from tests.test_cover_gui import _app, _load, _select_all, window  # noqa: E402,F401
from tests.test_search_replace_filename import drive  # noqa: E402,F401

FIXTURE = Path(__file__).parent / "fixtures" / "tiny.mp3"


def _same(a, b) -> bool:
    return os.path.normcase(os.path.abspath(str(a))) == os.path.normcase(os.path.abspath(str(b)))


def _song(folder, name="song.mp3", **tags) -> MP3File:
    path = Path(folder) / name
    shutil.copyfile(FIXTURE, path)
    mp3 = MP3File(path=path)
    load_tags(mp3)
    if tags:
        mp3.apply_tags(tags)
        assert save_tags(mp3, journal_kind=None)
    fresh = MP3File(path=path)
    load_tags(fresh)
    return fresh


def _saved_once(mp3: MP3File) -> None:
    """One journal entry for `mp3` at its current path."""
    mp3.apply_tags({"title": (mp3.title or "") + " (edited)"})
    assert save_tags(mp3)


# --- the hook itself ---------------------------------------------------------------------------------


def test_the_apps_rename_log_carries_the_journal_hook(_isolated_rename_log, monkeypatch, tmp_path):
    real_rename_log = _isolated_rename_log  # conftest hands back the app's own _rename_log
    monkeypatch.setattr(mw, "base_dir", lambda: tmp_path)
    log = real_rename_log()
    assert log.on_move is history.record_file_moves
    assert _same(log.path, tmp_path / "mp3redactor_rename_log.json")


def test_record_file_moves_does_nothing_while_history_is_off(journal):
    history.record_file_moves([("a.mp3", "b.mp3")])
    assert not os.path.exists(journal.path)
    history.set_history_enabled(True)
    history.record_file_moves([("a.mp3", "b.mp3")])
    assert os.path.exists(journal.path)


# --- the window: Search/Replace (filename) and Undo Last Rename ---------------------------------------


def test_a_gui_rename_and_its_undo_take_the_entry_along(window, tmp_path, drive, journal, monkeypatch):
    history.set_history_enabled(True)
    (a,) = _load(window, tmp_path, ["foo_a.mp3"])
    old = str(a.path)
    _saved_once(a)
    _select_all(window)
    drive("foo_", "bar_")
    window.open_search_replace_dialog()
    new = str(a.path)
    assert os.path.basename(new) == "bar_a.mp3"
    (entry,) = journal.entries()
    assert _same(entry.path, new) and _same(entry.original_path, old)
    assert len(journal.entries(path=new)) == 1

    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    window.undo_last_rename()
    (entry,) = journal.entries()
    assert _same(entry.path, old) and _same(entry.original_path, old)


def test_a_gui_rename_with_history_off_writes_nothing(window, tmp_path, drive, journal):
    (a,) = _load(window, tmp_path, ["foo_a.mp3"])
    _saved_once(a)
    _select_all(window)
    drive("foo_", "bar_")
    window.open_search_replace_dialog()
    assert a.path.name == "bar_a.mp3"
    assert not os.path.exists(journal.path)


# --- Redact: the entry is written where the save happened, then follows the rename / move -------------


def test_redact_rename_step_moves_its_own_entry_to_the_new_name(tmp_path, journal, monkeypatch):
    from tests.test_redact_steps import _patch_musicbrainz, run

    history.set_history_enabled(True)
    _patch_musicbrainz(monkeypatch)
    mp3 = _song(tmp_path, title="Song", album="Album", artist="Artist")
    old = str(mp3.path)
    log = history.make_rename_log(str(tmp_path / "log.json"))
    run(mp3, {"tags", "rename"}, settings=Settings(pattern_history=["%title% renamed"]), rename_log=log)
    assert mp3.path.name == "Song renamed.mp3"
    (entry,) = journal.entries()
    assert entry.kind == "redact"
    assert _same(entry.original_path, old) and _same(entry.path, mp3.path)
    assert log.undo_last() is not None
    (entry,) = journal.entries()
    assert _same(entry.path, old)


def test_redact_move_step_takes_the_entry_into_the_library(tmp_path, journal, monkeypatch):
    import core.redact_steps as rs
    from core.mp3_file import STATUS_ERROR
    from tests.test_redact_steps import run

    history.set_history_enabled(True)
    monkeypatch.setattr(rs, "detect_bpm", lambda p: (None, STATUS_ERROR, "not enough detected beats"))
    root = tmp_path / "library"
    root.mkdir()
    mp3 = _song(tmp_path, title="Song", artist="Band", album="Album", albumartist="Band")
    old = str(mp3.path)
    _saved_once(mp3)
    log = history.make_rename_log(str(tmp_path / "log.json"))
    opts = {"move_into_folders": {"pattern": "%albumartist%/%album%/%title%"}}
    run(mp3, {"move_into_folders"}, opts, settings=Settings(library_root=str(root)), rename_log=log)
    assert _same(mp3.path, root / "Band" / "Album" / "Song (edited).mp3")
    (entry,) = journal.entries()
    assert _same(entry.path, mp3.path) and _same(entry.original_path, old)


# --- the command line ----------------------------------------------------------------------------------


@pytest.fixture
def cli_settings(monkeypatch):
    import mp3cli.files as cli_files

    app = Settings()
    monkeypatch.setattr(cli_files, "load_settings", lambda *a, **k: app)
    return SimpleNamespace(settings=app)


def test_cli_rename_and_move_take_the_entry_along(tmp_path, journal, cli_settings):
    from mp3cli.main import main

    history.set_history_enabled(True)
    mp3 = _song(tmp_path, title="Song", artist="Band", album="Album")
    old = str(mp3.path)
    assert main(["set", old, "-s", "title=Tune", "-q"]) == 0
    assert main(["rename", old, "-p", "%artist% - %title%", "-q"]) == 0
    renamed = tmp_path / "Band - Tune.mp3"
    assert renamed.exists()
    (entry,) = journal.entries()
    assert _same(entry.path, renamed) and _same(entry.original_path, old)

    root = tmp_path / "library"
    root.mkdir()
    assert main(["move", str(renamed), "-p", "%artist%/%title%", "--root", str(root), "-q"]) == 0
    moved = root / "Band" / "Tune.mp3"
    assert moved.exists()
    (entry,) = journal.entries()
    assert _same(entry.path, moved)

    # a copy leaves the original (and its entry) where it is
    assert main(["move", str(moved), "-p", "copies/%title%", "--root", str(root), "--copy", "-q"]) == 0
    (entry,) = journal.entries()
    assert _same(entry.path, moved)


def test_cli_rename_with_history_off_writes_nothing(tmp_path, journal, cli_settings):
    from mp3cli.main import main

    mp3 = _song(tmp_path, title="Song", artist="Band")
    assert main(["rename", str(mp3.path), "-p", "%title%", "-q"]) == 0
    assert (tmp_path / "Song.mp3").exists() and not os.path.exists(journal.path)


def test_cli_redact_rename_takes_the_entry_along(tmp_path, journal, cli_settings):
    from mp3cli.main import main

    history.set_history_enabled(True)
    cli_settings.settings.pattern_history = ["%title% renamed"]
    mp3 = _song(tmp_path, title="Song")
    old = str(mp3.path)
    assert main(["set", old, "-s", "title=Tune", "-q"]) == 0
    recipe = tmp_path / "recipe.json"
    import core.redact_steps as rs

    keys = [step.key for step in rs.build_catalogue(Settings())]  # every other step OFF (no network, no tools)
    recipe.write_text(json.dumps({"order": keys, "enabled": {k: k == "rename" for k in keys}, "options": {}}), encoding="utf-8")
    trash = tmp_path / "trash"
    assert main(["redact", old, "--recipe", str(recipe), "--trash-dir", str(trash), "-q"]) == 0
    renamed = tmp_path / "Tune renamed.mp3"
    assert renamed.exists()
    (entry,) = journal.entries()
    assert _same(entry.path, renamed) and _same(entry.original_path, old)
