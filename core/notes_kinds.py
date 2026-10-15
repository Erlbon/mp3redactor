"""
core/notes_kinds.py -- 'What to create' for Create Notes: which kinds of folder get a note (Qt-free, no imports).

  parse_kinds("artist,genre")   -> ('artist', 'genre')      the kinds as the scan wants them, in a fixed order
  kinds_text(('artist',))       -> 'artist'                 the text that is remembered (settings, dialog, command line)

A kind is 'artist', 'album' or 'genre' (a genre or collection folder: its note is about.md). Older words still work:
'both' = artist + album, 'all' = all three, 'collection' = genre. An empty or unreadable text means artist notes only.
"""

from __future__ import annotations

ARTIST, ALBUM, GENRE = "artist", "album", "genre"
ORDER = (ARTIST, ALBUM, GENRE)
_WORDS = {"both": (ARTIST, ALBUM), "all": ORDER, "collection": (GENRE,), "genres": (GENRE,), "artists": (ARTIST,),
          "albums": (ALBUM,)}


def parse_kinds(text) -> tuple[str, ...]:
    """The kinds named in `text` (a comma list, or a tuple / list of words), in the order artist, album, genre."""
    if isinstance(text, (tuple, list, set, frozenset)):
        words = [str(w) for w in text]
    else:
        words = str(text or "").replace(";", ",").replace("+", ",").split(",")
    found: set[str] = set()
    for word in (w.strip().lower() for w in words):
        if word in ORDER:
            found.add(word)
        elif word in _WORDS:
            found.update(_WORDS[word])
    return tuple(k for k in ORDER if k in found) or (ARTIST,)


def kinds_text(kinds) -> str:
    return ",".join(parse_kinds(kinds))
