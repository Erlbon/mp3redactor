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

from dataclasses import dataclass

ALLOWED_HOSTS = ("musicbrainz.org", "discogs.com", "wikipedia.org", "wikidata.org")
ALLOWED_TEXT = "musicbrainz.org, discogs.com, wikipedia.org or wikidata.org"


class LinkError(ValueError):
    """The text is not a link the app can use; the message says why in plain words (shown next to the line edit)."""


@dataclass(frozen=True)
class LinkTarget:
    kind: str
    id: str
    lang: str = ""
    raw: str = ""


def _host_of(raw: str) -> str:
    from urllib.parse import urlsplit

    try:
        return (urlsplit(raw if "://" in raw else "https://" + raw).hostname or "").lower()
    except ValueError:
        return ""


def parse_link(text: str, bare_as: str = "artist") -> LinkTarget:
    """What `text` names. `bare_as` ('artist' or 'release-group') is what a bare MusicBrainz id is taken to be. The parsing is
    redactor_common's source_ids.explain_link (one link parser for the four Redactor apps: no user-info, no odd ports, no
    non-ASCII hosts, the Wikipedia language from the real sub-domain, mobile links accepted); this wrapper only keeps the four
    sites and the kinds Create Notes can use, with their plain messages."""
    from redactor_common.core import source_ids as sid

    raw = (text or "").strip()
    if not raw:
        raise LinkError("Paste a Wikipedia, MusicBrainz or Discogs link first.")
    try:
        ref = sid.explain_link(raw, bare_as=("musicbrainz", "release-group" if bare_as == "release-group" else "artist"))
    except sid.SourceIdError as exc:
        message = str(exc)
        if message.startswith("That MusicBrainz link is not an artist"):  # the app's own (shorter) wording
            message = ("That MusicBrainz link is not an artist or a release group page "
                       "(it looks like .../artist/<id> or .../release-group/<id>).")
        elif message.startswith("Only links from"):
            message = f"Only links from {ALLOWED_TEXT} are used: {_host_of(raw) or raw} is not one of them."
        raise LinkError(message) from exc
    if ref.service not in ("musicbrainz", "discogs", "wikipedia", "wikidata"):
        raise LinkError(f"Only links from {ALLOWED_TEXT} are used.")
    if ref.service == "musicbrainz":
        if ref.kind not in ("artist", "release-group"):
            raise LinkError("That MusicBrainz link is not an artist or a release group page "
                            "(it looks like .../artist/<id> or .../release-group/<id>).")
        return LinkTarget("mb-" + ref.kind, ref.id, raw=raw)
    if ref.service == "discogs":
        return LinkTarget("discogs-" + ref.kind, ref.id, raw=raw)
    if ref.service == "wikidata":
        return LinkTarget("wikidata", ref.id, raw=raw)
    return LinkTarget("wikipedia", ref.title, lang=ref.lang, raw=raw)
