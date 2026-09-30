"""One file's check raising (anything, e.g. UnicodeDecodeError from a
tool's output) becomes that file's STATUS_ERROR; the rest of the batch
still runs. Load-error files never get marked dirty by a detection."""

from pathlib import Path
from unittest.mock import patch

from core.mp3_file import MP3File, STATUS_ERROR, STATUS_OK, STATUS_TOOL_ERROR
from core.scan_service import (
    run_bpm_check, run_deep_check, run_key_detection, run_loudness_measurement,
    run_lyrics_fetch,
)


def _boom_for_bad(result):
    def fake(path, *args, **kwargs):
        if "bad" in str(path):
            raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
        return result
    return fake


def _files():
    return [MP3File(path=Path("bad.mp3")), MP3File(path=Path("good.mp3"))]


@patch("core.scan_service.detect_key", side_effect=_boom_for_bad(("C", STATUS_OK, "")))
def test_key_detection_isolates_a_raising_file(_m):
    bad, good = _files()
    run_key_detection([bad, good], max_workers=2)
    assert bad.key_status == STATUS_ERROR and "UnicodeDecodeError" in bad.key_message
    assert good.key_status == STATUS_OK and good.key_value == "C"


@patch("core.scan_service.detect_bpm", side_effect=_boom_for_bad((120.0, STATUS_OK, "")))
def test_bpm_check_isolates_a_raising_file(_m):
    bad, good = _files()
    run_bpm_check([bad, good], max_workers=2)
    assert bad.bpm_status == STATUS_ERROR and bad.bpm is None
    assert good.bpm == 120.0 and good.dirty


@patch("core.scan_service.fetch_lyrics", side_effect=_boom_for_bad(("la", STATUS_OK, "")))
def test_lyrics_fetch_isolates_a_raising_file(_m):
    bad, good = _files()
    run_lyrics_fetch([bad, good], max_workers=2)
    assert bad.lyrics_status == STATUS_ERROR
    assert good.lyrics == "la"


@patch("core.scan_service.measure_loudness", side_effect=_boom_for_bad((-20.0, 2.0, STATUS_OK, "")))
def test_loudness_isolates_a_raising_file(_m):
    bad, good = _files()
    run_loudness_measurement([bad, good], max_workers=2)
    assert bad.loudness_status == STATUS_ERROR
    assert good.loudness_status == STATUS_OK


@patch("core.scan_service.probe_format", return_value=("", None, None, STATUS_OK, ""))
@patch("core.scan_service.deep_check_integrity", side_effect=_boom_for_bad((STATUS_OK, "")))
def test_deep_check_isolates_a_raising_file(_m, _p):
    bad, good = _files()
    run_deep_check([bad, good], max_workers=2)
    assert bad.deep_check_status == STATUS_TOOL_ERROR
    assert good.deep_check_status == STATUS_OK


@patch("core.scan_service.detect_key", return_value=("C", STATUS_OK, ""))
def test_detection_does_not_mark_a_load_error_file_dirty(_m):
    mp3 = MP3File(path=Path("broken.mp3"))
    mp3.load_error = "failed to read tags"
    run_key_detection([mp3], max_workers=1)
    assert mp3.dirty is False


def test_integrity_fix_reloads_what_mp3val_changed(tmp_path):
    import shutil

    from mutagen.id3 import ID3, TALB, TIT2

    from core.scan_service import run_integrity_fix
    from core.tag_reader import load_tags

    path = tmp_path / "song.mp3"
    shutil.copyfile(Path(__file__).parent / "fixtures" / "tiny.mp3", path)
    tags = ID3(path)
    tags.add(TIT2(encoding=3, text=["Old"]))
    tags.save(path)
    mp3 = MP3File(path=path)
    load_tags(mp3)
    mp3.apply_tags({"album": "Mine"})  # a pending edit

    def fake_fix(p, **kwargs):
        t = ID3(p)
        t.add(TIT2(encoding=3, text=["Fixed"]))
        t.add(TALB(encoding=3, text=["Disk Album"]))
        t.save(p)
        return STATUS_OK, ""

    with patch("core.scan_service.fix_integrity", side_effect=fake_fix):
        run_integrity_fix([mp3])
    assert mp3.title == "Fixed"  # untouched field follows the disk
    assert mp3.album == "Mine" and mp3.dirty  # pending edit survives
    assert mp3.duration_seconds is not None
