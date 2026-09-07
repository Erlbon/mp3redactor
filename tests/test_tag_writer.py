"""
Uses a real (tiny, 0.3s silent) MP3 fixture rather than mocking
mutagen's internals -- mutagen genuinely is installed in this sandbox
(unlike aubio, see test_bpm_detector.py's guarded-import test), and a
real load -> edit -> save -> reload round trip through mutagen exercises
the actual ID3 frame handling far more convincingly than asserting which
mock methods got called.
"""

import shutil
from pathlib import Path

from core.mp3_file import MP3File
from core.tag_reader import load_tags
from core.tag_writer import save_tags

FIXTURE = Path(__file__).parent / "fixtures" / "tiny.mp3"


def _copy_fixture(tmp_path) -> Path:
    dest = tmp_path / "song.mp3"
    shutil.copyfile(FIXTURE, dest)
    return dest


def test_save_tags_writes_and_round_trips(tmp_path):
    path = _copy_fixture(tmp_path)
    mp3 = MP3File(path=path)
    mp3.apply_tags(
        {
            "title": "Test Title",
            "artist": "Test Artist",
            "album": "Test Album",
            "track": "3",
            "year": "2026",
            "genre": "Electronic",
            "language": "eng",
        }
    )

    assert save_tags(mp3) is True
    assert mp3.dirty is False
    assert mp3.save_error == ""

    reloaded = MP3File(path=path)
    load_tags(reloaded)
    assert reloaded.title == "Test Title"
    assert reloaded.artist == "Test Artist"
    assert reloaded.album == "Test Album"
    assert reloaded.track == "3"
    assert reloaded.year == "2026"
    assert reloaded.genre == "Electronic"
    assert reloaded.language == "eng"


def test_save_tags_blank_value_removes_the_frame_entirely(tmp_path):
    path = _copy_fixture(tmp_path)
    mp3 = MP3File(path=path)
    mp3.apply_tags({"title": "Has A Title", "artist": "Someone"})
    assert save_tags(mp3) is True

    # Blanking a field and re-saving is how you clear a tag -- confirm
    # the frame is actually gone afterward, not present-but-empty.
    mp3.apply_tags({"title": ""})
    assert save_tags(mp3) is True

    reloaded = MP3File(path=path)
    load_tags(reloaded)
    assert reloaded.title == ""
    assert reloaded.artist == "Someone"  # untouched field survives


def test_save_tags_reports_save_error_for_unwritable_path(tmp_path):
    missing = tmp_path / "does-not-exist.mp3"
    mp3 = MP3File(path=missing)
    mp3.apply_tags({"title": "Whatever"})

    assert save_tags(mp3) is False
    assert mp3.save_error != ""
    assert mp3.dirty is True  # left set -- stays visibly unsaved


def test_save_tags_writes_bpm_to_the_tbpm_frame(tmp_path):
    # mp3.bpm is a detection result (core.scan_service.run_bpm_check),
    # not a core.fields.FIELDS entry -- it doesn't go through
    # apply_tags()/the bulk-edit panel, so this sets it (and dirty) the
    # same way that caller does, and reads the actual frame back via
    # mutagen directly (not load_tags(), which never populates mp3.bpm
    # -- see _write_bpm_frame()'s docstring) to independently confirm
    # what actually landed on disk.
    path = _copy_fixture(tmp_path)
    mp3 = MP3File(path=path)
    mp3.bpm = 127.6
    mp3.dirty = True

    assert save_tags(mp3) is True

    from mutagen.id3 import ID3

    tags = ID3(path)
    # ID3v2's TBPM is specced as an integer numerical string -- 127.6
    # rounds to "128", not truncates to "127".
    assert str(tags["TBPM"].text[0]) == "128"


def test_save_tags_leaves_an_existing_tbpm_frame_alone_when_bpm_was_never_detected(tmp_path):
    # A file can already carry a TBPM tag from other software.
    # tag_reader.load_tags() never reads TBPM into mp3.bpm (BPM
    # detection is aubio-driven, not tag-driven), so mp3.bpm is None for
    # such a file until this app's own Detect BPM runs on it -- saving
    # an unrelated field (Title here) must not silently wipe that
    # pre-existing frame just because this app never looked at it.
    from mutagen.id3 import TBPM, ID3

    path = _copy_fixture(tmp_path)
    tags = ID3(path)
    tags.setall("TBPM", [TBPM(encoding=3, text="140")])
    tags.save(path)

    mp3 = MP3File(path=path)
    mp3.apply_tags({"title": "Untouched BPM"})
    assert mp3.bpm is None

    assert save_tags(mp3) is True

    reloaded_tags = ID3(path)
    assert str(reloaded_tags["TBPM"].text[0]) == "140"
