"""core.mp3_validator, the Python port of mp3val 0.1.8, and the
mp3val_runner functions that use it.

Agreement with the real mp3val.exe on many files is measured by
tools/compare_mp3val.py (it needs the exe); these tests pin the port's
output on small synthetic files to what mp3val.exe 0.1.8 printed and
wrote for exactly the same bytes (tests/mp3val_samples.py), and test the
file handling: any file name, the .bak backup, -nb, attributes, and the
fallback to the exe."""

import hashlib
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core import mp3_validator as mv
from core import mp3val_runner
from core.mp3_file import STATUS_ERROR, STATUS_OK, STATUS_TOOL_ERROR, STATUS_WARNING
from tests.mp3val_samples import frame, frames, id3v2, samples

# name: (lines mp3val.exe -f printed, sha256 of the file it rebuilt or None)
EXPECTED = {
    "clean": (
        ['INFO: "<file>": 40 MPEG frames (MPEG 1 Layer III), +ID3v1+ID3v2, CBR'],
        None,
    ),
    "garbage_mid": (
        [
            'WARNING: "<file>" (offset 0x2102): MPEG stream error, resynchronized successfully',
            'INFO: "<file>": 40 MPEG frames (MPEG 1 Layer III), +ID3v1+ID3v2, CBR',
            'FIXED: "<file>": File was rebuilt',
        ],
        "21627151ea8244f07c653450dfdd8321a9e477987c08dd7341d98f25f19e11b8",
    ),
    "truncated": (
        [
            'WARNING: "<file>" (offset 0x314c): It seems that file is truncated or there is garbage at the end of the file',
            'INFO: "<file>": 30 MPEG frames (MPEG 1 Layer III), +ID3v2, CBR',
            'FIXED: "<file>": File was rebuilt',
        ],
        "740757472c2d48813f9561d8fcc8f002de1ef9d239ae6b7525c1a2def513203e",
    ),
    "junk_start": (
        [
            'WARNING: "<file>" (offset 0x0): Garbage at the beginning of the file',
            'WARNING: "<file>": No supported tags in the file',
            'INFO: "<file>": 30 MPEG frames (MPEG 1 Layer III), no tags, CBR',
            'FIXED: "<file>": File was rebuilt',
        ],
        "e9a3c228639d7085deb909c2b536b03c4d709d61998a02c88d7fd6f68f534f44",
    ),
    "crc_wrong": (
        [
            'WARNING: "<file>": Wrong CRC in 6 frames',
            'INFO: "<file>": 30 MPEG frames (MPEG 1 Layer III), +ID3v2, CBR, CRC',
            'FIXED: "<file>": File was rebuilt',
        ],
        "7e140baa36d7bb221e7e36e1a110b9b668eb76958ac0f672a0b22fe56459d211",
    ),
    "xing_wrong": (
        [
            'WARNING: "<file>": Wrong number of MPEG frames specified in Xing header (99 instead of 31)',
            'WARNING: "<file>": Wrong number of MPEG data bytes specified in Xing header (1234 instead of 12927)',
            'INFO: "<file>": 31 MPEG frames (MPEG 1 Layer III), +ID3v2, Xing header',
            'FIXED: "<file>": File was rebuilt',
        ],
        "0183d9cb7e9da9fa50f6ccded01e2a51466e04b731610042e5abe2a35e3a990d",
    ),
    "xing_right": (
        ['INFO: "<file>": 31 MPEG frames (MPEG 1 Layer III), +ID3v1, Xing header'],
        None,
    ),
    "trailing_bytes": (
        [
            'WARNING: "<file>" (offset 0x30de): It seems that file is truncated or there is garbage at the end of the file',
            'WARNING: "<file>": No supported tags in the file',
            'INFO: "<file>": 30 MPEG frames (MPEG 1 Layer III), no tags, CBR',
            'FIXED: "<file>": File was rebuilt',
        ],
        "8902f63df5d2c90ab7d0e64fe3119e548b80c780d75f381953644392437ac370",
    ),
    "few_frames_no_tags": (
        [
            "WARNING: \"<file>\": Too few MPEG frames (it's unlikely that this is a MPEG audio file)",
            'WARNING: "<file>": No supported tags in the file',
            'INFO: "<file>": 5 MPEG frames (MPEG 1 Layer III), no tags, CBR',
        ],
        None,
    ),
    "unknown": (
        ['ERROR: "<file>": Unknown file format'],
        None,
    ),
}

