"""
core/notes_flow.py

The engine behind "create notes": Markdown folder notes (artist.md in an artist folder, album.md in an album
folder -- the contract is Pluteus's docs/notes-format.md and docs/library-layout.md) drafted from what the folder
holds and what MusicBrainz, Wikipedia and (with the user's token) Discogs say. Qt-free; the command line
(mp3cli/cmd_notes.py) and, later, the review dialog are thin layers over it.

  scan(paths, include_subfolders, kinds)        core.notes_local: the folders that should get a note
  build_proposals(targets, fetchers, ...)       one Proposal per target (network); never writes
  write_proposal(proposal, dry_run=...)         redactor_common's write_note: never overwrites, status: draft
  summarize(proposals, results)                 counts for a report
  undo_note_entry(entry)                        the opt-in journal's undo: Recycle Bin, only if unchanged

Judgement (core/notes_match.py): a candidate is only believed when the LOCAL album titles appear in ITS
discography. A Proposal is 'confident' (verified by at least one local album and a clear winner: the only kind
a batch run writes), 'uncertain' (plausible but ambiguous or unverified: the candidate list is kept for a person
to choose) or 'none' (nothing found). Every network function takes an injectable `fetch`, so the tests run on
recorded fixtures.
"""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

from core import notes_discogs as nd
from core.notes_link import LinkError, LinkTarget, parse_link
from core.notes_cache import CachedDiscogs, CachedFetch, RequestStats, ResponseCache
from core.notes_phases import TAGS, PhaseTimer
from core import notes_musicbrainz as nmb
from core import wikipedia_lookup as wp
from core.discogs_lookup import DiscogsClient, DiscogsError, load_token
from core.notes_level import (
    LEVEL_ALBUM, LEVEL_ARTIST, LEVEL_COLLECTION, LEVEL_GENRE, LEVEL_UNCLEAR, SUGGEST_ALBUM, SUGGEST_ARTIST,
    SUGGEST_COLLECTION, SUGGEST_SKIP, LevelDecision, artist_key, artist_level, is_soundtrack_like, is_various,
    names_agree, split_collab, work_title,
)
from core.notes_local import (
    ALL_KINDS, KIND_ALBUM, KIND_ARTIST, KIND_GENRE, KINDS, LocalAlbum, LocalGenre, read_genre, LocalArtist, ScanResult, Skipped, Target, album_children, classify,
    album_folder_of, fold_name, is_collection_name, lookup_name, is_generic_name, read_album, read_artist, scan, sidecar_for, split_year, Loader,
)
from core.notes_match import (
    Confidence, Evaluation, album_key, decide_artist, discography_hits, era_conflict, name_similarity,
    is_generic_album_title, score_release_group, titles_match, track_overlap,
)
from core.notes_render import (
    H_OTHER, LOCAL_ONLY_CREDIT, TrackInfo, discogs_facts_credit, group_discogs_discography, group_discography,
    render_album_note, render_artist_note, render_genre_note, wikipedia_credits,
)
from redactor_common.core import note_text
from redactor_common.core.notes_model import KIND_NOTE, STATUS_SKIPPED, Candidate, NotesSummary  # noqa: F401 -- the shared model
from redactor_common.core.notes_model import Proposal as _Proposal
from redactor_common.core.notes_model import summarize as _summarize
from redactor_common.core.notes_model import (
    record_note_entry, text_sha, undo_note_entry as _undo_note_entry, write_proposal as _write_proposal,
)
from redactor_common.core.notes import (
    STATUS_ERROR, STATUS_WRITTEN, NoteState, NoteWriteResult, normalize_note_text, note_path, replace_note, write_note,
)

__all__ = [
    "selection_scope", "artist_folder_of_file", "artist_folder_above",
    "ALL_KINDS", "Confidence", "Candidate", "Fetchers", "KIND_ALBUM", "KIND_ARTIST", "KIND_GENRE", "KINDS", "NotesEngine", "NotesSummary",
    "Proposal", "ScanResult", "Skipped", "Target", "build_proposals", "live_fetchers", "scan", "summarize",
    "undo_note_entry", "write_proposal", "STATUS_SKIPPED",
]

LEVEL_LOCAL_TAGS = "local-tags"  # rebuild(level=...): the explicit minimal note from the tags (never automatic)
DISCOGS_MISSING = "Discogs token not set: add it in Tools > API Keys... to build from Discogs candidates"
WIKIPEDIA_OFF = "Wikipedia lookups are switched off for this run"
MAX_CANDIDATES = 4
MAX_MEMBERS = 8

Progress = Callable[[int, int, str], None]


# ---------------------------------------------------------------------------
# What the engine is given and what it returns
# ---------------------------------------------------------------------------

@dataclass
class Fetchers:
    """The network, injectable. `mb` and `wiki` are `fetch(url) -> bytes` callables (None: the real, throttled
    one of this app); `discogs` is a DiscogsClient, or None for no Discogs (no token, --no-discogs)."""

    mb: Optional[Callable] = None
    wiki: Optional[Callable] = None
    discogs: Optional[DiscogsClient] = None
    wikipedia: bool = True


def live_fetchers(use_discogs: bool = True) -> Fetchers:
    """The real services. Discogs only when asked for AND a token is stored / in DISCOGS_TOKEN: without one the
    notes are simply thinner, never an error."""
    token = load_token() if use_discogs else ""
    return Fetchers(discogs=DiscogsClient(token) if token else None)


@dataclass
class Proposal(_Proposal):
    """The shared draft of one note (redactor_common.core.notes_model) plus what only a music folder has."""

    local: Optional[LocalArtist | LocalAlbum | LocalGenre] = None
    # The LEVEL decision (core/notes_level.py): 'artist', 'album', 'genre', 'collection' (an about.md folder found
    # from the tags: Soundtracks, Various Artists...) or 'unclear'. `suggested_levels` is what a review page may offer.
    level: str = ""
    level_reasons: list[str] = field(default_factory=list)
    suggested_levels: list[str] = field(default_factory=list)
    genres: list[str] = field(default_factory=list)  # a genre / collection note: the genres the folder covers, as looked up

    def to_dict(self, with_text: bool = True) -> dict:
        out = super().to_dict(with_text)
        out.update({"level": self.level, "level_reasons": list(self.level_reasons),
                    "suggested_levels": list(self.suggested_levels)})
        if self.genres:
            out["genres"] = list(self.genres)
        return out


def summarize(
    proposals: list[Proposal], results: Optional[dict[str, NoteWriteResult]] = None, cancelled: bool = False,
) -> NotesSummary:
    """Counts for a report (the shared summarize)."""
    return _summarize(proposals, results, cancelled)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _unique(items: Iterable[str]) -> list[str]:
    out: list[str] = []
    for item in items:
        if item and fold_name(item) not in [fold_name(o) for o in out]:
            out.append(item)
    return out


def _is_network_down(message: str) -> bool:
    return message.startswith("Could not reach")


def _years_text(begin: str, end: str, ended: bool) -> str:
    start, stop = begin[:4], end[:4]
    if not start.isdigit():
        return ""
    return f"{start}-{stop}" if ended and stop.isdigit() else f"{start}-"


def _prose(markdown_body: str) -> str:
    """The running text of a converted sidecar body: no heading, no '**Label:** value' lines, no lists."""
    paragraphs = []
    for block in re.split(r"\n{2,}", markdown_body):
        block = block.strip()
        if block and not block.startswith(("#", "**", "- ", "1.")):
            paragraphs.append(block)
    return "\n\n".join(paragraphs)


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _cap_text(text: str, limit: int) -> str:
    """`text` (paragraphs kept) cut at a sentence end within `limit` characters."""
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    ends = [m.end() for m in re.finditer(r"[.!?](?=\s|$)", cut)]
    return cut[: ends[-1]].rstrip() if ends else cut.rsplit(" ", 1)[0].rstrip() + "..."


def _tracks_from_mb(detail: nmb.ReleaseDetail) -> list[TrackInfo]:
    return [TrackInfo(t.disc, t.position, t.title, t.seconds) for t in detail.tracks]


@dataclass
class GuessCandidate(Candidate):
    """A candidate the ENGINE picked as its best guess (NotesEngine.best_guess). rebuild() builds around it like around any
    candidate but keeps every safety check, and its reasons say 'best guess' instead of 'chosen by you'."""


def _chosen(candidate: "Candidate") -> str:
    if isinstance(candidate, GuessCandidate):
        return "best guess"
    return "chosen by you from a link" if candidate.note.startswith("from a link") else "chosen by you"


def _can_stop_asking(sim: float, wanted: int, found: list, era: str, later_sims: list[float]) -> bool:
    """Stop evaluating candidates once an exact-name one holds EVERY local album, except when that is a single album and
    another candidate with the same exact name is still to come: two bands called 'Mirage' may each have a 'Mirage' album
    (and 'Greatest Hits', 'Live' and 'Demo' are on any discography), so decide_artist must see the namesake and say so."""
    if not (sim == 1.0 and wanted and len(found) == wanted and not era):
        return False
    return wanted >= 2 or not any(later == 1.0 for later in later_sims)


@dataclass
class _ArtistFound:
    """A verified candidate artist of one source and what was fetched to verify it."""

    evaluation: Evaluation
    confidence: Confidence
    hit: Optional[nmb.ArtistHit] = None
    groups: list[nmb.ReleaseGroup] = field(default_factory=list)
    discogs_hit: Optional[nd.DiscogsArtistHit] = None


@dataclass
class _Resolution:
    mb: Optional[_ArtistFound] = None
    discogs: Optional[_ArtistFound] = None
    candidates: list[Candidate] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    via: str = ""  # 'wikipedia': found through the article's Wikidata item (the name searches found nothing verified)
    page: Optional[object] = None  # the Wikipedia article that led to it (its introduction is used, no second search)

    @property
    def confidence(self) -> Confidence:
        found = [f for f in (self.mb, self.discogs) if f is not None]
        if any(f.confidence == Confidence.CONFIDENT for f in found):
            return Confidence.CONFIDENT
        return Confidence.UNCERTAIN if self.candidates else Confidence.NONE

    @property
    def mb_ok(self) -> Optional[_ArtistFound]:
        return self.mb if self.mb and self.mb.confidence == Confidence.CONFIDENT else None

    @property
    def discogs_ok(self) -> Optional[_ArtistFound]:
        return self.discogs if self.discogs and self.discogs.confidence == Confidence.CONFIDENT else None


# ---------------------------------------------------------------------------
# The engine
# ---------------------------------------------------------------------------

