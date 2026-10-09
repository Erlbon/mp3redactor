"""
core/audiobook_lookup.py

Looks an audiobook up by title and author, for Create M4B Audiobook's "Look Up" button. No page
scraping: two public JSON services, tried in this order.

  1. Audible's public catalog search (api.audible.com/1.0/catalog/products) -- the same one
     Audiobookshelf's own Audible provider uses. Keyless. Gives what audiobooks need: narrators,
     series and the book's number in it, publisher, release date, language, description, cover and
     the running time (used to rank the results: the right edition is the one whose length matches
     the files).
  2. Open Library's search (openlibrary.org/search.json), when Audible finds little: title, author,
     first publish year, publisher and cover; no narrator, series or language (its language list
     names every edition's language, so it says nothing about the audiobook).

Qt-free. Every network call goes through `fetch(url) -> bytes` (the default uses urllib with a
timeout and a User-Agent), so tests can feed canned answers. Failures raise AudiobookLookupError.
"""

from __future__ import annotations

import difflib
import html
import json
import re
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Callable

from core.mp3_languages import DEFAULT_LANGUAGES

USER_AGENT = "mp3redactor (audiobook lookup; https://github.com/Erlbon/mp3redactor)"
TIMEOUT_SECONDS = 20
AUDIBLE_RESPONSE_GROUPS = "contributors,media,product_attrs,product_desc,product_extended_attrs,series"
AUDIBLE_REGIONS = ("com", "co.uk", "de", "fr", "it", "es", "ca", "com.au", "in", "co.jp")
DEFAULT_REGION = "com"
RESULTS_PER_SOURCE = 8
OPEN_LIBRARY_MIN_AUDIBLE_HITS = 3  # fewer than this and Open Library is asked too

SOURCE_AUDIBLE = "Audible"
SOURCE_OPEN_LIBRARY = "Open Library"

Fetch = Callable[[str], bytes]


class AudiobookLookupError(Exception):
    pass


@dataclass
class BookMatch:
    source: str
    title: str
    authors: list[str] = field(default_factory=list)
    narrators: list[str] = field(default_factory=list)
    subtitle: str = ""
    publisher: str = ""
    year: str = ""
    language: str = ""  # ISO 639-2 code when it could be worked out
    series: str = ""
    series_index: str = ""
    description: str = ""
    cover_url: str = ""
    runtime_minutes: int | None = None
    asin: str = ""

    @property
    def author_text(self) -> str:
        return ", ".join(self.authors)

    @property
    def narrator_text(self) -> str:
        return ", ".join(self.narrators)


@dataclass
class LookupOutcome:
    matches: list[BookMatch]
    notes: list[str] = field(default_factory=list)  # a source that failed, said in words


# --- plumbing ---------------------------------------------------------------------


def default_fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        raise AudiobookLookupError(f"{exc.code} {exc.reason} from {urllib.parse.urlparse(url).netloc}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise AudiobookLookupError(f"could not reach {urllib.parse.urlparse(url).netloc}: {exc}") from exc


def _get_json(url: str, fetch: Fetch) -> dict:
    try:
        data = json.loads(fetch(url))
    except ValueError as exc:
        raise AudiobookLookupError(f"{urllib.parse.urlparse(url).netloc} sent something that is not JSON") from exc
    if not isinstance(data, dict):
        raise AudiobookLookupError(f"{urllib.parse.urlparse(url).netloc} sent an unexpected answer")
    return data


def html_to_text(text: str) -> str:
    """Audible's summaries are small HTML (<p>, <b>, <br>): plain text with paragraph breaks."""
    text = re.sub(r"(?i)</p>", "\n\n", text or "")
    text = re.sub(r"(?i)<br\s*/?>|</li>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n\n", text)).strip()


_LANGUAGE_CODES = {name.casefold(): code for code, name in DEFAULT_LANGUAGES}


def language_code(name: str) -> str:
    """"english" -> "eng" (ISO 639-2, as the rest of the app uses); a 3-letter code stays; "" if unknown."""
    name = (name or "").strip().casefold()
    if len(name) == 3 and name.isalpha():
        return name
    return _LANGUAGE_CODES.get(name, "")


def _year(date_text: str) -> str:
    match = re.match(r"\s*(\d{4})", date_text or "")
    return match.group(1) if match else ""


# --- Audible ---------------------------------------------------------------------