NON_ASCII_NAMES = ["Łódź – część.mp3", "東京の夜.mp3", "Москва.mp3", "blåbær.mp3"]


def _strip(lines, path):
    return [line.replace(f'"{os.path.abspath(path)}"', '"<file>"') for line in lines]


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_fix_matches_mp3val_exe(tmp_path, name):
    lines, sha = EXPECTED[name]
    path = tmp_path / "s.mp3"
    original = samples()[name]
    path.write_bytes(original)
    assert _strip(mv.fix_file(path), path) == lines
    backup = tmp_path / "s.mp3.bak"
    if sha is None:
        assert path.read_bytes() == original
        assert not backup.exists()
    else:
        assert hashlib.sha256(path.read_bytes()).hexdigest() == sha
        assert backup.read_bytes() == original


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_check_matches_mp3val_exe_and_never_writes(tmp_path, name):
    lines, _sha = EXPECTED[name]
    path = tmp_path / "s.mp3"
    path.write_bytes(samples()[name])
    before = path.stat().st_mtime_ns
    assert _strip(mv.check_file(path), path) == [line for line in lines if not line.startswith("FIXED")]
    assert path.stat().st_mtime_ns == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["s.mp3"]


def test_a_fixed_file_checks_clean_of_what_was_fixed(tmp_path):
    for name in ("garbage_mid", "crc_wrong", "xing_wrong", "truncated"):
        path = tmp_path / f"{name}.mp3"
        path.write_bytes(samples()[name])
        mv.fix_file(path)
        assert mp3val_runner.check_integrity(path) == (STATUS_OK, ""), name


def test_crc_check_and_fix_on_a_mono_frame():
    data = id3v2() + b"".join(frame(i, crc=(i != 3), mono=True) for i in range(12))
    result = mv.validate_bytes(data, "x", fix=True)
    assert any("Wrong CRC in 1 frames" in line for line in result.lines)
    assert not any("CRC in" in line for line in mv.validate_bytes(result.fixed, "x").lines)


@pytest.mark.parametrize("name", NON_ASCII_NAMES)
def test_any_file_name_is_checked_and_fixed(tmp_path, name):
    folder = tmp_path / "Kraków – 東京"
    folder.mkdir()
    path = folder / name
    path.write_bytes(samples()["garbage_mid"])
    status, message = mp3val_runner.check_integrity(path)
    assert status == STATUS_WARNING
    assert "MPEG stream error" in message
    assert str(path) in message
    status, _message = mp3val_runner.fix_integrity(path)
    assert status == STATUS_WARNING  # the findings from before the rebuild, as mp3val -f prints them
    assert hashlib.sha256(path.read_bytes()).hexdigest() == EXPECTED["garbage_mid"][1]
    assert (folder / (name + ".bak")).read_bytes() == samples()["garbage_mid"]
    assert mp3val_runner.check_integrity(path) == (STATUS_OK, "")


