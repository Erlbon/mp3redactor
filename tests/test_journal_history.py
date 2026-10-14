"""
The operations journal (core/history.py, Tools > History; owner-approved
design 2026-10-14): every tag change the app writes -- plain Save, Save All,
the command line's `set`, Redact -- is recorded with the old and new text of
each changed field, and apply_restore() (what the History dialog calls)
writes the old values back. Real small MP3 files (tests/fixtures/tiny.mp3).
The conftest gives every test its own journal in tmp_path.
"""

import os
import shutil
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from core import history, tag_writer  # noqa: E402
from core.mp3_file import MP3File  # noqa: E402
from core.scan_service import save_dirty_tags  # noqa: E402
from core.tag_reader import load_tags  # noqa: E402
from core.tag_writer import save_tags  # noqa: E402
from redactor_common.core.journal import Journal  # noqa: E402
from tests.test_cover_gui import _app, window  # noqa: E402,F401

FIXTURE = Path(__file__).parent / "fixtures" / "tiny.mp3"


@pytest.fixture(autouse=True)
def history_on(journal):
    """History is OPT-IN (off by default, see test_journal_opt_in.py); these
    round-trip tests switch it on first, as the user would."""
    history.set_history_enabled(True)
    yield
    history.set_history_enabled(False)


def _song(tmp_path, name="song.mp3", **tags) -> MP3File:
    """A copy of the fixture with `tags` saved WITHOUT a journal entry, loaded fresh."""
    path = tmp_path / name
    shutil.copyfile(FIXTURE, path)
    mp3 = MP3File(path=path)
    load_tags(mp3)
    if tags:
        mp3.apply_tags(tags)
        assert save_tags(mp3, journal_kind=None)
    fresh = MP3File(path=path)
    load_tags(fresh)
    return fresh


def _read(path) -> MP3File:
    mp3 = MP3File(path=Path(path))
    load_tags(mp3)
    return mp3


# --- J1: fields_text, the journal location ---------------------------------------------------------


def test_fields_text_is_the_editable_text_fields_only(tmp_path):
    mp3 = _song(tmp_path, title="T", lyrics="la la")
    mp3.record_scan("integrity", "OK", "")
    mp3.bpm, mp3.key_value = 120.0, "Am"
    mp3.set_cover(b"\xff\xd8 not really a jpeg", "image/jpeg")
    text = history.fields_text(mp3)
    assert text["title"] == "T" and text["lyrics"] == "la la"
    for key in ("comment", "albumartist", "musicbrainz_trackid", "publisher"):
        assert key in text
    for key in ("integrity_stamp", "deep_check_stamp", "bpm", "key_value", "duration_seconds",
                "bitrate_kbps", "cover_pending", "has_cover", "path", "save_error"):
        assert key not in text
    assert all(isinstance(v, str) for v in text.values())


def test_the_default_journal_lives_next_to_the_settings():
    from core.app_paths import base_dir

    assert history.journal_path() == os.path.join(str(base_dir()), "mp3redactor_journal.jsonl")


def test_tests_get_their_own_journal(journal):
    assert history.get_journal() is journal


# --- J2: the baseline ----------------------------------------------------------------------------


def test_baseline_is_taken_on_load_and_after_a_save(tmp_path):
    mp3 = _song(tmp_path, title="Old")
    assert mp3.baseline == history.fields_text(mp3)
    mp3.apply_tags({"title": "New"})
    assert mp3.baseline["title"] == "Old"
    assert save_tags(mp3)
    assert mp3.baseline["title"] == "New"


# --- J3: Save / Save All / the command line ------------------------------------------------------


