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

from typing import Iterable

MB_SECONDS = 1.1  # the throttle of core/musicbrainz_lookup.py
WIKI_SECONDS = 0.3
ARTIST_MB, ARTIST_WIKI = 3, 2  # requests per artist note (measured)
ALBUM_MB, ALBUM_WIKI = 2, 2  # requests per album note (measured live: release group + release, wikidata + summary)
GENRE_WIKI = 2  # a genre / collection note: Wikipedia only
ARTIST_SECONDS = ARTIST_MB * MB_SECONDS + ARTIST_WIKI * WIKI_SECONDS
ALBUM_SECONDS = ALBUM_MB * MB_SECONDS + ALBUM_WIKI * WIKI_SECONDS
GENRE_SECONDS = GENRE_WIKI * WIKI_SECONDS
HINT_MIN_FOLDERS = 100  # the scope dialog warns above this many folders
MIN_DONE_FOR_ESTIMATE = 3
RECENT = 12  # how many of the latest folders the remaining time is averaged over


def format_duration(seconds: float) -> str:
    """'35 s', '4 min', '1 h 41 min' (rounded: nobody needs the seconds of an hour)."""
    seconds = max(0.0, float(seconds))
    if seconds < 60:
        return f"{int(round(seconds))} s"
    minutes = int(round(seconds / 60))
    if minutes < 60:
        return f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes:02d} min" if minutes else f"{hours} h"


def clock(seconds: float) -> str:
    """H:MM:SS."""
    total = int(round(max(0.0, float(seconds))))
    return f"{total // 3600}:{total % 3600 // 60:02d}:{total % 60:02d}"


def progress_suffix(done: int, total: int, elapsed: float, durations: Iterable[float]) -> str:
    """The timing words for the progress label, or '' while it would be a silly guess (fewer than 3 folders done, or
    nothing left). The time left is the average of the RECENT folders times the folders left: a folder answered from the
    cache is fast and counts as fast, so a resumed run's estimate falls as it goes."""
    recent = [d for d in list(durations)[-RECENT:] if d >= 0]
    left = total - done
    if done < MIN_DONE_FOR_ESTIMATE or left <= 0 or not recent:
        return ""
    remaining = left * (sum(recent) / len(recent))
    if remaining < 15:
        return f", {format_duration(elapsed)} elapsed, almost done"
    return f", {format_duration(elapsed)} elapsed, about {format_duration(remaining)} left"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def lookup_sentence(folders: int, elapsed: float, requests: int, from_cache: int) -> str:
    return (f"Looked up {_plural(folders, 'folder')} in {clock(elapsed)} "
            f"({_plural(requests, 'request')}, {from_cache} from the cache).")


def estimate(artists: int, albums: int, genres: int, kinds: str) -> float:
    """Seconds for `kinds` ('artist' / 'both' / 'all') over that many folders to look up."""
    seconds = artists * ARTIST_SECONDS
    if kinds in ("both", "all"):
        seconds += albums * ALBUM_SECONDS
    if kinds == "all":
        seconds += genres * GENRE_SECONDS
    return seconds


def scope_hint(artists: int, albums: int, genres: int, kinds: str) -> str:
    """The scope dialog's one-line warning, or '' when the choice is small or already the fast one. Only a hint."""
    folders = artists + (albums if kinds in ("both", "all") else 0) + (genres if kinds == "all" else 0)
    if folders <= HINT_MIN_FOLDERS or kinds == "artist" or albums == 0:  # no album notes to warn about
        return ""
    slow, fast = estimate(artists, albums, genres, kinds), estimate(artists, albums, genres, "artist")
    text = f"About {folders} folders: roughly {format_duration(slow)} with album notes"
    if fast > 0 and slow / fast >= 1.5:
        text += f"; artist notes only is about {slow / fast:.0f}x faster"
    return text + " (a rough guess: a folder that is not found takes longer)."
