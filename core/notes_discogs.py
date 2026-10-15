"""
core/notes_discogs.py

What the folder notes (core/notes_flow.py) need from Discogs, on top of core/discogs_lookup.py (the app's own
DiscogsClient: token from DISCOGS_TOKEN or the credential store, 1 request per 1.1 s, 429 -> DiscogsRateLimited,
the token never in a message). Without a token nothing here is called: the notes are simply thinner.

  search_artists(client, name)      candidate artists
  artist_info(client, id)           profile text, real name, members, links
  artist_releases(client, id)       the titles Discogs lists for the artist (the discography to verify against)
  find_release(client, artist, album, year, file_count)   the best release for an album folder + its credits

Credits are STRUCTURED only (producer, writers, who played what): Discogs' free-text release notes are never
copied (pressing details, shipping remarks...).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from core.discogs_lookup import (
    Candidate as DiscogsHit, DiscogsClient, DiscogsError, DiscogsQuery, DiscogsRelease, clean_artist_name, parse_release,
    search_releases,
)
from core.notes_musicbrainz import Credits

__all__ = [
    "DiscogsArtistHit", "DiscogsArtist", "DiscogsAlbumInfo", "search_artists", "artist_info", "artist_releases",
    "find_release", "credits_from_release", "DiscogsError", "artist_release_entries", "search_album", "release_info",
    "MASTER_PREFIX",
]

MASTER_PREFIX = "master:"  # a Candidate id for a Discogs MASTER (resolved to its main release)


@dataclass
class DiscogsArtistHit:
    id: int
    name: str
    uri: str = ""


@dataclass
class DiscogsArtist:
    id: int
    name: str
    profile: str = ""
    real_name: str = ""
    members: list[str] = field(default_factory=list)
    active_members: list[str] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)
    uri: str = ""
    aliases: list[str] = field(default_factory=list)  # other names the artist is listed under (real name excluded)


@dataclass
class DiscogsAlbumInfo:
    release: DiscogsRelease
    credits: Credits
    score: float = 0.0
    released: str = ""  # Discogs' release date text of the release


def search_artists(client: DiscogsClient, name: str, limit: int = 5) -> list[DiscogsArtistHit]:
    if not name.strip():
        return []
    data = client.get_json("database/search", type="artist", q=name, per_page=limit)
    hits = []
    for r in data.get("results", []) if isinstance(data.get("results"), list) else []:
        if isinstance(r, dict) and isinstance(r.get("id"), int):
            hits.append(DiscogsArtistHit(id=r["id"], name=clean_artist_name(str(r.get("title") or "")), uri=str(r.get("uri") or "")))
    return hits


def artist_info(client: DiscogsClient, artist_id: int) -> DiscogsArtist:
    data = client.get_json(f"artists/{artist_id}")
    members = [m for m in data.get("members", []) if isinstance(m, dict) and m.get("name")] if isinstance(data.get("members"), list) else []
    uri = str(data.get("uri") or "")
    return DiscogsArtist(
        id=artist_id, name=clean_artist_name(str(data.get("name") or "")), profile=str(data.get("profile") or ""),
        real_name=str(data.get("realname") or "").strip(),
        aliases=[clean_artist_name(a["name"]) for a in data.get("aliases", [])
                 if isinstance(a, dict) and a.get("name")] if isinstance(data.get("aliases"), list) else [],
        members=[clean_artist_name(m["name"]) for m in members],
        active_members=[clean_artist_name(m["name"]) for m in members if m.get("active")],
        urls=[u for u in data.get("urls", []) if isinstance(u, str) and u.startswith(("http://", "https://"))],
        uri=uri if uri.startswith("https://www.discogs.com/") else f"https://www.discogs.com/artist/{artist_id}",
    )


def artist_release_entries(client: DiscogsClient, artist_id: int) -> list[dict]:
    """The entries Discogs lists for the artist where the artist is the MAIN artist (one page of 100, by year):
    dicts with title, year, type ('master' / 'release'), format (releases only; masters carry none in the list)."""
    data = client.get_json(f"artists/{artist_id}/releases", per_page=100, sort="year")
    out = []
    for r in data.get("releases", []) if isinstance(data.get("releases"), list) else []:
        if isinstance(r, dict) and r.get("title") and (r.get("role") in (None, "Main")):
            out.append({"title": str(r["title"]), "year": str(r.get("year") or ""), "type": str(r.get("type") or ""),
                        "format": str(r.get("format") or "")})
    return out


def artist_releases(client: DiscogsClient, artist_id: int) -> list[tuple[str, str]]:
    """[(title, year)] of the releases and masters the artist is the main artist of."""
    return [(e["title"], e["year"]) for e in artist_release_entries(client, artist_id)]


# ---------------------------------------------------------------------------
# Credits
# ---------------------------------------------------------------------------

_WRITER_ROLES = re.compile(r"^(written|composed|lyrics|music|words|songwriter|arranged|text)\b", re.IGNORECASE)
_NON_MUSIC = re.compile(
    r"(photograph|design|artwork|illustrat|cover|layout|art direction|liner|notes|management|manager|a&r|"
    r"legal|copyright|published|distributed|pressed|lacquer|typography|lettering|coordinat|executive|"
    r"booking|thanks|translat|calligraph|painting|concept)", re.IGNORECASE,
)
_ENGINEER_ROLES = re.compile(r"^(engineer|recorded|mixed|mastered|remix|programm|edited)", re.IGNORECASE)


def _add(items: list[str], name: str) -> None:
    if name and name not in items:
        items.append(name)


_DESIGN_ROLES = re.compile(r"(design|artwork|illustrat|layout|art direction|cover|painting|calligraph|lettering)", re.IGNORECASE)
_PHOTO_ROLES = re.compile(r"photograph", re.IGNORECASE)


def credits_from_release(data: dict, limit: int = 14, include_design: bool = False) -> Credits:
    """Producer, writers, other studio roles and who played what, from a GET /releases/{id} reply's
    `extraartists` (release-wide and per track). `include_design`: also the sleeve roles (design, artwork,
    photography) as 'sleeve design' / 'photography' lines. Only structured roles, never the free-text notes."""
    credits = Credits()
    entries = [e for e in data.get("extraartists", []) if isinstance(e, dict)] if isinstance(data.get("extraartists"), list) else []
    for track in data.get("tracklist", []) if isinstance(data.get("tracklist"), list) else []:
        if isinstance(track, dict):
            entries += [e for e in track.get("extraartists", []) if isinstance(e, dict)] if isinstance(track.get("extraartists"), list) else []
    for entry in entries:
        name = clean_artist_name(str(entry.get("name") or ""))
        for role in [r.strip() for r in re.split(r",\s*(?![^\[]*\])", str(entry.get("role") or "")) if r.strip()]:
            if not name:
                continue
            if include_design and _PHOTO_ROLES.search(role):
                _add(credits.others.setdefault("photography", []), name)
                continue
            if include_design and _DESIGN_ROLES.search(role):
                _add(credits.others.setdefault("sleeve design", []), name)
                continue
            if _NON_MUSIC.search(role):
                continue
            base = re.sub(r"\s*\[.*?\]", "", role).strip()
            if re.match(r"^(co-)?produc", base, re.IGNORECASE):
                _add(credits.producers, name)
            elif _WRITER_ROLES.match(base):
                _add(credits.writers, name)
            elif _ENGINEER_ROLES.match(base):
                _add(credits.others.setdefault(base.lower(), []), name)
            else:
                skills = credits.performers.setdefault(name, [])
                _add(skills, role.lower())
    credits.producers[:] = credits.producers[:limit]
    credits.writers[:] = credits.writers[:limit]
    credits.performers = dict(list(credits.performers.items())[:limit])
    return credits


def search_album(client: DiscogsClient, artist: str, album: str, year: str = "", limit: int = 6) -> list[DiscogsHit]:
    """Discogs releases that may be this album, best first (one request)."""
    return search_releases(client, DiscogsQuery(artist=artist, album=album, year=year), limit=limit)


def release_info(client: DiscogsClient, ident, score: float = 0.0, include_design: bool = False) -> DiscogsAlbumInfo:
    """A release with its structured credits. `ident` is a release id, or 'master:<id>' for a master (its main
    release is used). Two requests for a master, one for a release."""
    text = str(ident)
    if text.startswith(MASTER_PREFIX):
        master = client.get_json(f"masters/{text[len(MASTER_PREFIX):]}")
        main = master.get("main_release")
        if not isinstance(main, int):
            raise DiscogsError("Discogs has no main release for that master")
        text = str(main)
    data = client.get_json(f"releases/{text}")
    release = parse_release(data, int(text) if text.isdigit() else 0)
    return DiscogsAlbumInfo(release=release, credits=credits_from_release(data, include_design=include_design),
                            score=score, released=release.released)


def find_release(
    client: DiscogsClient, artist: str, album: str, year: str = "", file_count: Optional[int] = None,
    minimum: float = 0.9,
) -> Optional[DiscogsAlbumInfo]:
    """The best Discogs release for an album (artist and title both a close match: `minimum`, 0..1) with its
    structured credits, or None. Two requests: the search and the release."""
    candidates = search_album(client, artist, album, year, limit=8)
    if not candidates or candidates[0].score < minimum:
        return None
    return release_info(client, candidates[0].id, candidates[0].score)
