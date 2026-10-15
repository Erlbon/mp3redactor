"""
core/wikipedia_lookup.py

A small, generic Wikipedia client for the folder notes (core/notes_flow.py): the lead paragraph of the
article about a band or an album, found the way a person would find it.

Finding the page, in this order:
  1. the title MusicBrainz links (a "wikipedia" url-relation), or the English sitelink of its "wikidata"
     relation (the caller passes `linked_title` / `wikidata_id`);
  2. a search (the REST search endpoint). MusicBrainz often has no link at all ("Vienna (Ultravox album)"
     exists on Wikipedia but MusicBrainz does not point to it), and the candidate pages found by search are
     VERIFIED before one is used: the summary must say it is about a band / singer (or an album), name the
     artist, and, for an artist found only by search, either mention one of the local album titles or carry
     the artist's name as its own title.

Network rules: Wikimedia asks for a descriptive User-Agent (app name + version + a URL); every call goes
through one process-wide throttle. `fetch` is the injectable `fetch(url) -> bytes` of redactor_common's
lookup_client, so tests never touch the network. A missing page (404) is None, not an error.

Nothing in here is about MP3: epubredactor / cbzredactor / videoredactor will want the same client, so it is a
candidate for redactor_common once a second app needs it.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional
from urllib.parse import quote, unquote, urlencode, urlsplit

from core.version import APP_VERSION
from redactor_common.core.lookup_client import Throttle, fetch_json, make_default_fetch, throttled

SUMMARY_URL = "https://{lang}.wikipedia.org/api/rest_v1/page/summary/{title}"
SEARCH_URL = "https://{lang}.wikipedia.org/w/rest.php/v1/search/title"
WIKIDATA_URL = "https://www.wikidata.org/w/api.php"
USER_AGENT = f"mp3redactor/{APP_VERSION} ( https://github.com/Erlbon )"
MIN_REQUEST_INTERVAL = 0.35
_SOURCE_NAME = "Wikipedia"
MAX_EXTRACT_CHARS = 1200


class WikipediaError(Exception):
    """Searching or fetching from Wikipedia failed (not "there is no such page")."""


_throttle = Throttle(MIN_REQUEST_INTERVAL)
_default_fetch = throttled(make_default_fetch(USER_AGENT, timeout=30.0), _throttle)


def default_fetch() -> Callable:
    """The real, throttled fetch(url) -> bytes with this app's User-Agent."""
    return _default_fetch


def _get(url: str, fetch: Optional[Callable]) -> Optional[dict]:
    return fetch_json(
        url, fetch or _default_fetch, error_cls=WikipediaError, source_name=_SOURCE_NAME, ignore_404=True,
        retry_429=(2.0, 5.0),
    )


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@dataclass
class Page:
    """The lead of one article."""

    title: str
    extract: str = ""
    description: str = ""
    url: str = ""
    kind: str = "standard"  # Wikipedia's page type; "disambiguation" pages are never used
    via: str = ""  # how it was found: "musicbrainz link", "wikidata", "search"

    @property
    def is_disambiguation(self) -> bool:
        return self.kind == "disambiguation"


@dataclass
class SearchHit:
    title: str
    description: str = ""
    excerpt: str = ""


def title_from_url(url: str) -> str:
    """'Vienna_(Ultravox_album)' as 'Vienna (Ultravox album)' from an en.wikipedia.org/wiki/ link, else ''."""
    parts = urlsplit(url or "")
    host = (parts.hostname or "").lower()
    if not host.endswith("wikipedia.org") or not parts.path.startswith("/wiki/"):
        return ""
    return unquote(parts.path[len("/wiki/"):]).replace("_", " ").strip()


def language_of_url(url: str) -> str:
    host = (urlsplit(url or "").hostname or "").lower()
    return host.split(".")[0] if host.endswith(".wikipedia.org") else ""


