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


def test_find_mp3_files_recursive_false_ignores_subfolders(tmp_path):
    # Refresh List's own use case (gui/main_window.py's refresh_list()):
    # notice a file dropped directly into an already-loaded folder,
    # without also discovering an entire new subfolder tree -- that
    # stays Open Folder's job.
    (tmp_path / "one.mp3").write_bytes(b"")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "two.mp3").write_bytes(b"")

    result = find_mp3_files([tmp_path], recursive=False)

    assert [p.name for p in result] == ["one.mp3"]


def test_find_mp3_files_skips_the_familys_temp_names_through_the_shared_registry(tmp_path):
    from redactor_common.core.temp_names import is_app_temp_name

    import core.scan_service  # noqa: F401  (registers this app's own scratch patterns)

    leftovers = [".mp3redactor-redact-ab12.mp3", "song.redact-orig.mp3", "song.redact-orig2.mp3", ".0123abcd.part"]
    for name in ["real.mp3", *leftovers]:
        (tmp_path / name).write_bytes(b"")
    assert [p.name for p in find_mp3_files([tmp_path])] == ["real.mp3"]
    assert find_mp3_files([tmp_path / "song.redact-orig.mp3"]) == []  # named directly: still skipped
    assert is_app_temp_name(".mp3redactor-redact-x.mp3") and is_app_temp_name(".mp3redactor-1234.tmp")
    assert not is_app_temp_name("My.Partial.Song.mp3")
