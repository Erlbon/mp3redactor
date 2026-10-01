"""Transient Windows file locks (PermissionError) must not fail a Save; a lock
that never clears must fail cleanly. The pause is replaced so nothing sleeps."""

import os
import shutil
from pathlib import Path

import pytest

import core.tag_writer as tw
from core import lock_retry
from core.mp3_file import MP3File
from core.tag_reader import load_tags

FIXTURE = Path(__file__).parent / "fixtures" / "tiny.mp3"


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    pauses = []
    monkeypatch.setattr(lock_retry, "sleep", pauses.append)
    return pauses


def _file(tmp_path):
    path = tmp_path / "song.mp3"
    shutil.copyfile(FIXTURE, path)
    mp3 = MP3File(path=path)
    load_tags(mp3)
    mp3.apply_tags({"title": "New Title"})
    return mp3


def flaky(real, failures, exc=None, only=None):
    """Wraps `real` to raise a lock error `failures` times first (`only`: a predicate on the args)."""
    state = {"left": failures, "calls": 0}

    def wrapper(*args, **kwargs):
        if only is None or only(*args):
            state["calls"] += 1
            if state["left"] > 0:
                state["left"] -= 1
                raise exc or PermissionError(13, "Access is denied")
        return real(*args, **kwargs)

    wrapper.state = state
    return wrapper


def leftovers(folder):
    return [p.name for p in folder.iterdir() if p.name.startswith(".mp3redactor-")]


def assert_saved(mp3):
    assert tw.save_tags(mp3) is True and not mp3.dirty and mp3.save_error == ""
    fresh = MP3File(path=mp3.path)
    load_tags(fresh)
    assert fresh.title == "New Title"


@pytest.mark.parametrize("failures", [1, 2])
def test_save_survives_transient_locks_on_replace(tmp_path, monkeypatch, failures):
    mp3 = _file(tmp_path)
    wrapper = flaky(os.replace, failures)
    monkeypatch.setattr(tw.os, "replace", wrapper)
    assert_saved(mp3)
    assert wrapper.state["calls"] == failures + 1 and leftovers(tmp_path) == []


@pytest.mark.parametrize("failures", [1, 2])
def test_save_survives_transient_locks_on_copy(tmp_path, monkeypatch, failures):
    mp3 = _file(tmp_path)
    wrapper = flaky(shutil.copy2, failures)
    monkeypatch.setattr(tw.shutil, "copy2", wrapper)
    assert_saved(mp3)
    assert leftovers(tmp_path) == []


@pytest.mark.parametrize("failures", [1, 2])
def test_save_survives_transient_locks_on_opening_and_writing_the_temp(tmp_path, monkeypatch, failures):
    from mutagen.mp3 import MP3

    mp3 = _file(tmp_path)
    opener = flaky(MP3, failures)
    monkeypatch.setattr("mutagen.mp3.MP3", opener)
    assert_saved(mp3)
    # the write step: fail audio.save() transiently
    mp3.apply_tags({"title": "Again"})
    real_save = MP3.save
    wrapper = flaky(lambda self, *a, **k: real_save(self, *a, **k), failures)
    monkeypatch.setattr("mutagen.mp3.MP3", MP3)
    monkeypatch.setattr(MP3, "save", lambda self, *a, **k: wrapper(self, *a, **k))
    assert tw.save_tags(mp3) is True and mp3.save_error == ""
    assert wrapper.state["calls"] == failures + 1


def test_winerror_lock_codes_are_retried_like_permission_errors(tmp_path, monkeypatch):
    mp3 = _file(tmp_path)
    err = OSError(13, "sharing violation")
    err.winerror = 32
    monkeypatch.setattr(tw.os, "replace", flaky(os.replace, 1, err))
    assert_saved(mp3)


