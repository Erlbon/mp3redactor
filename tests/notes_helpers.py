"""Shared test helpers for the folder-notes tests: a fake `fetch(url) -> bytes` that answers from recorded
fixtures (tests/fixtures/notes/recorded.json), and a small real MP3 library builder."""

from __future__ import annotations

import json
import re
import shutil
import urllib.error
import urllib.request
from pathlib import Path

from mutagen.id3 import ID3, TALB, TCOM, TCON, TDRC, TIT2, TPE1, TPE2, TRCK

from core.discogs_lookup import DiscogsClient
from core.notes_flow import Fetchers
from redactor_common.core.lookup_client import Throttle

FIXTURES = Path(__file__).parent / "fixtures"
TINY = FIXTURES / "tiny.mp3"
RECORDED = json.loads((FIXTURES / "notes" / "recorded.json").read_text(encoding="utf-8"))


class FakeFetch:
    """fetch(url) -> bytes from routes [(regex, payload)]; payload None = HTTP 404, a callable gets the URL. An
    unexpected URL fails the test (nothing here may reach the network)."""

    def __init__(self, routes):
        self.routes = list(routes)
        self.urls: list[str] = []

    def __call__(self, request) -> bytes:
        url = request.full_url if isinstance(request, urllib.request.Request) else request
        self.urls.append(url)
        for pattern, payload in self.routes:
            if re.search(pattern, url):
                if payload is None:
                    raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
                if callable(payload):
                    payload = payload(url)
                return json.dumps(payload).encode("utf-8")
        raise AssertionError(f"unexpected request: {url}")

    def count(self, pattern: str) -> int:
        return sum(1 for u in self.urls if re.search(pattern, u))


def musicbrainz_routes(search_artist="mb_artist_search_ultravox", rgs="mb_rgs_ultravox", detail="mb_artist_ultravox"):
    r = RECORDED
    routes = [
        (r"ws/2/release-group\?query=[^ ]*limit=25", {"release-groups": []}),  # the search by album titles: nothing by default
        (r"ws/2/artist\?query", r[search_artist] if search_artist else {"artists": []}),
        (r"ws/2/artist/[^?]+\?", r[detail]),
        # the second search hit ('Ultravox!', synthetic) is another artist: it does not hold Vienna, Quartet ... (a namesake that
        # holds the same album is a case of its own: tests/test_notes_review_batch3.py)
        (r"ws/2/release-group\?artist=", lambda url: {"release-groups": []} if "synthetic-ultravox-2" in url else r[rgs]),
        (r"ws/2/release-group\?query=.*primarytype", r["mb_rg_search_vienna"]),
        (r"ws/2/release-group\?query=", r["mb_rg_search_vienna"]),
        (r"ws/2/release-group/", r["mb_rg_vienna_album"]),
        (r"ws/2/release\?release-group=", r["mb_releases_vienna"]),
        (r"ws/2/release/", r["mb_release_vienna"]),
    ]
    return routes


def wikipedia_routes(artist_page=True):
    r = RECORDED
    return [
        (r"wikidata\.org", r["wikidata_ultravox"]),
        (r"page/summary/Ultravox$", r["wiki_summary_ultravox"] if artist_page else None),
        (r"page/summary/Vienna_\(Ultravox_album\)", r["wiki_summary_vienna_album"]),
        (r"page/summary/Vienna_\(Ultravox_song\)", r["wiki_summary_vienna_song"]),
        (r"search/title\?q=Vienna", r["wiki_search_vienna"]),
        (r"search/title", {"pages": []}),
        (r"page/summary/", None),
    ]


def ultravox_fetchers(**mb_kwargs) -> Fetchers:
    return Fetchers(mb=FakeFetch(musicbrainz_routes(**mb_kwargs)), wiki=FakeFetch(wikipedia_routes()))


def cristina_fetchers(with_discogs: bool = True) -> Fetchers:
    r = RECORDED
    mb = FakeFetch([
        (r"ws/2/artist\?query", r["mb_artist_search_cristina"]),
        (r"ws/2/release-group\?artist=", r["mb_rgs_davena"]),
        (r"ws/2/release-group\?query=", {"release-groups": []}),  # the search by album titles (no artist found by name)
    ])
    wiki = FakeFetch([(r"search/title", {"pages": []}), (r"page/summary/", None)])
    client = None
    if with_discogs:
        discogs = FakeFetch([
            (r"database/search", r["discogs_search_cristina"]),
            (r"artists/55646/releases", r["discogs_releases_cristina"]),
            (r"artists/999/releases", r["discogs_releases_cristina2"]),
            (r"artists/55646", r["discogs_artist_cristina"]),
        ])
        client = DiscogsClient("test-token", fetch=discogs, throttle=Throttle(0))
    return Fetchers(mb=mb, wiki=wiki, discogs=client)


def make_mp3(
    path: Path, title: str = "", artist: str = "", album: str = "", track: int = 0, year: str = "",
    albumartist: str = "", composer: str = "", genre: str = "",
) -> Path:
    """A tiny real MP3 with ID3 tags."""
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(TINY, path)
    tags = ID3()
    if title:
        tags.add(TIT2(encoding=3, text=title))
    if artist:
        tags.add(TPE1(encoding=3, text=artist))
    if album:
        tags.add(TALB(encoding=3, text=album))
    if track:
        tags.add(TRCK(encoding=3, text=str(track)))
    if year:
        tags.add(TDRC(encoding=3, text=year))
    if albumartist:
        tags.add(TPE2(encoding=3, text=albumartist))
    if composer:
        tags.add(TCOM(encoding=3, text=composer))
    if genre:
        tags.add(TCON(encoding=3, text=genre))
    tags.save(path)
    return path


VIENNA_TRACKS = ["Astradyne", "New Europeans", "Private Lives", "Passing Strangers", "Sleepwalk", "Mr. X",
                 "Western Promise", "Vienna", "All Stood Still"]


def ultravox_library(root: Path, vienna_tracks: int = 3, with_quartet: bool = True) -> Path:
    """root/Ultravox/Vienna (1980)/NN Title.mp3 (+ Quartet (1982)); returns the artist folder."""
    artist = root / "Ultravox"
    for i, title in enumerate(VIENNA_TRACKS[:vienna_tracks], start=1):
        make_mp3(artist / "Vienna (1980)" / f"{i:02d} {title}.mp3", title, "Ultravox", "Vienna", i, "1980")
    if with_quartet:
        make_mp3(artist / "Quartet (1982)" / "01 Reap the Wild Wind.mp3", "Reap the Wild Wind", "Ultravox", "Quartet", 1, "1982")
        make_mp3(artist / "Quartet (1982)" / "02 Hymn.mp3", "Hymn", "Ultravox", "Quartet", 2, "1982")
    return artist


def cristina_library(root: Path) -> Path:
    artist = root / "Cristina"
    make_mp3(artist / "Cristina (1980)" / "01 Disco Clone.mp3", "Disco Clone", "Cristina", "Cristina", 1, "1980")
    make_mp3(artist / "Sleep It Off (1984)" / "01 Blame It on the Night.mp3", "Blame It on the Night", "Cristina", "Sleep It Off", 1, "1984")
    return artist


def set_kinds(dialog, kinds: str) -> None:
    """Ticks exactly these kinds ('artist,genre', or the words both / all) in the folder question's 'What to create'."""
    from core.notes_kinds import parse_kinds

    wanted = parse_kinds(kinds)
    for key, box in dialog.kind_boxes.items():
        box.setChecked(key in wanted)
