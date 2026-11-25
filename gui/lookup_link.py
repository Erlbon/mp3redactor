"""
gui/lookup_link.py

The "Link" box in the album lookups' Search Query form (2026-11-25): paste
a Discogs or MusicBrainz link to the right album, press Search This Item,
and that release is used as it is -- no search -- with the folder's files
matched to its tracks as usual. Each dialog resolves the link with
redactor_common's parse_*_link helpers; this holds the shared bits.
"""

from __future__ import annotations

LINK_FIELD = ("link", "Link")
LINK_TIP = (
    " Know the album already? Paste its link into the Link box under Search Query and press "
    "Search This Item: that release is used as it is."
)


def link_from(override: dict | None) -> str:
    return ((override or {}).get("link") or "").strip()


def no_tracks_matched(file_count: int, title: str, track_count: int) -> str:
    return (
        f"None of the folder's {file_count} file(s) matched a track on \"{title}\" ({track_count} track(s)). "
        "Check that the link is the right album."
    )
