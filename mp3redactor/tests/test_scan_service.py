from pathlib import Path

from core.scan_service import find_mp3_files


def test_find_mp3_files_expands_folder_and_filters_extension(tmp_path):
    (tmp_path / "one.mp3").write_bytes(b"")
    (tmp_path / "two.MP3").write_bytes(b"")  # case-insensitive match
    (tmp_path / "notes.txt").write_bytes(b"")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "three.mp3").write_bytes(b"")

    result = find_mp3_files([tmp_path])

    names = sorted(p.name for p in result)
    assert names == sorted(["one.mp3", "two.MP3", "three.mp3"])
    assert len(result) == 3


def test_find_mp3_files_dedupes_when_file_and_parent_folder_both_given(tmp_path):
    f = tmp_path / "song.mp3"
    f.write_bytes(b"")

    result = find_mp3_files([tmp_path, f])

    assert result == [f.resolve()]


def test_find_mp3_files_ignores_non_mp3_direct_file(tmp_path):
    f = tmp_path / "cover.jpg"
    f.write_bytes(b"")

    result = find_mp3_files([f])

    assert result == []