def test_pauses_are_between_attempts_only(tmp_path, monkeypatch, no_sleep):
    mp3 = _file(tmp_path)
    monkeypatch.setattr(tw.os, "replace", flaky(os.replace, 2))
    assert_saved(mp3)
    # One pause per failed attempt, none after the success. The pauses grow
    # (the shared helper backs off), starting at DELAY.
    assert len(no_sleep) == 2
    assert no_sleep[0] == lock_retry.DELAY and no_sleep[1] >= no_sleep[0]


def test_it_is_the_shared_helper_with_six_attempts():
    from redactor_common.core import os_utils

    assert lock_retry.ATTEMPTS == 6
    assert lock_retry.is_lock_error is os_utils.is_lock_error


def test_lock_hint_only_for_lock_errors():
    assert "locked by another program" in lock_retry.lock_hint(PermissionError(13, "denied"))
    assert lock_retry.lock_hint(OSError(28, "No space left on device")) == ""
    assert lock_retry.lock_hint(FileNotFoundError(2, "gone")) == ""


def test_attempts_and_delay_can_be_overridden(no_sleep):
    calls = []

    def op():
        calls.append(1)
        raise PermissionError(13, "denied")

    with pytest.raises(PermissionError):
        lock_retry.retry_on_lock(op, attempts=3, delay=0.5)
    assert len(calls) == 3 and no_sleep[0] == 0.5 and len(no_sleep) == 2


def test_file_exists_and_not_found_are_never_retried(no_sleep):
    for exc in (FileExistsError(17, "exists"), FileNotFoundError(2, "gone"), OSError(28, "full"), ValueError("x")):
        calls = []

        def op(exc=exc):
            calls.append(1)
            raise exc

        with pytest.raises(type(exc)):
            lock_retry.retry_on_lock(op)
        assert len(calls) == 1
    assert no_sleep == []


@pytest.mark.parametrize("where", ["replace", "copy", "open"])
def test_a_lock_that_never_clears_fails_cleanly(tmp_path, monkeypatch, where):
    mp3 = _file(tmp_path)
    before = mp3.path.read_bytes()
    always = 10 ** 6
    if where == "replace":
        wrapper = flaky(os.replace, always)
        monkeypatch.setattr(tw.os, "replace", wrapper)
    elif where == "copy":
        wrapper = flaky(shutil.copy2, always)
        monkeypatch.setattr(tw.shutil, "copy2", wrapper)
    else:
        from mutagen.mp3 import MP3

        wrapper = flaky(MP3, always)
        monkeypatch.setattr("mutagen.mp3.MP3", wrapper)
    assert tw.save_tags(mp3) is False
    assert mp3.dirty and mp3.save_error.startswith("failed to ")
    assert "locked by another program" in mp3.save_error
    assert wrapper.state["calls"] == lock_retry.ATTEMPTS
    assert mp3.path.read_bytes() == before and leftovers(tmp_path) == []


def test_other_errors_are_not_retried_and_do_not_mention_locks(tmp_path, monkeypatch):
    mp3 = _file(tmp_path)
    wrapper = flaky(os.replace, 5, OSError(28, "No space left on device"))
    monkeypatch.setattr(tw.os, "replace", wrapper)
    assert tw.save_tags(mp3) is False
    assert wrapper.state["calls"] == 1 and "locked" not in mp3.save_error and mp3.dirty
    assert leftovers(tmp_path) == []


def test_retry_helper_returns_the_value_and_reraises_the_last_error():
    calls = []

    def op():
        calls.append(1)
        raise PermissionError(13, f"attempt {len(calls)}")

    with pytest.raises(PermissionError, match=f"attempt {lock_retry.ATTEMPTS}"):
        lock_retry.retry_on_lock(op)
    assert len(calls) == lock_retry.ATTEMPTS
    assert lock_retry.retry_on_lock(lambda: 7) == 7
    with pytest.raises(FileNotFoundError):
        lock_retry.retry_on_lock(lambda: open("/definitely/not/here"))
