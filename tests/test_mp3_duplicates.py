"""Find Duplicates, phase 1 (core/mp3_duplicates.py): the audio payload
excluding tags, the size pre-bucketing, the tier/reason of each rule and
the shared dialog's dismissal flow. Hand-built files only -- no real MP3
decoding, fpcalc or aubio, so this runs the same on every platform."""

import os
import random
from pathlib import Path

import pytest

from redactor_common.core.duplicates import (
    TIER_IDENTICAL,
    TIER_POSSIBLE,
    TIER_STRONG,
    TIER_WEAK,
    JsonDismissStore,
)

from core import mp3_duplicates as dup
from core.mp3_file import MP3File


def _audio(seed: int, length: int = 4000) -> bytes:
    return random.Random(seed).randbytes(length)


def _id3v2(payload_size: int = 300, version: int = 3, footer: bool = False) -> bytes:
    size = bytes([(payload_size >> s) & 0x7F for s in (21, 14, 7, 0)])
    flags = 0x10 if footer else 0
    body = b"ID3" + bytes([version, 0, flags]) + size + b"\x01" * payload_size
    return body + (b"3DI" + bytes([version, 0, flags]) + size if footer else b"")


def _id3v1(title: bytes = b"x") -> bytes:
    return b"TAG" + title.ljust(125, b"\0")


def _ape(items: int = 40, header: bool = True) -> bytes:
    size = (items + 32).to_bytes(4, "little")
    flags_footer = (0x80000000 if header else 0).to_bytes(4, "little")
    head = b"APETAGEX" + (2000).to_bytes(4, "little") + size + b"\0\0\0\0" + (0x80000000 | 0x20000000).to_bytes(4, "little") + b"\0" * 8
    foot = b"APETAGEX" + (2000).to_bytes(4, "little") + size + b"\0\0\0\0" + flags_footer + b"\0" * 8
    return (head if header else b"") + b"\x02" * items + foot


def _mp3(tmp_path, name, audio, front=b"", back=b"", **fields) -> MP3File:
    path = tmp_path / name
    path.write_bytes(front + audio + back)
    fields.setdefault("duration_seconds", 200.0)
    return MP3File(path=path, **fields)


def _ranges_audio(tmp_path, front=b"", back=b"", audio=None):
    audio = audio if audio is not None else _audio(1)
    p = tmp_path / "t.mp3"
    p.write_bytes(front + audio + back)
    size = p.stat().st_size
    ranges = dup.audio_skip_ranges(str(p), size)
    start = ranges[0][1] if ranges and ranges[0][0] == 0 else 0
    end = ranges[-1][0] if ranges and ranges[-1][1] == size and ranges[-1][0] > 0 else size
    return p.read_bytes()[start:end] == audio, ranges


# --- tag skipping -----------------------------------------------------------


@pytest.mark.parametrize("front,back", [
    (b"", b""),
    (_id3v2(), b""),
    (_id3v2(700, version=4), b""),
    (_id3v2(100, version=4, footer=True), b""),
    (_id3v2(2000) + _id3v2(50), b""),
    (b"", _id3v1()),
    (_id3v2(), _id3v1()),
    (b"", b"TAG+" + b"\0" * 223 + _id3v1()),
    (_id3v2(), _ape(header=True)),
    (_id3v2(), _ape(header=False)),
    (_id3v2(), _ape(header=True) + _id3v1()),
    (b"", b"LYRICSBEGIN" + b"abc" + b"000014" + b"LYRICS200"),
    (b"", b"LYRICSBEGIN" + b"abc" + b"000014" + b"LYRICS200" + _id3v1()),
    (_id3v2(1), _id3v1() * 1),
])
def test_skip_ranges_leave_exactly_the_audio(tmp_path, front, back):
    ok, _ranges = _ranges_audio(tmp_path, front, back)
    assert ok


def test_id3v2_total_includes_header_footer_and_padding(tmp_path):
    ok, ranges = _ranges_audio(tmp_path, _id3v2(300), b"")
    assert ranges == [(0, 310)]
    ok, ranges = _ranges_audio(tmp_path, _id3v2(300, version=4, footer=True), b"")
    assert ranges == [(0, 320)]