class NotesEngine:
    """Builds proposals. One engine per run: it caches what it fetched (an artist's discography is fetched once
    however many of that artist's folders are asked about)."""

    def __init__(self, fetchers: Optional[Fetchers] = None, loader: Optional[Loader] = None,
                 cache: Optional[ResponseCache] = None, discogs_mode: str = "fallback", singles: bool = True):
        self.fetchers = fetchers or Fetchers()
        self.loader = loader
        # The request layer (core/notes_cache.py): a per-engine memo (the same URL is never asked twice in a run) and,
        # when a ResponseCache is given, the persistent cache (a repeated or resumed run is answered from disk).
        self.stats = RequestStats()
        self.phases = PhaseTimer()  # where the time goes (core/notes_phases.py)
        self.on_status: Optional[Callable[[str], None]] = None  # told 'MusicBrainz is busy, waiting 4 s' and the like
        self._mb = CachedFetch(self.fetchers.mb or nmb._fetch(), "mb", cache, self.stats, self.phases, "musicbrainz")
        self._wiki = CachedFetch(self.fetchers.wiki or wp.default_fetch(), "wiki", cache, self.stats, self.phases, "wikipedia")
        self._discogs = (CachedDiscogs(self.fetchers.discogs, cache, self.stats, self.phases)
                         if self.fetchers.discogs else None)
        self._mb.busy_hook = self._busy
        self.loader = self._timed(self.loader)
        # When Discogs is asked (core/notes_flow.py, resolve_artist): "fallback" (the default) only when MusicBrainz has no
        # verified match for the artist (or the album), "always" for every artist (alongside MusicBrainz, in a second
        # thread), "never" not at all. `singles` False leaves the singles out of the artist note's discography: the
        # release groups are then browsed with the type filter album|ep (about a third fewer requests for a big artist).
        self.discogs_mode = discogs_mode if discogs_mode in ("always", "fallback", "never") else "fallback"
        self.singles = singles
        if self.discogs_mode == "never":
            self._discogs = None
        self._pool: Optional[ThreadPoolExecutor] = None
        self._genre_overrides: dict[str, list[str]] = {}  # set_genres(): the genres a folder covers
        self._links: dict[str, str] = {}  # set_links(): a link given for a folder
        self._artists: dict[tuple, _Resolution] = {}
        self._levels: dict[str, tuple[LocalArtist, LevelDecision]] = {}
        self._groups: dict[str, list[nmb.ReleaseGroup]] = {}
        self._all_groups: dict[str, list[nmb.ReleaseGroup]] = {}
        self._network_failures = 0
        self._seen_groups: dict[str, nmb.ReleaseGroup] = {}  # release groups offered as candidates, by id
        self._link_hits: dict[str, tuple] = {}  # artist id -> (ArtistHit, Wikipedia page or None) of a pasted link
        self._d_entries: dict[int, list[dict]] = {}  # Discogs artist release lists, by artist id

    def _both(self, first: Callable, second: Callable):
        """(first(), second()) with `second` on the one helper thread while `first` runs on the calling thread: two
        independent services (MusicBrainz and Discogs, each with its own rate limit) at the same time. At most two
        threads; the results come back in this fixed order whatever finishes first; neither function touches a widget."""
        if self._pool is None:
            self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="notes-lookup")
        future = self._pool.submit(second)
        try:
            one = first()
        except BaseException:
            if not future.cancel():  # it is already running: let it finish, no work is left running behind us
                try:
                    future.result()
                except BaseException:  # noqa: BLE001 -- the first failure is the one that is raised
                    pass
            raise
        return one, future.result()

    def close(self) -> None:
        """Ends the helper thread (the engine is also fine to simply drop)."""
        if self._pool is not None:
            self._pool.shutdown(wait=True)
            self._pool = None

    def _busy(self, delay: float) -> None:
        if self.on_status is not None:
            try:
                self.on_status(f"MusicBrainz is busy, waiting {delay:g} s")
            except Exception:  # noqa: BLE001 -- a status line must never break a lookup
                pass

    def _timed(self, loader: Optional[Loader]) -> Loader:
        """The tag reader, with the files and seconds counted in the 'tags' phase."""
        from core.notes_local import default_loader

        inner = loader or default_loader

        def timed(paths):
            started = time.perf_counter()
            try:
                return inner(paths)
            finally:
                self.phases.add(TAGS, time.perf_counter() - started, count=len(paths))

        return timed

    # -- public ------------------------------------------------------------------------------------------

    def build_proposal(self, target: Target) -> Proposal:
        """One proposal; never raises for a service failure (the problem is in `problems`)."""
        if self._network_failures >= 2:
            return Proposal(target, Confidence.NONE, reasons=["skipped: the services could not be reached"],
                            problems=["could not reach MusicBrainz (repeated); the rest of the run was not looked up"])
        url = self._link_override(target.folder)
        if url:
            try:
                return self.rebuild(Proposal(target), link=url)
            except LinkError as exc:
                return Proposal(target, Confidence.NONE, reasons=[f"the link given for this folder cannot be used: {exc}"],
                                problems=[str(exc)])
        return self._dispatch(target)

    def set_links(self, folder: str, url: str) -> None:
        """A link (Wikipedia, MusicBrainz, Discogs) given for a folder: its FULL name or its full path (the command
        line's --link). The folder name must match completely after the usual folding (case, accents, punctuation): a
        short name no longer also matches 'Leningrad' inside '1999 - Leningrad' (the numbering-stripped name), and a
        name does not apply to a folder of the same name inside it (a self-titled album in its artist's folder): give
        the path to link that one."""
        parse_link(url)  # validated now: a bad link is an error at once, not a silent miss later
        for key in {fold_name(folder), os.path.normcase(os.path.abspath(folder))}:
            self._links[key] = url

    def _link_override(self, folder: Path) -> str:
        path_key = os.path.normcase(os.path.abspath(str(folder)))
        if path_key in self._links:
            return self._links[path_key]
        name_key = fold_name(folder.name)
        if name_key in self._links and fold_name(folder.parent.name) != name_key:
            return self._links[name_key]
        return ""

    def candidate_from_link(self, proposal: Proposal, link: str) -> Candidate:
        """The Candidate a pasted link names, for this proposal's kind (artist or album), looked up by id: the MusicBrainz /
        Discogs ids of a Wikipedia or Wikidata page come from Wikidata. Raises LinkError for a link that does not fit."""
        kind = proposal.target.kind
        target = parse_link(link, bare_as="release-group" if kind == KIND_ALBUM else "artist")
        names = [lookup_name(proposal.folder.name)]
        try:
            if kind == KIND_ARTIST:
                return self._artist_candidate_from_link(target, names, proposal)
            if kind == KIND_ALBUM:
                return self._album_candidate_from_link(target, proposal)
        except (nmb.MusicBrainzError, DiscogsError, wp.WikipediaError) as exc:
            raise LinkError(str(exc)) from exc
        raise LinkError("A link can be used for an artist or an album folder.")

    def _artist_candidate_from_link(self, target: LinkTarget, names: list[str], proposal: Proposal) -> Candidate:
        local = proposal.local if isinstance(proposal.local, LocalArtist) else self._artist_level(proposal.folder)[0]
        titles = [a.titles for a in local.albums]
        years = [a.year for a in local.albums]
        names = local.names or names
        mbid = discogs_id = ""
        origin = f"from a link: {target.raw or target.id}"
        if target.kind == "mb-artist":
            mbid = target.id
        elif target.kind == "mb-release-group":
            mbid, _group = nmb.release_group_artist(target.id, self._mb)
            if not mbid:
                raise LinkError("MusicBrainz names no artist for that release group.")
        elif target.kind == "discogs-artist":
            discogs_id = target.id
        elif target.kind in ("wikipedia", "wikidata"):
            ids = (wp.wikidata_identifiers(entity_id=target.id, fetch=self._wiki) if target.kind == "wikidata"
                   else wp.wikidata_identifiers(title=target.id, fetch=self._wiki, lang=target.lang or "en"))
            mbid, discogs_id = ids["musicbrainz"], ids["discogs"]
            if not mbid and not discogs_id:
                page = wp.get_summary(target.id, self._wiki, target.lang or "en") if target.kind == "wikipedia" else None
                if page is None:
                    raise LinkError("That page has no MusicBrainz or Discogs id on Wikidata and no Wikipedia article was found.")
                return Candidate("wikipedia", page.title, page.title, page.description or "Wikipedia article", [], 0.5,
                                 f"from a link: {target.raw} (a Wikipedia article only: it names no MusicBrainz or Discogs id)")
            origin = f"from a link: {target.raw} (Wikidata {ids['qid']})"
        else:
            raise LinkError("Use a link to an artist here (a MusicBrainz or Discogs artist page, a Wikipedia article, "
                            "a MusicBrainz release group of theirs).")
        if mbid:
            hit = nmb.artist_by_id(mbid, self._mb)
            evaluation, _groups = self._evaluate_mb(hit, names, [], titles, years)
            cand = Candidate("musicbrainz", hit.id, hit.name, evaluation.detail, list(evaluation.hits), 1.0, origin)
            page = None
            if target.kind == "wikipedia":
                try:
                    page = wp.get_summary(target.id, self._wiki, target.lang or "en")
                except wp.WikipediaError:
                    page = None
            self._link_hits[hit.id] = (hit, page)
            if discogs_id and discogs_id.isdigit() and self._discogs is not None:
                cand.also, cand.also_id = "discogs", discogs_id
            return cand
        if self._discogs is None:
            raise LinkError(DISCOGS_MISSING)
        info = nd.artist_info(self._discogs, int(discogs_id))
        return Candidate("discogs", discogs_id, info.name, f"Discogs {discogs_id}", [], 1.0, origin)

    def _album_candidate_from_link(self, target: LinkTarget, proposal: Proposal) -> Candidate:
        origin = f"from a link: {target.raw or target.id}"
        if target.kind == "mb-release-group":
            data = nmb.release_group_detail(target.id, self._mb)
            group = nmb._group({**data, "id": data.get("id") or target.id})
            self._seen_groups[group.id] = group
            return Candidate("musicbrainz", group.id, group.title, group.first_date[:4], [], 1.0, origin)
        if target.kind in ("discogs-release", "discogs-master"):
            if self._discogs is None:
                raise LinkError(DISCOGS_MISSING)
            ident = target.id if target.kind == "discogs-release" else nd.MASTER_PREFIX + target.id
            return Candidate("discogs", ident, f"Discogs {target.kind.split('-')[1]} {target.id}", "", [], 1.0, origin)
        if target.kind == "wikipedia":
            page = wp.get_summary(target.id, self._wiki, target.lang or "en")
            if page is None:
                raise LinkError("No Wikipedia article was found at that link.")
            return Candidate("wikipedia", page.title, page.title, page.description or "Wikipedia article", [], 0.5, origin)
        raise LinkError("Use a link to an album here (a MusicBrainz release group, a Discogs release or master, a Wikipedia article).")

    def can_choose(self, proposal: Proposal, candidate: Candidate) -> bool:
        """Can the proposal be rebuilt around this candidate? Artist and album notes can be built from a
        MusicBrainz, a Discogs (needs the token) or a Wikipedia candidate; genre / collection notes use none."""
        return not self.why_cannot_choose(proposal, candidate)

    def why_cannot_choose(self, proposal: Proposal, candidate: Candidate) -> str:
        """'' when the candidate can be used, else the plain reason (which source is missing)."""
        if proposal.kind not in (KIND_ARTIST, KIND_ALBUM):
            return "A genre or collection note is built from Wikipedia only; there is no candidate to choose."
        if candidate.source == "musicbrainz":
            return ""
        if candidate.source == "discogs":
            return "" if self.fetchers.discogs is not None else DISCOGS_MISSING  # (a chosen candidate is asked for, whatever the mode)
        if candidate.source == "wikipedia":
            return "" if self.fetchers.wikipedia else WIKIPEDIA_OFF
        return f"No note can be built from a {candidate.source} candidate."

    GUESS_MIN_ALBUM_SCORE = 0.75  # an album candidate below this fits too poorly to be a guess (the engine's own bar)

    def best_guess(self, proposal: Proposal) -> Optional[Candidate]:
        """The candidate to use for an UNCERTAIN proposal when the person wants the engine's best guess, or None. The guess is
        the engine's own top candidate and only an ACCEPTABLE one: an artist candidate with at least one of the local albums in
        its discography (ranked like the engine does, by hits then name), an album candidate whose title matches a local album
        and that scores at least GUESS_MIN_ALBUM_SCORE; never one that ties with a rival, never an era conflict. No guess for a
        confident or nothing-found proposal, a genre / collection folder, an unclear level. A guess keeps EVERY safety check of the
        engine (the track-overlap veto, the no-artist check): it is a GuessCandidate, rebuilt as 'best guess', not as 'chosen by
        you'. Only a candidate the person picks is believed past those checks. `why_no_guess` says why there is none."""
        return self._guess(proposal)[0]

    def why_no_guess(self, proposal: Proposal) -> str:
        """The plain reason `best_guess` has nothing for this proposal ('' when it has one)."""
        return self._guess(proposal)[1]

    def _guess(self, proposal: Proposal) -> tuple[Optional["GuessCandidate"], str]:
        if proposal.confidence != Confidence.UNCERTAIN or proposal.kind not in (KIND_ARTIST, KIND_ALBUM):
            return None, "only an uncertain artist or album match has a best guess"
        if proposal.kind == KIND_ARTIST and proposal.level != LEVEL_ARTIST:
            return None, "the level is unclear"
        if proposal.kind == KIND_ALBUM and proposal.level not in (LEVEL_ALBUM, ""):
            return None, "the level is unclear"
        usable = [c for c in proposal.candidates if self.can_choose(proposal, c)]
        if not usable:
            return None, "no usable candidate"
        if proposal.kind == KIND_ARTIST:
            ranked = sorted((c for c in usable if c.hits and not str(c.note).startswith("a local album from")),
                            key=lambda c: (len(c.hits), c.score), reverse=True)
            if not ranked:
                return None, "no candidate has any of the local albums in its discography"
            top = ranked[0]
            tie = next((c for c in ranked[1:] if (len(c.hits), c.score) == (len(top.hits), top.score)), None)
        else:
            local = proposal.local
            titles = list(getattr(local, "titles", []) or [])
            fits = []
            for c in usable:
                if c.score < self.GUESS_MIN_ALBUM_SCORE or c.source not in ("musicbrainz", "discogs"):
                    continue  # (a Wikipedia article scores 0.5: it names no release, so it is never a guess)
                if c.source == "musicbrainz":
                    group = self._seen_groups.get(c.id)
                    if group is None or not any(titles_match(t, group.title) for t in titles):
                        continue
                fits.append(c)
            if not fits:
                return None, f"no candidate has a title that matches and a score of at least {self.GUESS_MIN_ALBUM_SCORE:.2f}"
            fits.sort(key=lambda c: c.score, reverse=True)
            top = fits[0]
            tie = next((c for c in fits[1:] if abs(c.score - top.score) < 0.05), None)
        if tie is not None:
            return None, f"'{top.name}' and '{tie.name}' fit equally well"
        guess = GuessCandidate(**{f.name: getattr(top, f.name) for f in dataclasses.fields(top)})
        return guess, ""

    def set_genres(self, folder: str, genres: list[str]) -> None:
        """The genres a genre / collection folder covers, when they are not what its name says ('1.61 - Ska' covers ska and
        ska punk). `folder` is the raw or the cleaned name, or a path. Every genre is verified as a music genre like any."""
        wanted = [g.strip() for g in genres if g and g.strip()]
        for key in {fold_name(folder), fold_name(lookup_name(folder)), os.path.normcase(os.path.abspath(folder))}:
            self._genre_overrides[key] = wanted

    def _genre_override(self, folder: Path) -> list[str]:
        for key in (os.path.normcase(str(folder)), fold_name(folder.name), fold_name(lookup_name(folder.name))):
            if key in self._genre_overrides:
                return self._genre_overrides[key]
        return []

    def rebuild(self, proposal: Proposal, candidate: Optional[Candidate] = None, level: str = "",
                genres: Optional[list[str]] = None, link: Optional[str] = None) -> Proposal:
        """The proposal again after a PERSON decided: `candidate` (one of proposal.candidates) is the artist /
        release group to use whatever the score said, and/or `level` (one of proposal.suggested_levels:
        'artist', 'album', 'genre-collection') is the level to build. A chosen candidate is believed: the result
        is 'confident' when the note could be built, and its reasons say it was chosen. Cached lookups are
        reused, so this is usually one or two requests. Never raises for a service failure. 'skip' is not an
        engine matter (the review page just leaves the folder out)."""
        self._network_failures = 0
        target = proposal.target
        kind = target.kind
        if link and proposal.note_kind == "about":  # a genre / collection folder: the link names the genre's Wikipedia article
            return self._genre_from_link(proposal, link)
        if link:  # 'Look up by link': the pasted link names the artist / album: believed like a picked candidate
            candidate = self.candidate_from_link(proposal, link)
        if level == LEVEL_LOCAL_TAGS:
            return self.local_note(proposal)
        if level == SUGGEST_ARTIST:
            kind = KIND_ARTIST
        elif level == SUGGEST_ALBUM:
            kind = KIND_ALBUM
        if kind != target.kind:
            target = Target(target.folder, kind, target.parent_name)  # another level: no older note to refresh
        return self._dispatch(target, candidate, level, genres)

    def _dispatch(self, target: Target, force: Optional[Candidate] = None, force_level: str = "",
                  genres: Optional[list[str]] = None) -> Proposal:
        try:
            if target.kind == KIND_ARTIST:
                proposal = self._artist_proposal(target, force, force_level, genres)
            elif target.kind == KIND_GENRE:
                proposal = self._genre_proposal(target, genres)
            else:
                proposal = self._album_proposal(target, force, force_level)
        except nmb.MusicBrainzError as exc:
            proposal = Proposal(target, Confidence.NONE, reasons=["MusicBrainz failed"], problems=[str(exc)])
        except (wp.WikipediaError, DiscogsError) as exc:  # only reached when nothing was caught closer
            proposal = Proposal(target, Confidence.NONE, reasons=["a lookup failed"], problems=[str(exc)])
        # the older note to refresh is the one of the TARGET's kind: a folder that turned out to be a collection (about.md)
        # has no about.md to replace, whatever artist.md it had
        proposal.old_state = target.update if proposal.note_kind == target.note_kind else None
        if (proposal.kind in (KIND_ARTIST, KIND_ALBUM) and proposal.confidence != Confidence.CONFIDENT
                and not proposal.note_text and not proposal.candidates and proposal.level != LEVEL_UNCLEAR):
            if self._discogs is None:
                proposal.reasons.append(
                    "Discogs was not searched (no token set, or switched off): add the token in Tools > API Keys... "
                    "to look there too")
            if not self.fetchers.wikipedia:
                proposal.reasons.append(WIKIPEDIA_OFF)
        if any(_is_network_down(p) for p in proposal.problems):
            self._network_failures += 1
        else:
            self._network_failures = 0
        return proposal

    def _full_discography(self, mbid: str, fallback: list[nmb.ReleaseGroup]) -> list[nmb.ReleaseGroup]:
        """Every release group of the artist, for the note's discography (the matching only uses albums and EPs).
        One extra request per artist, cached; when it fails the albums and EPs already fetched are used."""
        if mbid not in self._all_groups:
            try:
                self._all_groups[mbid] = self._browse(mbid)
            except nmb.MusicBrainzError:
                return fallback
        return self._all_groups[mbid]

    def _browse(self, mbid: str) -> list[nmb.ReleaseGroup]:
        """EVERY release group (one request per 100), or with singles left out only the albums and EPs (the compilations,
        live albums and soundtracks are albums to MusicBrainz): the artist note's discography and the matching's list."""
        return nmb.browse_all_release_groups(mbid, self._mb) if self.singles else nmb.browse_release_groups(mbid, self._mb)

    def _discogs_entries(self, client: DiscogsClient, artist_id: int) -> list[dict]:
        if artist_id not in self._d_entries:
            self._d_entries[artist_id] = nd.artist_release_entries(client, artist_id)
        return self._d_entries[artist_id]

    # -- artist resolution --------------------------------------------------------------------------------

    def _discography(self, mbid: str) -> list[nmb.ReleaseGroup]:
        """The artist's albums and EPs, for the matching. They are taken from the FULL list of release groups that the
        artist note needs anyway (one browse, memoized), not asked for a second time with a type filter."""
        if mbid not in self._groups:
            if mbid not in self._all_groups:
                self._all_groups[mbid] = self._browse(mbid)
            self._groups[mbid] = [g for g in self._all_groups[mbid] if (g.primary or "").casefold() in ("album", "ep")]
        return self._groups[mbid]

    @staticmethod
    def _merge_same_artist(candidates: list[Candidate]) -> list[Candidate]:
        """A MusicBrainz and a Discogs candidate become ONE line ('musicbrainz + discogs') only when a verifiable fact
        agrees: a local album found in BOTH discographies. The same name alone proves nothing (two services may each have a
        'Chainfist' that are different artists): such candidates stay apart and the match stays uncertain. The merged
        candidate's note says what the two have in common."""
        merged: list[Candidate] = []
        for cand in candidates:
            twin = next((m for m in merged if cand.source == "discogs" and m.source == "musicbrainz" and not m.also
                         and fold_name(m.name) == fold_name(cand.name) and set(m.hits) & set(cand.hits)), None)
            if twin is None:
                merged.append(cand)
            else:
                shared = [h for h in twin.hits if h in set(cand.hits)]
                twin.also, twin.also_id = "discogs", cand.id
                twin.detail = "; ".join(x for x in (twin.detail, f"Discogs {cand.id}") if x)
                twin.note = (f"merged with Discogs {cand.id}: both discographies have {', '.join(repr(h) for h in shared)}"
                             + (f"; {twin.note}" if twin.note else ""))
        return merged

    def resolve_artist(
        self, names: list[str], local_titles: list[list[str]], local_years: list[str],
        partial: Optional[list[str]] = None,
    ) -> _Resolution:
        """The artist behind a folder: candidates from MusicBrainz and, by `discogs_mode`, Discogs, each scored by how many
        of the local album titles it has in its discography (see core/notes_match.py). 'always' runs the two services side by
        side; 'fallback' asks Discogs only when MusicBrainz found no verified match."""
        partial = [p for p in (partial or []) if p and fold_name(p) not in [fold_name(n) for n in names]]
        key = (tuple(fold_name(n) for n in names), tuple(fold_name(p) for p in partial), tuple(album_key(t) for names_ in local_titles for t in names_))
        if key in self._artists:
            return self._artists[key]
        res = _Resolution()
        self._artists[key] = res
        client = self._discogs
        asks = client is not None and not client.rate_limited
        dc = _Resolution()
        if asks and self.discogs_mode == "always":
            mb, dc = self._both(lambda: self._resolve_mb(names, partial, local_titles, local_years),
                                lambda: self._resolve_discogs(names, local_titles))
        else:
            mb = self._resolve_mb(names, partial, local_titles, local_years)
            if asks and self.discogs_mode == "fallback" and mb.confidence != Confidence.CONFIDENT:
                dc = self._resolve_discogs(names, local_titles)
        res.candidates = self._merge_same_artist([*mb.candidates, *dc.candidates])
        res.reasons = [*mb.reasons, *dc.reasons]
        res.problems = [*mb.problems, *dc.problems]
        res.mb, res.discogs = mb.mb, dc.discogs
        # The name searches verified nothing (a band whose plain name is shared with a city, or written in another script):
        # Wikipedia's article and its Wikidata item name the MusicBrainz / Discogs ids; then the local album titles.
        down = any(_is_network_down(p) for p in res.problems)  # the services cannot be reached: nothing more to ask
        if down:
            self._artists.pop(key, None)
        steps = [] if down else ([self._resolve_wikipedia] if self.fetchers.wikipedia else []) + [self._resolve_by_titles]
        for extra in steps:
            if res.mb_ok or res.discogs_ok:
                break
            more = extra(names, local_titles, local_years)
            ids = {m.id for m in more.candidates}
            res.candidates = [*more.candidates, *[c for c in res.candidates if c.id not in ids]]
            res.reasons += more.reasons
            res.problems += more.problems
            if more.mb is not None and (more.mb_ok or res.mb is None):
                res.mb = more.mb
            if more.discogs is not None and (more.discogs_ok or res.discogs is None):
                res.discogs = more.discogs
            if more.mb_ok or more.discogs_ok:
                res.via, res.page = more.via, more.page
        if down or any(_is_network_down(p) for p in res.problems):
            self._artists.pop(key, None)  # a timeout is not an answer: the next folder of this artist asks again
        return res

    def _evaluate_mb(self, hit: "nmb.ArtistHit", names, partial, local_titles, local_years):
        """(the Evaluation of one MusicBrainz artist against the folder, its release groups): the name through the name, sort
        name and aliases, the local albums found in its discography, the era."""
        forms = [hit.name, hit.sort_name, *hit.aliases]
        sim = max(name_similarity(names, forms), 0.55 * name_similarity(partial, forms) if partial else 0.0)
        groups = self._discography(hit.id)
        found = discography_hits(local_titles, [g.title for g in groups])
        return Evaluation(
            "musicbrainz", hit.id, hit.name, hits=found, name_sim=sim, search_score=hit.score,
            era_conflict=era_conflict(hit.begin, hit.end, local_years),
            detail=", ".join(x for x in (hit.disambiguation, hit.country, hit.begin[:4]) if x)), groups

    def _resolve_wikipedia(self, names, local_titles, local_years) -> _Resolution:
        """WIKIPEDIA FIRST: the article about the artist ('X (band)', 'X (musician)', the search), its Wikidata item's
        MusicBrainz id (P434) and Discogs id (P1953), then those ids looked up DIRECTLY and verified like any candidate by
        the local album titles. Verified by at least one local album: confident, 'found through Wikipedia'. No local album
        found: uncertain (a person decides; never written in a batch)."""
        res = _Resolution(via="wikipedia")
        if not self.fetchers.wikipedia:
            return res
        context = [t for titles in local_titles for t in titles]
        notes: list[str] = []
        try:
            page = None
            for name in names[:2]:
                page = wp.find_artist_page(name, context, self._wiki, notes=notes)
                if page is not None:
                    break
            if page is None:
                res.reasons.append("Wikipedia: no article about this artist was found and verified")
                return res
            ids = wp.wikidata_identifiers(title=page.title, fetch=self._wiki)
        except wp.WikipediaError as exc:
            res.problems.append(str(exc))
            return res
        if not ids["musicbrainz"] and not ids["discogs"]:
            res.reasons.append(f"Wikipedia: '{page.title}' has no MusicBrainz or Discogs id on Wikidata")
            return res
        res.page = page
        core_name = re.sub(r"\s*\([^)]*\)\s*$", "", page.title)
        mb_eval = dc_eval = None
        try:
            if ids["musicbrainz"]:
                hit = nmb.artist_by_id(ids["musicbrainz"], self._mb)
                mb_eval, groups = self._evaluate_mb(hit, names, [], local_titles, local_years)
                winner, confidence, why = decide_artist([mb_eval])
                res.mb = _ArtistFound(mb_eval, confidence, hit=hit, groups=groups)
                res.reasons += [f"MusicBrainz: {r}" for r in why]
        except nmb.MusicBrainzError as exc:
            res.problems.append(str(exc))
        try:
            if ids["discogs"] and ids["discogs"].isdigit() and self._discogs is not None and not self._discogs.rate_limited:
                entries = self._discogs_entries(self._discogs, int(ids["discogs"]))
                found = discography_hits(local_titles, [e["title"] for e in entries])
                forms = [core_name, *(([mb_eval.name] if mb_eval else []))]
                dc_eval = Evaluation("discogs", ids["discogs"], core_name, hits=found, name_sim=name_similarity(names, forms),
                                     detail=f"Discogs {ids['discogs']}")
                winner, confidence, why = decide_artist([dc_eval])
                res.discogs = _ArtistFound(dc_eval, confidence, discogs_hit=nd.DiscogsArtistHit(int(ids["discogs"]), core_name))
                res.reasons += [f"Discogs: {r}" for r in why]
        except DiscogsError as exc:
            res.problems.append(str(exc))
        sources = " and ".join(s for s, e in (("MusicBrainz", mb_eval), ("Discogs", dc_eval)) if e is not None)
        res.reasons.insert(0, f"found through Wikipedia: '{page.title}' (found by {page.via}); its Wikidata item {ids['qid']} "
                              f"names the artist at {sources or 'neither service'}")
        lead = mb_eval or dc_eval
        if lead is not None:
            cand = Candidate(lead.source, lead.id, lead.name, lead.detail, list(lead.hits), lead.name_sim,
                             f"found through Wikipedia '{page.title}' (Wikidata {ids['qid']})")
            if mb_eval is not None and dc_eval is not None:
                cand.also, cand.also_id = "discogs", dc_eval.id
                cand.detail = "; ".join(x for x in (cand.detail, f"Discogs {dc_eval.id}") if x)
            res.candidates.append(cand)
        return res

    def _resolve_by_titles(self, names, local_titles, local_years) -> _Resolution:
        """The artist's NAME found nothing: search MusicBrainz for the albums by their exact titles (any script) and group
        the hits by artist. An artist that owns two of the local albums, or the one album that only it has, becomes a
        candidate and is checked like any other (its discography against all the local albums). A few requests, only here."""
        res = _Resolution(via="titles")
        owned: dict[str, int] = {}
        sole: dict[str, int] = {}
        seen: set[str] = set()
        try:
            for titles in local_titles[:4]:
                title = titles[0] if titles else ""
                key = album_key(title)
                if not key or key in seen:
                    continue
                if is_generic_album_title(title, names):
                    continue  # 'Greatest Hits', 'Live', 'Demo', a self-titled album: on every discography, they identify nobody
                seen.add(key)
                owners = {g.artist_id for g in nmb.search_release_groups_by_title(title, self._mb)
                          if g.artist_id and titles_match(title, g.title)}
                for owner in owners:
                    owned[owner] = owned.get(owner, 0) + 1
                    if len(owners) == 1:
                        sole[owner] = sole.get(owner, 0) + 1
            chosen = sorted((o for o in owned if owned[o] >= 2 or sole.get(o)), key=lambda o: (-owned[o], o))[:2]
            for owner in chosen:
                hit = nmb.artist_by_id(owner, self._mb)
                evaluation, groups = self._evaluate_mb(hit, names, [], local_titles, local_years)
                winner, confidence, why = decide_artist([evaluation])
                distinctive_sole = sole.get(owner, 0) >= 2  # two DISTINCTIVE titles that only this artist has
                if (confidence != Confidence.CONFIDENT and len(evaluation.hits) >= 2 and not evaluation.era_conflict
                        and (evaluation.name_sim >= 0.5 or distinctive_sole)):
                    # two of the albums are theirs and the name is at least related (or nobody else has those titles): the
                    # name may be written in another script. A name that has nothing to do with it never gets here on
                    # titles alone: that is only a candidate for a person to look at.
                    confidence = Confidence.CONFIDENT
                    why = [f"{len(evaluation.hits)} local albums are in its discography ({', '.join(evaluation.hits)}), "
                           f"found by their titles although the name differs"]
                found = _ArtistFound(evaluation, confidence, hit=hit, groups=groups)
                if res.mb is None or confidence == Confidence.CONFIDENT:
                    res.mb = found
                res.candidates.append(Candidate(
                    "musicbrainz", hit.id, hit.name, evaluation.detail, list(evaluation.hits), evaluation.name_sim,
                    f"found by the local album titles ({owned[owner]} of them)"))
                res.reasons += [f"MusicBrainz (by album titles): {r}" for r in why]
        except nmb.MusicBrainzError as exc:
            res.problems.append(str(exc))
        return res

    def _resolve_mb(self, names, partial, local_titles, local_years) -> _Resolution:
        res = _Resolution()
        wanted = len([t for t in local_titles if t])
        try:
            hits: list[nmb.ArtistHit] = []
            for name in [*names[:2], *partial[:1]]:
                for hit in nmb.search_artists(name, self._mb):
                    if hit.id not in [h.id for h in hits]:
                        hits.append(hit)
            scored = []
            for hit in hits:
                forms = [hit.name, hit.sort_name, *hit.aliases]
                # a collaboration folder ('A & B') found only under its first-named artist is a loose match
                sim = max(name_similarity(names, forms), 0.55 * name_similarity(partial, forms) if partial else 0.0)
                if sim >= 0.5:
                    scored.append((sim, hit.score, hit))
            scored.sort(key=lambda s: (s[0], s[1]), reverse=True)
            evaluations: list[tuple[Evaluation, nmb.ArtistHit, list[nmb.ReleaseGroup]]] = []
            top = scored[:MAX_CANDIDATES]
            for position, (sim, _score, hit) in enumerate(top):
                groups = self._discography(hit.id)
                found = discography_hits(local_titles, [g.title for g in groups])
                evaluation = Evaluation(
                    "musicbrainz", hit.id, hit.name, hits=found, name_sim=sim, search_score=hit.score,
                    era_conflict=era_conflict(hit.begin, hit.end, local_years),
                    detail=", ".join(x for x in (hit.disambiguation, hit.country, hit.begin[:4]) if x),
                )
                evaluations.append((evaluation, hit, groups))
                if _can_stop_asking(sim, wanted, found, evaluation.era_conflict, [t[0] for t in top[position + 1:]]):
                    break  # every local album is in this artist's discography: no need to look further
            winner, confidence, reasons = decide_artist([e for e, _h, _g in evaluations])
            for evaluation, hit, groups in evaluations:
                res.candidates.append(Candidate("musicbrainz", hit.id, hit.name, evaluation.detail, evaluation.hits,
                                                evaluation.name_sim, evaluation.era_conflict))
                if winner is not None and evaluation is winner:
                    res.mb = _ArtistFound(evaluation, confidence, hit=hit, groups=groups)
            res.reasons += [f"MusicBrainz: {r}" for r in reasons]
        except nmb.MusicBrainzError as exc:
            res.problems.append(str(exc))
        return res

    def _resolve_discogs(self, names, local_titles) -> _Resolution:
        res = _Resolution()
        wanted = len([t for t in local_titles if t])
        client = self._discogs
        if client is not None and not client.rate_limited:
            try:
                dhits = nd.search_artists(client, names[0])
                scored_d = sorted(
                    ((name_similarity(names, [h.name]), h) for h in dhits), key=lambda s: s[0], reverse=True)
                devals: list[tuple[Evaluation, nd.DiscogsArtistHit]] = []
                near = [s for s in scored_d if s[0] >= 0.5][:3]
                for position, (sim, hit) in enumerate(near):
                    entries = self._discogs_entries(client, hit.id)
                    found = discography_hits(local_titles, [e["title"] for e in entries])
                    evaluation = Evaluation("discogs", str(hit.id), hit.name, hits=found, name_sim=sim,
                                            search_score=0, detail=f"Discogs {hit.id}")
                    devals.append((evaluation, hit))
                    if _can_stop_asking(sim, wanted, found, "", [t[0] for t in near[position + 1:]]):
                        break
                winner, confidence, reasons = decide_artist([e for e, _h in devals])
                for evaluation, hit in devals:
                    res.candidates.append(Candidate("discogs", str(hit.id), hit.name, evaluation.detail,
                                                    evaluation.hits, evaluation.name_sim))
                    if winner is not None and evaluation is winner:
                        res.discogs = _ArtistFound(evaluation, confidence, discogs_hit=hit)
                res.reasons += [f"Discogs: {r}" for r in reasons]
            except DiscogsError as exc:
                res.problems.append(str(exc))
        return res

    # -- artist notes -------------------------------------------------------------------------------------

    SAMPLE_ALBUMS = 4  # the artist match is verified from the tags of the first albums ...
    SAMPLE_FILES = 3  # ... and the first files of each (the other albums count by their folder names); then all, if unsure

    def _artist_level(self, folder: Path, full: bool = False) -> tuple[LocalArtist, LevelDecision]:
        """The artist folder read (cached) and the artist test applied to it (core/notes_level.py). By default only a SAMPLE
        of the tags is read (the first SAMPLE_FILES files of the first SAMPLE_ALBUMS albums: on a slow share the tags cost
        more than anything else); `full` reads all of them (an unclear level or an unverified match asks for that)."""
        key = (os.path.normcase(str(folder)), full)
        if key not in self._levels:
            if full:
                local = read_artist(folder, self.loader)
            else:
                local = read_artist(folder, self.loader, max_albums=self.SAMPLE_ALBUMS, files_per_album=self.SAMPLE_FILES)
            self._levels[key] = (local, artist_level(local.name, local.albums))
        return self._levels[key]

    def _forced_resolution(self, res: _Resolution, candidate: Candidate) -> _Resolution:
        """The resolution with the artist a person chose as its (confident) winner."""
        forced = _Resolution(candidates=list(res.candidates), problems=list(res.problems))
        evaluation = Evaluation(candidate.source, candidate.id, candidate.name, hits=list(candidate.hits),
                                name_sim=candidate.score, detail=candidate.detail)
        if candidate.source == "musicbrainz":
            link_hit, link_page = self._link_hits.get(candidate.id, (None, None))
            forced.mb = _ArtistFound(evaluation, Confidence.CONFIDENT, hit=link_hit, groups=self._discography(candidate.id))
            if link_page is not None:
                forced.via, forced.page = "wikipedia", link_page
            if candidate.also == "discogs":  # both sources name this artist: members and profile come from Discogs too
                forced.discogs = _ArtistFound(Evaluation("discogs", candidate.also_id, candidate.name, hits=list(candidate.hits),
                                                         name_sim=candidate.score, detail=f"Discogs {candidate.also_id}"),
                                              Confidence.CONFIDENT)
        else:
            forced.discogs = _ArtistFound(evaluation, Confidence.CONFIDENT)
        how = _chosen(candidate)
        forced.reasons = [f"{how}: {candidate.name} ({candidate.source})"]
        if candidate.hits:
            forced.reasons.append(f"{len(candidate.hits)} local album(s) found in its discography: {', '.join(candidate.hits)}")
        return forced

    def _artist_proposal(self, target: Target, force: Optional[Candidate] = None, force_level: str = "",
                         genres: Optional[list[str]] = None) -> Proposal:
        local, decision = self._artist_level(target.folder)
        if local.sampled and decision.level != LEVEL_ARTIST:  # an odd level needs ALL the evidence
            local, decision = self._artist_level(target.folder, full=True)
        level = {SUGGEST_ARTIST: LEVEL_ARTIST, SUGGEST_COLLECTION: LEVEL_COLLECTION}.get(force_level, decision.level)
        proposal = Proposal(target, local=local, level=level, level_reasons=list(decision.reasons),
                            suggested_levels=list(decision.suggested))
        if level != decision.level:
            proposal.level_reasons.append(f"level chosen by you: {force_level}")
        if level == LEVEL_COLLECTION:
            return self._collection_proposal(proposal, local, genres)
        if level == LEVEL_UNCLEAR:
            proposal.confidence = Confidence.UNCERTAIN
            proposal.reasons = list(decision.reasons)
            return proposal
        proposal.reasons = list(proposal.level_reasons)
        titles = [a.titles for a in local.albums]
        years = [a.year for a in local.albums]
        collab = split_collab(local.name)
        partial = collab[:1] if len(collab) > 1 else []
        res = self.resolve_artist(local.names, titles, years, partial)
        if res.confidence != Confidence.CONFIDENT and local.sampled and force is None:
            # not verified from a sample of the tags: read the rest and ask again (the answers are already in memory)
            local, _decision = self._artist_level(target.folder, full=True)
            proposal.local = local
            titles = [a.titles for a in local.albums]
            years = [a.year for a in local.albums]
            res = self.resolve_artist(local.names, titles, years, partial)
        if force is not None and force.source == "wikipedia":
            return self._wikipedia_artist(proposal, local, res, force, titles)
        if force is not None:
            res = self._forced_resolution(res, force)
        proposal.candidates = list(res.candidates)
        proposal.reasons += list(res.reasons)
        proposal.problems = list(res.problems)
        proposal.confidence = res.confidence
        if res.confidence != Confidence.CONFIDENT:
            if res.candidates and not any(c.hits for c in res.candidates):
                shown = ", ".join(f"'{t[0]}'" for t in titles[:3] if t)
                two = len({c.source for c in res.candidates}) > 1
                proposal.reasons.append(
                    f"why it is uncertain: no local album ({shown}{', ...' if len(titles) > 3 else ''}) was found in "
                    f"{'either discography' if two else 'the discography of a candidate'}"
                    + (": the candidates of the two services may be different artists with the same name" if two else "")
                    + "; pick the one you trust")
            if not res.candidates and not res.problems:
                proposal.reasons.append("no artist of that name was found")
            if not res.candidates and not res.problems:
                self._offer_wikipedia(proposal, "artist", local.name, "", [t for names_ in titles for t in names_])
            return proposal

        mb = res.mb_ok
        detail: dict = {}
        sources: list[str] = []
        footer: list[str] = []
        if res.via == "wikipedia":
            footer.append("Links to MusicBrainz and Discogs found through Wikipedia's Wikidata item (CC0)")
        info: Optional[nd.DiscogsArtist] = None
        want_info = res.discogs_ok is not None and self._discogs is not None

        def fetch_detail():
            try:
                return nmb.artist_detail(mb.evaluation.id, self._mb), None
            except nmb.MusicBrainzError as exc:
                return {}, exc

        def fetch_info():
            try:
                return nd.artist_info(self._discogs, int(res.discogs_ok.evaluation.id)), None
            except DiscogsError as exc:
                return None, exc

        # the two services side by side when both have to be asked
        if mb is not None and want_info:
            (detail, detail_error), (info, info_error) = self._both(fetch_detail, fetch_info)
        else:
            detail, detail_error = fetch_detail() if mb is not None else ({}, None)
            info, info_error = fetch_info() if want_info else (None, None)
        if mb is not None:
            if detail_error is None:
                sources.append("musicbrainz")
                footer.append(note_text.musicbrainz_credit())
            else:
                proposal.problems.append(str(detail_error))
        if want_info:
            if info_error is None:
                sources.append("discogs")
            else:
                proposal.problems.append(str(info_error))

        name = (detail.get("name") if detail else "") or (info.name if info else "") or local.name
        if fold_name(name) == fold_name(local.name):
            name = local.name
        elif mb is not None and mb.hit is not None and fold_name(local.name) in {
                fold_name(f) for f in (mb.hit.sort_name, *mb.hit.aliases)}:
            name = local.name  # 'Leningrad' is the band's alias and sort name: the folder's own name stays the title
        # -- the introduction: Wikipedia, else Discogs' profile, else a sidecar's text
        intro, intro_credit, wiki_url = "", "", ""
        if self.fetchers.wikipedia:
            hint_title, wikidata = nmb.wikipedia_hint(detail) if detail else ("", "")
            page = res.page if res.via == "wikipedia" else None  # the article that named the ids is the introduction
            try:
                if page is None:
                    page = wp.find_page(
                        "artist", name, linked_title=hint_title, wikidata_id=wikidata,
                        context_terms=[t for names_ in titles for t in names_], fetch=self._wiki,
                        notes=proposal.reasons)
            except wp.WikipediaError as exc:
                page = None
                proposal.problems.append(str(exc))
            if page is not None:
                intro, wiki_url = wp.trim_extract(page.extract), page.url or ""
                intro_credit = note_text.wikipedia_credit(page.title)
                sources.append("wikipedia")
                proposal.reasons.append(f"Wikipedia: '{page.title}' (found by {page.via})")
        if (not intro and info is None and self._discogs is not None and self.discogs_mode == "fallback"
                and res.discogs is None and not self._discogs.rate_limited):
            # no Wikipedia introduction: in fallback mode Discogs is asked now, for the profile and the members
            part = self._resolve_discogs(local.names, titles)
            proposal.problems += part.problems
            if part.discogs_ok is not None:
                try:
                    info = nd.artist_info(self._discogs, int(part.discogs_ok.evaluation.id))
                    sources.append("discogs")
                    proposal.reasons.append("Discogs asked for the profile and members: Wikipedia had no introduction")
                    name = name if detail else (info.name or name)
                except DiscogsError as exc:
                    proposal.problems.append(str(exc))
        if not intro and info is not None and info.profile.strip():
            intro = _cap_text(note_text.clean_discogs_markup(info.profile), 1500)
            intro_credit = note_text.discogs_credit("artist", info.id)
        nfo_genres: list[str] = []
        sidecar = sidecar_for(target.folder)
        if sidecar:
            from redactor_common.core.note_convert import from_nfo

            converted = from_nfo(sidecar)
            if converted.ok:
                if not intro and _prose(converted.draft.body):
                    intro = _prose(converted.draft.body)
                    intro_credit = f"Taken from {converted.draft.source}"
                    sources.append("sidecar")
                nfo_genres = converted.draft.front.genres
        if intro_credit:
            footer.append(intro_credit)

        # -- facts
        members = (nmb.member_names(detail) if detail else []) or (
            (info.active_members or info.members) if info else [])
        life = detail.get("life-span") or {} if detail else {}
        area = ((detail.get("area") or {}).get("name") if detail else "") or (detail.get("country") if detail else "")
        active = ""
        if (life.get("begin") or "")[:4]:
            active = life["begin"][:4] + (f" to {life['end'][:4]}" if life.get("ended") and life.get("end") else " to present")
        facts = [
            ("Type", detail.get("type") or "" if detail else ""),
            ("Country", area or ""),
            ("Active", active),
            ("Members", ", ".join(members[:MAX_MEMBERS])),
            ("Real name", info.real_name if info else ""),
            ("Also known as", ", ".join(info.aliases[:6]) if info and mb is None else ""),
        ]
        # -- links: homepage, Wikipedia (the page actually used), AllMusic, Discogs, Last.fm
        found_links = nmb.relation_links(detail) if detail else {}
        info_wiki = next((u for u in (info.urls if info else []) if "wikipedia.org/wiki/" in u), "")
        links = [found_links.get("official homepage", ""), wiki_url or found_links.get("wikipedia", "") or info_wiki,
                 found_links.get("allmusic", ""), found_links.get("discogs", "") or (info.uri if info else ""),
                 found_links.get("last.fm", "")]
        if mb:  # the verified MusicBrainz discography wins; Discogs then gives members / profile only
            discography = group_discography(self._full_discography(mb.evaluation.id, mb.groups))
        elif info is not None and self.fetchers.discogs is not None:
            try:
                discography = group_discogs_discography(self._discogs_entries(self._discogs, info.id))
            except DiscogsError as exc:
                discography = []
                proposal.problems.append(str(exc))
        else:
            discography = []
        years = _years_text(life.get("begin") or "", life.get("end") or "", bool(life.get("ended"))) if detail else ""
        proposal.note_text = render_artist_note(
            name=name, years=years, genres=nmb.genres_of(detail) if detail else nfo_genres, links=[l for l in links if l],
            intro=intro, facts=facts, discography=discography, footer=footer)
        proposal.title = name
        proposal.sources = sources
        return proposal

    # -- album notes --------------------------------------------------------------------------------------

    # -- genre and collection notes (about.md) --------------------------------------------------------------

    def _genre_proposal(self, target: Target, genres: Optional[list[str]] = None) -> Proposal:
        local = read_genre(target.folder)
        proposal = Proposal(target, local=local, level=LEVEL_GENRE, suggested_levels=[SUGGEST_COLLECTION, SUGGEST_SKIP])
        proposal.level_reasons = [f"{len(local.artists)} artist folder(s) directly below it"
                                  if local.artists else f"{len(local.albums)} album folder(s) directly below it"]
        return self._genre_like(proposal, local.name, genres)

    def _collection_proposal(self, proposal: Proposal, local: LocalArtist, genres: Optional[list[str]] = None) -> Proposal:
        """A folder found (from the tags / its name) to be a collection, not an artist: it gets about.md and each
        album folder below it is an album of its own, with no artist level."""
        proposal.kind_override = "about"
        return self._genre_like(proposal, local.name.lstrip("_").strip(), genres)

    def _verify_genres(self, names: list[str], notes: list[str], problems: list[str]):
        """([(genre as asked, its verified Wikipedia page)], [genres that could not be verified as a music genre])."""
        found, failed = [], []
        for genre in names:
            page = None
            if self.fetchers.wikipedia:
                try:
                    page = wp.find_page("genre", genre, fetch=self._wiki, notes=notes)
                except wp.WikipediaError as exc:
                    problems.append(str(exc))
            (found if page is not None else failed).append((genre, page) if page is not None else genre)
        return found, failed

    def _genre_from_link(self, proposal: Proposal, link: str) -> Proposal:
        """'Look up by link' for a genre / collection folder: the pasted Wikipedia article IS the genre's page, believed like a picked
        candidate (no check that its lead says 'genre'). The note is that article's introduction and the credit. Raises LinkError for a
        link that is not a Wikipedia article or names none; nothing else changes then."""
        target = parse_link(link)
        if target.kind != "wikipedia":
            raise LinkError("For a genre or collection folder paste the Wikipedia article about the genre "
                            "(a link like https://en.wikipedia.org/wiki/Ska_punk).")
        try:
            page = wp.get_summary(target.id, self._wiki, target.lang or "en")
        except wp.WikipediaError as exc:
            raise LinkError(str(exc)) from exc
        if page is None or page.is_disambiguation:
            raise LinkError("No Wikipedia article about one genre was found at that link.")
        page.via = f"link: {target.raw or target.id}"
        fresh = copy.copy(proposal)
        fresh.candidates, fresh.problems, fresh.note_text = [], [], ""
        name = proposal.title or lookup_name(proposal.folder.name)
        result = self._genre_like(fresh, name, link_page=page)
        result.reasons.append("chosen by you from a link")
        return result

    def _genre_like(self, proposal: Proposal, name: str, genres: Optional[list[str]] = None, link_page=None) -> Proposal:
        """Wikipedia's page about the music genre `name` (verified: its lead says it is a genre / style of music): the note
        is that introduction and the credit. A folder can cover SEVERAL genres ('1.61 - Ska' = ska and ska punk): the ones
        a person gave (`genres`, or set_genres), else the name split on ' & ', ' and ', ',', '/', ' + ' ONLY when every
        part verifies as its own genre page ('Rock and Roll', 'Drum and Bass' stay whole). Each is verified; the note has
        one section per genre and one credit. A genre that cannot be verified makes the row 'uncertain', and the reasons
        name which one. Never written in a batch run when uncertain."""
        proposal.reasons = list(proposal.level_reasons)
        explicit = [g.strip() for g in (genres or self._genre_override(proposal.folder)) if g and g.strip()]
        covered = list(dict.fromkeys(explicit)) if explicit else [name]
        found, failed = [], []
        if link_page is not None:                                # a pasted article: used as it is, named by its own title
            covered = [link_page.title]
            found = [(link_page.title, link_page)]
        elif explicit:
            found, failed = self._verify_genres(covered, proposal.reasons, proposal.problems)
        else:
            parts = split_genre_name(name)
            if len(parts) > 1:  # 'Ska & Ska Punk': split only when every part is a genre of its own
                scratch: list[str] = []
                trial, bad = self._verify_genres(parts, scratch, proposal.problems)
                if not bad:
                    found, failed, covered = trial, [], parts
                    proposal.reasons.append(f"covers {len(parts)} genres: {', '.join(parts)} (each verified on Wikipedia)")
            if not found:
                found, failed = self._verify_genres([name], proposal.reasons, proposal.problems)
        proposal.genres = list(covered)
        if failed or not found:
            proposal.confidence = Confidence.UNCERTAIN
            for genre in failed:
                proposal.reasons.append(f"no Wikipedia page about a music genre called '{genre}' could be verified")
            if found:
                proposal.reasons.append("verified: " + ", ".join(f"'{g}'" for g, _p in found))
            return proposal
        proposal.confidence = Confidence.CONFIDENT
        for _genre, page in found:
            proposal.reasons.append(f"Wikipedia: '{page.title}' (found by {page.via})")
        proposal.sources = ["wikipedia"]
        proposal.title = name
        links = [page.url for _g, page in found if page.url]
        credit = wikipedia_credits([(page.title, page.url or "") for _g, page in found])
        if len(found) == 1:
            page = found[0][1]
            proposal.note_text = render_genre_note(
                name=name, intro=wp.trim_extract(page.extract), links=links, footer=[credit])
        else:
            proposal.note_text = render_genre_note(
                name=name, links=links, footer=[credit], genres=[g for g, _p in found],
                sections=[(page.title, wp.trim_extract(page.extract)) for _g, page in found])
        return proposal

    # -- other sources: Discogs albums, Wikipedia-only notes, the explicit local-only note -----------------------

    def _album_candidates_from_discogs(self, proposal: Proposal, hits: list, info: Optional["nd.DiscogsAlbumInfo"]) -> None:
        """The Discogs releases that may be this album, as candidates (the best three that fit at all)."""
        have = {c.id for c in proposal.candidates if c.source == "discogs"}
        for hit in [h for h in hits if h.score >= 0.6][:3]:
            if str(hit.id) in have:
                continue
            detail = ", ".join(x for x in (hit.year, hit.country, " ".join(y for y in (hit.label, hit.catno) if y),
                                           hit.formats) if x)
            proposal.candidates.append(Candidate(
                "discogs", str(hit.id), f"{hit.artist} - {hit.album}" if hit.artist else hit.album, detail, [], hit.score,
                f"Discogs search match: artist, title and year compared with the folder ({hit.score:.2f})"))
            have.add(str(hit.id))
        if info is not None and str(info.release.id) not in have:
            proposal.candidates.append(Candidate(
                "discogs", str(info.release.id), f"{info.release.artist} - {info.release.title}", info.release.year, [],
                info.score))
        if proposal.candidates and proposal.confidence == Confidence.NONE:
            proposal.confidence = Confidence.UNCERTAIN

    def _offer_wikipedia(self, proposal: Proposal, kind: str, name: str, artist: str, context: list[str],
                         soundtrack: bool = False) -> None:
        """Nothing at MusicBrainz or Discogs: a Wikipedia article about the artist / album that passes the
        verification becomes the one candidate (a thin note can be built from it when a person picks it; never in
        a batch run, the proposal stays 'uncertain')."""
        page = self._wiki_intro(proposal, kind, name, artist, context, soundtrack)
        if page is None:
            return
        proposal.candidates.append(Candidate(
            "wikipedia", page.title, page.title, page.description or "Wikipedia article", [], 0.5,
            "a Wikipedia article that passed the check; a thin note (introduction and what the summary says)"))
        proposal.confidence = Confidence.UNCERTAIN
        proposal.reasons.append(
            f"only Wikipedia has a verified article ('{page.title}'): pick it to build a thin draft from it")

    def _wiki_intro(self, proposal: Proposal, kind: str, name: str, artist: str, context: list[str],
                    soundtrack: bool = False) -> Optional["wp.Page"]:
        if not self.fetchers.wikipedia:
            return None
        try:
            return wp.find_page("soundtrack" if soundtrack else kind, work_title(name) if soundtrack else name,
                                artist=artist, context_terms=context, fetch=self._wiki, notes=proposal.reasons)
        except wp.WikipediaError as exc:
            proposal.problems.append(str(exc))
            return None

    def _discogs_album(self, proposal: Proposal, local: LocalAlbum, force: Candidate, names: list[str],
                       soundtrack: bool) -> Proposal:
        """An album note from a Discogs release (or master -> its main release) a person chose: the tracklist
        (side positions A1 / B2 are dropped: tracks are numbered 1..n down the release, one disc per Discogs disc),
        label and catalogue number, country, released, structured credits (producer, written by, musicians,
        sleeve design and photography; never the free-text release notes), genres and styles, a verified
        Wikipedia introduction, and a Discogs credit. A chosen candidate is believed; the overlap with the local
        files is reported."""
        client = self._discogs
        if client is None:
            proposal.confidence = Confidence.UNCERTAIN
            proposal.problems.append(DISCOGS_MISSING)
            return proposal
        try:
            info = nd.release_info(client, force.id, force.score, include_design=True)
        except DiscogsError as exc:
            proposal.confidence = Confidence.UNCERTAIN
            proposal.problems.append(str(exc))
            proposal.reasons.append("Discogs failed while the release was fetched: not written, try again later")
            return proposal
        release = info.release
        tracks = [TrackInfo(t.disc, t.number, t.title, _seconds(t.duration)) for t in release.tracks]
        proposal.reasons.append(f"{_chosen(force)}: {force.name} (Discogs release {release.id})")
        local_titles = [t.title for t in local.tracks if t.title]
        if tracks and len(local_titles) >= 3:
            overlap = track_overlap(local_titles, [t.title for t in tracks])
            proposal.reasons.append(f"{round(overlap * 100)}% of the local track titles are on the release")
            if overlap < 0.3 and isinstance(force, GuessCandidate):  # a guess keeps the veto; only a person's pick is believed past it
                proposal.confidence = Confidence.UNCERTAIN
                proposal.reasons.append(f"only {round(overlap * 100)}% of the track titles match this release")
                return proposal
        if release.year and local.year and release.year != local.year:
            proposal.reasons.append(f"Discogs says {release.year}, the folder says {local.year}")
        artist_name = release.artist or (names[0] if names else "")
        title = release.title
        if fold_name(title) == fold_name(local.title):
            title = local.title
        sources = ["discogs"]
        footer = [discogs_facts_credit("release", release.id)]
        intro, wiki_url = "", ""
        page = self._wiki_intro(proposal, "album", title, artist_name, [], soundtrack)
        if page is not None:
            intro, wiki_url = wp.trim_extract(page.extract), page.url or ""
            footer.append(note_text.wikipedia_credit(page.title))
            sources.append("wikipedia")
            proposal.reasons.append(f"Wikipedia: '{page.title}' (found by {page.via})")
        genres = [g.lower() for g in release.genres + release.styles][:6]
        if soundtrack and "soundtrack" not in genres:
            genres.append("soundtrack")
        released = info.released or release.year
        facts = [
            ("Artist", artist_name), ("Released", released),
            ("Label", f"{release.label} ({release.catno})" if release.label and release.catno else release.label),
            ("Country", release.country),
        ]
        links = [wiki_url, release.uri or f"https://www.discogs.com/release/{release.id}"]
        proposal.confidence = Confidence.CONFIDENT
        proposal.note_text = render_album_note(
            title=title, album=local, years=release.year or local.year, genres=genres, links=[l for l in links if l],
            intro=intro, facts=facts, credits=None if info.credits.empty else info.credits, tracks=tracks,
            footer=footer)
        proposal.title = title
        proposal.sources = sources
        return proposal

    def _wikipedia_album(self, proposal: Proposal, local: LocalAlbum, force: Candidate, names: list[str],
                         soundtrack: bool) -> Proposal:
        page = self._linked_page(force)
        return self._thin_wikipedia(proposal, local, page, force, names[0] if names else "", local.year)

    def _wikipedia_artist(self, proposal: Proposal, local: LocalArtist, res: _Resolution, force: Candidate,
                          titles: list[list[str]]) -> Proposal:
        proposal.candidates = [*res.candidates, force]
        proposal.problems = list(res.problems)
        return self._thin_wikipedia(proposal, local, self._linked_page(force), force, "", "")

    def _linked_page(self, force: Candidate) -> Optional["wp.Page"]:
        """The Wikipedia article a candidate stands for (its id is the article title), fetched and checked again
        (a disambiguation page is never used)."""
        if not self.fetchers.wikipedia:
            return None
        try:
            return wp.find_page("page", force.id, linked_title=force.id, fetch=self._wiki)
        except wp.WikipediaError:
            return None

    def _thin_wikipedia(self, proposal: Proposal, local, page: Optional["wp.Page"], force: Candidate,
                        artist: str, year: str) -> Proposal:
        if page is None:
            proposal.confidence = Confidence.UNCERTAIN
            proposal.reasons.append(f"the Wikipedia article '{force.name}' could not be fetched again: try later")
            return proposal
        is_album = isinstance(local, LocalAlbum)
        name = (local.title if is_album else local.name) or page.title
        facts = [("Artist", artist), ("Released", year)] if is_album else []
        facts.append(("Wikipedia describes it as", page.description))
        proposal.reasons.append(f"chosen by you: Wikipedia article '{page.title}' (thin draft: introduction only)")
        proposal.confidence = Confidence.CONFIDENT
        proposal.sources = ["wikipedia"]
        proposal.title = name
        links = [page.url] if page.url else []
        intro = wp.trim_extract(page.extract)
        footer = [note_text.wikipedia_credit(page.title)]
        if is_album:
            proposal.note_text = render_album_note(
                title=name, album=local, years=year, links=links, intro=intro, facts=facts, tracks=_local_tracks(local),
                footer=footer)
        else:
            proposal.note_text = render_artist_note(name=name, links=links, intro=intro, facts=facts, footer=footer)
        return proposal

    def local_note(self, proposal: Proposal) -> Proposal:
        """The minimal note built from the file tags and folder names only (no network), written as a draft whose
        footer says so. Only ever asked for explicitly (the review page's button): never automatic, never in a
        batch. An album: its title, year, artist and the tracklist read from the files. An artist: its name and the
        albums' titles (from the folder names) as plain '## Releases' lines, so Pluteus can still show badges."""
        target = proposal.target
        out = Proposal(target, local=proposal.local, level=proposal.level, level_reasons=list(proposal.level_reasons),
                       suggested_levels=list(proposal.suggested_levels), candidates=list(proposal.candidates),
                       problems=list(proposal.problems), kind_override=proposal.kind_override,
                       old_state=proposal.old_state)
        footer = [LOCAL_ONLY_CREDIT]
        if target.kind == KIND_ARTIST:
            local = proposal.local if isinstance(proposal.local, LocalArtist) else read_artist(target.folder, self.loader)
            releases = sorted([(a.title, a.year) for a in local.albums if a.title],
                              key=lambda ty: (ty[1] or "9999", ty[0].casefold()))
            out.note_text = render_artist_note(
                name=local.name, discography=[(H_OTHER, releases)] if releases else [], footer=footer)
            out.title = local.name
        else:
            local = proposal.local if isinstance(proposal.local, LocalAlbum) else read_album(target.folder, self.loader)
            facts = [("Artist", local.album_artist or local.artist)]
            genres = [g.lower() for g in local.genres[:4]]
            out.note_text = render_album_note(
                title=local.title or target.folder.name, album=local, years=local.year, genres=genres, facts=facts,
                tracks=_local_tracks(local), footer=footer)
            out.title = local.title or target.folder.name
        out.confidence = Confidence.CONFIDENT
        out.sources = ["tags"]
        out.reasons = ["minimal note from the file tags only (you asked for it): no online source"]
        return out

    def _sibling_titles(self, target: Target, local: LocalAlbum) -> tuple[list[list[str]], list[str]]:
        """(titles, years) of the album folders next to this one when the parent is an artist folder: more
        evidence for who the artist is than one album gives. Folder names only (no tags are read here)."""
        parent = target.folder.parent
        if parent.name and classify(parent) == KIND_ARTIST:
            titles, years = [], []
            for child in album_children(parent):
                title, year = split_year(child.name)
                titles.append([title])
                years.append(year)
            if titles:
                return titles, years
        return [local.titles], [local.year]

    def _album_proposal(self, target: Target, force: Optional[Candidate] = None, force_level: str = "") -> Proposal:
        local = read_album(target.folder, self.loader)
        proposal = Proposal(target, local=local, level=LEVEL_ALBUM, suggested_levels=[SUGGEST_ALBUM, SUGGEST_SKIP])
        parent = target.folder.parent
        parent_structural = bool(parent.name) and classify(parent) == KIND_ARTIST
        parent_is_artist = False
        if parent_structural:
            parent_decision = self._artist_level(parent)[1]
            parent_is_artist = parent_decision.level == LEVEL_ARTIST
            if not parent_is_artist:
                proposal.level_reasons.append(
                    f"the folder above ('{parent.name}') is not an artist folder: {parent_decision.reasons[0]}")
        # one folder of loose tracks of one artist (several album tags, no album folder): an artist level
        if (force_level != SUGGEST_ALBUM and len(local.tag_titles) >= 3 and local.artist
                and names_agree(lookup_name(target.folder.name), local.artist)):
            proposal.level, proposal.suggested_levels = LEVEL_ARTIST, [SUGGEST_ARTIST, SUGGEST_ALBUM, SUGGEST_SKIP]
            proposal.level_reasons.append(f"{len(local.tag_titles)} different album tags in a folder named like the artist")
            proposal.confidence = Confidence.UNCERTAIN
            proposal.reasons = ["level unclear: loose tracks of one artist, not one album"] + proposal.level_reasons
            return proposal
        names = [n for n in _unique([local.artist, target.parent_name if parent_is_artist else ""]) if not is_generic_name(n)]
        soundtrack = is_soundtrack_like(local.title, local.genres, lookup_name(parent.name) if not parent_is_artist else "")
        if not local.titles:
            proposal.reasons.append("the folder has no usable album title")
            return proposal

        # 1. who is the artist (verified through the sibling albums too)
        resolution: Optional[_Resolution] = None
        if names:
            titles, years = self._sibling_titles(target, local) if parent_is_artist else ([local.titles], [local.year])
            collab = split_collab(names[0])
            resolution = self.resolve_artist(names, titles, years, collab[:1] if len(collab) > 1 else [])
            proposal.problems += resolution.problems
        mb_artist = resolution.mb_ok if resolution else None

        # 2. the release group: the verified artist's own discography first, then a search
        scored: list[tuple[nmb.ReleaseGroup, "object"]] = []
        pool: list[nmb.ReleaseGroup] = []
        if mb_artist is not None:
            pool = [g for g in mb_artist.groups if any(titles_match(t, g.title) for t in local.titles)]
            proposal.reasons.append(f"artist verified through MusicBrainz: {mb_artist.evaluation.name}")
        if not pool:
            searched: list[nmb.ReleaseGroup] = []
            for title in local.titles[:2]:
                searched = nmb.search_release_groups(
                    names[0] if names else "", title, self._mb, primary_album=True, soundtrack=soundtrack)
                if not searched:
                    searched = nmb.search_release_groups(
                        names[0] if names else "", title, self._mb, primary_album=False, soundtrack=soundtrack)
                if searched:
                    break
            pool = searched
        artist_names = names or [""]
        for group in pool:
            scored.append((group, score_release_group(
                local.titles, local.year, artist_names, group.title, group.year, group.primary, group.secondary,
                group.artist, group.id)))
        scored.sort(key=lambda s: (s[1].title_match, s[1].score), reverse=True)
        for group, _score in scored:
            self._seen_groups[group.id] = group
        for group, score in scored[:MAX_CANDIDATES]:
            proposal.candidates.append(Candidate(
                "musicbrainz", group.id, f"{group.artist} - {group.title}" if group.artist else group.title,
                ", ".join(x for x in (group.primary, *group.secondary, group.year) if x), [], score.score,
                "; ".join(score.reasons)))

        best = scored[0] if scored else None
        discogs_info: Optional[nd.DiscogsAlbumInfo] = None
        discogs_hits: list = []
        client = self._discogs
        weak = mb_artist is None or best is None or not best[1].title_match or best[1].score < 0.75
        wanted_discogs = (self.discogs_mode == "always"
                          or (self.discogs_mode == "fallback"
                              and (weak or (force is not None and force.source in ("discogs", "wikipedia")))))
        if client is not None and not client.rate_limited and names and wanted_discogs:
            try:
                discogs_hits = nd.search_album(client, names[0], local.titles[0], local.year, limit=8)
                if discogs_hits and discogs_hits[0].score >= 0.9:
                    discogs_info = nd.release_info(client, discogs_hits[0].id, discogs_hits[0].score)
            except DiscogsError as exc:
                proposal.problems.append(str(exc))
        if force is not None and force.source in ("discogs", "wikipedia"):
            self._album_candidates_from_discogs(proposal, discogs_hits, discogs_info)
            if force.source == "discogs":
                return self._discogs_album(proposal, local, force, names, soundtrack)
            return self._wikipedia_album(proposal, local, force, names, soundtrack)

        confident_group = None
        chosen_group = self._seen_groups.get(force.id) if force is not None and force.source == "musicbrainz" else None
        if chosen_group is not None:
            confident_group = chosen_group
            proposal.reasons.append(f"{_chosen(force)}: {force.name}")
        elif best is not None:
            group, score = best
            rivals = [g for g, s in scored[1:] if s.title_match and abs(s.score - score.score) < 0.05
                      and g.primary == group.primary and g.year == group.year]
            relaxed = mb_artist is None and not names  # a compilation / soundtrack: no artist to check
            artist_ok = mb_artist is not None or (names and score.score >= 0.75) or (relaxed and score.title_match)
            if not score.title_match:
                proposal.reasons.append(f"no release group is called '{local.titles[0]}'")
            elif score.score < 0.75:
                proposal.reasons.append(f"best release group '{group.title}' fits poorly ({score.score:.2f})")
            elif rivals:
                proposal.reasons.append(f"{len(rivals) + 1} release groups fit equally well")
            elif not artist_ok:
                proposal.reasons.append("the artist could not be verified")
            else:
                confident_group = group
        if confident_group is None and discogs_info is not None and best is None:
            proposal.reasons.append(f"only Discogs knows this album ({discogs_info.release.title})")
        if confident_group is None:
            proposal.confidence = Confidence.UNCERTAIN if (proposal.candidates or discogs_info) else Confidence.NONE
            self._album_candidates_from_discogs(proposal, discogs_hits, discogs_info)
            if not proposal.candidates and not proposal.problems:
                self._offer_wikipedia(proposal, "album", local.title, names[0] if names else "", [], soundtrack)
            return proposal

        return self._finish_album(proposal, local, confident_group, mb_artist, discogs_info, names, soundtrack,
                                  mb_artist is None and not names,
                                  forced=chosen_group is not None and not isinstance(force, GuessCandidate))

    def _finish_album(
        self, proposal: Proposal, local: LocalAlbum, group: nmb.ReleaseGroup, mb_artist: Optional[_ArtistFound],
        discogs_info: Optional[nd.DiscogsAlbumInfo], names: list[str], soundtrack: bool = False,
        relaxed: bool = False, forced: bool = False,
    ) -> Proposal:
        group_detail: dict = {}
        release: Optional[nmb.ReleaseDetail] = None
        try:
            group_detail = nmb.release_group_detail(group.id, self._mb)
            summaries = nmb.releases_from_group_detail(group_detail)
            if summaries is None:  # a reply without the releases: the separate list
                summaries = nmb.releases_of_group(group.id, self._mb)
            first = (group_detail.get("first-release-date") or group.first_date) if group_detail else group.first_date
            chosen = _pick_release(summaries, local.track_count, first)
            if chosen is not None:
                release = nmb.fetch_release_detail(chosen.id, self._mb)
        except nmb.MusicBrainzError as exc:
            # a thin note written because a service was busy would say "no tracklist" wrongly: try again later
            proposal.problems.append(str(exc))
            proposal.confidence = Confidence.UNCERTAIN
            proposal.reasons.append("MusicBrainz failed while the tracklist was fetched: not written, try again later")
            return proposal
        tracks = _tracks_from_mb(release) if release else []
        if not tracks and discogs_info is not None:
            tracks = [TrackInfo(t.disc, t.number, t.title) for t in discogs_info.release.tracks]
        # a folder that holds several tracks must really look like this release
        local_titles = [t.title for t in local.tracks if t.title]
        if tracks and len(local_titles) >= 3:
            overlap = track_overlap(local_titles, [t.title for t in tracks])
            if overlap < 0.3 and not forced:
                proposal.confidence = Confidence.UNCERTAIN
                proposal.reasons.append(f"only {round(overlap * 100)}% of the track titles match this release")
                return proposal
            proposal.reasons.append(f"{round(overlap * 100)}% of the local track titles are on the release")
        if relaxed and not forced:  # no artist to check: the folder itself must look like this release
            overlap_ok = len(local_titles) >= 3 and track_overlap(local_titles, [t.title for t in tracks]) >= 0.6
            if not (overlap_ok or (group.year and group.year == local.year)):
                proposal.confidence = Confidence.UNCERTAIN
                proposal.reasons.append("no artist to check and neither the year nor the tracks confirm this release")
                return proposal
        proposal.confidence = Confidence.CONFIDENT
        sources = ["musicbrainz"]
        footer = [note_text.musicbrainz_credit()]
        proposal.reasons.append(f"release group '{group.title}' ({group.primary}{', ' + group.year if group.year else ''})")

        artist_name = (mb_artist.evaluation.name if mb_artist else "") or group.artist or (names[0] if names else "")
        title = group.title
        if fold_name(title) == fold_name(local.title):
            title = local.title
        # introduction
        intro, wiki_url = "", ""
        if self.fetchers.wikipedia:
            hint_title, wikidata = nmb.wikipedia_hint(group_detail) if group_detail else ("", "")
            try:
                if soundtrack:  # the soundtrack's article, or the film / game / series it belongs to
                    page = wp.find_page(
                        "soundtrack", work_title(title), linked_title=hint_title, wikidata_id=wikidata,
                        fetch=self._wiki, notes=proposal.reasons)
                else:
                    page = wp.find_page(
                        "album", title, artist=artist_name, linked_title=hint_title, wikidata_id=wikidata,
                        fetch=self._wiki, notes=proposal.reasons)
            except wp.WikipediaError as exc:
                page = None
                proposal.problems.append(str(exc))
            if page is not None:
                intro, wiki_url = wp.trim_extract(page.extract), page.url or ""
                footer.append(note_text.wikipedia_credit(page.title))
                sources.append("wikipedia")
                proposal.reasons.append(f"Wikipedia: '{page.title}' (found by {page.via})")
        sidecar = sidecar_for(local.folder)
        nfo_genres: list[str] = []
        if sidecar:
            from redactor_common.core.note_convert import from_nfo

            converted = from_nfo(sidecar)
            if converted.ok:
                if not intro and _prose(converted.draft.body):
                    intro = _prose(converted.draft.body)
                    footer.append(f"Taken from {converted.draft.source}")
                    sources.append("sidecar")
                nfo_genres = converted.draft.front.genres
        # credits: MusicBrainz' structured ones, else Discogs'
        credits = release.credits if release and not release.credits.empty else None
        if credits is None and discogs_info is not None and not discogs_info.credits.empty:
            credits = discogs_info.credits
            sources.append("discogs")
            footer.append(note_text.discogs_credit("release", discogs_info.release.id).replace("Profile from", "Credits from"))
        label = (release.label if release else "") or (discogs_info.release.label if discogs_info else "")
        catno = (release.catno if release else "") or (discogs_info.release.catno if discogs_info else "")
        country = (release.country if release else "") or (discogs_info.release.country if discogs_info else "")
        released = (group_detail.get("first-release-date") if group_detail else "") or group.first_date or (release.date if release else "")
        genres = nmb.genres_of(group_detail) if group_detail else []
        if not genres and discogs_info is not None:
            genres = [g.lower() for g in discogs_info.release.genres + discogs_info.release.styles][:6]
        genres = genres or nfo_genres
        if soundtrack and "soundtrack" not in [g.casefold() for g in genres]:
            genres = [*genres, "soundtrack"]
        facts = [
            ("Artist", artist_name),
            ("Released", released),
            ("Label", f"{label} ({catno})" if label and catno else label),
            ("Country", country),
        ]
        links = [wiki_url, f"https://musicbrainz.org/release-group/{group.id}"]
        proposal.note_text = render_album_note(
            title=title, album=local, years=group.year or local.year, genres=genres, links=[l for l in links if l],
            intro=intro, facts=facts, credits=credits, tracks=tracks, footer=footer)
        proposal.title = title
        proposal.sources = sources
        return proposal


