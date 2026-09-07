"""
core.scan_service.run_deep_check() -- runs a full ffmpeg decode and an
ffprobe format query per file, concurrently via a thread pool (same
reasoning as run_bpm_check()/run_key_detection(), see either one's
docstring). Mocked here (deep_check_integrity/probe_format are
themselves real-binary-tested in test_ffmpeg_probe.py) so this file
can focus purely on the orchestration: dispatch, dirty-flag behavior
(there should be none -- this is a read-only check), progress
reporting, cancellation.
"""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from core.mp3_file import MP3File, STATUS_ERROR, STATUS_OK, STATUS_TOOL_MISSING, STATUS_UNCHECKED
from core.scan_service import run_deep_check


def _fake_deep_check(path, override_path=None):
    return STATUS_OK, ""


def _fake_probe(path, override_path=None):
    return "LAME3.100", 44100, 2, STATUS_OK, ""


@patch("core.scan_service.probe_format", side_effect=_fake_probe)
@patch("core.scan_service.deep_check_integrity", side_effect=_fake_deep_check)
def test_run_deep_check_sets_check_and_probe_fields(mock_check, mock_probe):
    files = [MP3File(path=Path(f"song{i}.mp3")) for i in range(3)]

    run_deep_check(files, max_workers=3)

    for mp3 in files:
        assert mp3.deep_check_status == STATUS_OK
        assert mp3.audio_encoder == "LAME3.100"
        assert mp3.sample_rate_hz == 44100
        assert mp3.channels == 2
    assert mock_check.call_count == 3
    assert mock_probe.call_count == 3


@patch("core.scan_service.probe_format", side_effect=_fake_probe)
@patch("core.scan_service.deep_check_integrity", side_effect=_fake_deep_check)
def test_run_deep_check_never_marks_dirty(mock_check, mock_probe):
    # Purely diagnostic -- neither deep_check_status/message nor the
    # probe fields are ID3 data, so there's nothing for Save to write
    # and this must never flip dirty, unlike BPM/key/loudness.
    files = [MP3File(path=Path("song.mp3"))]

    run_deep_check(files, max_workers=1)

    assert files[0].dirty is False


@patch(
    "core.scan_service.probe_format",
    return_value=("", None, None, STATUS_ERROR, "ffprobe failed"),
)
@patch("core.scan_service.deep_check_integrity", return_value=(STATUS_OK, ""))
def test_run_deep_check_keeps_probe_fields_unset_when_probe_fails(mock_check, mock_probe):
    # A file can decode cleanly (deep check OK) while ffprobe itself
    # fails for some unrelated reason -- the probe fields should stay
    # at their defaults rather than get populated with garbage.
    files = [MP3File(path=Path("song.mp3"))]

    run_deep_check(files, max_workers=1)

    assert files[0].deep_check_status == STATUS_OK
    assert files[0].audio_encoder == ""
    assert files[0].sample_rate_hz is None


@patch("core.scan_service.probe_format", side_effect=_fake_probe)
@patch(
    "core.scan_service.deep_check_integrity",
    return_value=(STATUS_TOOL_MISSING, "ffmpeg.exe not found"),
)
def test_run_deep_check_tool_missing(mock_check, mock_probe):
    files = [MP3File(path=Path("song.mp3"))]

    run_deep_check(files, max_workers=1)

    assert files[0].deep_check_status == STATUS_TOOL_MISSING


@patch("core.scan_service.probe_format", side_effect=_fake_probe)
@patch("core.scan_service.deep_check_integrity", side_effect=_fake_deep_check)
def test_run_deep_check_reports_progress_sequentially_to_total(mock_check, mock_probe):
    files = [MP3File(path=Path(f"song{i}.mp3")) for i in range(3)]
    seen = []

    run_deep_check(files, progress=lambda done, total: seen.append((done, total)), max_workers=3)

    assert seen == [(1, 3), (2, 3), (3, 3)]


def test_run_deep_check_empty_list_is_a_noop():
    calls = []
    run_deep_check([], progress=lambda d, t: calls.append((d, t)))
    assert calls == []


@patch("core.scan_service.probe_format", side_effect=_fake_probe)
@patch("core.scan_service.deep_check_integrity", side_effect=_fake_deep_check)
@patch("core.scan_service.os.cpu_count", return_value=8)
def test_run_deep_check_workers_capped_at_file_count(_mock_cpu, mock_check, mock_probe):
    files = [MP3File(path=Path("a.mp3")), MP3File(path=Path("b.mp3"))]
    with patch("core.scan_service.ThreadPoolExecutor", wraps=ThreadPoolExecutor) as mock_pool:
        run_deep_check(files)
    _, kwargs = mock_pool.call_args
    assert kwargs["max_workers"] == 2


@patch("core.scan_service.probe_format", side_effect=_fake_probe)
@patch("core.scan_service.deep_check_integrity", side_effect=_fake_deep_check)
def test_run_deep_check_should_cancel_stops_further_dispatch(mock_check, mock_probe):
    files = [MP3File(path=Path(f"song{i}.mp3")) for i in range(5)]

    run_deep_check(files, max_workers=1, should_cancel=lambda: True)

    updated = [mp3 for mp3 in files if mp3.deep_check_status != STATUS_UNCHECKED]
    assert 1 <= len(updated) <= 2
