"""
core/notes_match.py

The judgement part of the folder notes (core/notes_flow.py): does this MusicBrainz / Discogs candidate really
describe the folder? Pure functions, no network, no files.

The two failures that shaped this (seen in the 2026-10-15 spike):
  - searching the artist "Cristina" returned Cristina D'Avena (an Italian children's-TV singer) while the library's
    Cristina is Cristina Monet Palaci. A name match is NOT a match: a candidate artist is scored by how many of
    the LOCAL album titles appear in ITS discography.
  - searching the album "Vienna" by Ultravox returned the SINGLE release group first: the album search asks for
    primary type Album, and a Single can never win over an Album of the same title.

Confidence: 'confident' = verified by at least one local album AND a clear winner; 'uncertain' = plausible but
ambiguous or unverified (the candidates are kept for a person to choose); 'none' = nothing found.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from enum import Enum  # noqa: F401
from typing import Iterable, Optional

from core.notes_local import fold_name
from core.notes_numbers import compare_key
from redactor_common.core.notes_model import Confidence  # noqa: E402,F401 -- the one definition, shared



# ---------------------------------------------------------------------------
# Names and titles
# ---------------------------------------------------------------------------

_EDITION_WORDS = (
    r"deluxe|edition|remaster(?:ed)?|expanded|anniversary|bonus|reissue|re-issue|mono|stereo|version|special|"
    r"collector'?s?|digipak|limited|explicit|clean|original|\d{4}"
)
_EDITION_GROUP = re.compile(rf"\s*[\(\[][^)\]]*\b(?:{_EDITION_WORDS})\b[^)\]]*[\)\]]", re.IGNORECASE)
_EDITION_TAIL = re.compile(rf"\s*[-:]\s*(?:[\w' ]*\b(?:deluxe|remaster(?:ed)?|expanded|anniversary)\b[\w' ]*)$", re.IGNORECASE)
_LEADING_THE = re.compile(r"^(the|a|an|der|die|das|le|la|les|el|los)\s+")


def album_key(title: str) -> str:
    """An album title as it is compared: ' (1980)', '(Deluxe Edition)', '[Remastered]' and a trailing
    '- Remastered 2011' dropped, accents/case/punctuation folded, a leading 'The ' dropped."""
    text = _EDITION_GROUP.sub("", title or "")
    text = _EDITION_TAIL.sub("", text)
    return _LEADING_THE.sub("", compare_key(text))


def titles_match(a: str, b: str, threshold: float = 0.93) -> bool:
    ka, kb = album_key(a), album_key(b)
    if not ka or not kb:
        return False
    return ka == kb or difflib.SequenceMatcher(None, ka, kb).ratio() >= threshold


def _name_forms(name: str) -> set[str]:
    """'Icicle Works, The' also as 'the icicle works'; leading 'The ' optional."""
    forms = set()
    text = (name or "").strip()
    match = re.match(r"^(.*),\s*(the|a|an)$", text, re.IGNORECASE)
    if match:
        text = f"{match.group(2)} {match.group(1)}"
    folded = compare_key(text)
    if folded:
        forms.add(folded)
        forms.add(_LEADING_THE.sub("", folded))
    return forms


def name_similarity(local_names: Iterable[str], candidate_names: Iterable[str]) -> float:
    """0..1. 1.0 = the same name (ignoring 'The ', case, accents, punctuation); 0.5 = one name only extends the
    other ('Cristina' / "Cristina D'Avena"); otherwise the closeness of the spellings, capped at 0.9."""
    best = 0.0
    for local in local_names:
        for candidate in candidate_names:
            la, ca = _name_forms(local), _name_forms(candidate)
            if la & ca:
                return 1.0
            for x in la:
                for y in ca:
                    if x.startswith(y + " ") or y.startswith(x + " "):
                        best = max(best, 0.5)
                    else:
                        best = max(best, min(0.9, difflib.SequenceMatcher(None, x, y).ratio()))
    return best


def _year(text: str) -> Optional[int]:
    match = re.match(r"\s*(\d{4})", text or "")
    return int(match.group(1)) if match else None


# ---------------------------------------------------------------------------
# Artists
# ---------------------------------------------------------------------------

@dataclass
class Evaluation:
    """One candidate artist checked against the folder."""

    source: str  # "musicbrainz" / "discogs"
    id: str
    name: str
    hits: list[str] = field(default_factory=list)  # local album titles found in the candidate's discography
    name_sim: float = 0.0
    search_score: float = 0.0  # the source's own relevance, 0..100
    era_conflict: str = ""  # '' or why the dates cannot fit
    detail: str = ""  # disambiguation / country, for the review page

    @property
    def key(self) -> tuple:
        return (len(self.hits), self.name_sim, not self.era_conflict, self.search_score)


def discography_hits(local_albums: Iterable[Iterable[str]], discography_titles: Iterable[str]) -> list[str]:
    """For each local album (given as the list of names it goes by) the first of those names that matches a
    title in `discography_titles`, in folder order; an album counts once."""
    pool = list(discography_titles)
    hits = []
    for names in local_albums:
        names = [n for n in names if n]
        if any(titles_match(n, title) for n in names for title in pool):
            hits.append(names[0])
    return hits


def era_conflict(begin: str, end: str, local_years: Iterable[str], slack: int = 3) -> str:
    """A reason when a local album's year lies before the artist began (more than `slack` years) -- the sign of
    a namesake. Ending is never held against an artist (reunions, reissues, posthumous releases)."""
    start = _year(begin)
    if start is None:
        return ""
    for text in local_years:
        year = _year(text)
        if year is not None and year < start - slack:
            return f"a local album from {year} is older than the artist's start ({start})"
    return ""


def decide_artist(evaluations: list[Evaluation]) -> tuple[Optional[Evaluation], Confidence, list[str]]:
    """(winner, confidence, reasons) over one source's evaluated candidates."""
    if not evaluations:
        return None, Confidence.NONE, ["no candidate was found"]
    ranked = sorted(evaluations, key=lambda e: e.key, reverse=True)
    top = ranked[0]
    second = ranked[1] if len(ranked) > 1 else None
    reasons: list[str] = []
    if not top.hits:
        return top, Confidence.UNCERTAIN, [
            f"none of the local albums was found in the discography of any of the {len(ranked)} candidate(s)"]
    reasons.append(f"{len(top.hits)} local album(s) found in its discography: {', '.join(top.hits)}")
    if top.era_conflict:
        return top, Confidence.UNCERTAIN, reasons + [top.era_conflict]
    if top.name_sim < 0.6:
        return top, Confidence.UNCERTAIN, reasons + [f"the name '{top.name}' is only a loose match"]
    if second is not None and second.hits:
        if len(second.hits) == len(top.hits) and top.name_sim - second.name_sim < 0.2:
            return top, Confidence.UNCERTAIN, reasons + [
                f"'{second.name}' ({second.detail}) fits the folder equally well"]
    return top, Confidence.CONFIDENT, reasons


