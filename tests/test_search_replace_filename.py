"""
Edit > Search and Replace > Filename: renames the files on disk (the name
without its extension), with collisions numbered instead of overwritten,
progress, the rename log behind File > Undo Last Rename, and MP3File.path
updated so a later Save writes to the new name. The real shared dialog is
driven (its own preview decides the new names); only exec() is replaced.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtWidgets import QMessageBox  # noqa: E402

import gui.main_window as mw  # noqa: E402
from core.mp3_file import MP3File  # noqa: E402
from core.tag_reader import load_tags  # noqa: E402
from core.tag_writer import save_tags  # noqa: E402
from redactor_common.gui.search_replace_dialog import FILENAME_FIELD_KEY, SearchReplaceDialog  # noqa: E402
from tests.test_cover_gui import _app, _load, _select_all, window  # noqa: E402,F401


@pytest.fixture
def drive(monkeypatch):
    """drive(find, replace, regex=False, case=False) makes the next Search and
    Replace dialog pick the Filename column, type those and accept. Returns the
    dialog's preview rows as a list of (display name, old, new) tuples, filled
    in when the handler runs."""
    seen: list[tuple[str, str, str]] = []

    def drive(find, replace, regex=False, case=False):
        class Driven(SearchReplaceDialog):
            def exec(self):
                self.field_combo.setCurrentIndex(self.field_combo.findData(FILENAME_FIELD_KEY))
                self.regex_cb.setChecked(regex)
                self.case_sensitive_cb.setChecked(case)
                self.replace_edit.setText(replace)
                self.search_edit.setText(find)
                table = self.preview_table
                seen.clear()
                for r in range(table.rowCount()):  # the tick-box column has no item
                    cells = [table.item(r, c) for c in range(table.columnCount())]
                    seen.append(tuple(cell.text() for cell in cells if cell is not None))
                return self.DialogCode.Accepted

        monkeypatch.setattr(mw, "SearchReplaceDialog", Driven)
        return seen

    return drive


@pytest.fixture
def warnings(monkeypatch):
    shown: list[str] = []
    monkeypatch.setattr(
        QMessageBox, "warning", staticmethod(lambda parent, title, text, *a, **k: shown.append(text))
    )
    return shown


def _names(folder):
    return sorted(p.name for p in folder.iterdir() if p.suffix == ".mp3")  # not the rename log beside them


def test_preview_lists_old_and_new_names_and_skips_unreadable_files(window, tmp_path, drive, warnings):
    a, b, bad = _load(window, tmp_path, ["foo_a.mp3", "foo_b.mp3", "foo_c.mp3"])
    bad.load_error = "failed to read tags: boom"
    _select_all(window)
    rows = drive("foo_", "bar_")
    window.open_search_replace_dialog()
    assert sorted(rows) == [
        ("foo_a.mp3", "foo_a", "bar_a"),
        ("foo_b.mp3", "foo_b", "bar_b"),
    ]
    # The unreadable file is left alone, with a note.
    assert bad.path.name == "foo_c.mp3" and (tmp_path / "foo_c.mp3").exists()
    assert len(warnings) == 1 and "foo_c.mp3" in warnings[0] and "could not be read" in warnings[0]


def test_apply_renames_files_on_disk_keeping_the_extension(window, tmp_path, drive, warnings):
    a, b, c = _load(window, tmp_path, ["foo_a.mp3", "foo_b.mp3", "keep.mp3"])
    _select_all(window)
    drive("foo_", "bar_")
    window.open_search_replace_dialog()
    assert _names(tmp_path) == ["bar_a.mp3", "bar_b.mp3", "keep.mp3"]
    assert (a.path.name, b.path.name, c.path.name) == ("bar_a.mp3", "bar_b.mp3", "keep.mp3")
    assert a.path.parent == tmp_path and a.path.exists()
    column = window._col_index["filename"]
    shown = {window.table.item(r, column).text() for r in range(window.table.rowCount())}
    assert shown == {"bar_a.mp3", "bar_b.mp3", "keep.mp3"}
    assert warnings == []


def test_the_extension_is_not_searched(window, tmp_path, drive):
    (a,) = _load(window, tmp_path, ["song.mp3"])
    _select_all(window)
    rows = drive("mp3", "wav")
    window.open_search_replace_dialog()
    assert rows == [] and a.path.name == "song.mp3"


def test_a_taken_name_is_numbered_never_overwritten(window, tmp_path, drive, warnings):
    (a,) = _load(window, tmp_path, ["x.mp3"])
    existing = tmp_path / "target.mp3"
    existing.write_bytes(b"not mine")
    _select_all(window)
    drive("x", "target")
    window.open_search_replace_dialog()
    assert existing.read_bytes() == b"not mine"
    assert a.path.name == "target (2).mp3" and a.path.exists() and not (tmp_path / "x.mp3").exists()


def test_files_renamed_to_the_same_name_are_numbered(window, tmp_path, drive):
    a1, a2 = _load(window, tmp_path, ["a1.mp3", "a2.mp3"])
    window.files = [a1, a2]
    _select_all(window)
    drive(r"\d", "", regex=True)
    window.open_search_replace_dialog()
    assert _names(tmp_path) == ["a (2).mp3", "a.mp3"]
    assert {a1.path.name, a2.path.name} == {"a.mp3", "a (2).mp3"}


def test_a_case_only_change_renames_the_same_file(window, tmp_path, drive):
    (a,) = _load(window, tmp_path, ["song.mp3"])
    _select_all(window)
    drive("song", "Song", case=True)
    window.open_search_replace_dialog()
    assert a.path.name == "Song.mp3"
    assert _names(tmp_path) == ["Song.mp3"]


def test_a_name_the_filesystem_would_reject_is_reported_and_left_alone(window, tmp_path, drive, warnings):
    a, b = _load(window, tmp_path, ["a.mp3", "b.mp3"])
    _select_all(window)
    drive("a", "bad:name")
    window.open_search_replace_dialog()
    assert a.path.name == "a.mp3" and (tmp_path / "a.mp3").exists()
    assert len(warnings) == 1 and "a.mp3" in warnings[0]


def test_undo_last_rename_puts_the_names_back(window, tmp_path, drive, monkeypatch):
    a, b = _load(window, tmp_path, ["foo_a.mp3", "foo_b.mp3"])
    _select_all(window)
    drive("foo_", "bar_")
    window.open_search_replace_dialog()
    assert _names(tmp_path) == ["bar_a.mp3", "bar_b.mp3"]
    batch = mw._rename_log().last_batch()
    assert batch.label == "Search/Replace (filename)" and len(batch.renames) == 2

    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
    )
    window.undo_last_rename()
    assert _names(tmp_path) == ["foo_a.mp3", "foo_b.mp3"]
    assert {a.path.name, b.path.name} == {"foo_a.mp3", "foo_b.mp3"}
    assert mw._rename_log().last_batch() is None


def test_save_after_rename_writes_to_the_new_path_and_keeps_unsaved_edits(window, tmp_path, drive):
    (a,) = _load(window, tmp_path, ["old.mp3"])
    a.apply_tags({"title": "Unsaved"})  # a pending edit: only the path changes
    assert a.dirty
    _select_all(window)
    drive("old", "new")
    window.open_search_replace_dialog()
    assert a.dirty and a.title == "Unsaved"
    assert save_tags(a)
    assert _names(tmp_path) == ["new.mp3"]
    fresh = MP3File(path=tmp_path / "new.mp3")
    load_tags(fresh)
    assert fresh.title == "Unsaved"


def test_the_rename_runs_with_a_cancellable_progress_dialog(window, tmp_path, drive, monkeypatch):
    _load(window, tmp_path, ["f1.mp3", "f2.mp3", "f3.mp3"])
    _select_all(window)
    calls = []
    real = mw.run_with_progress

    def spy(parent, items, step, label, **kwargs):
        calls.append((label, kwargs))
        return real(parent, items, step, label, **kwargs)

    monkeypatch.setattr(mw, "run_with_progress", spy)
    drive("f", "g")
    window.open_search_replace_dialog()
    (label, kwargs), = calls
    assert label == "Renaming files..." and kwargs["cancellable"] is True
    assert _names(tmp_path) == ["g1.mp3", "g2.mp3", "g3.mp3"]


def test_a_cancelled_rename_still_logs_what_was_renamed(window, tmp_path, drive, monkeypatch):
    _load(window, tmp_path, ["f1.mp3", "f2.mp3", "f3.mp3"])
    _select_all(window)

    def cancel_after_one(parent, items, step, label, **kwargs):
        step(next(iter(items)), 0)
        return False  # the user pressed Cancel

    monkeypatch.setattr(mw, "run_with_progress", cancel_after_one)
    drive("f", "g")
    window.open_search_replace_dialog()
    assert len(mw._rename_log().last_batch().renames) == 1
    assert sum(name.startswith("g") for name in _names(tmp_path)) == 1
