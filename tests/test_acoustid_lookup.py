"""Tests for core/acoustid_lookup.py (stage 2 of the MusicBrainz lookup:
identifying files by sound) -- offline: canned AcoustID answers shaped
like the real API's, fpcalc faked -- and the dialog using it."""

import json
from pathlib import Path
from urllib.parse import parse_qs

import pytest

from core import acoustid_lookup as ac
from core import musicbrainz_lookup as mb
from tests.test_musicbrainz_lookup import RING_OF_FIRE, FakeMusicBrainz, _app, _release_json  # noqa: F401


def _answer(*results, status="ok"):
    return json.dumps({"status": status, "results": list(results)}).encode()


def _result(score, *recordings):
    return {"id": "acoustid-x", "score": score, "recordings": [
        {"id": rid, "title": title, "artists": [{"name": "Johnny Cash"}], "releases": [{"id": r} for r in releases]}
        for rid, title, releases in recordings
    ]}


class FakeAcoustId:
    """Answers by fingerprint text; records what was sent."""

    def __init__(self, by_fingerprint):
        self.by_fingerprint = by_fingerprint
        self.sent = []

    def __call__(self, url, data):
        form = {k: v[0] for k, v in parse_qs(data.decode()).items()}
        self.sent.append(form)
        return self.by_fingerprint[form["fingerprint"]]


def test_lookup_parses_recordings_and_drops_weak_hits():
    fake = FakeAcoustId({"FP": _answer(
        _result(0.99, ("rec-6", "Big River", ["za", "xe"]), ("rec-wrong", "Delia's Gone", ["other"])),
        _result(0.94, ("rec-6", "Big River", ["box-set"])),
        _result(0.3, ("rec-noise", "Noise", ["x"])),
    )})
    hits = ac.lookup(ac.Fingerprint(151, "FP"), fake)
    assert [h.recording_id for h in hits] == ["rec-6", "rec-wrong"]  # below MIN_SCORE dropped
    assert hits[0].score == 0.99 and hits[0].release_ids == {"za", "xe", "box-set"}
    sent = fake.sent[0]
    assert sent["client"] == ac.ACOUSTID_APP_KEY and sent["duration"] == "151"
    assert sent["meta"] == "recordings releaseids"


def test_errors_are_reported():
    bad_key = FakeAcoustId({"FP": json.dumps({"status": "error", "error": {"code": 4, "message": "invalid API key"}}).encode()})
    with pytest.raises(ac.AcoustIdError, match="invalid API key"):
        ac.lookup(ac.Fingerprint(10, "FP"), bad_key)

    def unreachable(_url, _data):
        raise OSError("no network")

    with pytest.raises(ac.AcoustIdError, match="couldn't be reached"):
        ac.lookup(ac.Fingerprint(10, "FP"), unreachable)


def test_candidate_releases_prefer_what_holds_the_whole_folder():
    hits = [
        [ac.RecordingHit("rec-6", 0.99, {"za", "xe", "single"}), ac.RecordingHit("wrong", 0.99, {"other"})],
        [ac.RecordingHit("rec-18", 0.95, {"za", "xe"})],
        [],  # a file AcoustID doesn't know
    ]
    ranked = ac.candidate_releases(hits)
    assert set(ranked[:2]) == {"za", "xe"}  # both hold 2 of the 3 files
    assert ranked.index("single") > 1 and ranked.index("other") > 1


def test_identify_files_keeps_going_past_one_bad_file(tmp_path, monkeypatch):
    def fake_fingerprint(path, _fpcalc):
        if path.name == "broken.mp3":
            raise ac.AcoustIdError("fpcalc failed: not an audio file")
        return ac.Fingerprint(151, "FP")

    monkeypatch.setattr(ac, "fingerprint_file", fake_fingerprint)
    fake = FakeAcoustId({"FP": _answer(_result(0.99, ("rec-6", "Big River", ["za"])))})
    hits, problems = ac.identify_files([Path("a.mp3"), Path("broken.mp3")], Path("fpcalc"), fake)
    assert [len(h) for h in hits] == [1, 0] and "broken.mp3" in problems[0]


def test_a_recording_id_makes_the_track_pairing_certain():
    release = mb.Release(id="r1", title="T", artist="A", tracks=[
        mb.ReleaseTrack(1, pos, t, secs, f"rec-{pos}", "A") for pos, t, secs in RING_OF_FIRE
    ])
    junk = [mb.FileFacts(title="Track 01", recordings={"rec-6": 0.99}), mb.FileFacts(recordings={"rec-18": 0.9})]
    match = mb.match_release(junk, release)
    assert match.matched == 2 and match.score == 1.0
    assert match.assignment[0].position == 6 and match.assignment[1].position == 18


# ---------------------------------------------------------------------------
# The dialog: a junk-tagged folder found by sound alone
# ---------------------------------------------------------------------------

def test_dialog_identifies_a_junk_tagged_folder_by_sound(tmp_path, monkeypatch):
    from core.mp3_file import MP3File
    from gui import musicbrainz_lookup_dialog as dlg_module

    monkeypatch.setattr(dlg_module, "fetch_front_cover", lambda _id: None)
    monkeypatch.setattr(dlg_module, "read_cover", lambda _path: None)
    monkeypatch.setattr(ac, "fingerprint_file", lambda path, _fpcalc: ac.Fingerprint(151, path.name))
    folder = tmp_path / "New Folder (3)"
    folder.mkdir()
    files = [MP3File(path=folder / name, duration_seconds=secs) for name, secs in (("a.mp3", 151.0), ("b.mp3", 199.0))]
    acoustid = FakeAcoustId({
        "a.mp3": _answer(_result(0.99, ("rec-6", "Big River", ["za", "xe"]))),
        "b.mp3": _answer(_result(0.97, ("rec-18", "Personal Jesus", ["za"]))),
    })
    musicbrainz = FakeMusicBrainz(
        [_release_json("za", "Ring of Fire", "ZA", RING_OF_FIRE), _release_json("xe", "Ring of Fire", "XE", RING_OF_FIRE)],
        search_hits=[],  # no text search possible: nothing to search for
    )
    dialog = dlg_module.MusicBrainzLookupDialog(
        dlg_module.group_by_folder(files), fetch=musicbrainz, fpcalc=Path("fpcalc"), post=acoustid,
    )
    result = dialog._row_results[0]
    assert result.found, result.error
    changes = {mp3.path.name: fields for mp3, fields in dialog.file_changes()}
    assert changes["a.mp3"]["musicbrainz_albumid"] == "za"  # the release holding BOTH files
    assert (changes["a.mp3"]["track"], changes["b.mp3"]["track"]) == ("6", "18")
    assert changes["b.mp3"]["musicbrainz_trackid"] == "rec-18"


def test_dialog_without_fpcalc_is_stage_one_only(tmp_path, monkeypatch):
    from core.mp3_file import MP3File
    from gui import musicbrainz_lookup_dialog as dlg_module

    monkeypatch.setattr(dlg_module, "fetch_front_cover", lambda _id: None)
    monkeypatch.setattr(dlg_module, "read_cover", lambda _path: None)

    def no_fingerprinting(*_a, **_k):
        raise AssertionError("must not fingerprint without fpcalc")

    monkeypatch.setattr(ac, "fingerprint_file", no_fingerprinting)
    folder = tmp_path / "New Folder"
    folder.mkdir()
    dialog = dlg_module.MusicBrainzLookupDialog(
        dlg_module.group_by_folder([MP3File(path=folder / "a.mp3")]), fetch=FakeMusicBrainz([], search_hits=[]),
    )
    assert not dialog._row_results[0].found