def search_audible(
    title: str, author: str = "", fetch: Fetch | None = None, region: str = DEFAULT_REGION,
    limit: int = RESULTS_PER_SOURCE,
) -> list[BookMatch]:
    region = region if region in AUDIBLE_REGIONS else DEFAULT_REGION
    params = {
        "num_results": limit, "products_sort_by": "Relevance", "response_groups": AUDIBLE_RESPONSE_GROUPS,
        "title": title,
    }
    if author.strip():
        params["author"] = author
    url = f"https://api.audible.{region}/1.0/catalog/products?" + urllib.parse.urlencode(params)
    data = _get_json(url, fetch or default_fetch)
    matches = []
    for product in data.get("products") or []:
        series = (product.get("series") or [{}])[0] or {}
        images = product.get("product_images") or {}
        cover = images.get("500") or next(iter(images.values()), "")
        matches.append(BookMatch(
            source=SOURCE_AUDIBLE,
            title=(product.get("title") or "").strip(),
            subtitle=(product.get("subtitle") or "").strip(),
            authors=[a.get("name", "") for a in product.get("authors") or [] if a.get("name")],
            narrators=[n.get("name", "") for n in product.get("narrators") or [] if n.get("name")],
            publisher=(product.get("publisher_name") or "").strip(),
            year=_year(product.get("release_date") or product.get("issue_date") or ""),
            language=language_code(product.get("language") or ""),
            series=(series.get("title") or "").strip(),
            series_index=str(series.get("sequence") or "").strip(),
            description=html_to_text(product.get("publisher_summary") or ""),
            cover_url=cover,
            runtime_minutes=product.get("runtime_length_min") or None,
            asin=product.get("asin") or "",
        ))
    return [m for m in matches if m.title]


# --- Open Library ----------------------------------------------------------------


def search_open_library(
    title: str, author: str = "", fetch: Fetch | None = None, limit: int = RESULTS_PER_SOURCE
) -> list[BookMatch]:
    params = {
        "title": title, "limit": limit,
        "fields": "title,subtitle,author_name,first_publish_year,publisher,cover_i",
    }
    if author.strip():
        params["author"] = author
    data = _get_json("https://openlibrary.org/search.json?" + urllib.parse.urlencode(params), fetch or default_fetch)
    matches = []
    for doc in data.get("docs") or []:
        cover_id = doc.get("cover_i")
        matches.append(BookMatch(
            source=SOURCE_OPEN_LIBRARY,
            title=(doc.get("title") or "").strip(),
            subtitle=(doc.get("subtitle") or "").strip(),
            authors=list(doc.get("author_name") or [])[:3],
            publisher=((doc.get("publisher") or [""])[0] or "").strip(),
            year=str(doc.get("first_publish_year") or ""),
            cover_url=f"https://covers.openlibrary.org/b/id/{cover_id}-L.jpg" if cover_id else "",
        ))
    return [m for m in matches if m.title]


# --- ranking and the combined search ------------------------------------------------


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "").casefold()
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[\W_]+", " ", text).strip()


def _similar(a: str, b: str) -> float:
    a, b = _norm(a), _norm(b)
    return difflib.SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def score(match: BookMatch, title: str, author: str = "", runtime_minutes: float | None = None) -> float:
    """Higher is a better match: the title counts most, then the author, then how close the
    running time is to the files' (the same book in another edition or abridgement differs)."""
    total = 2.0 * max(_similar(match.title, title), _similar(f"{match.title} {match.subtitle}", title))
    if author.strip():
        total += max((_similar(name, author) for name in match.authors), default=0.0)
    if runtime_minutes and match.runtime_minutes:
        gap = abs(match.runtime_minutes - runtime_minutes) / max(match.runtime_minutes, runtime_minutes)
        total += 1.0 - min(1.0, gap * 4)  # within a few percent scores full, 25% off scores nothing
    return total


def search(
    title: str, author: str = "", runtime_minutes: float | None = None, fetch: Fetch | None = None,
    region: str = DEFAULT_REGION,
) -> LookupOutcome:
    """Audible first (best first by score), then Open Library when Audible found little or failed.
    Raises AudiobookLookupError only when every source failed."""
    if not title.strip():
        raise AudiobookLookupError("type a title to look up")
    matches: list[BookMatch] = []
    notes: list[str] = []
    failed = 0
    audible: list[BookMatch] = []
    try:
        audible = search_audible(title, author, fetch, region)
    except AudiobookLookupError as exc:
        failed += 1
        notes.append(f"Audible: {exc}")
    audible.sort(key=lambda m: -score(m, title, author, runtime_minutes))
    matches += audible
    if len(audible) < OPEN_LIBRARY_MIN_AUDIBLE_HITS:
        try:
            library = search_open_library(title, author, fetch)
            library.sort(key=lambda m: -score(m, title, author, runtime_minutes))
            matches += library
        except AudiobookLookupError as exc:
            failed += 1
            notes.append(f"Open Library: {exc}")
    if failed == 2:
        raise AudiobookLookupError("; ".join(notes))
    return LookupOutcome(matches, notes)


_IMAGE_MIMES = {b"\xff\xd8\xff": "image/jpeg", b"\x89PNG": "image/png"}


def download_cover(url: str, fetch: Fetch | None = None) -> tuple[bytes, str]:
    """(image bytes, MIME type) of a result's cover; raises AudiobookLookupError."""
    if not url:
        raise AudiobookLookupError("this result has no cover")
    data = (fetch or default_fetch)(url)
    for magic, mime in _IMAGE_MIMES.items():
        if data.startswith(magic):
            return data, mime
    raise AudiobookLookupError("the cover is not a JPEG or PNG image")
