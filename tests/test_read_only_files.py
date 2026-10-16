"""A read-only song through the real save paths (2026-10-16 review, finding 3): a clear 'read-only' message BEFORE
anything is copied, the original untouched (and still read-only), and no scratch copy left in the folder. shutil.copy2
copies the read-only bit, so the scratch copy of a read-only file was read-only too, the tag write failed on it and the
cleanup `os.remove` was refused and swallowed: a hidden .mp3redactor-*.tmp copy of the whole song stayed behind."""

import os
import shutil
import stat
import unittest.mock as mock
from pathlib import Path

import pytest

import core.redact_steps as rs
import core.tag_writer as tw
from core import file_access
from core.mp3_file import STATUS_OK, MP3File
from core.tag_reader import load_tags
from core.tag_writer import save_tags
from redactor_common.core.pipeline import FileStatus
from tests.test_redact_steps import run

FIXTURE = Path(__file__).parent / "fixtures" / "tiny.mp3"


@pytest.fixture
def ro_mp3(tmp_path):
    path = tmp_path / "a.mp3"
    shutil.copyfile(FIXTURE, path)
    mp3 = MP3File(path=path)
    load_tags(mp3)
    os.chmod(path, stat.S_IREAD)
    yield mp3
    os.chmod(path, stat.S_IREAD | stat.S_IWRITE)


def names(folder: Path) -> list[str]:
    return sorted(os.listdir(folder))


def test_saving_a_read_only_mp3_says_so_and_leaves_no_temp_file(ro_mp3):
    before = ro_mp3.path.read_bytes()
    ro_mp3.title, ro_mp3.dirty = "New title", True
    assert save_tags(ro_mp3) is False
    assert "read-only" in ro_mp3.save_error and "a.mp3" in ro_mp3.save_error
    assert "Permission denied" not in ro_mp3.save_error and ".mp3redactor-" not in ro_mp3.save_error
    assert ro_mp3.dirty  # still unsaved
    assert names(ro_mp3.path.parent) == ["a.mp3"]  # no hidden copy of the song
    assert ro_mp3.path.read_bytes() == before and not os.access(ro_mp3.path, os.W_OK)


def test_a_scratch_copy_that_ends_up_read_only_is_removed_after_a_failed_write(tmp_path):
    path = tmp_path / "a.mp3"
    shutil.copyfile(FIXTURE, path)
    mp3 = MP3File(path=path)
    load_tags(mp3)
    mp3.title, mp3.dirty = "New", True

    def fail_on_a_read_only_scratch(m, scratch):
        os.chmod(scratch, stat.S_IREAD)
        return "the write failed"

    with mock.patch.object(tw, "apply_tags_to_file", side_effect=fail_on_a_read_only_scratch):
        assert save_tags(mp3) is False
    assert "the write failed" in mp3.save_error
    assert names(tmp_path) == ["a.mp3"]


def test_a_scratch_copy_is_made_writable_right_after_copying(tmp_path):
    """copy2 hands the copy the source's read-only bit (here: the source turned read-only after the up-front check,
    as a race would); writing the copy still has to work."""
    path = tmp_path / "a.mp3"
    shutil.copyfile(FIXTURE, path)
    mp3 = MP3File(path=path)
    load_tags(mp3)
    mp3.title, mp3.dirty = "Edited", True
    real_copy2 = shutil.copy2

    def copy_then_read_only(src, dst, *a, **k):
        out = real_copy2(src, dst, *a, **k)
        os.chmod(dst, stat.S_IREAD)
        return out

    with mock.patch.object(tw.shutil, "copy2", side_effect=copy_then_read_only):
        assert save_tags(mp3) is True, mp3.save_error
    fresh = MP3File(path=path)
    load_tags(fresh)
    assert fresh.title == "Edited" and names(tmp_path) == ["a.mp3"]


def test_remove_scratch_removes_a_read_only_file_and_accepts_a_missing_one(tmp_path):
    f = tmp_path / "x.tmp"
    f.write_bytes(b"data")
    os.chmod(f, stat.S_IREAD)
    assert file_access.remove_scratch(str(f)) and not f.exists()
    assert file_access.remove_scratch(str(f)) and file_access.remove_scratch("")


def test_a_writable_files_permissions_are_not_touched(tmp_path):
    f = tmp_path / "x.mp3"
    f.write_bytes(b"data")
    mode = os.stat(f).st_mode
    file_access.make_scratch_writable(str(f))
    assert os.stat(f).st_mode == mode


def test_redact_skips_a_read_only_original_before_making_a_working_copy(ro_mp3, monkeypatch, recycle_bin):
    before = ro_mp3.path.read_bytes()
    monkeypatch.setattr(rs, "check_integrity", lambda p, override_path=None: (STATUS_OK, ""))
    entry, _env = run(ro_mp3, {"integrity"})
    assert entry.status is FileStatus.SKIPPED and "read-only" in entry.skips[0] and not entry.failures
    assert ro_mp3.path.read_bytes() == before and recycle_bin.trashed == []
    assert not any(n.startswith(".") for n in os.listdir(ro_mp3.path.parent))  # no hidden scratch of any kind
