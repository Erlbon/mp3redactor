"""
core.scan_service.run_lyrics_fetch() -- thread-pooled like
run_key_detection() (see its own test file's docstring for why a
network-bound check gets this treatment). fetch_lyrics() itself is
mocked at the core.scan_service module level (not core.lyricy_fetcher)
so these tests never make a real network call, same convention
test_scan_service_key_detection.py uses for detect_key().
"""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from core.mp3_file import MP3File, STATUS_ERROR, STATUS_OK, STATUS_TOOL_MISSING, STATUS_UNCHECKED
from core.scan_service import run_lyrics_fetch


def _fake_fetch_lyrics(query):
    return (f"lyrics for {query}", STATUS_OK, "")


@patch("core.scan_service.fetch_lyrics", side_effect=_fake_fetch_lyrics)
def test_run_lyrics_fetch_sets_fields_for_every_file(mock_fetch):
    files = [MP3File(path=Path(f"song{i}.mp3"), artist="Artist", title=f"Title{i}") for i in range(4)]

    run_lyrics_fetch(files, max_workers=4)

    for i, mp3 in enumerate(files):
        assert mp3.lyrics == f"lyrics for Artist Title{i}"
        assert mp3.lyrics_status == STATUS_OK
    assert mock_fetch.call_count == 4


@patch("core.scan_service.fetch_lyrics", side_effect=_fake_fetch_lyrics)
def test_run_lyrics_fetch_reports_progress_sequentially_to_total(mock_fetch):
    files = [MP3File(path=Path(f"song{i}.mp3")) for i in range(3)]
    seen = []

    run_lyrics_fetch(files, progress=lambda done, total: seen.append((done, total)), max_workers=3)

    assert seen == [(1, 3), (2, 3), (3, 3)]


@patch("core.scan_service.fetch_lyrics", side_effect=_fake_fetch_lyrics)
def test_run_lyrics_fetch_marks_files_dirty_on_success(mock_fetch):
    files = [MP3File(path=Path("song.mp3"))]
    assert files[0].dirty is False

    run_lyrics_fetch(files, max_workers=1)

    assert files[0].dirty is True


@patch("core.scan_service.fetch_lyrics", return_value=("", STATUS_TOOL_MISSING, "lyricy is not installed"))
def test_run_lyrics_fetch_does_not_mark_dirty_when_tool_missing(mock_fetch):
    files = [MP3File(path=Path("song.mp3"))]

    run_lyrics_fetch(files, max_workers=1)

    assert files[0].dirty is False
    assert files[0].lyrics_status == STATUS_TOOL_MISSING


@patch("core.scan_service.fetch_lyrics", return_value=("", STATUS_ERROR, "no lyrics found"))
def test_run_lyrics_fetch_does_not_mark_dirty_when_nothing_found(mock_fetch):
    files = [MP3File(path=Path("song.mp3"))]

    run_lyrics_fetch(files, max_workers=1)

    assert files[0].dirty is False
    assert files[0].lyrics == ""


def test_run_lyrics_fetch_empty_list_is_a_noop():
    calls = []
    run_lyrics_fetch([], progress=lambda d, t: calls.append((d, t)))
    assert calls == []


@patch("core.scan_service.fetch_lyrics", side_effect=_fake_fetch_lyrics)
@patch("core.scan_service.os.cpu_count", return_value=8)
def test_run_lyrics_fetch_workers_capped_at_file_count(_mock_cpu, _mock_fetch):
    files = [MP3File(path=Path("a.mp3")), MP3File(path=Path("b.mp3"))]
    with patch("core.scan_service.ThreadPoolExecutor", wraps=ThreadPoolExecutor) as mock_pool:
        run_lyrics_fetch(files)
    _, kwargs = mock_pool.call_args
    assert kwargs["max_workers"] == 2


@patch("core.scan_service.fetch_lyrics", side_effect=_fake_fetch_lyrics)
def test_run_lyrics_fetch_should_cancel_stops_further_dispatch(mock_fetch):
    # Same documented race as test_scan_service_key_detection.py's own
    # should_cancel test -- already-launched work finishes rather than
    # being killed, so at most one extra call can sneak through.
    files = [MP3File(path=Path(f"song{i}.mp3")) for i in range(5)]

    run_lyrics_fetch(files, max_workers=1, should_cancel=lambda: True)

    updated = [mp3 for mp3 in files if mp3.lyrics_status != STATUS_UNCHECKED]
    assert 1 <= len(updated) <= 2
    assert 1 <= mock_fetch.call_count <= 2
