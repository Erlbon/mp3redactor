"""Review of 2026-11-27, batch 4 (cards 43-47, 49, 56, 57, 63): the "check first" cards.
Every test fails without its fix. Files live under tmp_path; nothing touches the network."""

import os
from pathlib import Path

import pytest

import core.redact_steps as rs
from core.mp3_file import STATUS_ERROR, STATUS_OK
from core.settings import Settings
from redactor_common.core import secret_store
from tests.test_discogs_lookup import TOKEN
from tests.test_redact_discogs import album_folder, run_files, single_fake
from tests.test_redact_steps import make_file, run


# --- 43: Redact must not commit a copy that is older than the file -------------------------------------


def test_43_redact_does_not_commit_when_the_file_changed_during_the_run(tmp_path, monkeypatch, recycle_bin):
    mp3 = make_file(tmp_path)
    other_taggers_bytes = {}

    def slow_step_while_another_program_saves(path, override_path=None):
        # The integrity step runs on the scratch copy; meanwhile another tagger rewrites the original.
        original = mp3.path
        data = original.read_bytes() + b"\0" * 64
        original.write_bytes(data)
        other_taggers_bytes["data"] = data
        return STATUS_OK, ""

    monkeypatch.setattr(rs, "check_integrity", slow_step_while_another_program_saves)
    entry, _env = run(mp3, {"integrity"})
    assert any("NOT SAVED" in f and "changed by something else" in f for f in entry.failures)
    assert mp3.path.read_bytes() == other_taggers_bytes["data"]  # the newer file stays
    assert recycle_bin.trashed == []
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".mp3redactor-redact-")] == []


# --- 56: Discogs lookups after the Move step must see the whole folder ---------------------------------


def test_56_discogs_lookup_keeps_the_folders_original_file_list_after_move(tmp_path):
    from core.tag_writer import save_tags

    secret_store.set_secret("mp3redactor", "discogs_token", TOKEN)
    # Only the first file carries the album tag; the folder name says nothing about the album.
    files = album_folder(tmp_path, count=5, name="CD1")
    for mp3 in files[1:]:
        mp3.album = ""
        assert save_tags(mp3)
    library = tmp_path / "library"
    library.mkdir()
    settings = Settings(library_root=str(library), rename_zero_pad=True)
    opts = {"move_into_folders": {"pattern": "%albumartist%/%artist%/%track% - %title%"}}
    fake = single_fake()
    entries, _env = run_files(files, fake, settings=settings, only=("discogs", "move_into_folders"), options=opts)
    searches = [r for r in fake.requests if "/database/search" in r.full_url]
    assert len(searches) == 1, fake.requests  # one search for the folder, not a second one for the files left
    for entry in entries:
        assert any("auto-applied at 93%" in a for a in entry.applied), entry


# --- 44: key detection's ffmpeg fallback must not leave ffmpeg running ---------------------------------


def test_44_a_failed_key_analysis_kills_the_ffmpeg_it_started(tmp_path, monkeypatch):
    import io
    import struct
    import sys
    import types

    import core.subprocess_utils as su
    import core.tool_locator as tl
    from core import key_detector as kd
    from core.mp3_file import STATUS_TOOL_ERROR

    monkeypatch.setitem(sys.modules, "numpy", types.ModuleType("numpy"))  # only the import check is used
    fmt = struct.pack("<HHIIHH", 1, 1, 1000, 2000, 2, 16)  # 1 kHz: far too low to analyse
    wav = (b"RIFF" + struct.pack("<I", 1000) + b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt
           + b"data" + struct.pack("<I", 100) + b"\0" * 100)

    class FakeProc:
        def __init__(self):
            self.stdout, self.stderr = io.BytesIO(wav), io.BytesIO(b"")
            self.killed = False
            self.waited = False

        def kill(self):
            self.killed = True

        def wait(self, timeout=None):
            self.waited = True
            return -9 if self.killed else 0

    procs = []

    def fake_popen(args, **kwargs):
        procs.append(FakeProc())
        return procs[-1]

    def too_low(rate, blocks):
        raise kd.KeyDetectionError(f"sample rate {rate} Hz is too low to analyse")

    def no_aubio(path):
        raise ImportError("no aubio")

    monkeypatch.setattr(su, "popen_tool", fake_popen)
    monkeypatch.setattr(kd, "_aubio_blocks", no_aubio)
    monkeypatch.setattr(kd, "_analyse_blocks", too_low)
    monkeypatch.setattr(tl, "find_tool", lambda name, override=None: tmp_path / "ffmpeg.exe")
    key, status, message = kd.detect_key_native(tmp_path / "song.mp3")
    assert status == STATUS_TOOL_ERROR and "too low" in message
    assert len(procs) == 1 and procs[0].killed and procs[0].waited


# --- 46: Look Up Audiobook: no second Search while one runs ------------------------------------------


def test_46_search_and_table_are_disabled_while_a_search_runs(monkeypatch):
    from PyQt6.QtWidgets import QApplication

    import gui.m4b_lookup_dialog as lookup_mod
    from gui.m4b_lookup_dialog import M4bLookupDialog

    _app = QApplication.instance() or QApplication([])
    dlg = M4bLookupDialog("Book", "Author", None)
    seen = []

    def fake_call(fn, *args, **kwargs):
        seen.append((dlg.search_btn.isEnabled(), dlg.table.isEnabled()))
        dlg.search_btn.click()  # a second click inside the nested wait
        raise lookup_mod.AudiobookLookupError("stop here")

    monkeypatch.setattr(lookup_mod, "call_in_background", fake_call)
    dlg._search()
    assert seen == [(False, False)]  # the click on the disabled button started no second search
    assert dlg.search_btn.isEnabled() and dlg.table.isEnabled()  # and both are back afterwards


