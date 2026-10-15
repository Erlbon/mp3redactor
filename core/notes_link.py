"""
core/notes_link.py -- 'Look up by link': a pasted Wikipedia, MusicBrainz, Discogs or Wikidata link (or a bare MusicBrainz id)
turned into WHAT it names, without any network. Only four sites are accepted (musicbrainz.org, discogs.com, wikipedia.org,
wikidata.org, with their sub-domains); anything else is refused with a plain message and nothing is fetched.

  parse_link("https://musicbrainz.org/artist/2bf7...")  -> LinkTarget("mb-artist", "2bf7...")
  parse_link("https://en.wikipedia.org/wiki/Leningrad_(band)") -> LinkTarget("wikipedia", "Leningrad (band)", lang="en")
  parse_link("2bf704b3-...", bare_as="release-group") -> LinkTarget("mb-release-group", "2bf704b3-...")

Kinds: mb-artist, mb-release-group, discogs-artist, discogs-release, discogs-master, wikipedia, wikidata.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

ALLOWED_HOSTS = ("musicbrainz.org", "discogs.com", "wikipedia.org", "wikidata.org")
ALLOWED_TEXT = "musicbrainz.org, discogs.com, wikipedia.org or wikidata.org"
_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


class LinkError(ValueError):
    """The text is not a link the app can use; the message says why in plain words (shown next to the line edit)."""


@dataclass(frozen=True)
class LinkTarget:
    kind: str
    id: str
    lang: str = ""
    raw: str = ""


def _host_ok(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def parse_link(text: str, bare_as: str = "artist") -> LinkTarget:
    """What `text` names. `bare_as` ('artist' or 'release-group') is what a bare MusicBrainz id is taken to be."""
    raw = (text or "").strip()
    if not raw:
        raise LinkError("Paste a Wikipedia, MusicBrainz or Discogs link first.")
    if _UUID.match(raw):
        return LinkTarget("mb-release-group" if bare_as == "release-group" else "mb-artist", raw.lower(), raw=raw)
    candidate = raw if "://" in raw else ("https://" + raw if re.match(r"^[\w.-]+\.[a-z]{2,}/", raw, re.IGNORECASE) else "")
    if not candidate:
        raise LinkError(f"That is not a link I can use. Paste a link from {ALLOWED_TEXT}, or a MusicBrainz id.")
    parts = urlsplit(candidate)
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or not any(_host_ok(host, d) for d in ALLOWED_HOSTS):
        raise LinkError(f"Only links from {ALLOWED_TEXT} are used: {host or raw} is not one of them.")
    path = unquote(parts.path)
    if _host_ok(host, "musicbrainz.org"):
        match = re.match(r"^/(artist|release-group)/([0-9a-fA-F-]{36})\b", path)
        if not match:
            raise LinkError("That MusicBrainz link is not an artist or a release group page "
                            "(it looks like .../artist/<id> or .../release-group/<id>).")
        return LinkTarget("mb-" + match.group(1), match.group(2).lower(), raw=raw)
    if _host_ok(host, "discogs.com"):
        match = re.search(r"/(artist|release|master)/(\d+)", path)
        if not match:
            raise LinkError("That Discogs link is not an artist, release or master page (.../artist/<number>-Name).")
        return LinkTarget("discogs-" + match.group(1), match.group(2), raw=raw)
    if _host_ok(host, "wikidata.org"):
        match = re.search(r"/(?:wiki|entity)/(Q\d+)", path) or re.match(r"^.*?(Q\d+)$", path)
        if not match:
            raise LinkError("That Wikidata link has no item number (.../wiki/Q123).")
        return LinkTarget("wikidata", match.group(1), raw=raw)
    match = re.match(r"^/wiki/(.+)$", path)
    if not match:
        raise LinkError("That Wikipedia link is not an article (.../wiki/Title).")
    return LinkTarget("wikipedia", match.group(1).replace("_", " "), lang=host.split(".")[0], raw=raw)
