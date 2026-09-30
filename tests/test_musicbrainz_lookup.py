"""Tests for core/musicbrainz_lookup.py (offline: canned MusicBrainz
responses shaped like the real web service's JSON), the MusicBrainz id
tags' round trip through mutagen, and the lookup dialog."""

import json
import shutil
import sys
from pathlib import Path
from urllib.parse import unquote

from PyQt6.QtWidgets import QApplication

from core import musicbrainz_lookup as mb
from core.mp3_file import MP3File
from core.tag_reader import load_tags
from core.tag_writer import save_tags

FIXTURE = Path(__file__).parent / "fixtures" / "tiny.mp3"
# Module level: an unreferenced QApplication is garbage-collected at once.
_app = QApplication.instance() or QApplication(sys.argv)
# (The fake MusicBrainz below is called directly, bypassing the real
# request pacing -- see mb._default_fetch.)


def _release_json(release_id, title, country, tracks, date="2005", status="Official"):
    return {
        "id": release_id, "title": title, "date": date, "country": country, "status": status,
        "artist-credit": [{"name": "Johnny Cash", "joinphrase": ""}],
        "media": [{"position": 1, "format": "CD", "tracks": [
            {"position": pos, "number": str(pos), "title": t, "length": secs * 1000,
             "recording": {"id": f"rec-{pos}", "title": t, "length": secs * 1000}}
            for pos, t, secs in tracks
        ]}],
    }


RING_OF_FIRE = [(i, f"Song {i}", 150 + i) for i in range(1, 22)]
RING_OF_FIRE[5] = (6, "Big River", 151)
RING_OF_FIRE[17] = (18, "Personal Jesus", 199)


class FakeMusicBrainz:
    def __init__(self, releases, search_hits=None):
        self.releases = {r["id"]: r for r in releases}
        self.search_hits = search_hits if search_hits is not None else list(self.releases.values())
        self.urls = []

    def __call__(self, url):
        url = getattr(url, "full_url", url)
        self.urls.append(unquote(url))
        path = url.split("/ws/2/", 1)[1].split("?", 1)[0]
        if path == "release":
            hits = [dict(r, **{"track-count": sum(len(m["tracks"]) for m in r["media"])}) for r in self.search_hits]
            return json.dumps({"releases": hits}).encode()
        release_id = path.split("/", 1)[1]
        return json.dumps(self.releases[release_id]).encode()


def _files():
    return [mb.FileFacts(title="Big River", track=6, seconds=151.2), mb.FileFacts(title="Personal Jesus", track=18, seconds=199.4)]


def test_album_query_prefers_tags_then_folder_name():
    q = mb.album_query(["Ring of Fire", "Ring of Fire", ""], ["Johnny Cash"], Path("x/whatever"))
    assert (q.artist, q.album) == ("Johnny Cash", "Ring of Fire")
    q = mb.album_query(["", ""], ["", ""], Path("x/Johnny Cash - At Folsom Prison (1968)"))
    assert (q.artist, q.album) == ("Johnny Cash", "At Folsom Prison")
    q = mb.album_query([], [], Path("x/Greatest Hits"))
    assert (q.artist, q.album) == ("", "Greatest Hits")


def test_files_are_assigned_to_their_tracks():
    release = mb.Release(id="r1", title="Ring of Fire", artist="Johnny Cash", tracks=[
        mb.ReleaseTrack(1, pos, t, secs, f"rec-{pos}", "Johnny Cash") for pos, t, secs in RING_OF_FIRE
    ])
    match = mb.match_release(_files(), release)
    assert match.matched == 2 and match.score > 0.95
    assert match.assignment[0].position == 6 and match.assignment[1].position == 18


def test_titles_and_lengths_find_a_track_even_without_track_numbers():
    release = mb.Release(id="r1", title="T", artist="A", tracks=[
        mb.ReleaseTrack(1, pos, t, secs, f"rec-{pos}", "A") for pos, t, secs in RING_OF_FIRE
    ])
    match = mb.match_release([mb.FileFacts(title="big river", seconds=150)], release)
    assert match.assignment[0].position == 6


def test_a_wrong_album_scores_low():
    other = mb.Release(id="r2", title="Other", artist="Other", tracks=[
        mb.ReleaseTrack(1, i, f"Different {i}", 300 + i, f"x{i}", "Other") for i in range(1, 22)
    ])
    assert mb.match_release(_files(), other).matched == 0


def test_find_album_searches_then_ranks_editions():
    fake = FakeMusicBrainz([
        _release_json("za", "Ring of Fire: The Legend of Johnny Cash", "ZA", RING_OF_FIRE),
        _release_json("xe", "Ring of Fire: The Legend of Johnny Cash", "XE", RING_OF_FIRE, date="2005-11-21"),
        _release_json("bad", "Ring of Fire (bootleg)", "XE", [(1, "Nope", 10)], status="Bootleg"),
    ])
    matches = mb.find_album(_files(), mb.AlbumQuery("Johnny Cash", "Ring of Fire: The Legend of Johnny Cash"), fake)
    assert [m.release.id for m in matches][:2] == ["za", "xe"]
    assert matches[-1].release.id == "bad" and matches[-1].matched == 0
    assert 'release:"Ring of Fire: The Legend of Johnny Cash" AND artist:"Johnny Cash"' in fake.urls[0]