# --- 47: Notes: writing many notes runs under a progress dialog, Cancel stops between writes ---------


def test_47_write_remaining_and_write_ticked_go_through_the_progress_helper(monkeypatch):
    from types import SimpleNamespace

    from PyQt6.QtWidgets import QApplication, QWidget

    import gui.notes_dialog as nd

    _app = QApplication.instance() or QApplication([])
    written = []

    class FakeSession:
        def __init__(self):
            self.items = [
                SimpleNamespace(tickable=True, ticked=True, proposal=SimpleNamespace(is_update=False, folder=Path(f"x{n}")))
                for n in range(4)
            ]

        def write(self, i):
            written.append(i)
            return SimpleNamespace(status="written", message="", path=f"x{i}")

        def next_pending(self, after=-1):
            return 3

        def totals(self):
            return SimpleNamespace(text=lambda: "", written=0)

        def ticked_pending(self):
            return [0, 1, 2, 3]

    class Holder(QWidget):
        view = "list"
        _lookup_note = _history_note = ""
        write_remaining = nd.NotesReviewDialog.write_remaining
        write_ticked = nd.NotesReviewDialog.write_ticked
        _write_many = nd.NotesReviewDialog._write_many
        _confirm_updates = lambda self, indices: True  # noqa: E731
        _report_problems = lambda self, results: None  # noqa: E731
        _summary_box = _refresh_all = _fill_tree = _update_followup = lambda self: None  # noqa: E731
        _select_tree_index = lambda self, i: None  # noqa: E731
        index = 0

    holder = Holder()
    holder.session = FakeSession()
    calls = []

    def cancelling(parent, items, step, label, **kwargs):
        calls.append((list(items), label))
        step(items[0], 0)  # the person pressed Cancel after the first write
        step(items[1], 1)
        return False

    monkeypatch.setattr(nd, "run_with_progress", cancelling)
    holder.write_remaining()
    assert calls and calls[0][0] == [0, 1, 2, 3] and written == [0, 1]  # stopped between two writes
    written.clear()
    holder.write_ticked()
    assert len(calls) == 2 and written == [0, 1]


# --- 49: Notes: one unexpected exception must not end the whole run ----------------------------------


def test_49_a_malformed_answer_fails_that_folder_not_the_run(tmp_path):
    from core.notes_flow import Fetchers, build_proposals, scan
    from tests.notes_helpers import FakeFetch, musicbrainz_routes, ultravox_library, wikipedia_routes

    ultravox_library(tmp_path)
    for bad in ({"artists": [None]}, {"artists": 5}, {"artists": [{"id": 1, "name": None, "score": "x"}]}):
        routes = [(r"ws/2/artist\?query", bad)] + musicbrainz_routes()
        fetchers = Fetchers(mb=FakeFetch(routes), wiki=FakeFetch(wikipedia_routes()))
        targets = scan([tmp_path], kinds=("artist",)).targets
        proposals = build_proposals(targets, fetchers)
        assert len(proposals) == len(targets) and proposals
        assert all(p.confidence.value == "none" and p.problems for p in proposals), bad


# --- 57: m4b: a leftover .replaced.m4b is never overwritten and is reported ---------------------------


def test_57_a_stale_replaced_file_is_kept_and_reported(tmp_path):
    from types import SimpleNamespace

    from core.m4b_builder import _install, _sweep_stale_builds

    output = tmp_path / "Book.m4b"
    aside = tmp_path / ".Book.replaced.m4b"
    output.write_bytes(b"older audiobook A")
    aside.write_bytes(b"audiobook left by a killed run")
    old = 1_000_000_000
    os.utime(aside, (old, old))  # well past the sweep's age limit
    final_tmp = tmp_path / ".Book.building.m4b"
    final_tmp.write_bytes(b"new audiobook")
    trashed = []
    spec = SimpleNamespace(trash=lambda p: (trashed.append(Path(p).read_bytes()), os.remove(p)))

    _sweep_stale_builds(tmp_path)
    assert aside.exists()  # never swept: it is the user's older audiobook
    note = _install(final_tmp, output, spec)
    assert output.read_bytes() == b"new audiobook"
    assert trashed == [b"older audiobook A"]
    kept = tmp_path / "Book (earlier build).m4b"
    assert kept.read_bytes() == b"audiobook left by a killed run"  # not overwritten
    assert ".Book.replaced.m4b" in note and kept.name in note
    assert not aside.exists()


# --- 63: the wheel over a bulk-edit field needs focus first -----------------------------------------


def test_63_wheel_over_an_unfocused_field_changes_nothing(monkeypatch):
    from PyQt6.QtCore import QPoint, QPointF, Qt
    from PyQt6.QtGui import QWheelEvent
    from PyQt6.QtWidgets import QApplication

    from gui.tag_panel import MultiValueLineEdit

    _app = QApplication.instance() or QApplication([])
    edit = MultiValueLineEdit()
    edit.set_alternatives(["Rock", "Jazz"])
    edit.setPlaceholderText("<multiple values>")
    picked = []
    edit.valuePicked.connect(lambda: picked.append(1))

    def wheel():
        event = QWheelEvent(
            QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, 120), Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False,
        )
        edit.wheelEvent(event)
        return event

    event = wheel()
    assert edit.text() == "" and picked == [] and not event.isAccepted()  # unfocused: nothing changes
    monkeypatch.setattr(edit, "hasFocus", lambda: True)
    wheel()
    assert edit.text() == "Rock" and picked == [1]  # focused: cycles as before
    assert edit.focusPolicy() == Qt.FocusPolicy.StrongFocus
