"""
core/notes_musicbrainz.py

What the folder notes (core/notes_flow.py) need from MusicBrainz, on top of core/musicbrainz_lookup.py (same
process-wide throttle of 1 request per 1.1 s, same error type, same `fetch(url) -> bytes` injection):

  search_artists(name)             candidate artists for a folder name
  artist_detail(mbid)              one artist with url-relations, genres, tags, annotation and band members
  browse_release_groups(mbid)      the artist's albums and EPs (the discography the local folders are checked against)
  search_release_groups(artist, album, primary_album=True)   an album when the artist is not known yet
  release_group_detail(rgid)       one release group with url-relations and genres
  releases_of_group(rgid)          its releases (date, country, status, track count, label) to pick one
  fetch_release_detail(rid)        one release with its track list, label and the structured credits

Every function returns plain dataclasses (or the raw dict for the detail the caller reads links from) and
raises MusicBrainzError when MusicBrainz cannot be reached. Nothing here decides anything.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Optional

import time

from core.musicbrainz_lookup import (
    MusicBrainzError, _credit, _get as _single_get, _leading_int, _lucene,
)
from redactor_common.core.lookup_client import make_default_fetch, throttled

__all__ = [
    "ArtistHit", "ReleaseGroup", "ReleaseSummary", "ReleaseDetail", "Credits", "MusicBrainzError",
    "search_artists", "artist_detail", "browse_release_groups", "search_release_groups", "release_group_detail",
    "releases_of_group", "fetch_release_detail", "parse_release_detail", "relation_links",
    "wikipedia_hint", "member_names", "genres_of",
]


RETRY_DELAYS = (4.0, 10.0)  # seconds: MusicBrainz answers 503 "busy" when it is rate limiting; wait and ask again
_sleep = time.sleep


def _get(path: str, fetch: Callable, **params) -> dict:
    """musicbrainz_lookup's _get, asking again after a pause when MusicBrainz says it is busy (HTTP 503)."""
    for delay in (*RETRY_DELAYS, None):
        try:
            return _single_get(path, fetch, **params)
        except MusicBrainzError as exc:
            if delay is None or "busy" not in str(exc):
                raise
            _sleep(delay)
    return {}


@dataclass
class ArtistHit:
    id: str
    name: str
    score: int = 0
    type: str = ""
    country: str = ""
    disambiguation: str = ""
    begin: str = ""
    end: str = ""
    aliases: list[str] = field(default_factory=list)
    sort_name: str = ""


@dataclass
class ReleaseGroup:
    id: str
    title: str
    first_date: str = ""
    primary: str = ""  # "Album", "Single", "EP"...
    secondary: list[str] = field(default_factory=list)  # "Compilation", "Live"...
    artist: str = ""
    score: int = 0

    @property
    def year(self) -> str:
        return self.first_date[:4] if self.first_date[:4].isdigit() else ""

    @property
    def is_studio_album(self) -> bool:
        return self.primary.casefold() == "album" and all(s.casefold() == "soundtrack" for s in self.secondary)

    @property
    def is_soundtrack(self) -> bool:
        return any(s.casefold() == "soundtrack" for s in self.secondary)


@dataclass
class ReleaseSummary:
    id: str
    title: str = ""
    date: str = ""
    country: str = ""
    status: str = ""
    track_count: int = 0
    label: str = ""
    catno: str = ""


@dataclass
class Credits:
    producers: list[str] = field(default_factory=list)
    writers: list[str] = field(default_factory=list)
    performers: dict[str, list[str]] = field(default_factory=dict)  # name -> instruments / vocal kinds
    others: dict[str, list[str]] = field(default_factory=dict)  # role -> names (engineer, mix...)

    @property
    def empty(self) -> bool:
        return not (self.producers or self.writers or self.performers or self.others)


@dataclass
class DetailTrack:
    disc: int
    position: int
    title: str
    seconds: Optional[float] = None
    recording_id: str = ""


@dataclass
class ReleaseDetail:
    id: str
    title: str = ""
    artist: str = ""
    date: str = ""
    country: str = ""
    status: str = ""
    label: str = ""
    catno: str = ""
    tracks: list[DetailTrack] = field(default_factory=list)
    credits: Credits = field(default_factory=Credits)


# ---------------------------------------------------------------------------
# Artists
# ---------------------------------------------------------------------------

