"""
core.scan_service.run_import_conversion() -- sequential (unlike the
thread-pooled checks), driving core.mp3_converter.convert_to_mp3()
(mocked here; real-binary-tested in test_mp3_converter.py) once per
(src, dest) pair.
"""

from pathlib import Path
from unittest.mock import patch

from core.mp3_file import STATUS_ERROR, STATUS_OK
from core.scan_service import run_import_conversion


@patch("core.scan_service.convert_to_mp3")
def test_run_import_conversion_converts_every_pair(mock_convert):
    mock_convert.return_value = (STATUS_OK, "")
    conversions = [(Path(f"song{i}.flac"), Path(f"song{i}.mp3")) for i in range(3)]

    results = run_import_conversion(conversions, bitrate_kbps=192)

    assert mock_convert.call_count == 3
    assert results == [
        (Path("song0.flac"), Path("song0.mp3"), STATUS_OK, ""),
        (Path("song1.flac"), Path("song1.mp3"), STATUS_OK, ""),
        (Path("song2.flac"), Path("song2.mp3"), STATUS_OK, ""),
    ]


@patch("core.scan_service.convert_to_mp3")
def test_run_import_conversion_forwards_bitrate_and_ffmpeg_path(mock_convert):
    mock_convert.return_value = (STATUS_OK, "")
    conversions = [(Path("song.wav"), Path("song.mp3"))]

    run_import_conversion(conversions, bitrate_kbps=320, ffmpeg_path="C:/custom/ffmpeg.exe")

    mock_convert.assert_called_once_with(
        Path("song.wav"), Path("song.mp3"), bitrate_kbps=320, override_path="C:/custom/ffmpeg.exe"
    )


@patch("core.scan_service.convert_to_mp3")
def test_run_import_conversion_one_failure_does_not_abort_the_batch(mock_convert):
    mock_convert.side_effect = [(STATUS_OK, ""), (STATUS_ERROR, "decode error"), (STATUS_OK, "")]
    conversions = [(Path(f"song{i}.flac"), Path(f"song{i}.mp3")) for i in range(3)]

    results = run_import_conversion(conversions)

    assert [r[2] for r in results] == [STATUS_OK, STATUS_ERROR, STATUS_OK]
    assert results[1][3] == "decode error"


@patch("core.scan_service.convert_to_mp3", return_value=(STATUS_OK, ""))
def test_run_import_conversion_reports_progress_sequentially_to_total(mock_convert):
    conversions = [(Path(f"song{i}.flac"), Path(f"song{i}.mp3")) for i in range(3)]
    seen = []

    run_import_conversion(conversions, progress=lambda done, total: seen.append((done, total)))

    assert seen == [(1, 3), (2, 3), (3, 3)]


@patch("core.scan_service.convert_to_mp3", return_value=(STATUS_OK, ""))
def test_run_import_conversion_should_cancel_stops_further_conversions(mock_convert):
    conversions = [(Path(f"song{i}.flac"), Path(f"song{i}.mp3")) for i in range(5)]

    results = run_import_conversion(conversions, should_cancel=lambda: True)

    # Cancel is checked after the first item completes -- exactly one
    # conversion happens before the loop stops.
    assert mock_convert.call_count == 1
    assert len(results) == 1


def test_run_import_conversion_empty_list_is_a_noop():
    assert run_import_conversion([]) == []
