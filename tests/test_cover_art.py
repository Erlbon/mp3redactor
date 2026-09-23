"""
Embedded cover art (core/cover_art.py, MP3File's cover state, and the
APIC write in core/tag_writer.py), against a copy of the real
tests/fixtures/tiny.mp3. mutagen doesn't validate image data, so small
byte strings with a real JPEG/PNG signature are enough here.
"""

import shutil
from pathlib import Path

from core import cover_art
from core.mp3_file import MP3File
from core.tag_reader import load_tags
from core.tag_writer import save_tags

FIXTURE = Path(__file__).parent / "fixtures" / "tiny.mp3"
JPEG = b"\xff\xd8\xff\xe0" + b"jpeg-body" * 10
PNG = b"\x89PNG\r\n\x1a\n" + b"png-body" * 10


def _loaded_copy(tmp_path, name="song.mp3") -> MP3File:
    dest = tmp_path / name
    shutil.copyfile(FIXTURE, dest)
    mp3 = MP3File(path=dest)
    load_tags(mp3)
    return mp3


def _reload(path) -> MP3File:
    mp3 = MP3File(path=path)
    load_tags(mp3)
    return mp3


def test_sniff_mime():
    assert cover_art.sniff_mime(JPEG) == "image/jpeg"
    assert cover_art.sniff_mime(PNG) == "image/png"
    assert cover_art.sniff_mime(b"GIF89a....") is None
    assert cover_art.sniff_mime(b"") is None


def test_fixture_starts_without_a_cover(tmp_path):
    mp3 = _loaded_copy(tmp_path)
    assert mp3.has_cover is False
    assert mp3.cover_bytes() is None
    assert cover_art.read_cover(mp3.path) is None


def test_set_cover_is_staged_until_save(tmp_path):
    mp3 = _loaded_copy(tmp_path)
    before = mp3.cover_version
    mp3.set_cover(JPEG, "image/jpeg")
    assert mp3.dirty and mp3.has_cover and mp3.cover_change_pending
    assert mp3.cover_version != before
    assert mp3.cover_bytes() == (JPEG, "image/jpeg")  # pending, served from memory
    assert cover_art.read_cover(mp3.path) is None  # nothing on disk yet

    assert save_tags(mp3)
    assert not mp3.cover_change_pending and mp3.cover_pending is None
    assert cover_art.read_cover(mp3.path) == (JPEG, "image/jpeg")
    reloaded = _reload(mp3.path)
    assert reloaded.has_cover is True
    assert reloaded.cover_bytes() == (JPEG, "image/jpeg")


def test_replacing_writes_a_single_front_cover(tmp_path):
    from mutagen.id3 import APIC, ID3

    mp3 = _loaded_copy(tmp_path)
    tags = ID3(str(mp3.path))
    tags.add(APIC(encoding=3, mime="image/png", type=4, desc="Back", data=PNG))
    tags.add(APIC(encoding=3, mime="image/png", type=3, desc="Old front", data=PNG))
    tags.save()

    mp3 = _reload(mp3.path)
    assert mp3.cover_bytes() == (PNG, "image/png")  # the front cover is the one shown
    mp3.set_cover(JPEG, "image/jpeg")
    assert save_tags(mp3)

    frames = ID3(str(mp3.path)).getall("APIC")
    assert len(frames) == 1
    assert frames[0].type == cover_art.FRONT_COVER and frames[0].data == JPEG


def test_remove_clears_every_picture(tmp_path):
    from mutagen.id3 import ID3

    mp3 = _loaded_copy(tmp_path)
    mp3.set_cover(PNG, "image/png")
    assert save_tags(mp3)

    mp3.remove_cover()
    assert mp3.cover_bytes() is None  # already gone from the app's point of view
    assert cover_art.read_cover(mp3.path) is not None  # but still on disk until Save
    assert save_tags(mp3)
    assert ID3(str(mp3.path)).getall("APIC") == []
    assert _reload(mp3.path).has_cover is False


def test_saving_other_tags_leaves_existing_pictures_alone(tmp_path):
    from mutagen.id3 import APIC, ID3

    mp3 = _loaded_copy(tmp_path)
    tags = ID3(str(mp3.path))
    tags.add(APIC(encoding=3, mime="image/png", type=4, desc="Back", data=PNG))
    tags.save()

    mp3 = _reload(mp3.path)
    mp3.apply_tags({"title": "New Title"})
    assert save_tags(mp3)
    frames = ID3(str(mp3.path)).getall("APIC")
    assert [f.type for f in frames] == [4]  # untouched: nothing pending


def test_find_folder_image(tmp_path):
    mp3 = _loaded_copy(tmp_path)
    assert cover_art.find_folder_image(mp3.path) is None
    (tmp_path / "Folder.JPG").write_bytes(JPEG)
    assert cover_art.find_folder_image(mp3.path).name == "Folder.JPG"
    (tmp_path / "cover.png").write_bytes(PNG)
    assert cover_art.find_folder_image(mp3.path).name == "cover.png"  # cover.* wins


def test_versions_are_never_reused():
    seen = {cover_art.next_version() for _ in range(100)}
    assert len(seen) == 100
