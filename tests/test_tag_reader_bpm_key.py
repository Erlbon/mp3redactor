"""
core.tag_reader.load_tags() reading TBPM/TKEY back on load -- the other
half of the round trip test_tag_writer.py's TBPM/TKEY tests cover.
Without this, a successfully saved BPM/key vanished from the table the
moment the file was reloaded (a fresh load, or just restarting the
app), which read exactly like "it isn't actually writing to the file"
even when it genuinely was -- see core/tag_writer.py's
_write_bpm_frame()/_write_key_frame() docstrings and this file's own
module docstring for the full story.

Own test file rather than folding into test_tag_writer.py since these
exercise load_tags(), a different module, even though the fixture
pattern (real tiny.mp3, copied to tmp_path) is identical.
"""

import shutil
from pathlib import Path

from core.mp3_file import MP3File, STATUS_ERROR, STATUS_OK
from core.tag_reader import load_tags
from core.tag_writer import save_tags

FIXTURE = Path(__file__).parent / "fixtures" / "tiny.mp3"


def _copy_fixture(tmp_path) -> Path:
    dest = tmp_path / "song.mp3"
    shutil.copyfile(FIXTURE, dest)
    return dest


def test_load_tags_reads_an_existing_tbpm_frame(tmp_path):
    from mutagen.id3 import TBPM, ID3

    path = _copy_fixture(tmp_path)
    tags = ID3(path)
    tags.setall("TBPM", [TBPM(encoding=3, text="140")])
    tags.save(path)

    mp3 = MP3File(path=path)
    load_tags(mp3)

    assert mp3.bpm == 140.0
    assert mp3.bpm_status == STATUS_OK
    assert mp3.dirty is False  # reading back an existing tag is not an unsaved change


def test_load_tags_reads_an_existing_tkey_frame(tmp_path):
    from mutagen.id3 import TKEY, ID3

    path = _copy_fixture(tmp_path)
    tags = ID3(path)
    tags.setall("TKEY", [TKEY(encoding=3, text="Abm")])
    tags.save(path)

    mp3 = MP3File(path=path)
    load_tags(mp3)

    assert mp3.key_value == "Abm"
    assert mp3.key_status == STATUS_OK
    assert mp3.dirty is False


def test_load_tags_ignores_a_malformed_tbpm_frame(tmp_path):
    # Some other tool could have written a non-numeric TBPM -- don't
    # crash the whole load over it, just leave bpm unset (same "don't
    # let one bad field abort the file" philosophy as the try/except
    # around MP3(mp3.path) itself).
    from mutagen.id3 import TBPM, ID3

    path = _copy_fixture(tmp_path)
    tags = ID3(path)
    tags.setall("TBPM", [TBPM(encoding=3, text="fast")])
    tags.save(path)

    mp3 = MP3File(path=path)
    load_tags(mp3)

    assert mp3.bpm is None
    assert mp3.bpm_status != STATUS_OK


def test_load_tags_leaves_bpm_and_key_unset_when_no_such_frames_exist(tmp_path):
    path = _copy_fixture(tmp_path)  # pristine fixture, no TBPM/TKEY

    mp3 = MP3File(path=path)
    load_tags(mp3)

    assert mp3.bpm is None
    assert mp3.key_value == ""


def test_detect_bpm_save_reload_round_trip(tmp_path):
    # The exact scenario that looked like data loss: detect (or here,
    # simulate a successful detection the way core.scan_service.
    # run_bpm_check does) -> Save -> reload in a brand new MP3File (as
    # a restart would) -- the value must still be there.
    path = _copy_fixture(tmp_path)
    mp3 = MP3File(path=path)
    mp3.bpm = 127.6
    mp3.dirty = True
    assert save_tags(mp3) is True

    reloaded = MP3File(path=path)
    load_tags(reloaded)
    assert reloaded.bpm == 128.0  # rounded on write, see _write_bpm_frame()
    assert reloaded.bpm_status == STATUS_OK


def test_detect_key_save_reload_round_trip(tmp_path):
    path = _copy_fixture(tmp_path)
    mp3 = MP3File(path=path)
    mp3.key_value = "F#m"
    mp3.key_status = STATUS_OK
    mp3.dirty = True
    assert save_tags(mp3) is True

    reloaded = MP3File(path=path)
    load_tags(reloaded)
    assert reloaded.key_value == "F#m"
    assert reloaded.key_status == STATUS_OK