def test_not_a_tag_is_not_skipped(tmp_path):
    # "ID3" with a non-synchsafe size, a short file and plain audio
    bad = b"ID3\x03\x00\x00\xff\xff\xff\xff" + _audio(2, 50)
    p = tmp_path / "bad.mp3"
    p.write_bytes(bad)
    assert dup.audio_skip_ranges(str(p)) == []
    p.write_bytes(b"ID3")
    assert dup.audio_skip_ranges(str(p)) == []
    p.write_bytes(b"")
    assert dup.audio_skip_ranges(str(p)) == []
    # a tag size that runs past the end of the file is not trusted
    p.write_bytes(_id3v2(300)[:100])
    assert dup.audio_skip_ranges(str(p)) == []


def test_tag_edit_keeps_identity_and_audio_edit_changes_it(tmp_path):
    audio = _audio(3, 300_000)
    a = tmp_path / "a.mp3"
    a.write_bytes(_id3v2(300) + audio + _id3v1(b"old"))
    before = dup.audio_fingerprint(str(a), dup.audio_skip_ranges(str(a)))
    full_before = dup.full_audio_hash(str(a), dup.audio_skip_ranges(str(a)))
    # retag: a bigger ID3v2, no ID3v1, an APE block
    a.write_bytes(_id3v2(5000, version=4) + audio + _ape())
    ranges = dup.audio_skip_ranges(str(a))
    assert dup.audio_fingerprint(str(a), ranges) == before
    assert dup.full_audio_hash(str(a), ranges) == full_before
    # change the audio: every kind of hash notices
    changed = bytearray(audio)
    changed[150_000] ^= 0xFF
    a.write_bytes(_id3v2(300) + bytes(changed) + _id3v1(b"old"))
    ranges = dup.audio_skip_ranges(str(a))
    assert dup.full_audio_hash(str(a), ranges) != full_before
    changed = bytearray(audio)
    changed[10] ^= 0xFF   # inside the first sampled chunk
    a.write_bytes(_id3v2(300) + bytes(changed))
    assert dup.audio_fingerprint(str(a), dup.audio_skip_ranges(str(a))) != before


def test_unreadable_file_hashes_to_empty(tmp_path):
    missing = str(tmp_path / "gone.mp3")
    assert dup.audio_fingerprint(missing, []) == ""
    assert dup.full_audio_hash(missing, []) == ""
    with pytest.raises(OSError):
        dup.audio_skip_ranges(missing)


# --- identical audio ----------------------------------------------------------


def _find(files):
    return dup.find_duplicate_groups(files)


def test_identical_audio_with_different_tags_is_one_identical_group(tmp_path):
    audio = _audio(4)
    a = _mp3(tmp_path, "a.mp3", audio, _id3v2(100), artist="A", title="One")
    b = _mp3(tmp_path, "b.mp3", audio, _id3v2(900, version=4), _id3v1(), artist="Z", title="Other")
    c = _mp3(tmp_path, "c.mp3", _audio(5), _id3v2(100), artist="A", title="Another")
    groups = _find([a, b, c])
    assert len(groups) == 1
    g = groups[0]
    assert g.tier == TIER_IDENTICAL and g.reason == dup.IDENTICAL_REASON
    assert {id(m.item) for m in g.members} == {id(a), id(b)}
    assert all(m.fingerprint for m in g.members)


def test_only_files_with_a_same_length_audio_neighbour_are_hashed(tmp_path, monkeypatch):
    audio = _audio(6)
    a = _mp3(tmp_path, "a.mp3", audio, _id3v2(100), title="", artist="")
    b = _mp3(tmp_path, "b.mp3", audio, _id3v2(400), title="", artist="")
    loner = _mp3(tmp_path, "loner.mp3", _audio(7, 4001), _id3v2(100), title="", artist="")
    sampled, full = [], []
    real_fp, real_full = dup.audio_fingerprint, dup.full_audio_hash
    monkeypatch.setattr(dup, "audio_fingerprint", lambda p, r: sampled.append(os.path.basename(p)) or real_fp(p, r))
    monkeypatch.setattr(dup, "full_audio_hash", lambda p, r, c=None: full.append(os.path.basename(p)) or real_full(p, r))
    assert len(_find([a, b, loner])) == 1
    assert "loner.mp3" not in sampled + full