_GENRE_SPLIT = re.compile(r"\s*(?:,|/|\+|&|\band\b)\s*", re.IGNORECASE)


def split_genre_name(name: str) -> list[str]:
    """'Ska & Ska Punk' -> ['Ska', 'Ska Punk']; a name with no separator, or that would leave a part empty, comes back whole.
    Only a CANDIDATE split: the engine uses it only when every part verifies as its own genre."""
    parts = [p.strip() for p in _GENRE_SPLIT.split(name or "") if p.strip()]
    return parts if len(parts) > 1 else [name]


def _seconds(text: str) -> Optional[float]:
    """'3:45' / '1:02:03' as seconds, None for anything else."""
    parts = text.strip().split(":") if text else []
    if not parts or not all(p.isdigit() for p in parts) or len(parts) > 3:
        return None
    total = 0
    for p in parts:
        total = total * 60 + int(p)
    return float(total) or None


def _local_tracks(local: LocalAlbum) -> list[TrackInfo]:
    """The tracklist read from the files: tag title (else the file name), number and disc from the tags, in
    disc / track order; a file with no number is numbered after the numbered ones."""
    ordered = sorted(local.tracks, key=lambda t: (t.disc or 1, t.track if t.track else 9999, t.path.name.casefold()))
    out, counter = [], {}
    for t in ordered:
        disc = t.disc or 1
        counter[disc] = counter.get(disc, 0) + 1
        out.append(TrackInfo(disc, t.track or counter[disc], t.title or t.path.stem,
                             t.seconds if t.seconds and t.seconds >= 1 else None))
    return out


