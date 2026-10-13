"""2026-10-13 review (A list): a scan stamp alone is not an unsaved edit (A14), the integrity fix is opt-in and an
OK file is not rewritten on every run (A14), the Discogs dialog uses the shared public row API (A15a), and the
lyrics fetch runs off the GUI thread (A15e)."""

import os
import shutil
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402
from redactor_common.core.pipeline import FileStatus  # noqa: E402

from core import redact_steps as rs  # noqa: E402
from core.mp3_file import MP3File, STATUS_OK, STATUS_WARNING  # noqa: E402
from core.tag_reader import load_tags  # noqa: E402
from tests.test_redact_steps import FIXTURE, make_file, run  # noqa: E402

_app = QApplication.instance() or QApplication([])


def test_a14_a_scan_stamp_alone_is_stamp_only_dirty_and_any_edit_clears_it(tmp_path):
    mp3 = make_file(tmp_path)
    assert not mp3.dirty and not mp3.stamp_only_dirty
    mp3.record_scan("integrity", STATUS_OK, "")
    assert mp3.dirty and mp3.stamp_only_dirty
    mp3.title = "x"
    mp3.dirty = True  # what apply_tags() does
    assert mp3.dirty and not mp3.stamp_only_dirty
    fresh = make_file(tmp_path, name="b.mp3")
    fresh.dirty = True
    fresh.record_scan("integrity", STATUS_OK, "")
    assert not fresh.stamp_only_dirty  # an edit was already pending: the stamp doesn't hide it


def test_a14_redact_does_not_skip_a_file_whose_only_unsaved_change_is_a_stamp(tmp_path, monkeypatch):
    mp3 = make_file(tmp_path)
    mp3.record_scan("deep_check", STATUS_OK, "")  # Analyze ran, Save not yet pressed
    assert mp3.dirty and mp3.stamp_only_dirty
    monkeypatch.setattr(rs, "check_integrity", lambda p, override_path=None: (STATUS_OK, ""))
    entry, _ = run(mp3, {"integrity"})
    assert entry.status is not FileStatus.SKIPPED, entry.notes
    edited = make_file(tmp_path, name="e.mp3")
    edited.dirty = True
    entry, _ = run(edited, {"integrity"})
    assert entry.status is FileStatus.SKIPPED


def test_a14_fix_is_off_by_default_and_an_ok_stamped_file_is_not_rewritten(tmp_path, monkeypatch):
    step = next(s for s in rs.build_catalogue(rs.Settings()) if s.key == "integrity")
    assert step.options[0].key == "fix" and step.options[0].default is False
    mp3 = make_file(tmp_path)
    monkeypatch.setattr(rs, "check_integrity", lambda p, override_path=None: (STATUS_OK, ""))
    monkeypatch.setattr(rs, "fix_integrity", lambda *a, **k: pytest.fail("must not fix by default"))
    first, _ = run(mp3, {"integrity"})
    assert first.status is FileStatus.CHANGED  # first run: stamped
    stamped = mp3.path.read_bytes()
    second, _ = run(mp3, {"integrity"})
    assert second.status is FileStatus.UNCHANGED and mp3.path.read_bytes() == stamped
    assert any("already stamped" in n for n in second.notes)
    # a problem found is still reported (scanning stays on), just not fixed
    monkeypatch.setattr(rs, "check_integrity", lambda p, override_path=None: (STATUS_WARNING, "WARNING: a"))
    third, _ = run(mp3, {"integrity"})
    assert any("not fixed" in a for a in third.applied)


def test_a15a_discogs_dialog_uses_the_public_row_api():
    import inspect

    from gui import discogs_lookup_dialog as d

    src = inspect.getsource(d.DiscogsLookupDialog._on_styles_toggled)
    for private in ("_row_results", "_update_row_cells", "_current_detail_row", "_render_detail"):
        assert private not in src
    assert "refresh_row" in src and "rerender_detail" in src


def test_a15e_lyrics_fetch_runs_through_run_busy(tmp_path, monkeypatch):
    from gui import lyrics_dialog as ld

    mp3 = make_file(tmp_path, artist="A", title="T")
    seen = {}

    def fake_run_busy(parent, label, fn, *args, **kwargs):
        seen["fn"], seen["args"] = fn, args
        return ("la la", STATUS_OK, "")

    monkeypatch.setattr(ld, "run_busy", fake_run_busy)
    dialog = ld.LyricsDialog(mp3)
    dialog._fetch()
    assert seen["fn"] is ld.fetch_lyrics and seen["args"][1:] == ("A", "T")
    assert dialog.result_lyrics() == "la la"
