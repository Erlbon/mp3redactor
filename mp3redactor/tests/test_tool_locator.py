from pathlib import Path
from unittest.mock import patch

from core.tool_locator import find_tool


def test_find_tool_override_existing_path_wins(tmp_path):
    fake_exe = tmp_path / "mp3val.exe"
    fake_exe.write_bytes(b"")
    result = find_tool("mp3val.exe", override=str(fake_exe))
    assert result == fake_exe


def test_find_tool_override_missing_path_is_not_found_no_fallback(tmp_path):
    # An override that doesn't exist must NOT silently fall back to
    # bundled/PATH search -- otherwise the Settings dialog's found/
    # not-found indicator would lie about what's actually being used.
    missing = tmp_path / "does_not_exist.exe"
    with patch("core.tool_locator.shutil.which", return_value="/usr/bin/mp3val.exe"):
        result = find_tool("mp3val.exe", override=str(missing))
    assert result is None


def test_find_tool_no_override_falls_back_to_bundled_dir():
    with patch("core.tool_locator.bundled_tool_path") as mock_bundled:
        fake_path = Path("/fake/tools/mp3val.exe")
        mock_bundled.return_value = fake_path
        with patch.object(Path, "exists", return_value=True):
            result = find_tool("mp3val.exe")
    assert result == fake_path


def test_find_tool_falls_back_to_path_when_not_bundled():
    with patch("core.tool_locator.bundled_tool_path") as mock_bundled:
        mock_bundled.return_value = Path("/nonexistent/tools/mp3val.exe")
        with patch("core.tool_locator.shutil.which", return_value="/usr/local/bin/mp3val.exe"):
            result = find_tool("mp3val.exe")
    assert result == Path("/usr/local/bin/mp3val.exe")


def test_find_tool_returns_none_when_nothing_found():
    with patch("core.tool_locator.bundled_tool_path") as mock_bundled:
        mock_bundled.return_value = Path("/nonexistent/tools/mp3val.exe")
        with patch("core.tool_locator.shutil.which", return_value=None):
            result = find_tool("mp3val.exe")
    assert result is None
