"""
core.scan_service.run_bpm_check() runs concurrently (a thread pool of
detect_bpm() calls) rather than one file at a time, same as
run_key_detection() (see test_scan_service_key_detection.py, which this
mirrors) -- confirmed by measurement that aubio's C code releases the
GIL enough for this to be worth it (~2.8x on an 8-worker benchmark),
not just assumed the way subprocess-based keyfinder-cli's GIL release
could be.
"""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from core.mp3_file import MP3File, STATUS_OK, STATUS_UNCHECKED
from core.scan_service import run_bpm_check


def _fake_detect_bpm(path):
    return (120.0, STATUS_OK, "")


@patch("core.scan_service.detect_bpm", side_effect=_fake_detect_bpm)
def test_run_bpm_check_sets_fields_for_every_file(mock_detect):
    files = [MP3File(path=Path(f"song{i}.mp3")) for i in range(4)]

    run_bpm_check(files, max_workers=4)

    for mp3 in files:
        assert mp3.bpm == 120.0
        assert mp3.bpm_status == STATUS_OK
    assert mock_detect.call_count == 4


@patch("core.scan_service.detect_bpm", side_effect=_fake_detect_bpm)
def test_run_bpm_check_reports_progress_sequentially_to_total(mock_detect):
    files = [MP3File(path=Path(f"song{i}.mp3")) for i in range(3)]
    seen = []

    run_bpm_check(files, progress=lambda done, total: seen.append((done, total)), max_workers=3)

    # WHICH file finishes first across threads is nondeterministic, but
    # the (done, total) counter itself is only ever incremented by the
    # single consuming loop in as_completed(), so it's always exactly
    # 1, 2, 3 in order regardless of completion order.
    assert seen == [(1, 3), (2, 3), (3, 3)]


def test_run_bpm_check_empty_list_is_a_noop():
    calls = []
    run_bpm_check([], progress=lambda d, t: calls.append((d, t)))
    assert calls == []


@patch("core.scan_service.detect_bpm", side_effect=_fake_detect_bpm)
@patch("core.scan_service.os.cpu_count", return_value=8)
def test_run_bpm_check_workers_capped_at_file_count(_mock_cpu, _mock_detect):
    # Only 2 files -- shouldn't spin up 8 workers for 2 units of work,
    # even though the (mocked) machine has 8 cores.
    files = [MP3File(path=Path("a.mp3")), MP3File(path=Path("b.mp3"))]
    with patch("core.scan_service.ThreadPoolExecutor", wraps=ThreadPoolExecutor) as mock_pool:
        run_bpm_check(files)
    _, kwargs = mock_pool.call_args
    assert kwargs["max_workers"] == 2


@patch("core.scan_service.detect_bpm", side_effect=_fake_detect_bpm)
def test_run_bpm_check_should_cancel_stops_further_dispatch(mock_detect):
    # max_workers=1 keeps this close to deterministic (only one
    # detect_bpm() call in flight at a time), but not exactly -- there's
    # a genuine race between the worker thread grabbing the next
    # already-queued future the instant it finishes the first one, and
    # the main thread's shutdown(cancel_futures=True) landing, so at
    # most one extra call can sneak through. See
    # test_scan_service_key_detection.py's identical test for the same
    # reasoning in more detail.
    files = [MP3File(path=Path(f"song{i}.mp3")) for i in range(5)]

    run_bpm_check(files, max_workers=1, should_cancel=lambda: True)

    updated = [mp3 for mp3 in files if mp3.bpm_status != STATUS_UNCHECKED]
    assert 1 <= len(updated) <= 2
    assert 1 <= mock_detect.call_count <= 2
