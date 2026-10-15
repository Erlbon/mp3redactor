"""
core/notes_level.py

Is a folder that has album folders below it really an ARTIST folder? Depth alone is not enough: a "Soundtracks"
or "Games" folder holds Blade Runner (1982)/, Pulp Fiction/ ... under it and has no artist at all, a classical
library is Composer/Work (Performer)/ or Performer/Composer - Work/. The decision is made from the albums'
tags and the folder's name, never from the name alone. Pure functions (no network, no files): the engine
(core/notes_flow.py) reads the tags and asks here.

THE ARTIST TEST (what `artist_level` computes), over the folder's album folders:

  1. A folder with a collection name (Various Artists, VA, Compilations, Samplers, Soundtracks, OST, Games...)
     is a COLLECTION: about.md, never artist.md.
  2. Albums tagged album-artist "Various Artists" are set aside; if nothing is left the folder is a COLLECTION.
  3. Each remaining album has one artist KEY: its album-artist tag (else its most common artist tag), folded
     (case, accents, punctuation, '&' = 'and'), a leading 'The ' dropped, a 'feat. ...' part dropped.
  4. The folder name gets the same key. An album AGREES when its key equals the folder's key, or the first-named
     artist of one equals the other's ('A & B' / 'A feat. B': the collaboration folder keeps its full name).
  5. All albums agree  -> ARTIST. At least 70% agree -> ARTIST (the odd one is named in the reasons).
     Albums with no artist tag at all -> ARTIST (nothing contradicts it; the discography match decides later).
  6. Some agree, fewer than 70%  -> UNCLEAR.
  7. None agrees:
       - the albums share ONE artist that is not the folder name -> UNCLEAR (the folder is named after something
         else: a series, a label, a composer);
       - the albums' composer tags all equal the folder name while the artists differ -> UNCLEAR (classical:
         composer or performer?);
       - the albums' artists differ from each other (the Soundtracks case) -> COLLECTION.

UNCLEAR and COLLECTION both come with `suggested` levels for the review page: artist / genre-collection / skip.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from core.notes_local import LocalAlbum, VARIOUS_ARTIST_NAMES, fold_name, is_collection_name

LEVEL_ARTIST = "artist"
LEVEL_COLLECTION = "collection"
LEVEL_UNCLEAR = "unclear"
LEVEL_ALBUM = "album"
LEVEL_GENRE = "genre"

SUGGEST_ARTIST = "artist"
SUGGEST_ALBUM = "album"
SUGGEST_COLLECTION = "genre-collection"
SUGGEST_SKIP = "skip"

_FEAT = re.compile(r"\s+(?:feat\.?|ft\.?|featuring|with)\s+.*$", re.IGNORECASE)
_SPLIT = re.compile(r"\s+(?:&|and|feat\.?|ft\.?|featuring|with|x|vs\.?|/|\+)\s+|\s*,\s*", re.IGNORECASE)
_THE = re.compile(r"^(the)\s+")


@dataclass
class LevelDecision:
    level: str
    reasons: list[str] = field(default_factory=list)
    suggested: list[str] = field(default_factory=list)  # levels the review page may offer, best first

    @property
    def clear(self) -> bool:
        return self.level != LEVEL_UNCLEAR


def split_collab(name: str) -> list[str]:
    """['A', 'B'] from 'A & B' / 'A feat. B' / 'A, B' (the first-named artist first)."""
    return [p.strip() for p in _SPLIT.split(name or "") if p.strip()]


def artist_key(name: str) -> str:
    """The comparable form of an artist name (see the module text, step 3)."""
    return _THE.sub("", fold_name(_FEAT.sub("", name or "")))


def _first_key(name: str) -> str:
    parts = split_collab(name)
    return artist_key(parts[0]) if parts else ""


def names_agree(a: str, b: str) -> bool:
    ka, kb = artist_key(a), artist_key(b)
    if not ka or not kb:
        return False
    return ka == kb or _first_key(a) == kb or ka == _first_key(b) or (_first_key(a) == _first_key(b) and bool(_first_key(a)))


def is_various(album: LocalAlbum) -> bool:
    return fold_name(album.album_artist) in VARIOUS_ARTIST_NAMES


def album_artist_name(album: LocalAlbum) -> str:
    return album.album_artist or (album.track_artists[0] if album.track_artists else "")


def artist_level(folder_name: str, albums: list[LocalAlbum]) -> LevelDecision:
    """ARTIST, COLLECTION or UNCLEAR for a folder with these album folders (the module text lists the steps)."""
    if is_collection_name(folder_name):
        return LevelDecision(LEVEL_COLLECTION, [f"'{folder_name}' is a collection name, not an artist"],
                             [SUGGEST_COLLECTION, SUGGEST_SKIP])
    considered = [a for a in albums if not is_various(a)]
    various = len(albums) - len(considered)
    if albums and not considered:
        return LevelDecision(LEVEL_COLLECTION, ["every album is by 'Various Artists'"], [SUGGEST_COLLECTION, SUGGEST_SKIP])
    tagged = [(a, album_artist_name(a)) for a in considered if album_artist_name(a)]
    if not tagged:
        return LevelDecision(LEVEL_ARTIST, ["the albums have no artist tags: the discography match decides"],
                             [SUGGEST_ARTIST, SUGGEST_COLLECTION, SUGGEST_SKIP])
    agreeing = [(a, n) for a, n in tagged if names_agree(folder_name, n)]
    share = len(agreeing) / len(tagged)
    keys = {artist_key(n) for _a, n in tagged}
    note = f" ({various} 'Various Artists' album(s) set aside)" if various else ""
    if share >= 0.7:
        odd = [f"{a.folder_name}: {n}" for a, n in tagged if (a, n) not in agreeing]
        reasons = [f"{len(agreeing)} of {len(tagged)} albums are tagged '{folder_name}'{note}"]
        if odd:
            reasons.append("tagged otherwise: " + "; ".join(odd))
        return LevelDecision(LEVEL_ARTIST, reasons, [SUGGEST_ARTIST, SUGGEST_COLLECTION, SUGGEST_SKIP])
    if agreeing:
        return LevelDecision(
            LEVEL_UNCLEAR, [f"level unclear: only {len(agreeing)} of {len(tagged)} albums are tagged '{folder_name}'"],
            [SUGGEST_ARTIST, SUGGEST_COLLECTION, SUGGEST_SKIP])
    composers = {artist_key(c) for a in considered for c in a.composers[:1] if c}
    if composers == {artist_key(folder_name)} and len(keys) > 1:
        return LevelDecision(
            LEVEL_UNCLEAR, [f"level unclear: the folder is named like the composer, the albums are by different "
                            f"performers ({', '.join(sorted({n for _a, n in tagged})[:4])})"],
            [SUGGEST_ARTIST, SUGGEST_COLLECTION, SUGGEST_SKIP])
    if len(keys) == 1:
        only = tagged[0][1]
        return LevelDecision(
            LEVEL_UNCLEAR, [f"level unclear: the albums are all by '{only}', not '{folder_name}'"],
            [SUGGEST_COLLECTION, SUGGEST_ARTIST, SUGGEST_SKIP])
    names = sorted({n for _a, n in tagged})
    return LevelDecision(
        LEVEL_COLLECTION, [f"the albums are by different artists ({', '.join(names[:4])}"
                           + (", ..." if len(names) > 4 else "") + f"), none named '{folder_name}'"],
        [SUGGEST_COLLECTION, SUGGEST_ARTIST, SUGGEST_SKIP])


_OST_TITLE = re.compile(
    r"\b(soundtrack|ost|original (?:motion picture|film|game|tv|television|series|cast|score|video game)\b.*|"
    r"(?:film|movie|game|tv) score|music from)\b",
    re.IGNORECASE,
)
_OST_STRIP = re.compile(
    r"\s*[-:(\[]?\s*\b(?:original\s+(?:motion\s+picture|film|game|tv|television|series|video\s+game)?\s*"
    r"(?:soundtrack|score|recording)|soundtrack|ost|music\s+from\s+the\s+(?:motion\s+picture|film|series))\b[\])]?\s*$",
    re.IGNORECASE,
)


def is_soundtrack_like(title: str, genres: list[str], parent_name: str = "") -> bool:
    """A folder that looks like a film / game / series soundtrack: a title that says so, a Genre tag of
    'Soundtrack', or a parent folder named like a soundtrack collection."""
    if _OST_TITLE.search(title or ""):
        return True
    if any("soundtrack" in fold_name(g) or fold_name(g) in ("ost", "score") for g in genres):
        return True
    return "soundtrack" in fold_name(parent_name) or fold_name(parent_name) in ("ost", "osts", "film music", "game music")


def work_title(title: str) -> str:
    """The film / game / series an album title names: 'Blade Runner (Original Motion Picture Soundtrack)' ->
    'Blade Runner'."""
    stripped = _OST_STRIP.sub("", title or "").strip(" -:([")
    return stripped or (title or "").strip()
