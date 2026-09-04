from pathlib import Path
from unittest.mock import MagicMock, patch

from core.keyfinder_runner import detect_key
from core.mp3_file import STATUS_ERROR, STATUS_OK, STATUS_TOOL_MISSING


def test_detect_key_tool_missing():
    key, status, message = detect_key(Path("song.mp3"), tool_path=None)
    # find_tool() will genuinely fail to find keyfinder-cli in this
    # sandbox -- exercising the real fallback path end to end, not
    # mocking it away (same approach test_mp3val_runner.py takes).
    assert key == ""
    assert status == STATUS_TOOL_MISSING
    assert "keyfinder-cli" in message


@patch("core.keyfinder_runner.subprocess.run")
def test_detect_key_success(mock_run):
    mock_run.return_value = MagicMock(returncode=0, stdout="A\n", stderr="")
    key, status, message = detect_key(Path("song.mp3"), tool_path=Path("keyfinder-cli.exe"))
    assert key == "A"
    assert status == STATUS_OK
    assert message == ""
    mock_run.assert_called_once()


@patch("core.keyfinder_runner.subprocess.run")
def test_detect_key_silent_file_is_ok_with_no_key(mock_run):
    # keyfinder-cli prints nothing (and exits 0) for a genuinely silent
    # file -- that's not a failure, just no key to report.
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    key, status, message = detect_key(Path("song.mp3"), tool_path=Path("keyfinder-cli.exe"))
    assert key == ""
    assert status == STATUS_OK
    assert "no key" in message


@patch("core.keyfinder_runner.subprocess.run")
def test_detect_key_nonzero_exit_is_error(mock_run):
    mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="could not decode file")
    key, status, message = detect_key(Path("song.mp3"), tool_path=Path("keyfinder-cli.exe"))
    assert key == ""
    assert status == STATUS_ERROR
    assert "could not decode file" in message


@patch("core.keyfinder_runner.subprocess.run")
def test_detect_key_handles_launch_failure(mock_run):
    mock_run.side_effect = OSError("permission denied")
    key, status, message = detect_key(Path("song.mp3"), tool_path=Path("keyfinder-cli.exe"))
    assert key == ""
    assert status == STATUS_ERROR
    assert "permission denied" in message


@patch("core.keyfinder_runner.subprocess.run")
@patch("core.keyfinder_runner.find_tool")
def test_detect_key_forwards_override_path_to_find_tool(mock_find_tool, mock_run):
    mock_find_tool.return_value = Path("keyfinder-cli.exe")
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    detect_key(Path("song.mp3"), override_path="C:/custom/keyfinder-cli.exe")
    mock_find_tool.assert_called_once_with("keyfinder-cli.exe", override="C:/custom/keyfinder-cli.exe")


@patch("core.keyfinder_runner.subprocess.run")
def test_detect_key_passes_no_window_kwargs(mock_run):
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    with patch("core.keyfinder_runner.no_window_kwargs", return_value={"creationflags": 0x08000000}):
        detect_key(Path("song.mp3"), tool_path=Path("keyfinder-cli.exe"))
    _, call_kwargs = mock_run.call_args
    assert call_kwargs.get("creationflags") == 0x08000000