_COUNTRY_RANK = {"GB": 0, "US": 1, "XW": 2, "XE": 2}  # the usual home releases before a regional pressing


def _pick_release(releases: list[nmb.ReleaseSummary], local_count: int, first_date: str) -> Optional[nmb.ReleaseSummary]:
    """The release whose tracklist the folder is compared with: an official one, the track count nearest the
    folder's when the folder holds a few files (a one-file folder says nothing about the count), the one dated
    like the album's first release, a GB / US / worldwide one before a regional pressing, then the oldest."""
    if not releases:
        return None
    official = [r for r in releases if r.status.casefold() == "official"] or releases

    def key(r: nmb.ReleaseSummary):
        gap = abs(r.track_count - local_count) if local_count >= 3 and r.track_count else 0
        same = 0 if first_date and r.date == first_date else 1 if first_date[:4] and r.date[:4] == first_date[:4] else 2
        return (gap, r.track_count == 0, same, _COUNTRY_RANK.get(r.country, 3), r.date or "9999", r.id)

    return sorted(official, key=key)[0]


# ---------------------------------------------------------------------------
# Running it
# ---------------------------------------------------------------------------

def selection_scope(files: Iterable[str | os.PathLike], loader: Optional[Loader] = None) -> list[Path]:
    """The folders that get a note proposal for these selected MP3 files: each file's ALBUM folder and the ARTIST
    folder above it. The artist folder is included only when it is an artist level by the engine's own test
    (core/notes_level.py: it holds album folders and they are tagged as that artist's); a genre or collection
    folder, 'Various Artists', a folder that is not named like an artist and an unclear level never get an
    artist note from here. Tracks sitting directly in an artist folder give that artist folder. Order of first
    appearance, each folder once."""
    out: list[Path] = []
    seen: set[str] = set()

    def add(folder: Path) -> None:
        key = os.path.normcase(str(folder))
        if key not in seen:
            seen.add(key)
            out.append(folder)

    for file in files:
        album = album_folder_of(file)
        add(album)
        parent = artist_folder_above(album, loader)
        if parent is not None:
            add(parent)
    return out