# ---------------------------------------------------------------------------
# Albums
# ---------------------------------------------------------------------------

def type_rank(primary: str, secondary: Iterable[str] = ()) -> float:
    """Which release groups are an album: a studio Album 1.0, an EP .7, a compilation/live album .5, a Single
    .1 (never the answer for an album folder while an Album of that title exists)."""
    primary = (primary or "").casefold()
    secondary = [s.casefold() for s in secondary if s.casefold() != "soundtrack"]  # a soundtrack is an album
    if primary == "single":
        return 0.1
    if primary == "ep":
        return 0.7
    if primary == "album":
        return 0.5 if secondary else 1.0
    return 0.3


def year_closeness(local_year: str, found_year: str) -> Optional[float]:
    a, b = _year(local_year), _year(found_year)
    if a is None or b is None:
        return None
    gap = abs(a - b)
    return 1.0 if gap == 0 else 0.9 if gap == 1 else 0.6 if gap <= 3 else 0.2


@dataclass
class GroupScore:
    group_id: str
    title: str
    score: float
    title_match: bool
    reasons: list[str] = field(default_factory=list)


def score_release_group(
    local_titles: Iterable[str], local_year: str, local_artists: Iterable[str], title: str, found_year: str,
    primary: str, secondary: Iterable[str], found_artist: str, group_id: str = "",
) -> GroupScore:
    """How well one release group fits an album folder, 0..1: the title (.5), the type (.2: Album over EP over
    Single), the year (.15), the artist (.15). A part that is unknown on either side is left out."""
    local_titles = [t for t in local_titles if t]
    title_ok = any(titles_match(t, title) for t in local_titles)
    parts = [(1.0 if title_ok else max((difflib.SequenceMatcher(None, album_key(t), album_key(title)).ratio()
                                       for t in local_titles), default=0.0) * 0.8, 0.5),
             (type_rank(primary, secondary), 0.2)]
    reasons = []
    closeness = year_closeness(local_year, found_year)
    if closeness is not None:
        parts.append((closeness, 0.15))
        if closeness >= 0.9:
            reasons.append(f"year {found_year}")
    names = [n for n in local_artists if n]
    if names and found_artist:
        parts.append((name_similarity(names, [found_artist]), 0.15))
    total = sum(s * w for s, w in parts) / sum(w for _s, w in parts)
    if primary:
        reasons.append(f"primary type {primary}" + (f" ({', '.join(secondary)})" if secondary else ""))
    return GroupScore(group_id, title, total, title_ok, reasons)


def track_overlap(local_titles: Iterable[str], release_titles: Iterable[str]) -> float:
    """The share (0..1) of the local track titles found on the release; 1.0 when there are no local titles."""
    local = [t for t in local_titles if t and t.strip()]
    pool = [compare_key(t) for t in release_titles]
    if not local:
        return 1.0
    found = 0
    for title in local:
        key = compare_key(re.sub(r"\s*[\(\[][^)\]]*[\)\]]\s*$", "", title)) or compare_key(title)
        if any(key == p or key == compare_key(re.sub(r"\s*[\(\[][^)\]]*[\)\]]\s*$", "", t))
               or difflib.SequenceMatcher(None, key, p).ratio() >= 0.88 for p, t in zip(pool, release_titles)):
            found += 1
    return found / len(local)