def search_artists(name: str, fetch: Optional[Callable] = None, limit: int = 6) -> list[ArtistHit]:
    """Candidate artists for `name`, best search score first. MusicBrainz also matches aliases and sort names,
    which is how "Cristina" finds Cristina D'Avena: the caller verifies each candidate's discography."""
    if not name.strip():
        return []
    data = _get("artist", fetch or _fetch(), query=f"artist:{_lucene(name)}", limit=limit)
    hits = []
    for a in data.get("artists", []):
        if not a.get("id"):
            continue
        life = a.get("life-span") or {}
        hits.append(ArtistHit(
            id=a["id"], name=a.get("name") or "", score=int(a.get("score") or 0), type=a.get("type") or "",
            country=a.get("country") or "", disambiguation=a.get("disambiguation") or "",
            begin=life.get("begin") or "", end=life.get("end") or "", sort_name=a.get("sort-name") or "",
            aliases=[x.get("name", "") for x in a.get("aliases", []) if x.get("name")],
        ))
    return hits


def artist_detail(mbid: str, fetch: Optional[Callable] = None) -> dict:
    """The raw artist: url relations (links, Wikipedia, Wikidata), genres, tags, annotation, area, life span,
    and the band-member relations."""
    return _get(f"artist/{mbid}", fetch or _fetch(), inc="url-rels+artist-rels+tags+genres+annotation")


def browse_release_groups(mbid: str, fetch: Optional[Callable] = None, max_pages: int = 2) -> list[ReleaseGroup]:
    """The artist's albums and EPs (not singles), oldest first. Two pages of 100 at most."""
    fetch = fetch or _fetch()
    groups: list[ReleaseGroup] = []
    for page in range(max_pages):
        data = _get("release-group", fetch, artist=mbid, type="album|ep", limit=100, offset=page * 100)
        for g in data.get("release-groups", []):
            if g.get("id"):
                groups.append(_group(g))
        if len(data.get("release-groups", [])) < 100:
            break
    return sorted(groups, key=lambda g: (g.first_date or "9999", g.title))


def _group(g: dict) -> ReleaseGroup:
    return ReleaseGroup(
        id=g["id"], title=g.get("title") or "", first_date=g.get("first-release-date") or "",
        primary=g.get("primary-type") or "", secondary=list(g.get("secondary-types") or []),
        artist=_credit(g.get("artist-credit")), score=int(g.get("score") or 0),
    )


def search_release_groups(
    artist: str, album: str, fetch: Optional[Callable] = None, primary_album: bool = True, limit: int = 6,
    soundtrack: bool = False,
) -> list[ReleaseGroup]:
    """Release groups called `album` by `artist`. With `primary_album` only primary type Album is searched
    (the single "Vienna" is a different release group from the album "Vienna"); the caller falls back to
    everything when that finds nothing."""
    if not album.strip():
        return []
    query = f"releasegroup:{_lucene(album)}"
    if artist.strip():
        query += f" AND artist:{_lucene(artist)}"
    if primary_album:
        query += " AND primarytype:album"
    if soundtrack:
        query += " AND secondarytype:soundtrack"
    data = _get("release-group", fetch or _fetch(), query=query, limit=limit)
    return [_group(g) for g in data.get("release-groups", []) if g.get("id")]


def release_group_detail(rgid: str, fetch: Optional[Callable] = None) -> dict:
    return _get(f"release-group/{rgid}", fetch or _fetch(), inc="url-rels+genres+tags+annotation")


def releases_of_group(rgid: str, fetch: Optional[Callable] = None) -> list[ReleaseSummary]:
    data = _get("release", fetch or _fetch(), **{"release-group": rgid, "inc": "labels+media", "limit": 100})
    out = []
    for r in data.get("releases", []):
        if not r.get("id"):
            continue
        info = (r.get("label-info") or [{}])[0] or {}
        out.append(ReleaseSummary(
            id=r["id"], title=r.get("title") or "", date=r.get("date") or "", country=r.get("country") or "",
            status=r.get("status") or "",
            track_count=sum(int(m.get("track-count") or 0) for m in r.get("media", [])) or int(r.get("track-count") or 0),
            label=(info.get("label") or {}).get("name") or "", catno=info.get("catalog-number") or "",
        ))
    return out


def fetch_release_detail(rid: str, fetch: Optional[Callable] = None) -> ReleaseDetail:
    data = _get(
        f"release/{rid}", fetch or _fetch(),
        inc="recordings+labels+artist-credits+artist-rels+recording-level-rels+work-rels+work-level-rels",
    )
    return parse_release_detail(data, rid)


_notes_fetch: Optional[Callable] = None


def _fetch() -> Callable:
    """The real fetch: this app's version in the User-Agent, and the SAME process-wide throttle as the rest of
    the app's MusicBrainz requests (one request per 1.1 s, whoever asks)."""
    global _notes_fetch
    if _notes_fetch is None:
        from core import musicbrainz_lookup
        from core.version import APP_VERSION

        agent = f"mp3redactor/{APP_VERSION} ( https://github.com/Erlbon )"
        _notes_fetch = throttled(make_default_fetch(agent, timeout=30.0), musicbrainz_lookup._throttle)
    return _notes_fetch


# ---------------------------------------------------------------------------
# Reading the replies
# ---------------------------------------------------------------------------