def test_same_length_but_different_audio_is_not_identical(tmp_path):
    a = _mp3(tmp_path, "a.mp3", _audio(8), title="", artist="")
    b = _mp3(tmp_path, "b.mp3", _audio(9), title="", artist="")
    assert _find([a, b]) == []


def test_equal_samples_are_confirmed_with_a_full_hash(tmp_path):
    audio = bytearray(_audio(10, 1_000_000))
    other = bytearray(audio)
    other[100_000] ^= 0xFF   # outside the three sampled chunks
    a = _mp3(tmp_path, "a.mp3", bytes(audio), title="", artist="")
    b = _mp3(tmp_path, "b.mp3", bytes(other), title="", artist="")
    assert dup.audio_fingerprint(str(a.path), []) == dup.audio_fingerprint(str(b.path), [])
    assert _find([a, b]) == []


def test_files_that_failed_to_load_are_ignored(tmp_path):
    audio = _audio(11)
    a = _mp3(tmp_path, "a.mp3", audio, title="", artist="")
    b = _mp3(tmp_path, "b.mp3", audio, title="", artist="", load_error="broken")
    assert _find([a, b]) == []


def test_unreadable_path_is_skipped_quietly(tmp_path):
    a = _mp3(tmp_path, "a.mp3", _audio(12), title="", artist="")
    gone = MP3File(path=tmp_path / "gone.mp3", title="", artist="")
    assert _find([a, gone]) == []


# --- tags ---------------------------------------------------------------------


def _tagged(tmp_path, name, seed, **fields):
    return _mp3(tmp_path, name, _audio(seed), **fields)


def test_same_artist_title_album_and_length_is_strong(tmp_path):
    a = _tagged(tmp_path, "a.mp3", 20, artist="Pink Floyd", title="Time", album="DSOTM", track="4",
                duration_seconds=413.0)
    b = _tagged(tmp_path, "b.mp3", 21, artist="Pink Floyd", title="Time", album="DSOTM", track="4/10",
                duration_seconds=414.5)
    (g,) = _find([a, b])
    assert g.tier == TIER_STRONG
    assert g.reason == "same artist, title, album and length"


def test_same_album_different_track_number_is_only_possible(tmp_path):
    a = _tagged(tmp_path, "a.mp3", 22, artist="X", title="Intro", album="Y", track="1")
    b = _tagged(tmp_path, "b.mp3", 23, artist="X", title="Intro", album="Y", track="9")
    (g,) = _find([a, b])
    assert g.tier == TIER_POSSIBLE and "different track numbers" in g.reason


def test_compilation_copy_is_weak_with_the_explanation(tmp_path):
    a = _tagged(tmp_path, "a.mp3", 24, artist="Queen", title="Under Pressure", album="Hot Space")
    b = _tagged(tmp_path, "b.mp3", 25, artist="Queen", title="Under Pressure", album="Greatest Hits II")
    (g,) = _find([a, b])
    assert g.tier == TIER_WEAK
    assert g.reason == dup.DIFFERENT_ALBUM_NOTE


def test_missing_album_is_possible(tmp_path):
    a = _tagged(tmp_path, "a.mp3", 26, artist="X", title="Song", album="Alb")
    b = _tagged(tmp_path, "b.mp3", 27, artist="X", title="Song", album="")
    (g,) = _find([a, b])
    assert g.tier == TIER_POSSIBLE and "album is missing" in g.reason


def test_lengths_more_than_two_seconds_apart_are_not_grouped(tmp_path):
    a = _tagged(tmp_path, "a.mp3", 28, artist="X", title="Song", album="A", duration_seconds=200.0)
    b = _tagged(tmp_path, "b.mp3", 29, artist="X", title="Song", album="A", duration_seconds=203.5)
    assert _find([a, b]) == []
    c = _tagged(tmp_path, "c.mp3", 30, artist="X", title="Song", album="A", duration_seconds=None)
    d = _tagged(tmp_path, "d.mp3", 31, artist="X", title="Song", album="A", duration_seconds=None)
    assert _find([c, d]) == []   # no length known: nothing to compare


