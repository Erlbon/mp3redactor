"""
core.scan_service.run_key_detection() is the one function in that
module that runs work concurrently (a thread pool of keyfinder-cli
subprocess calls) rather than one file at a time -- see its docstring
for why. Its own module gets a dedicated test file rather than joining
test_scan_service.py (which only covers find_mp3_files() today) since
the concurrency behavior needs a bit more setup/explanation than that
file's existing tests.
"""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from core.mp3_file import MP3File, STATUS_OK, STATUS_UNCHECKED
from core.scan_service import run_key_detection


def _fake_detect_key(path, override_path=None):
    return (f"KEY-{path.name}", STATUS_OK, "")


@patch("core.scan_service.detect_key", side_effect=_fake_detect_key)
def test_run_key_detection_sets_fields_for_every_file(mock_detect):
    files = [MP3File(path=Path(f"song{i}.mp3")) for i in range(4)]

    run_key_detection(files, max_workers=4)

    for i, mp3 in enumerate(files):
        assert mp3.key_value == f"KEY-song{i}.mp3"
        assert mp3.key_status == STATUS_OK
    assert mock_detect.call_count == 4


@patch("core.scan_service.detect_key", side_effect=_fake_detect_key)
def test_run_key_detection_reports_progress_sequentially_to_total(mock_detect):
    files = [MP3File(path=Path(f"song{i}.mp3")) for i in range(3)]
    seen = []

    run_key_detection(files, progress=lambda done, total: seen.append((done, total)), max_workers=3)

    # WHICH file finishes first across threads is nondeterministic, but
    # the (done, total) counter itself is only ever incremented by the
    # single consuming loop in as_completed(), so it's always exactly
    # 1, 2, 3 in order regardless of completion order.
    assert seen == [(1, 3), (2, 3), (3, 3)]


def test_run_key_detection_empty_list_is_a_noop():
    calls = []
    run_key_detection([], progress=lambda d, t: calls.append((d, t)))
    assert calls == []


@patch("core.scan_service.detect_key")
def test_run_key_detection_forwards_override_path(mock_detect):
    mock_detect.return_value = ("A", STATUS_OK, "")
    files = [MP3File(path=Path("song.mp3"))]

    run_key_detection(files, keyfinder_cli_path="C:/custom/keyfinder-cli.exe")

    mock_detect.assert_called_once_with(Path("song.mp3"), override_path="C:/custom/keyfinder-cli.exe")


@patch("core.scan_service.detect_key", side_effect=_fake_detect_key)
@patch("core.scan_service.os.cpu_count", return_value=8)
def test_run_key_detection_workers_capped_at_file_count(_mock_cpu, _mock_detect):
    # Only 2 files -- shouldn't spin up 8 workers for 2 units of work,
    # even though the (mocked) machine has 8 cores.
    files = [MP3File(path=Path("a.mp3")), MP3File(path=Path("b.mp3"))]
    with patch("core.scan_service.ThreadPoolExecutor", wraps=ThreadPoolExecutor) as mock_pool:
        run_key_detection(files)
    _, kwargs = mock_pool.call_args
    assert kwargs["max_workers"] == 2


@patch("core.scan_service.detect_key", side_effect=_fake_detect_key)
def test_run_key_detection_should_cancel_stops_further_dispatch(mock_detect):
    # max_workers=1 keeps this close to deterministic (only one
    # keyfinder-cli call in flight at a time), but not exactly --
    # there's a genuine race between the worker thread grabbing the
    # next already-queued future the instant it finishes the first one,
    # and the main thread's shutdown(cancel_futures=True) landing, so
    # at most one extra call can sneak through. That's the documented
    # trade-off (already-launched work finishes rather than being
    # killed), not a bug -- what matters is the bulk of the 5 files
    # never get dispatched at all.
    files = [MP3File(path=Path(f"song{i}.mp3")) for i in range(5)]

    run_key_detection(files, max_workers=1, should_cancel=lambda: True)

    updated = [mp3 for mp3 in files if mp3.key_status != STATUS_UNCHECKED]
    assert 1 <= len(updated) <= 2
    assert 1 <= mock_detect.call_count <= 2