_WRITER_TYPES = {"composer", "lyricist", "writer", "librettist", "arranger"}
_PERFORMER_TYPES = {"instrument", "vocal", "performer", "performing orchestra", "conductor"}
_OTHER_TYPES = {"engineer", "mix", "recording", "mastering", "producer", "audio", "sound", "programming"}
MAX_PEOPLE = 14


def _add(items: list[str], name: str) -> None:
    if name and name not in items:
        items.append(name)


def _read_relations(relations, credits: Credits) -> None:
    for rel in relations or []:
        artist = rel.get("artist") or {}
        name = artist.get("name") or ""
        kind = (rel.get("type") or "").casefold()
        if not name:
            work = rel.get("work") or {}
            for inner in work.get("relations", []) if work else []:
                _read_relations([inner], credits)
            continue
        if kind == "producer":
            _add(credits.producers, name)
        elif kind in _WRITER_TYPES:
            _add(credits.writers, name)
        elif kind in _PERFORMER_TYPES:
            attrs = credits.performers.setdefault(name, [])
            for attribute in rel.get("attributes") or []:
                if attribute not in attrs and attribute not in ("additional", "guest", "solo", "co"):
                    attrs.append(attribute)
        elif kind in _OTHER_TYPES:
            _add(credits.others.setdefault(kind, []), name)


def parse_release_detail(data: dict, rid: str = "") -> ReleaseDetail:
    """A release with recordings and relations (see fetch_release_detail) as a ReleaseDetail."""
    info = (data.get("label-info") or [{}])[0] or {}
    detail = ReleaseDetail(
        id=data.get("id", rid), title=data.get("title") or "", artist=_credit(data.get("artist-credit")),
        date=data.get("date") or "", country=data.get("country") or "", status=data.get("status") or "",
        label=(info.get("label") or {}).get("name") or "", catno=info.get("catalog-number") or "",
    )
    credits = detail.credits
    _read_relations(data.get("relations"), credits)
    for medium in data.get("media", []):
        disc = medium.get("position") or 1
        for t in medium.get("tracks", []):
            recording = t.get("recording") or {}
            length = t.get("length") or recording.get("length")
            detail.tracks.append(DetailTrack(
                disc=disc, position=t.get("position") or _leading_int(str(t.get("number", ""))) or 0,
                title=t.get("title") or recording.get("title") or "", seconds=length / 1000 if length else None,
                recording_id=recording.get("id") or "",
            ))
            _read_relations(recording.get("relations"), credits)
    credits.producers[:] = credits.producers[:MAX_PEOPLE]
    credits.writers[:] = credits.writers[:MAX_PEOPLE]
    credits.performers = dict(list(credits.performers.items())[:MAX_PEOPLE])
    return detail


_LINK_TYPES = ("official homepage", "allmusic", "discogs", "wikipedia", "last.fm")


def relation_links(detail: dict) -> dict[str, str]:
    """{relation type: url} for the link relations worth keeping in a note (first of each type)."""
    found: dict[str, str] = {}
    for rel in detail.get("relations", []):
        url = (rel.get("url") or {}).get("resource") or ""
        kind = rel.get("type") or ""
        if kind in _LINK_TYPES and url.startswith(("http://", "https://")) and kind not in found:
            found[kind] = url
    return found


def wikipedia_hint(detail: dict) -> tuple[str, str]:
    """(English Wikipedia title, Wikidata id) from a detail's url relations; either may be ''."""
    from core.wikipedia_lookup import language_of_url, title_from_url

    title = wikidata = ""
    for rel in detail.get("relations", []):
        url = (rel.get("url") or {}).get("resource") or ""
        if rel.get("type") == "wikipedia" and language_of_url(url) == "en" and not title:
            title = title_from_url(url)
        elif rel.get("type") == "wikidata" and not wikidata:
            match = re.search(r"/(Q\d+)$", url)
            wikidata = match.group(1) if match else ""
    return title, wikidata


def member_names(detail: dict) -> list[str]:
    """Current band members first, then former ones, from the "member of band" relations."""
    current, former = [], []
    for rel in detail.get("relations", []):
        if rel.get("type") != "member of band" or rel.get("direction") != "backward":
            continue
        name = (rel.get("artist") or {}).get("name") or ""
        if name:
            _add(former if rel.get("ended") else current, name)
    return current + [n for n in former if n not in current]


def genres_of(detail: dict, limit: int = 6) -> list[str]:
    """Genres by vote count (else tags), lower-case, at most `limit`."""
    genres = sorted(detail.get("genres") or [], key=lambda g: -int(g.get("count") or 0))
    names = [g["name"] for g in genres if g.get("name")]
    if not names:
        tags = sorted(detail.get("tags") or [], key=lambda g: -int(g.get("count") or 0))
        names = [t["name"] for t in tags if t.get("name")]
    return names[:limit]