def test_empty_title_or_artist_is_never_grouped(tmp_path):
    files = [
        _tagged(tmp_path, "a.mp3", 32, artist="", title="Song"),
        _tagged(tmp_path, "b.mp3", 33, artist="", title="Song"),
        _tagged(tmp_path, "c.mp3", 34, artist="X", title=""),
        _tagged(tmp_path, "d.mp3", 35, artist="X", title="  "),
    ]
    assert _find(files) == []


def test_folding_the_article_case_accents_and_featuring(tmp_path):
    a = _tagged(tmp_path, "a.mp3", 36, artist="The Beatles", title="Yesterday", album="Help!")
    b = _tagged(tmp_path, "b.mp3", 37, artist="beatles", title="YESTERDAY (feat. Someone)", album="help")
    c = _tagged(tmp_path, "c.mp3", 38, artist="Beatles feat. Guest", title="Yesterday", album="Help!")
    (g,) = _find([a, b, c])
    assert len(g.members) == 3 and g.tier == TIER_STRONG
    e = _tagged(tmp_path, "e.mp3", 39, artist="Beyonce", title="Halo", album="I Am")
    f = _tagged(tmp_path, "f.mp3", 40, artist="Beyoncé", title="Halo", album="I Am")
    assert len(_find([e, f])) == 1


def test_primary_artist_and_title_keys():
    assert dup.primary_artist_key("The Rolling Stones") == "rolling stones"
    assert dup.primary_artist_key("Daft Punk ft. Pharrell") == "daft punk"
    assert dup.primary_artist_key("Daft Punk (feat. Pharrell Williams)") == "daft punk"
    assert dup.title_key("Get Lucky (feat. Pharrell)") == ("get lucky", frozenset())
    assert dup.title_key("Song (Remastered 2011)") == ("song", frozenset({"remaster"}))
    assert dup.title_key("Song - 2009 Remaster") == ("song", frozenset({"remaster"}))
    assert dup.title_key("Song - Live") == ("song", frozenset({"live"}))
    assert dup.title_key("Song (Radio Edit)") == ("song", frozenset({"radio", "edit"}))
    assert dup.title_key("1999") == ("1999", frozenset())


@pytest.mark.parametrize("other", [
    "Song (Live)", "Song - Live at Wembley", "Song (Remix)", "Song (Acoustic)", "Song (Demo)",
    "Song (Instrumental)", "Song (Radio Edit)", "Song (Extended Mix)",
])
def test_different_versions_are_not_the_same_song(tmp_path, other):
    a = _tagged(tmp_path, "a.mp3", 41, artist="X", title="Song", album="A")
    b = _tagged(tmp_path, "b.mp3", 42, artist="X", title=other, album="A")
    assert _find([a, b]) == []


def test_two_copies_of_the_same_live_version_are_grouped(tmp_path):
    a = _tagged(tmp_path, "a.mp3", 43, artist="X", title="Song (Live)", album="A")
    b = _tagged(tmp_path, "b.mp3", 44, artist="X", title="Song - Live", album="A")
    (g,) = _find([a, b])
    assert g.tier == TIER_STRONG


def test_remaster_next_to_its_original_is_a_separate_weak_group(tmp_path):
    a = _tagged(tmp_path, "a.mp3", 45, artist="X", title="Song", album="A")
    b = _tagged(tmp_path, "b.mp3", 46, artist="X", title="Song (Remastered 2011)", album="A (Deluxe)")
    (g,) = _find([a, b])
    assert g.tier == TIER_WEAK and g.reason == dup.REMASTER_REASON


