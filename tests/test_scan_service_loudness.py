"""
core.scan_service.run_loudness_measurement() -- concurrent thread-pool
dispatch (same shape as run_bpm_check()/run_key_detection()), with
measure_loudness() itself mocked (real-binary-tested in
test_ffmpeg_probe.py). Focus here: dirty-flag behavior, which has the
same "silent audio is a real result but has nothing to write" gate BPM
does NOT have and key detection handles differently again -- see
core/tag_writer.py's _write_loudness_frame() docstring.
"""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from core.mp3_file import MP3File, STATUS_ERROR, STATUS_OK, STATUS_TOOL_MISSING, STATUS_UNCHECKED
from core.scan_service import run_loudness_measurement


def _fake_measure(path, override_path=None):
    return -14.2, -3.8, STATUS_OK, ""


@patch("core.scan_service.measure_loudness", side_effect=_fake_measure)
def test_run_loudness_measurement_sets_fields_for_every_file(mock_measure):
    files = [MP3File(path=Path(f"song{i}.mp3")) for i in range(4)]

    run_loudness_measurement(files, max_workers=4)

    for mp3 in files:
        assert mp3.loudness_lufs == -14.2
        assert mp3.loudness_gain_db == -3.8
        assert mp3.loudness_status == STATUS_OK
    assert mock_measure.call_count == 4


@patch("core.scan_service.measure_loudness", side_effect=_fake_measure)
def test_run_loudness_measurement_marks_dirty_on_a_successful_measurement(mock_measure):
    files = [MP3File(path=Path("song.mp3"))]
    assert files[0].dirty is False

    run_loudness_measurement(files, max_workers=1)

    assert files[0].dirty is True


@patch(
    "core.scan_service.measure_loudness",
    return_value=(None, None, STATUS_OK, "no measurable loudness (silent audio)"),
)
def test_run_loudness_measurement_does_not_mark_dirty_for_silent_audio(mock_measure):
    # STATUS_OK but nothing finite to write -- same "nothing to save"
    # outcome as BPM never detecting a tempo, even though the
    # underlying status is technically OK here (a real, positive
    # result) rather than an error.
    files = [MP3File(path=Path("song.mp3"))]

    run_loudness_measurement(files, max_workers=1)

    assert files[0].loudness_status == STATUS_OK
    assert files[0].loudness_lufs is None
    assert files[0].dirty is False


@patch(
    "core.scan_service.measure_loudness",
    return_value=(None, None, STATUS_TOOL_MISSING, "ffmpeg.exe not found"),
)
def test_run_loudness_measurement_does_not_mark_dirty_when_tool_missing(mock_measure):
    files = [MP3File(path=Path("song.mp3"))]

    run_loudness_measurement(files, max_workers=1)

    assert files[0].dirty is False


@patch(
    "core.scan_service.measure_loudness",
    return_value=(None, None, STATUS_ERROR, "ffmpeg timed out"),
)
def test_run_loudness_measurement_does_not_mark_dirty_on_error(mock_measure):
    files = [MP3File(path=Path("song.mp3"))]

    run_loudness_measurement(files, max_workers=1)

    assert files[0].dirty is False


def test_run_loudness_measurement_empty_list_is_a_noop():
    calls = []
    run_loudness_measurement([], progress=lambda d, t: calls.append((d, t)))
    assert calls == []


@patch("core.scan_service.measure_loudness", side_effect=_fake_measure)
@patch("core.scan_service.os.cpu_count", return_value=8)
def test_run_loudness_measurement_workers_capped_at_file_count(_mock_cpu, mock_measure):
    files = [MP3File(path=Path("a.mp3")), MP3File(path=Path("b.mp3"))]
    with patch("core.scan_service.ThreadPoolExecutor", wraps=ThreadPoolExecutor) as mock_pool:
        run_loudness_measurement(files)
    _, kwargs = mock_pool.call_args
    assert kwargs["max_workers"] == 2


@patch("core.scan_service.measure_loudness", side_effect=_fake_measure)
def test_run_loudness_measurement_should_cancel_stops_further_dispatch(mock_measure):
    files = [MP3File(path=Path(f"song{i}.mp3")) for i in range(5)]

    run_loudness_measurement(files, max_workers=1, should_cancel=lambda: True)

    updated = [mp3 for mp3 in files if mp3.loudness_status != STATUS_UNCHECKED]
    assert 1 <= len(updated) <= 2
