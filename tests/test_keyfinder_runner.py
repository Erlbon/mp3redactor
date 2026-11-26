from pathlib import Path
from unittest.mock import MagicMock, patch

from core.keyfinder_runner import detect_key, detect_key_with_exe
from core.mp3_file import STATUS_OK, STATUS_TOOL_ERROR, STATUS_TOOL_MISSING


@patch("core.keyfinder_runner.find_tool", return_value=None)
def test_detect_key_tool_missing(mock_find_tool):
    # find_tool() returning None (not found on PATH or bundled) is
    # forced explicitly rather than relied on as a sandbox-environment
    # fact -- a real keyfinder-cli.exe now genuinely lives in this
    # project's tools/ for local manual verification (see README.md),
    # so leaving this to "the sandbox happens not to have one" would
    # make the test pass or fail depending on that, the same lesson
    # test_bpm_detector.py's aubio-guarded-import test already learned.
    key, status, message = detect_key_with_exe(Path("song.mp3"), tool_path=None)
    assert key == ""
    assert status == STATUS_TOOL_MISSING
    assert "keyfinder-cli" in message


@patch("core.keyfinder_runner.subprocess.run")
def test_detect_key_success(mock_run):
    mock_run.return_value = MagicMock(returncode=0, stdout="A\n", stderr="")
    key, status, message = detect_key_with_exe(Path("song.mp3"), tool_path=Path("keyfinder-cli.exe"))
    assert key == "A"
    assert status == STATUS_OK
    assert message == ""
    mock_run.assert_called_once()


@patch("core.keyfinder_runner.subprocess.run")
def test_detect_key_silent_file_is_ok_with_no_key(mock_run):
    # keyfinder-cli prints nothing (and exits 0) for a genuinely silent
    # file -- that's not a failure, just no key to report.
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    key, status, message = detect_key_with_exe(Path("song.mp3"), tool_path=Path("keyfinder-cli.exe"))
    assert key == ""
    assert status == STATUS_OK
    assert "no key" in message


@patch("core.keyfinder_runner.subprocess.run")
def test_detect_key_nonzero_exit_is_tool_error(mock_run):
    mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="could not decode file")
    key, status, message = detect_key_with_exe(Path("song.mp3"), tool_path=Path("keyfinder-cli.exe"))
    assert key == ""
    assert status == STATUS_TOOL_ERROR
    assert "could not decode file" in message


@patch("core.keyfinder_runner.subprocess.run")
def test_detect_key_handles_launch_failure(mock_run):
    mock_run.side_effect = OSError("permission denied")
    key, status, message = detect_key_with_exe(Path("song.mp3"), tool_path=Path("keyfinder-cli.exe"))
    assert key == ""
    assert status == STATUS_TOOL_ERROR
    assert "permission denied" in message


@patch("core.keyfinder_runner.subprocess.run")
@patch("core.keyfinder_runner.find_tool")
def test_detect_key_forwards_override_path_to_find_tool(mock_find_tool, mock_run):
    mock_find_tool.return_value = Path("keyfinder-cli.exe")
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    detect_key_with_exe(Path("song.mp3"), override_path="C:/custom/keyfinder-cli.exe")
    mock_find_tool.assert_called_once_with("keyfinder-cli.exe", override="C:/custom/keyfinder-cli.exe")


@patch("core.keyfinder_runner.subprocess.run")
def test_detect_key_passes_no_window_kwargs(mock_run):
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    with patch("redactor_common.core.subprocess_utils.no_window_kwargs", return_value={"creationflags": 0x08000000}):
        detect_key_with_exe(Path("song.mp3"), tool_path=Path("keyfinder-cli.exe"))
    _, call_kwargs = mock_run.call_args
    assert call_kwargs.get("creationflags") == 0x08000000


def test_undecodable_output_does_not_raise():
    import subprocess

    raw = subprocess.CompletedProcess([], 0, stdout=bytes([65, 255, 10]), stderr=b"")
    with patch("core.keyfinder_runner.subprocess.run", return_value=raw):
        key, status, _ = detect_key_with_exe(Path("song.mp3"), tool_path=Path("keyfinder-cli.exe"))
    assert status == "OK" and key.startswith("A")


# detect_key(): the in-process port first, keyfinder-cli only as its fallback.


@patch("core.keyfinder_runner.find_tool")
@patch("core.keyfinder_runner.detect_key_native", return_value=("Dm", STATUS_OK, ""))
def test_detect_key_uses_the_port_and_never_the_tool(mock_native, mock_find_tool):
    assert detect_key(Path("song.mp3")) == ("Dm", STATUS_OK, "")
    mock_find_tool.assert_not_called()


@patch("core.keyfinder_runner.find_tool", return_value=None)
@patch("core.keyfinder_runner.detect_key_native", return_value=("", STATUS_TOOL_ERROR, "cannot decode this file"))
def test_detect_key_port_failure_without_the_tool_is_the_ports_error(mock_native, mock_find_tool):
    assert detect_key(Path("song.mp3")) == ("", STATUS_TOOL_ERROR, "cannot decode this file")


@patch("core.keyfinder_runner.subprocess.run")
@patch("core.keyfinder_runner.detect_key_native", return_value=("", STATUS_TOOL_ERROR, "cannot decode this file"))
def test_detect_key_falls_back_to_the_tool(mock_native, mock_run):
    mock_run.return_value = MagicMock(returncode=0, stdout="Ebm\n", stderr="")
    key, status, _ = detect_key(Path("song.mp3"), tool_path=Path("keyfinder-cli.exe"))
    assert (key, status) == ("Ebm", STATUS_OK)


@patch("core.keyfinder_runner.subprocess.run")
@patch("core.keyfinder_runner.detect_key_native", return_value=("", STATUS_TOOL_ERROR, "cannot decode this file"))
def test_detect_key_both_failing_reports_both(mock_native, mock_run):
    mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="bad file")
    key, status, message = detect_key(Path("song.mp3"), tool_path=Path("keyfinder-cli.exe"))
    assert (key, status) == ("", STATUS_TOOL_ERROR)
    assert "cannot decode this file" in message and "bad file" in message