def test_recording_id_alone_is_possible_with_the_release_caveat(tmp_path):
    a = _tagged(tmp_path, "a.mp3", 47, artist="A", title="One", album="X", musicbrainz_trackid="AAAA-1")
    b = _tagged(tmp_path, "b.mp3", 48, artist="B", title="Two", album="Y", musicbrainz_trackid="aaaa-1")
    c = _tagged(tmp_path, "c.mp3", 49, artist="C", title="Three", album="Z", musicbrainz_trackid="other")
    (g,) = _find([a, b, c])
    assert g.tier == TIER_POSSIBLE and g.reason == dup.RECORDING_REASON
    assert {id(m.item) for m in g.members} == {id(a), id(b)}


def test_combined_signals_report_the_strongest_tier_and_every_reason(tmp_path):
    audio = _audio(50)
    a = _mp3(tmp_path, "a.mp3", audio, _id3v2(100), artist="X", title="Song", album="A", musicbrainz_trackid="m1")
    b = _mp3(tmp_path, "b.mp3", audio, _id3v2(500), artist="X", title="Song", album="B", musicbrainz_trackid="m1")
    (g,) = _find([a, b])
    assert g.tier == TIER_IDENTICAL
    assert g.reason.startswith(dup.IDENTICAL_REASON)
    assert dup.RECORDING_REASON in g.reason and dup.DIFFERENT_ALBUM_NOTE in g.reason


def test_member_columns_and_fingerprint_survive_a_tag_edit(tmp_path):
    a = _mp3(tmp_path, "a.mp3", _audio(51), _id3v2(100), artist="X", title="Song", album="A", track="3",
             bitrate_kbps=192, duration_seconds=125.4)
    b = _mp3(tmp_path, "b.mp3", _audio(52), artist="X", title="Song", album="A", track="3", duration_seconds=125.9)
    (g,) = _find([a, b])
    row = next(m for m in g.members if m.item is a)
    assert row.fields["artist"] == "X" and row.fields["track"] == "3"
    assert row.fields["duration"] == "2:05" and row.fields["bitrate"] == "192 kbps"
    assert row.fields["size"].endswith("KB") and row.fields["path"] == str(a.path)
    before = row.fingerprint
    assert before
    a.path.write_bytes(_id3v2(2500, version=4) + _audio(51) + _id3v1(b"retagged"))   # tag edit
    (g2,) = _find([a, b])
    assert next(m for m in g2.members if m.item is a).fingerprint == before


def test_cancel_returns_nothing(tmp_path):
    audio = _audio(53)
    files = [_mp3(tmp_path, f"{i}.mp3", audio, title="", artist="") for i in range(3)]
    assert dup.find_duplicate_groups(files, cancelled=lambda: True) == []


def test_progress_is_reported_and_bounded(tmp_path):
    audio = _audio(54)
    files = [_mp3(tmp_path, f"{i}.mp3", audio, title="", artist="") for i in range(4)]
    seen = []
    dup.find_duplicate_groups(files, progress=lambda done, total, label: seen.append((done, total, label)))
    assert seen and all(0 <= d <= t == 4 for d, t, _l in seen)


# --- dismissal ------------------------------------------------------------------


def test_dismissal_persists_across_a_rename_and_reappears_with_a_third_copy(tmp_path):
    audio = _audio(55)
    a = _mp3(tmp_path, "a.mp3", audio, _id3v2(100), title="", artist="")
    b = _mp3(tmp_path, "b.mp3", audio, _id3v2(200), title="", artist="")
    store_path = str(tmp_path / "mp3redactor_duplicates_dismissed.json")
    (g,) = _find([a, b])
    JsonDismissStore(store_path).dismiss(g.identities)

    # a new run, new store object, file renamed and retagged
    new_path = tmp_path / "renamed.mp3"
    b.path.rename(new_path)
    b.path = new_path
    new_path.write_bytes(_id3v2(900, version=4) + audio)
    (g2,) = _find([a, b])
    assert JsonDismissStore(store_path).is_dismissed(g2.identities)

    # a third copy turns up: a different set, so the group comes back
    c = _mp3(tmp_path, "c.mp3", audio, _id3v2(50), title="", artist="")
    (g3,) = _find([a, b, c])
    assert len(g3.members) == 3
    assert not JsonDismissStore(store_path).is_dismissed(g3.identities)
