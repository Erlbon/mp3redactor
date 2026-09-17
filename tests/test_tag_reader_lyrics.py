"""
core.tag_reader.load_tags() reading an existing USLT frame back on load
-- the other half of the round trip test_tag_writer.py's lyrics tests
cover. Same "a successful save shouldn't look like it silently failed"
reasoning as test_tag_reader_bpm_key.py, applied to lyrics -- see
core/tag_writer.py's _write_lyrics_frame() docstring.
"""

import shutil
from pathlib import Path

from core.mp3_file import MP3File, STATUS_OK
from core.tag_reader import load_tags
from core.tag_writer import save_tags

FIXTURE = Path(__file__).parent / "fixtures" / "tiny.mp3"


def _copy_fixture(tmp_path) -> Path:
    dest = tmp_path / "song.mp3"
    shutil.copyfile(FIXTURE, dest)
    return dest


def test_load_tags_reads_an_existing_uslt_frame(tmp_path):
    from mutagen.id3 import ID3, USLT

    path = _copy_fixture(tmp_path)
    tags = ID3(path)
    tags.add(USLT(encoding=3, lang="eng", desc="", text="Line one\nLine two"))
    tags.save(path)

    mp3 = MP3File(path=path)
    load_tags(mp3)

    assert mp3.lyrics == "Line one\nLine two"
    assert mp3.lyrics_status == STATUS_OK
    assert mp3.dirty is False  # reading back an existing tag is not an unsaved change


def test_load_tags_leaves_lyrics_unset_when_no_uslt_frame_exists(tmp_path):
    path = _copy_fixture(tmp_path)  # pristine fixture, no USLT

    mp3 = MP3File(path=path)
    load_tags(mp3)

    assert mp3.lyrics == ""


def test_fetch_lyrics_save_reload_round_trip(tmp_path):
    # Simulates a successful core.scan_service.run_lyrics_fetch() result
    # (or a gui/lyrics_dialog.py Save) -> Save -> reload in a brand new
    # MP3File, the same scenario test_tag_reader_bpm_key.py's own round
    # trip tests cover for BPM/key.
    path = _copy_fixture(tmp_path)
    mp3 = MP3File(path=path)
    mp3.lyrics = "Fetched lyrics text"
    mp3.dirty = True
    assert save_tags(mp3) is True

    reloaded = MP3File(path=path)
    load_tags(reloaded)
    assert reloaded.lyrics == "Fetched lyrics text"
    assert reloaded.lyrics_status == STATUS_OK