def get_summary(title: str, fetch: Optional[Callable] = None, lang: str = "en") -> Optional[Page]:
    """The summary (lead paragraph, short description, link) of the article `title`, or None when there is no
    such article."""
    title = (title or "").strip()
    if not title:
        return None
    url = SUMMARY_URL.format(lang=lang, title=quote(title.replace(" ", "_"), safe="_,.:'!*~-()"))
    data = _get(url, fetch)
    if not isinstance(data, dict) or not data.get("title"):
        return None
    page_url = ((data.get("content_urls") or {}).get("desktop") or {}).get("page") or ""
    return Page(
        title=str(data["title"]), extract=str(data.get("extract") or "").strip(),
        description=str(data.get("description") or "").strip(), url=page_url, kind=str(data.get("type") or "standard"),
    )


def search_titles(query: str, fetch: Optional[Callable] = None, limit: int = 5, lang: str = "en") -> list[SearchHit]:
    """The pages whose TITLE matches `query` (REST search/title), best first."""
    if not (query or "").strip():
        return []
    url = SEARCH_URL.format(lang=lang) + "?" + urlencode({"q": query, "limit": limit})
    data = _get(url, fetch) or {}
    hits = []
    for page in data.get("pages", []) if isinstance(data.get("pages"), list) else []:
        if isinstance(page, dict) and page.get("title"):
            hits.append(SearchHit(
                title=str(page["title"]), description=str(page.get("description") or ""),
                excerpt=re.sub(r"<[^>]+>", "", str(page.get("excerpt") or "")),
            ))
    return hits


def title_from_wikidata(entity_id: str, fetch: Optional[Callable] = None, lang: str = "en") -> str:
    """The article title of a Wikidata item in `lang` ('' if it has none)."""
    if not re.fullmatch(r"Q\d+", entity_id or ""):
        return ""
    url = WIKIDATA_URL + "?" + urlencode({
        "action": "wbgetentities", "ids": entity_id, "props": "sitelinks", "sitefilter": f"{lang}wiki", "format": "json",
    })
    data = _get(url, fetch) or {}
    entity = (data.get("entities") or {}).get(entity_id) or {}
    return str((entity.get("sitelinks") or {}).get(f"{lang}wiki", {}).get("title") or "")


def wikidata_identifiers(title: str = "", entity_id: str = "", fetch: Optional[Callable] = None, lang: str = "en") -> dict:
    """What a Wikipedia article's Wikidata item says about the artist elsewhere: {'qid', 'musicbrainz' (P434, the MusicBrainz
    artist id), 'discogs' (P1953, the Discogs artist id)}; empty strings for what the item does not have. By the article
    `title` (one request: wbgetentities with sites / titles) or by the item id. Raises WikipediaError only when the service
    cannot be reached."""
    params = {"action": "wbgetentities", "props": "claims", "format": "json"}
    if entity_id:
        if not re.fullmatch(r"Q\d+", entity_id):
            return {"qid": "", "musicbrainz": "", "discogs": ""}
        params["ids"] = entity_id
    elif title:
        params.update({"sites": f"{lang}wiki", "titles": title})
    else:
        return {"qid": "", "musicbrainz": "", "discogs": ""}
    data = _get(WIKIDATA_URL + "?" + urlencode(params), fetch) or {}
    found = {"qid": "", "musicbrainz": "", "discogs": ""}
    for qid, entity in (data.get("entities") or {}).items():
        if not isinstance(entity, dict) or "missing" in entity or not str(qid).startswith("Q"):
            continue
        found["qid"] = str(qid)
        claims = entity.get("claims") or {}
        for prop, key in (("P434", "musicbrainz"), ("P1953", "discogs")):
            for claim in claims.get(prop) or []:
                value = (((claim.get("mainsnak") or {}).get("datavalue") or {}).get("value"))
                if isinstance(value, str) and value.strip():
                    found[key] = value.strip()
                    break
        break
    return found


ARTIST_SUFFIXES = ("band", "musician", "singer", "group", "rapper", "duo", "DJ")