def artist_folder_above(album_folder: Path, loader: Optional[Loader] = None) -> Optional[Path]:
    """The artist folder directly above `album_folder`, or None when the folder above is not an artist level."""
    parent = album_folder.parent
    if not parent.name or parent == album_folder or is_collection_name(parent.name) or is_generic_name(parent.name):
        return None
    if classify(parent) != KIND_ARTIST:
        return None
    local = read_artist(parent, loader)
    return parent if artist_level(local.name, local.albums).level == LEVEL_ARTIST else None


def artist_folder_of_file(file: str | os.PathLike, loader: Optional[Loader] = None) -> Path:
    """The folder to offer as 'the artist folder of this file': the artist level above its album folder, an
    artist folder the file sits in directly, else the album folder itself."""
    album = album_folder_of(file)
    if classify(album) == KIND_ARTIST:
        return album
    return artist_folder_above(album, loader) or album


def build_proposals(
    targets: list[Target], fetchers: Optional[Fetchers] = None, progress: Optional[Progress] = None,
    cancel: Optional[Callable[[], bool]] = None, loader: Optional[Loader] = None,
    on_proposal: Optional[Callable[[Proposal], None]] = None, engine: Optional[NotesEngine] = None,
    cache: Optional[ResponseCache] = None,
) -> list[Proposal]:
    """One Proposal per target, in order. `progress(done, total, label)` is called before each target;
    `cancel()` returning True ends the run early (the proposals built so far are returned); `on_proposal(p)` is
    called as each one is ready (the command line writes it there, so an interrupted run keeps what it did).
    Network only; this function writes no file."""
    engine = engine or NotesEngine(fetchers, loader, cache)  # a caller that keeps the engine can rebuild with its caches
    proposals: list[Proposal] = []
    for index, target in enumerate(targets):
        if cancel is not None and cancel():
            break
        if progress is not None:
            progress(index, len(targets), f"{target.kind}: {target.folder.name}")
        proposal = engine.build_proposal(target)
        proposals.append(proposal)
        if on_proposal is not None:
            on_proposal(proposal)
    if progress is not None and len(proposals) == len(targets):
        progress(len(targets), len(targets), "")
    return proposals


