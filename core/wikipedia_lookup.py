"""
core/wikipedia_lookup.py

The Wikipedia client for the folder notes (core/notes_flow.py): the lead paragraph of the article about a band or an
album, found the way a person would find it. The fetching and parsing (summary, search, Wikidata) is redactor_common's
(redactor_common/core/wikipedia.py, shared with videoredactor); what stays here is what is about MUSIC: which article
counts as the band / album / genre, and how MusicBrainz links lead to it.

Finding the page, in this order:
  1. the title MusicBrainz links (a "wikipedia" url-relation), or the English sitelink of its "wikidata"
     relation (the caller passes `linked_title` / `wikidata_id`);
  2. a search (the REST search endpoint). MusicBrainz often has no link at all ("Vienna (Ultravox album)"
     exists on Wikipedia but MusicBrainz does not point to it), and the candidate pages found by search are
     VERIFIED before one is used: the summary must say it is about a band / singer (or an album), name the
     artist, and, for an artist found only by search, either mention one of the local album titles or carry
     the artist's name as its own title.

`fetch` is the injectable `fetch(url) -> bytes`, so tests never touch the network. A missing page (404) is None,
not an error.
"""

from __future__ import annotations

import re
from typing import Callable, Iterable, Optional

from core.notes_numbers import compare_key
from core.version import APP_VERSION
from redactor_common.core import wikipedia as _w
from redactor_common.core.wikipedia import (  # noqa: F401 -- re-exported: the engine and the tests import them from here
    MAX_EXTRACT_CHARS, MIN_REQUEST_INTERVAL, Page, SearchHit, WikipediaError, language_of_url, title_from_url,
    trim_extract,
)

USER_AGENT = f"mp3redactor/{APP_VERSION} ( https://github.com/Erlbon )"
_default_fetch = _w.make_fetch(USER_AGENT)
_fold = _w.fold
_verified = _w.usable


def default_fetch() -> Callable:
    """The real, throttled fetch(url) -> bytes with this app's User-Agent."""
    return _default_fetch


def get_summary(title: str, fetch: Optional[Callable] = None, lang: str = "en") -> Optional[Page]:
    return _w.get_summary(title, fetch or _default_fetch, lang)


def search_titles(query: str, fetch: Optional[Callable] = None, limit: int = 5, lang: str = "en") -> list[SearchHit]:
    return _w.search_titles(query, fetch or _default_fetch, limit, lang)


def title_from_wikidata(entity_id: str, fetch: Optional[Callable] = None, lang: str = "en") -> str:
    return _w.title_from_wikidata(entity_id, fetch or _default_fetch, lang)


def wikidata_identifiers(title: str = "", entity_id: str = "", fetch: Optional[Callable] = None, lang: str = "en") -> dict:
    """What a Wikipedia article's Wikidata item says about the artist elsewhere: {'qid', 'musicbrainz' (P434, the MusicBrainz
    artist id), 'discogs' (P1953, the Discogs artist id)}; empty strings for what the item does not have. By the article
    `title` (one request) or by the item id. Raises WikipediaError only when the service cannot be reached."""
    return _w.wikidata_claims({"P434": "musicbrainz", "P1953": "discogs"}, title, entity_id, fetch or _default_fetch, lang)


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
    name = compare_key(name)  # 'Seven' finds '7' and the reverse
    return bool(name) and f" {name} " in f" {compare_key(text)} "


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


# ---------------------------------------------------------------------------
# Finding the page
# ---------------------------------------------------------------------------

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