def find_artist_page(name: str, context_terms: Iterable[str] = (), fetch: Optional[Callable] = None, lang: str = "en",
                     notes: Optional[list[str]] = None) -> Optional[Page]:
    """The Wikipedia article of a band / musician whose plain name is shared with a city, a film or another act:
    'Leningrad (band)', 'X (musician)', 'X (singer)'... tried as the article title, then the usual search. Each page must
    pass the same check as any artist page (a music word in its lead, the name in its title or lead, and the name as the
    title's own or a local album named in the lead). None when no such page is found."""
    notes = notes if notes is not None else []
    context_terms = [t for t in context_terms if t]
    for suffix in ARTIST_SUFFIXES[:3]:
        page = _verified(get_summary(f"{name} ({suffix})", fetch, lang))
        if page and looks_like(page, "artist", name, "", context_terms):
            page.via = f"the title '{name} ({suffix})'"
            return page
    return find_page("artist", name, context_terms=context_terms, fetch=fetch, lang=lang, notes=notes)


# ---------------------------------------------------------------------------
# Verifying a page
# ---------------------------------------------------------------------------

def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c)).casefold().replace("&", " and ")
    return re.sub(r"[\W_]+", " ", text).strip()


_MUSIC_WORDS = re.compile(
    r"\b(band|singer|musician|group|duo|trio|quartet|rapper|songwriter|vocalist|composer|orchestra|ensemble|dj|"
    r"pianist|guitarist|rock|pop|punk|hip hop|new wave|singer-songwriter|recording artist|music project)\b",
    re.IGNORECASE,
)
_ALBUM_WORD = re.compile(r"\b(album|ep|soundtrack|mixtape|compilation|record)\b", re.IGNORECASE)
_GENRE_WORD = re.compile(
    r"\b(music genre|genre|subgenre|style of (?:popular |rock |electronic |dance )?music|musical style|"
    r"music style|musical movement|music movement|form of (?:popular )?music)\b", re.IGNORECASE)
_SINGLE_WORD = re.compile(r"\b(single|song)\b", re.IGNORECASE)


def _mentions(text: str, name: str) -> bool:
    name = _fold(name)
    return bool(name) and f" {name} " in f" {_fold(text)} "


def looks_like(page: Page, kind: str, name: str, artist: str = "", context_terms: Iterable[str] = ()) -> bool:
    """(kind "genre": a music genre named `name`, see below.) Is `page` about the band/singer `name` (kind "artist") or the album `name` by `artist` (kind "album")?

    Artist: a music word in the description or lead, the name in the page title or lead, and -- because many
    bands share a name -- either one of `context_terms` (the local album titles) in the lead or the name as
    the page's own title. Album: the lead or description says "album" (a "single"/"song" does not count), the
    artist is named, the album title is the page title's start or in the lead."""
    if page.is_disambiguation or not page.extract and not page.description:
        return False
    head = f"{page.description}. {page.extract[:700]}"
    if kind == "artist":
        if not _MUSIC_WORDS.search(head):
            return False
        if not (_mentions(page.title, name) or _mentions(head, name)):
            return False
        title_core = _fold(re.sub(r"\s*\([^)]*\)\s*$", "", page.title))
        if title_core == _fold(name):
            return True
        return any(_mentions(page.extract, term) for term in context_terms if term)
    if kind == "soundtrack":
        # the soundtrack's own article, or the film / game / series it belongs to
        if not re.search(r"\b(soundtrack|score|album|film|movie|video game|game|television|series|musical)\b", head, re.IGNORECASE):
            return False
        title_core = _fold(re.sub(r"\s*\([^)]*\)\s*$", "", page.title))
        return title_core.startswith(_fold(name)) or _fold(name).startswith(title_core) and len(title_core) > 3
    if kind == "genre":
        # about a MUSIC genre: the lead says genre/style of music, and the name is the page's title ("New wave
        # music") or named in its lead
        if not _GENRE_WORD.search(head):
            return False
        title_core = _fold(re.sub(r"\s*\([^)]*\)\s*$", "", page.title))
        return title_core in (_fold(name), _fold(name) + " music") or _mentions(page.title, name)
    # album
    if not _ALBUM_WORD.search(head):
        return False
    if _SINGLE_WORD.search(page.description) and not _ALBUM_WORD.search(page.description):
        return False
    if artist and not (_mentions(head, artist) or _mentions(page.title, artist)):
        return False
    title_core = _fold(re.sub(r"\s*\([^)]*\)\s*$", "", page.title))
    return title_core == _fold(name) or _mentions(head, name)


