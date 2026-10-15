"""Folder notes: which folders are artist / album level, which lack a note, what the folder holds
(core/notes_local.py), on real small trees with real tagged MP3 files."""

from pathlib import Path

from core.notes_local import (
    ALL_KINDS, KIND_ALBUM, KIND_ARTIST, classify, read_album, read_artist, scan, split_year,
)
from tests.notes_helpers import make_mp3, ultravox_library


def kinds(result):
    return sorted((t.folder.name, t.kind) for t in result.targets)


def test_split_year():
    assert split_year("Vienna (1980)") == ("Vienna", "1980")
    assert split_year("Vienna [1980]") == ("Vienna", "1980")
    assert split_year("Vienna") == ("Vienna", "")
    assert split_year("2112") == ("2112", "")


def test_classify_artist_album_and_neither(tmp_path):
    ultravox_library(tmp_path)
    (tmp_path / "Genre" / "Empty").mkdir(parents=True)
    assert classify(tmp_path / "Ultravox") == KIND_ARTIST
    assert classify(tmp_path / "Ultravox" / "Vienna (1980)") == KIND_ALBUM
    assert classify(tmp_path) == ""  # the library root: its child is not an album folder
    assert classify(tmp_path / "Genre") == ""


def test_scan_library_finds_artist_and_album_targets(tmp_path):
    ultravox_library(tmp_path)
    result = scan([tmp_path])
    assert kinds(result) == [("Quartet (1982)", KIND_ALBUM), ("Ultravox", KIND_ARTIST), ("Vienna (1980)", KIND_ALBUM)]
    assert not result.skipped and not result.missing
    album = next(t for t in result.targets if t.kind == KIND_ALBUM)
    assert album.parent_name == "Ultravox"


def test_scan_kind_filter_and_no_recursion(tmp_path):
    ultravox_library(tmp_path)
    assert kinds(scan([tmp_path], kinds=[KIND_ARTIST])) == [("Ultravox", KIND_ARTIST)]
    # not recursing: only the named folder itself (an album folder named directly is an album target)
    assert kinds(scan([tmp_path], include_subfolders=False)) == []
    assert kinds(scan([tmp_path / "Ultravox"], include_subfolders=False)) == [("Ultravox", KIND_ARTIST)]
    assert kinds(scan([tmp_path / "Ultravox" / "Vienna (1980)"], include_subfolders=False)) == [("Vienna (1980)", KIND_ALBUM)]


def test_existing_note_in_any_letter_case_skips_the_folder(tmp_path):
    artist = ultravox_library(tmp_path)
    (artist / "ARTIST.MD").write_text("mine", encoding="utf-8")
    (artist / "Quartet (1982)" / "About.md").write_text("mine too", encoding="utf-8")
    result = scan([tmp_path])
    assert kinds(result) == [("Vienna (1980)", KIND_ALBUM)]
    skipped = {s.folder.name: s.reason for s in result.skipped}
    assert skipped["Ultravox"] == "already has ARTIST.MD"
    assert skipped["Quartet (1982)"] == "already has About.md"


def test_a_sidecar_is_not_a_note_of_our_own(tmp_path):
    artist = ultravox_library(tmp_path)
    (artist / "artist.nfo").write_text("<artist><name>Ultravox</name></artist>", encoding="utf-8")
    assert ("Ultravox", KIND_ARTIST) in kinds(scan([tmp_path]))


def test_collection_names_get_about_md_never_artist_md(tmp_path):
    tmp_path = tmp_path / "Music"  # a library root (a generic name): the folder you start from gets no note of its own
    make_mp3(tmp_path / "Various Artists" / "Some Compilation" / "01 a.mp3", "a")
    # by default (artist + album) the container is not a target at all; with the genre/collection level it is,
    # as an about.md level, and the album below keeps its own album.md
    assert kinds(scan([tmp_path])) == [("Some Compilation", KIND_ALBUM)]
    assert kinds(scan([tmp_path], kinds=ALL_KINDS)) == [("Some Compilation", KIND_ALBUM), ("Various Artists", "genre")]
    assert next(t for t in scan([tmp_path], kinds=ALL_KINDS).targets if t.kind == "genre").note_file_name == "about.md"


def test_library_place_names_are_skipped(tmp_path):
    make_mp3(tmp_path / "Music" / "Artist" / "Album" / "01 a.mp3", "a")
    result = scan([tmp_path / "Music"], kinds=ALL_KINDS)
    assert kinds(result) == [("Album", KIND_ALBUM), ("Artist", KIND_ARTIST)]  # the root (Music) is never a note level
    result = scan([tmp_path], kinds=ALL_KINDS)
    assert ("Music", "genre") not in kinds(result)
    assert any("'Music' is not a genre folder name" in s.reason for s in result.skipped)


def test_disc_folders_make_one_album_not_two(tmp_path):
    album = tmp_path / "Artist" / "Double (1999)"
    make_mp3(album / "CD1" / "01 a.mp3", "a", "Artist", "Double", 1)
    make_mp3(album / "CD2" / "01 b.mp3", "b", "Artist", "Double", 1)
    assert kinds(scan([tmp_path])) == [("Artist", KIND_ARTIST), ("Double (1999)", KIND_ALBUM)]
    local = read_album(album)
    assert local.track_count == 2


def test_files_wildcards_and_missing_arguments(tmp_path):
    ultravox_library(tmp_path)
    song = tmp_path / "Ultravox" / "Quartet (1982)" / "01 Reap the Wild Wind.mp3"
    assert kinds(scan([song])) == [("Quartet (1982)", KIND_ALBUM)]
    assert kinds(scan([str(tmp_path / "Ultra*")])) == [("Quartet (1982)", KIND_ALBUM), ("Ultravox", KIND_ARTIST), ("Vienna (1980)", KIND_ALBUM)]
    result = scan([str(tmp_path / "nothing here"), tmp_path / "Ultravox"])
    assert result.missing == [str(tmp_path / "nothing here")]
    assert len(result.targets) == 3


def test_the_apps_own_leftovers_are_not_music(tmp_path):
    make_mp3(tmp_path / "Band" / "Album" / ".mp3redactor-redact-abc.mp3", "x")
    make_mp3(tmp_path / "Band" / "Album" / "song.redact-orig.mp3", "x")
    assert scan([tmp_path]).targets == []


def test_hidden_and_symlink_free_walk_skips_hidden_folders(tmp_path):
    make_mp3(tmp_path / ".hidden" / "Album" / "01 a.mp3", "a")
    assert scan([tmp_path]).targets == []


def test_read_artist_reads_tags_and_folder_names(tmp_path):
    artist = ultravox_library(tmp_path)
    local = read_artist(artist)
    assert local.name == "Ultravox" and local.names == ["Ultravox"]
    assert [a.title for a in local.albums] == ["Quartet", "Vienna"]
    vienna = next(a for a in local.albums if a.title == "Vienna")
    assert vienna.year == "1980" and vienna.track_count == 3 and vienna.tag_titles == ["Vienna"]
    assert [t.title for t in vienna.tracks] == ["Astradyne", "New Europeans", "Private Lives"]
    assert [t.track for t in vienna.tracks] == [1, 2, 3]
    assert vienna.artist == "Ultravox"


def test_read_album_falls_back_to_the_tags_for_year_and_title(tmp_path):
    folder = tmp_path / "Whatever"
    make_mp3(folder / "01 x.mp3", "x", "Someone", "Real Title", 1, "1991")
    local = read_album(folder)
    assert local.title == "Whatever" and local.year == "1991"
    assert local.titles == ["Whatever", "Real Title"]
    assert local.artist == "Someone"
