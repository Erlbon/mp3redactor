"""
core/notes_timing.py -- how long the Create Notes lookups take, said plainly (Qt-free, pure).

  progress_suffix(done, total, elapsed, durations)   ", 4 min elapsed, about 1 h 41 min left" for the progress label
                                                     ('' while the guess would be silly: fewer than 3 folders done)
  lookup_sentence(folders, elapsed, requests, cached)  'Looked up 656 folders in 1:50:12 (5120 requests, 800 from the cache).'
  estimate(artists, albums, genres, kinds)           a rough duration for the scope dialog's hint, from the engine's own request
                                                     counts and the MusicBrainz limit of one request per 1.1 s
  scope_hint(...)                                    the one-line warning text (or '' when there is nothing to warn about)

The numbers behind the estimate were MEASURED on the live services with the engine as it is (see CHANGELOG): an artist note
needs 5 requests (3 MusicBrainz, 2 Wikipedia), an album note 4 (2 MusicBrainz, 2 Wikipedia). MusicBrainz allows one
request per second and the app keeps to 1.1 s, so a MusicBrainz request costs 1.1 s and a Wikipedia one a quarter of that.
An artist that is not found costs more (it looks at up to 4 candidates), so a real library runs slower than the estimate:
it is a hint, never a promise.
"""

from __future__ import annotations

from core.notes_kinds import parse_kinds
from redactor_common.core.notes_timing import (  # noqa: F401 -- re-exported: the dialog, the CLI and the tests import them from here
    MIN_DONE_FOR_ESTIMATE, RECENT, clock, format_duration, lookup_sentence, progress_suffix,
)

MB_SECONDS = 1.1  # the throttle of core/musicbrainz_lookup.py
WIKI_SECONDS = 0.3
ARTIST_MB, ARTIST_WIKI = 3, 2  # requests per artist note (measured)
ALBUM_MB, ALBUM_WIKI = 2, 2  # requests per album note (measured live: release group + release, wikidata + summary)
GENRE_WIKI = 2  # a genre / collection note: Wikipedia only
ARTIST_SECONDS = ARTIST_MB * MB_SECONDS + ARTIST_WIKI * WIKI_SECONDS
ALBUM_SECONDS = ALBUM_MB * MB_SECONDS + ALBUM_WIKI * WIKI_SECONDS
GENRE_SECONDS = GENRE_WIKI * WIKI_SECONDS
HINT_MIN_FOLDERS = 100  # the scope dialog warns above this many folders


def estimate(artists: int, albums: int, genres: int, kinds) -> float:
    """Seconds for the chosen `kinds` (see core/notes_kinds.py) over that many folders to look up."""
    kinds = parse_kinds(kinds)
    return ((artists * ARTIST_SECONDS if "artist" in kinds else 0.0) + (albums * ALBUM_SECONDS if "album" in kinds else 0.0)
            + (genres * GENRE_SECONDS if "genre" in kinds else 0.0))


def scope_hint(artists: int, albums: int, genres: int, kinds) -> str:
    """The scope dialog's one-line hint: how many folders the choice looks up and about how long it takes; a warning (with
    the faster choice) when album notes make a big scope slow. Only a hint, never blocking."""
    kinds = parse_kinds(kinds)
    folders = ((artists if "artist" in kinds else 0) + (albums if "album" in kinds else 0)
               + (genres if "genre" in kinds else 0))
    if kinds == ("genre",):  # one Wikipedia lookup per folder, nothing else
        if genres <= 0:
            return ""
        return (f"About {genres} genre / collection folder{'s' if genres != 1 else ''}: about "
                f"{format_duration(max(estimate(0, 0, genres, kinds), 1))} (one Wikipedia lookup each, no tags read).")
    if folders <= HINT_MIN_FOLDERS or "album" not in kinds or albums == 0:  # no album notes to warn about
        return ""
    slow = estimate(artists, albums, genres, kinds)
    kinds_without = tuple(k for k in kinds if k != "album")
    fast = estimate(artists, albums, genres, kinds_without)
    text = f"About {folders} folders: roughly {format_duration(slow)} with album notes"
    if fast > 0 and slow / fast >= 1.5:
        without = "artist notes only" if kinds_without == ("artist",) else "without the album notes"
        text += f"; {without} is about {slow / fast:.0f}x faster"
    return text + " (a rough guess: a folder that is not found takes longer)."