def trim_extract(text: str, limit: int = MAX_EXTRACT_CHARS) -> str:
    """`text` cut at a sentence end within `limit` characters (whole text when it is shorter)."""
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    ends = [m.end() for m in re.finditer(r"[.!?](?=\s|$)", cut)]
    return cut[: ends[-1]] if ends else cut.rsplit(" ", 1)[0] + "..."


# ---------------------------------------------------------------------------
# Finding the page
# ---------------------------------------------------------------------------

def _verified(page: Optional[Page]) -> Optional[Page]:
    return page if page is not None and not page.is_disambiguation and page.extract else None


def find_page(
    kind: str, name: str, *, artist: str = "", linked_title: str = "", wikidata_id: str = "",
    context_terms: Iterable[str] = (), fetch: Optional[Callable] = None, lang: str = "en",
    notes: Optional[list[str]] = None,
) -> Optional[Page]:
    """The Wikipedia page for an "artist" (`name` = the band) or an "album" (`name` = its title, `artist` =
    who made it), or None. `linked_title` / `wikidata_id` come from MusicBrainz (tried first, trusted unless the
    page is a disambiguation page); then the search with verification. `notes` collects what happened, for the
    proposal's reasons. Raises WikipediaError only when Wikipedia cannot be reached."""
    notes = notes if notes is not None else []
    context_terms = [t for t in context_terms if t]
    if linked_title:
        page = _verified(get_summary(linked_title, fetch, lang))
        if page:
            page.via = "musicbrainz link"
            return page
        notes.append(f"the Wikipedia link in MusicBrainz ({linked_title}) gave no usable page")
    if wikidata_id:
        title = title_from_wikidata(wikidata_id, fetch, lang)
        page = _verified(get_summary(title, fetch, lang)) if title else None
        if page:
            page.via = "wikidata"
            return page
    if kind == "album":
        queries = [f"{name} ({artist} album)", f"{name} (album)", f"{name} {artist} album".strip()]
    elif kind == "genre":
        queries = [f"{name} music", name, f"{name} (genre)"]
    elif kind == "soundtrack":
        queries = [f"{name} soundtrack", f"{name} (soundtrack)", name]
    else:
        queries = [name, f"{name} band"]
    seen: set[str] = set()
    checked = 0
    for query in queries:
        for hit in search_titles(query, fetch, limit=6, lang=lang):
            if hit.title in seen or checked >= 5:
                continue
            seen.add(hit.title)
            blurb = f"{hit.description} {hit.excerpt}"
            if kind == "album" and not (_ALBUM_WORD.search(blurb) or re.search(r"album", hit.title, re.IGNORECASE)):
                continue  # a song, a film...: not worth a request
            if kind == "genre" and not (_GENRE_WORD.search(blurb) or _fold(hit.title).endswith(("music", "genre"))
                                        or _fold(hit.title) == _fold(name)):
                continue
            if kind == "artist" and _SINGLE_WORD.search(hit.description) and not _MUSIC_WORDS.search(hit.description):
                continue
            checked += 1
            page = _verified(get_summary(hit.title, fetch, lang))
            if page and looks_like(page, kind, name, artist, context_terms):
                page.via = "search"
                return page
        if checked >= 5:
            break
    notes.append("no Wikipedia page was found and verified")
    return None