def test_search_falls_back_to_album_only():
    class Empty(FakeMusicBrainz):
        def __call__(self, url):
            url_text = unquote(getattr(url, "full_url", url))
            if "AND artist" in url_text:
                self.urls.append(url_text)
                return json.dumps({"releases": []}).encode()
            return super().__call__(url)

    fake = Empty([_release_json("za", "Ring of Fire", "ZA", RING_OF_FIRE)])
    assert [r.id for r in mb.search_releases(mb.AlbumQuery("Wrong Artist", "Ring of Fire"), fake)] == ["za"]


def test_fields_for_a_matched_file():
    release = mb.fetch_release("za", FakeMusicBrainz([_release_json("za", "Ring of Fire", "ZA", RING_OF_FIRE)]))
    fields = mb.fields_for(release, release.tracks[5])
    assert fields == {
        "title": "Big River", "artist": "Johnny Cash", "albumartist": "Johnny Cash", "album": "Ring of Fire",
        "track": "6", "year": "2005", "musicbrainz_albumid": "za", "musicbrainz_trackid": "rec-6",
    }


def test_throttle_spaces_requests():
    throttle = mb._Throttle(0.2)
    import time
    start = time.monotonic()
    for _ in range(3):
        throttle.wait()
    assert time.monotonic() - start >= 0.39


def test_musicbrainz_ids_round_trip_as_picard_writes_them(tmp_path):
    path = tmp_path / "song.mp3"
    shutil.copyfile(FIXTURE, path)
    mp3 = MP3File(path=path)
    mp3.apply_tags({"musicbrainz_albumid": "d8799f17-6191-4e7d-9ef2-fdb7f8c5da92",
                    "musicbrainz_trackid": "6a3d7e5e-20ac-45c6-bd03-56e5bd17ae5c"})
    assert save_tags(mp3)

    from mutagen.id3 import ID3
    tags = ID3(path)
    assert tags["TXXX:MusicBrainz Album Id"].text == ["d8799f17-6191-4e7d-9ef2-fdb7f8c5da92"]
    assert tags["UFID:http://musicbrainz.org"].data == b"6a3d7e5e-20ac-45c6-bd03-56e5bd17ae5c"

    reloaded = MP3File(path=path)
    load_tags(reloaded)
    assert reloaded.musicbrainz_albumid == "d8799f17-6191-4e7d-9ef2-fdb7f8c5da92"
    assert reloaded.musicbrainz_trackid == "6a3d7e5e-20ac-45c6-bd03-56e5bd17ae5c"

    reloaded.apply_tags({"musicbrainz_trackid": ""})
    assert save_tags(reloaded)
    assert "UFID:http://musicbrainz.org" not in ID3(path)


# ---------------------------------------------------------------------------
# The dialog
# ---------------------------------------------------------------------------

def test_dialog_matches_a_folder_and_builds_per_file_changes(tmp_path, monkeypatch):
    from gui import musicbrainz_lookup_dialog as dlg_module

    monkeypatch.setattr(dlg_module, "fetch_front_cover", lambda _id: None)
    monkeypatch.setattr(dlg_module, "read_cover", lambda _path: None)
    folder = tmp_path / "Johnny Cash - Ring of Fire"
    folder.mkdir()
    files = []
    for name, title, track, secs in [("06 - Big River.mp3", "Big River", "6", 151.0), ("18 - Personal Jesus.mp3", "Personal Jesus", "18", 199.0)]:
        mp3 = MP3File(path=folder / name, title=title, track=track, album="Ring of Fire", artist="Johnny Cash",
                      duration_seconds=secs)
        files.append(mp3)
    fake = FakeMusicBrainz([
        _release_json("za", "Ring of Fire", "ZA", RING_OF_FIRE),
        _release_json("xe", "Ring of Fire", "XE", RING_OF_FIRE, date="2005-11-21"),
    ])
    dialog = dlg_module.MusicBrainzLookupDialog(dlg_module.group_by_folder(files), fetch=fake)

    result = dialog._row_results[0]
    assert result.found and result.fields["match"].startswith("2 of 2 files matched")
    assert len(result.alternatives) == 1  # the other edition
    changes = dict((mp3.path.name, fields) for mp3, fields in dialog.file_changes())
    assert changes["06 - Big River.mp3"]["musicbrainz_albumid"] == "za"
    assert changes["18 - Personal Jesus.mp3"]["track"] == "18"

    dialog._resolve(dialog.items[0], result.alternatives[0].data)  # pick the other edition
    assert {f["musicbrainz_albumid"] for _m, f in dialog.file_changes()} == {"xe"}


def test_fields_for_drops_an_unknown_track_position_and_disc():
    release = mb.fetch_release("za", FakeMusicBrainz([_release_json("za", "Ring of Fire", "ZA", RING_OF_FIRE)]))
    track = release.tracks[5]
    track.position = 0
    track.disc = 0
    release.tracks[0].disc = 2  # makes it a multi-disc release
    fields = mb.fields_for(release, track)
    assert "track" not in fields and "discnumber" not in fields