def test_delete_backup_leaves_no_bak_and_no_temp_file(tmp_path):
    path = tmp_path / "s.mp3"
    path.write_bytes(samples()["truncated"])
    mp3val_runner.fix_integrity(path, delete_backup=True)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["s.mp3"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == EXPECTED["truncated"][1]


def test_an_old_bak_is_replaced(tmp_path):
    path = tmp_path / "s.mp3"
    path.write_bytes(samples()["truncated"])
    (tmp_path / "s.mp3.bak").write_bytes(b"old backup")
    mv.fix_file(path)
    assert (tmp_path / "s.mp3.bak").read_bytes() == samples()["truncated"]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows file attributes")
def test_read_only_and_hidden_attributes_are_kept(tmp_path):
    import ctypes
    import stat

    path = tmp_path / "s.mp3"
    path.write_bytes(samples()["truncated"])
    hidden_readonly = 0x2 | 0x1
    ctypes.windll.kernel32.SetFileAttributesW(str(path), hidden_readonly)
    try:
        mv.fix_file(path)
        attrs = ctypes.windll.kernel32.GetFileAttributesW(str(path))
        assert attrs & hidden_readonly == hidden_readonly
        assert hashlib.sha256(path.read_bytes()).hexdigest() == EXPECTED["truncated"][1]
    finally:
        for p in tmp_path.iterdir():
            ctypes.windll.kernel32.SetFileAttributesW(str(p), 0x80)
            os.chmod(p, stat.S_IWRITE)


def test_runner_verdicts():
    assert mp3val_runner._parse_output("\n".join(mv.validate_bytes(samples()["unknown"], "x").lines))[0] == STATUS_ERROR


def test_empty_file_is_a_tool_error_like_the_exe(tmp_path):
    path = tmp_path / "empty.mp3"
    path.write_bytes(b"")
    status, message = mp3val_runner.check_integrity(path)
    assert status == STATUS_TOOL_ERROR and "empty" in message


def test_missing_file_is_a_tool_error(tmp_path):
    status, message = mp3val_runner.check_integrity(tmp_path / "gone.mp3")
    assert status == STATUS_TOOL_ERROR and "could not open" in message


def test_a_file_the_exe_hangs_on_is_a_tool_error_and_the_exe_is_not_tried(tmp_path):
    path = tmp_path / "loop.mp3"
    # An APEv2 tag whose size field is -32: mp3val.exe loops on it forever.
    path.write_bytes(frames(12) + b"APETAGEX" + (2000).to_bytes(4, "little") + (0xFFFFFFE0).to_bytes(4, "little")
                     + bytes(16) + frames(3))
    with patch("core.mp3val_runner.run_tool") as run_tool:
        status, message = mp3val_runner.check_integrity(path, tool_path=Path("mp3val.exe"))
    assert status == STATUS_TOOL_ERROR and "hangs" in message
    run_tool.assert_not_called()


def test_a_bug_in_the_port_falls_back_to_the_exe(tmp_path):
    path = tmp_path / "s.mp3"
    path.write_bytes(samples()["clean"])
    result = MagicMock(returncode=0, stdout="WARNING: from the exe\n", stderr="")
    with patch("core.mp3_validator.validate_bytes", side_effect=ZeroDivisionError("bug")), \
            patch("core.mp3val_runner.run_tool", return_value=result) as run_tool:
        assert mp3val_runner.fix_integrity(path, tool_path=Path("mp3val.exe"), delete_backup=True) == (
            STATUS_WARNING, "WARNING: from the exe")
    args = run_tool.call_args[0][0]
    assert args[1:3] == ["-f", "-nb"]


def test_a_bug_in_the_port_without_the_exe_is_a_tool_error(tmp_path):
    path = tmp_path / "s.mp3"
    path.write_bytes(samples()["clean"])
    with patch("core.mp3_validator.validate_bytes", side_effect=ZeroDivisionError("bug")), \
            patch("core.mp3val_runner.find_tool", return_value=None):
        status, message = mp3val_runner.check_integrity(path)
    assert status == STATUS_TOOL_ERROR and "built-in mp3val check failed" in message


def test_check_needs_no_exe(tmp_path):
    path = tmp_path / "s.mp3"
    path.write_bytes(samples()["clean"])
    with patch("core.mp3val_runner.find_tool", return_value=None), \
            patch("core.mp3val_runner.run_tool") as run_tool:
        assert mp3val_runner.check_integrity(path) == (STATUS_OK, "")
    run_tool.assert_not_called()


def test_crc16_matches_a_known_value():
    # CRC-16 (polynomial 0x8005, initial 0xFFFF, MSB first) of "123456789" is 0xAEE7.
    assert mv.calculate_crc16(0xFFFF, 0x8005, b"123456789", 0, 9) == 0xAEE7