def test_save_records_old_and_new_values_and_restore_puts_them_back(tmp_path, journal):
    mp3 = _song(tmp_path, title="Old Title", artist="Old Artist", album="Same")
    mp3.apply_tags({"title": "New Title", "artist": "", "album": "Same"})
    assert save_tags(mp3)

    (entry,) = journal.entries()
    assert entry.kind == "save" and entry.status == "ok"
    assert os.path.normcase(entry.path) == os.path.normcase(str(mp3.path))
    assert entry.changes == {"title": ("Old Title", "New Title"), "artist": ("Old Artist", "")}
    assert not entry.cover_changed

    values, skipped = Journal.restore_values(entry)
    assert skipped == []
    assert history.apply_restore(entry.path, values) == ""
    back = _read(mp3.path)
    assert (back.title, back.artist, back.album) == ("Old Title", "Old Artist", "Same")
    assert len(journal.entries()) == 1  # apply_restore writes no entry: the dialog records the restore


def test_a_failed_save_records_failed(tmp_path, journal, monkeypatch):
    mp3 = _song(tmp_path, title="Old")
    mp3.apply_tags({"title": "New"})
    monkeypatch.setattr(tag_writer, "apply_tags_to_file", lambda *_a: "disk on fire")
    assert not save_tags(mp3)
    (entry,) = journal.entries()
    assert entry.status == "failed" and "disk on fire" in entry.error
    assert entry.changes == {"title": ("Old", "New")}
    assert not entry.restorable


def test_a_save_with_no_real_change_records_nothing(tmp_path, journal):
    mp3 = _song(tmp_path, title="Same")
    mp3.apply_tags({"title": "Same"})
    assert save_tags(mp3)
    mp3.record_scan("integrity", "OK", "")  # a scan stamp alone is not a tag edit
    assert save_tags(mp3)
    assert journal.entries() == []


def test_save_all_of_two_files_gives_two_entries(tmp_path, journal):
    a = _song(tmp_path, "a.mp3", title="A")
    b = _song(tmp_path, "b.mp3", title="B")
    untouched = _song(tmp_path, "c.mp3", title="C")
    a.apply_tags({"title": "A2"})
    b.apply_tags({"year": "1999"})
    save_dirty_tags([a, b, untouched])
    entries = journal.entries()
    assert len(entries) == 2
    by_name = {os.path.basename(e.path): e.changes for e in entries}
    assert by_name == {"a.mp3": {"title": ("A", "A2")}, "b.mp3": {"year": ("", "1999")}}


def test_a_cover_change_is_flagged_not_stored(tmp_path, journal):
    mp3 = _song(tmp_path, title="T")
    mp3.set_cover(b"\xff\xd8\xff\xe0 fake jpeg bytes", "image/jpeg")
    assert save_tags(mp3)
    (entry,) = journal.entries()
    assert entry.cover_changed and entry.changes == {}
    assert b"fake jpeg" not in Path(journal.path).read_bytes()


def test_the_command_line_set_is_journaled(tmp_path, journal, capsys):
    from mp3cli.main import main

    mp3 = _song(tmp_path, artist="Old")
    assert main(["set", str(mp3.path), "-s", "artist=Queen", "-q"]) == 0
    (entry,) = journal.entries()
    assert entry.kind == "save" and entry.changes == {"artist": ("Old", "Queen")}


def test_a_journal_failure_never_blocks_a_save_and_is_reported_once(tmp_path, capsys):
    blocker = tmp_path / "not_a_folder"
    blocker.write_text("x")
    history.set_journal_for_tests(Journal(str(blocker / "journal.jsonl")), enabled=True)  # its folder is a file
    a = _song(tmp_path, "a.mp3", title="A")
    b = _song(tmp_path, "b.mp3", title="B")
    a.apply_tags({"title": "A2"})
    b.apply_tags({"title": "B2"})
    assert save_tags(a) and save_tags(b)
    assert _read(a.path).title == "A2" and _read(b.path).title == "B2"
    problem = history.take_journal_problem()
    assert "history could not be written" in problem
    assert history.take_journal_problem() == ""
    assert capsys.readouterr().err.count("history could not be written") == 1


# --- J4: Redact ----------------------------------------------------------------------------------


