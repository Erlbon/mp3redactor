from pathlib import Path

from core.mp3_file import MP3File


def test_apply_tags_sets_fields_and_marks_dirty():
    mp3 = MP3File(path=Path("song.mp3"))
    assert mp3.dirty is False

    mp3.apply_tags({"title": "New Title", "genre": "Jazz"})

    assert mp3.title == "New Title"
    assert mp3.genre == "Jazz"
    assert mp3.dirty is True


def test_apply_tags_only_touches_given_keys():
    mp3 = MP3File(path=Path("song.mp3"), artist="Existing Artist")

    mp3.apply_tags({"title": "New Title"})

    assert mp3.title == "New Title"
    assert mp3.artist == "Existing Artist"  # untouched
