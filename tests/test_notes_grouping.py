"""Alphabetical grouping folders (Bands/D/<Band>/<Album>): no note, no proposal, listed in the scan summary."""

import pytest

from core.notes_local import ALL_KINDS, KIND_ALBUM, KIND_ARTIST, GROUPING_REASON, is_alphabetical_grouping, scan
from tests.notes_helpers import make_mp3


@pytest.mark.parametrize("name", ["D", "d", "#", "0", "9", "A-C", "A - C", "A-F", "0-9", "Aa-Az", "d.", "Misc", "other",
                                  "Others", "Various", "A–C", "a-c", "0-F", "Ba-Bf", "10-19"])
def test_these_names_are_alphabetical_groupings(name):
    assert is_alphabetical_grouping(name)


@pytest.mark.parametrize("name", ["Rock", "Depeche Mode", "U2", "Bands", "Music", "Artists", "Ab", "Various Artists", "",
                                  "A-ha", "AC-DC", "Lo-Fi", "Go-Go", "Hi-Fi", "Bo-Ba", "A-c", "Z-A", "Aa-Bz", "1.63 - Lo-Fi"])
def test_these_names_are_not(name):
    assert not is_alphabetical_grouping(name)


def tree(root, group):
    make_mp3(root / "Bands" / group / "Depeche Mode" / "Violator (1990)" / "01 a.mp3", "a", "Depeche Mode", "Violator", 1, "1990")
    make_mp3(root / "Bands" / group / "Depeche Mode" / "Music for the Masses (1987)" / "01 a.mp3", "a", "Depeche Mode",
             "Music for the Masses", 1, "1987")
    return root / "Bands"


@pytest.mark.parametrize("group", ["D", "d", "A-C", "0-9", "#"])
def test_a_grouping_folder_gets_no_genre_note_only_artist_and_albums(tmp_path, group):
    bands = tree(tmp_path, group)
    found = scan([bands], kinds=ALL_KINDS)
    assert sorted((t.kind, t.folder.name) for t in found.targets) == [
        ("album", "Music for the Masses (1987)"), ("album", "Violator (1990)"), ("artist", "Depeche Mode")]
    assert [(g.folder.name, g.reason) for g in found.groupings] == [(group, GROUPING_REASON)]
    assert not found.skipped


def test_a_real_genre_folder_still_gets_its_about_proposal(tmp_path):
    bands = tree(tmp_path, "Rock")
    found = scan([bands], kinds=ALL_KINDS)
    assert ("genre", "Rock") in [(t.kind, t.folder.name) for t in found.targets]
    assert not found.groupings


def test_the_grouping_folder_scanned_directly_without_subfolders_is_skipped_too(tmp_path):
    bands = tree(tmp_path, "D")
    found = scan([bands / "D"], include_subfolders=False, kinds=ALL_KINDS)
    assert not found.targets and [g.folder.name for g in found.groupings] == ["D"]


def test_a_band_called_d_that_holds_albums_is_an_artist_folder(tmp_path):
    make_mp3(tmp_path / "D" / "Album One (2001)" / "01 a.mp3", "a", "D", "Album One", 1, "2001")
    found = scan([tmp_path], kinds=(KIND_ARTIST, KIND_ALBUM))
    assert ("artist", "D") in [(t.kind, t.folder.name) for t in found.targets] and not found.groupings


def test_a_genre_folder_named_lo_fi_gets_its_note_not_skipped_as_a_grouping(tmp_path):
    """Review 2026-10-16: 'Lo-Fi', 'Go-Go', 'Hi-Fi' were taken for alphabetical ranges, so '1.63 - Lo-Fi' silently got no note."""
    bands = tree(tmp_path, "1.63 - Lo-Fi")
    found = scan([bands], kinds=ALL_KINDS)
    assert ("genre", "1.63 - Lo-Fi") in [(t.kind, t.folder.name) for t in found.targets]
    assert not found.groupings
