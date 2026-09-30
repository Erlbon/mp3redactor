from pathlib import Path
from unittest.mock import MagicMock, patch

from core.mp3_file import (
    STATUS_ERROR, STATUS_OK, STATUS_TOOL_ERROR, STATUS_TOOL_MISSING, STATUS_WARNING,
)
from core.mp3val_runner import _parse_output, check_integrity, fix_integrity


def test_parse_output_clean_file():
    stdout = "Analyzing song.mp3\n"
    status, message = _parse_output(stdout)
    assert status == STATUS_OK
    assert message == ""


def test_parse_output_warning_only():
    stdout = "Analyzing song.mp3\nWARNING: Frame size mismatch\n"
    status, message = _parse_output(stdout)
    assert status == STATUS_WARNING
    assert "Frame size mismatch" in message


def test_parse_output_error_takes_precedence_over_warning():
    stdout = "Analyzing song.mp3\nWARNING: minor thing\nERROR: corrupt frame header\n"
    status, message = _parse_output(stdout)
    assert status == STATUS_ERROR
    assert "corrupt frame header" in message
    assert "minor thing" in message  # both retained, error just decides the status


def test_check_integrity_tool_missing():
    status, message = check_integrity(Path("song.mp3"), tool_path=None)
    # find_tool() will genuinely fail to find mp3val in this sandbox --
    # exercising the real fallback path end to end, not mocking it away.
    assert status == STATUS_TOOL_MISSING
    assert "mp3val" in message


@patch("core.mp3val_runner.subprocess.run")
def test_check_integrity_runs_tool_and_parses_result(mock_run):
    mock_run.return_value = MagicMock(returncode=0, stdout="ERROR: bad frame\n", stderr="")
    status, message = check_integrity(Path("song.mp3"), tool_path=Path("mp3val.exe"))
    assert status == STATUS_ERROR
    assert "bad frame" in message
    mock_run.assert_called_once()


@patch("core.mp3val_runner.subprocess.run")
def test_check_integrity_handles_launch_failure(mock_run):
    mock_run.side_effect = OSError("permission denied")
    status, message = check_integrity(Path("song.mp3"), tool_path=Path("mp3val.exe"))
    assert status == STATUS_TOOL_ERROR
    assert "permission denied" in message


@patch("core.mp3val_runner.subprocess.run")
def test_check_integrity_does_not_pass_fix_flag(mock_run):
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    check_integrity(Path("song.mp3"), tool_path=Path("mp3val.exe"))
    called_args = mock_run.call_args[0][0]
    assert "-f" not in called_args


@patch("core.mp3val_runner.subprocess.run")
def test_fix_integrity_passes_fix_flag(mock_run):
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    fix_integrity(Path("song.mp3"), tool_path=Path("mp3val.exe"))
    called_args = mock_run.call_args[0][0]
    assert "-f" in called_args
    # -f must come before the file path for mp3val to parse it as a flag
    assert called_args.index("-f") < called_args.index("song.mp3")


@patch("core.mp3val_runner.subprocess.run")
def test_fix_integrity_reports_remaining_issues_after_fix(mock_run):
    # Not everything mp3val finds is fixable -- a fix attempt can still
    # come back WARNING/ERROR, which callers must treat as "some issues
    # remain," not as the fix call itself having failed.
    mock_run.return_value = MagicMock(returncode=0, stdout="WARNING: unfixable oddity\n", stderr="")
    status, message = fix_integrity(Path("song.mp3"), tool_path=Path("mp3val.exe"))
    assert status == STATUS_WARNING
    assert "unfixable oddity" in message


@patch("core.mp3val_runner.subprocess.run")
def test_fix_integrity_clean_after_successful_fix(mock_run):
    mock_run.return_value = MagicMock(returncode=0, stdout="Analyzing song.mp3\n", stderr="")
    status, message = fix_integrity(Path("song.mp3"), tool_path=Path("mp3val.exe"))
    assert status == STATUS_OK
    assert message == ""


@patch("core.mp3val_runner.subprocess.run")
def test_fix_integrity_default_does_not_pass_nb(mock_run):
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    fix_integrity(Path("song.mp3"), tool_path=Path("mp3val.exe"))
    called_args = mock_run.call_args[0][0]
    assert "-nb" not in called_args


@patch("core.mp3val_runner.subprocess.run")
def test_fix_integrity_delete_backup_passes_nb(mock_run):
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    fix_integrity(Path("song.mp3"), tool_path=Path("mp3val.exe"), delete_backup=True)
    called_args = mock_run.call_args[0][0]
    assert "-f" in called_args
    assert "-nb" in called_args


def test_fix_integrity_tool_missing():
    status, message = fix_integrity(Path("song.mp3"), tool_path=None)
    assert status == STATUS_TOOL_MISSING
    assert "mp3val" in message


@patch("core.mp3val_runner.subprocess.run")
@patch("core.mp3val_runner.find_tool")
def test_check_integrity_forwards_override_path_to_find_tool(mock_find_tool, mock_run):
    mock_find_tool.return_value = Path("mp3val.exe")
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    check_integrity(Path("song.mp3"), override_path="C:/custom/mp3val.exe")
    mock_find_tool.assert_called_once_with("mp3val.exe", override="C:/custom/mp3val.exe")


@patch("core.mp3val_runner.subprocess.run")
def test_check_integrity_override_path_ignored_when_tool_path_given(mock_run):
    # tool_path (direct injection) should win over override_path -- the
    # two aren't meant to be combined, but tool_path is the more
    # specific/explicit of the two.
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    with patch("core.mp3val_runner.find_tool") as mock_find_tool:
        check_integrity(
            Path("song.mp3"), tool_path=Path("explicit.exe"), override_path="C:/should/be/ignored.exe"
        )
        mock_find_tool.assert_not_called()


@patch("core.mp3val_runner.subprocess.run")
def test_run_passes_no_window_kwargs(mock_run):
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    with patch("redactor_common.core.subprocess_utils.no_window_kwargs", return_value={"creationflags": 0x08000000}):
        check_integrity(Path("song.mp3"), tool_path=Path("mp3val.exe"))
    _, call_kwargs = mock_run.call_args
    assert call_kwargs.get("creationflags") == 0x08000000


def test_parse_output_nonzero_exit_without_issue_lines_is_an_error():
    nl = chr(10)
    status, message = _parse_output("Analyzing song.mp3" + nl, returncode=2, stderr="boom")
    assert status == STATUS_TOOL_ERROR
    assert message == "boom"
    status, message = _parse_output("", returncode=3)
    assert status == STATUS_TOOL_ERROR and "3" in message


def test_parse_output_warnings_still_win_over_a_nonzero_exit():
    status, _ = _parse_output("WARNING: x", returncode=1)
    assert status == STATUS_WARNING


def test_undecodable_output_does_not_raise():
    import subprocess

    raw = subprocess.CompletedProcess(
        [], 0, stdout=b"Analyzing caf" + bytes([233]) + b".mp3" + bytes([10]) + b"WARNING: odd", stderr=b"",
    )
    with patch("core.mp3val_runner.subprocess.run", return_value=raw):
        status, _ = check_integrity(Path("song.mp3"), tool_path=Path("mp3val.exe"))
    assert status == STATUS_WARNING
