"""
core.tag_reader.load_tags() reading TXXX:REPLAYGAIN_TRACK_GAIN back on
load -- the loudness counterpart to test_tag_reader_bpm_key.py (same
"a saved value shouldn't vanish on reload" story, see that file's own
module docstring). Own file since read/write shapes differ enough
(gain_db is parsed back out of a formatted string, and lufs is
back-computed from it algebraically, rather than a direct numeric
round trip) to warrant separate tests rather than folding in.
"""

import shutil
from pathlib import Path

from core.ffmpeg_probe import REPLAYGAIN_REFERENCE_LUFS
from core.mp3_file import MP3File, STATUS_OK
from core.tag_reader import load_tags
from core.tag_writer import save_tags

FIXTURE = Path(__file__).parent / "fixtures" / "tiny.mp3"


def _copy_fixture(tmp_path) -> Path:
    dest = tmp_path / "song.mp3"
    shutil.copyfile(FIXTURE, dest)
    return dest


def test_load_tags_reads_an_existing_replaygain_frame(tmp_path):
    from mutagen.id3 import TXXX, ID3

    path = _copy_fixture(tmp_path)
    tags = ID3(path)
    tags.setall("TXXX:REPLAYGAIN_TRACK_GAIN", [TXXX(encoding=3, desc="REPLAYGAIN_TRACK_GAIN", text=["-3.80 dB"])])
    tags.save(path)

    mp3 = MP3File(path=path)
    load_tags(mp3)

    assert mp3.loudness_gain_db == -3.8
    assert mp3.loudness_lufs == REPLAYGAIN_REFERENCE_LUFS - (-3.8)
    assert mp3.loudness_status == STATUS_OK
    assert mp3.dirty is False  # reading back an existing tag is not an unsaved change


def test_load_tags_ignores_a_malformed_replaygain_frame(tmp_path):
    from mutagen.id3 import TXXX, ID3

    path = _copy_fixture(tmp_path)
    tags = ID3(path)
    tags.setall(
        "TXXX:REPLAYGAIN_TRACK_GAIN",
        [TXXX(encoding=3, desc="REPLAYGAIN_TRACK_GAIN", text=["not a number"])],
    )
    tags.save(path)

    mp3 = MP3File(path=path)
    load_tags(mp3)

    assert mp3.loudness_gain_db is None
    assert mp3.loudness_status != STATUS_OK


def test_load_tags_leaves_loudness_unset_when_no_such_frame_exists(tmp_path):
    path = _copy_fixture(tmp_path)  # pristine fixture, no REPLAYGAIN tag

    mp3 = MP3File(path=path)
    load_tags(mp3)

    assert mp3.loudness_gain_db is None
    assert mp3.loudness_lufs is None


def test_measure_save_reload_round_trip(tmp_path):
    # The exact scenario that mattered for BPM/key: measure (simulated
    # the way core.scan_service.run_loudness_measurement() would set
    # these fields) -> Save -> reload in a brand new MP3File (as a
    # restart would) -- the gain must still be there.
    path = _copy_fixture(tmp_path)
    mp3 = MP3File(path=path)
    mp3.loudness_lufs = -14.2
    mp3.loudness_gain_db = -3.8
    mp3.loudness_status = STATUS_OK
    mp3.dirty = True
    assert save_tags(mp3) is True

    reloaded = MP3File(path=path)
    load_tags(reloaded)
    assert reloaded.loudness_gain_db == -3.8
    assert reloaded.loudness_status == STATUS_OK