def test_a_redact_save_records_one_redact_entry(tmp_path, journal, monkeypatch):
    from tests.test_redact_steps import _patch_musicbrainz, run

    _patch_musicbrainz(monkeypatch)
    mp3 = _song(tmp_path, title="Song", album="Album", artist="Artist")
    run(mp3, {"tags"})
    (entry,) = journal.entries()
    assert entry.kind == "redact" and entry.status == "ok"
    assert entry.changes["track"] == ("", "1") and entry.changes["year"] == ("", "1999")
    assert os.path.normcase(entry.path) == os.path.normcase(str(mp3.path))
    assert history.apply_restore(entry.path, Journal.restore_values(entry)[0]) == ""
    back = _read(mp3.path)
    assert back.track == "" and back.year == "" and back.title == "Song"


def test_a_redact_that_changes_nothing_records_nothing(tmp_path, journal):
    from tests.test_redact_steps import run

    mp3 = _song(tmp_path, title="Song")
    run(mp3, set())
    assert journal.entries() == []


def test_a_failed_redact_save_records_failed(tmp_path, journal, monkeypatch):
    import core.redact_steps as rs
    from tests.test_redact_steps import _patch_musicbrainz, run

    _patch_musicbrainz(monkeypatch)
    monkeypatch.setattr(rs, "apply_tags_to_file", lambda *_a: "no space left")
    mp3 = _song(tmp_path, title="Song", album="Album", artist="Artist")
    run(mp3, {"tags"})
    (entry,) = journal.entries()
    assert entry.kind == "redact" and entry.status == "failed" and "no space left" in entry.error


# --- J1: apply_restore's own problems --------------------------------------------------------------


def test_apply_restore_reports_problems_in_plain_language(tmp_path):
    mp3 = _song(tmp_path, title="T")
    assert "does not know" in history.apply_restore(str(mp3.path), {"bogus": "x"})
    broken = tmp_path / "broken.mp3"
    broken.write_bytes(b"not an mp3 at all")
    assert history.apply_restore(str(broken), {"title": "x"}) != ""


def test_apply_restore_keeps_fields_it_was_not_given(tmp_path):
    mp3 = _song(tmp_path, title="Now", artist="Kept", comment="Also kept")
    assert history.apply_restore(str(mp3.path), {"title": "Then"}) == ""
    back = _read(mp3.path)
    assert (back.title, back.artist, back.comment) == ("Then", "Kept", "Also kept")


# --- J5: Tools > History -----------------------------------------------------------------------------


def test_tools_history_opens_the_dialog_and_reloads_restored_rows(window, tmp_path, journal, monkeypatch):
    import gui.main_window as mw

    mp3 = _song(tmp_path, title="Old")
    mp3.apply_tags({"title": "New"})
    window.files = [mp3]
    window.save_changed()
    assert _read(mp3.path).title == "New" and mp3.title == "New"

    seen = {}

    class FakeDialog:
        """Stands in for HistoryDialog: 'restores' the newest entry the way the
        real dialog does (the callable from the app, then the journal record)."""

        def __init__(self, journal_, apply_restore, parent, title):
            seen.update(journal=journal_, parent=parent, title=title)
            self.apply_restore = apply_restore

        def exec(self):
            (entry,) = journal.entries()
            seen["problem"] = self.apply_restore(entry.path, Journal.restore_values(entry)[0])
            return 0

    monkeypatch.setattr(mw, "HistoryDialog", FakeDialog)
    window.action_registry["history"].trigger()
    assert seen == {"journal": journal, "parent": window, "title": "History", "problem": ""}
    assert _read(mp3.path).title == "Old"
    assert mp3.title == "Old" and mp3.baseline["title"] == "Old" and not mp3.dirty


def test_the_history_action_is_in_the_tools_menu(window):
    action = window.action_registry["history"]
    assert action.text().replace("&", "").rstrip("…") == "History"
    assert action.isEnabled()