# ---------------------------------------------------------------------------
# Writing, the journal and its undo
# ---------------------------------------------------------------------------

def _sha(text: str) -> str:
    return text_sha(text)


def write_proposal(proposal: Proposal, dry_run: bool = False, allow_uncertain: bool = False) -> NoteWriteResult:
    """Writes the draft of a CONFIDENT proposal (the shared write_proposal: an uncertain / none one is refused with
    status 'skipped' unless a person chose it, an existing note is never overwritten). With the opt-in history on, a
    written note is recorded so it can be undone from Tools > History (undo_note_entry). A refresh sends the older note
    to the Recycle Bin, or to Preferences' backup folder on a drive without one (a network share); with neither it is
    refused and says so."""
    from core.recycle import note_mover

    return _write_proposal(proposal, dry_run, allow_uncertain, record=record_note_created, trash=note_mover())


def record_note_created(path: str, text: str, replaced: bool = False, old_bytes=None) -> None:
    """Journal entry for a written note when the opt-in history is on (otherwise nothing is touched, nothing stored). A
    created note is 'note'; a refresh is 'note-update' and keeps the replaced text in notes_backup next to the
    settings, so the undo can put it back. The entry keeps the SHA-256 of what was written so an undo can tell whether
    the person edited it since."""
    from core.history import _report, get_journal, history_enabled, notes_backup_base

    if not history_enabled():
        return
    try:
        record_note_entry(get_journal(), notes_backup_base(), path, text, replaced, old_bytes)
    except Exception as exc:  # noqa: BLE001 -- the note is already written; a journal failure never undoes it
        _report(exc)


def undo_note_entry(entry, journal=None) -> str:
    """Undoes a journal entry of a written note (the shared undo: Recycle Bin or the backup folder, only if it still
    holds exactly what was written; a refresh gets its older text back). Returns '' or a plain-language problem."""
    from core.history import get_journal, notes_backup_base
    from core.recycle import note_mover

    if journal is None:
        journal = get_journal()
    return _undo_note_entry(entry, journal, trash=note_mover(), backup_base=notes_backup_base())
