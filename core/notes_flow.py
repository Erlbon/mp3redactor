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

import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

from core import notes_discogs as nd
from core.notes_cache import CachedDiscogs, CachedFetch, RequestStats, ResponseCache
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
    album_folder_of, fold_name, is_collection_name, is_generic_name, read_album, read_artist, scan, sidecar_for, split_year, Loader,
)
from core.notes_match import (
    Confidence, Evaluation, album_key, decide_artist, discography_hits, era_conflict, name_similarity,
    score_release_group, titles_match, track_overlap,
)
from core.notes_render import (
    H_OTHER, LOCAL_ONLY_CREDIT, TrackInfo, discogs_facts_credit, group_discogs_discography, group_discography,
    render_album_note, render_artist_note, render_genre_note,
)
from redactor_common.core import note_text
from redactor_common.core.notes import (
    STATUS_ERROR, STATUS_WRITTEN, NoteWriteResult, normalize_note_text, note_path, write_note,
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
STATUS_SKIPPED = "skipped"  # write_proposal's own status: an uncertain/none proposal is never written
KIND_NOTE = "note"  # the journal's kind for a created note (not a tag change: never "restorable" as one)
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
class Candidate:
    """One artist / release the engine considered, kept for the review page."""

    source: str  # "musicbrainz" / "discogs"
    id: str
    name: str
    detail: str = ""
    hits: list[str] = field(default_factory=list)  # local albums found in its discography
    score: float = 0.0
    note: str = ""

    def to_dict(self) -> dict:
        return {"source": self.source, "id": self.id, "name": self.name, "detail": self.detail,
                "hits": list(self.hits), "score": round(self.score, 3), "note": self.note}


@dataclass
class Proposal:
    target: Target
    confidence: Confidence = Confidence.NONE
    note_text: str = ""  # the draft; only built for a confident proposal
    title: str = ""
    reasons: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    candidates: list[Candidate] = field(default_factory=list)
    local: Optional[LocalArtist | LocalAlbum | LocalGenre] = None
    problems: list[str] = field(default_factory=list)  # network errors and the like, in plain words
    # The LEVEL decision (core/notes_level.py): 'artist', 'album', 'genre', 'collection' (an about.md folder found
    # from the tags: Soundtracks, Various Artists...) or 'unclear'. `suggested_levels` is what a review page may offer.
    level: str = ""
    level_reasons: list[str] = field(default_factory=list)
    suggested_levels: list[str] = field(default_factory=list)
    kind_override: str = ""  # the note kind when the level differs from the scan's guess (collection -> about)

    @property
    def folder(self) -> Path:
        return self.target.folder

    @property
    def kind(self) -> str:
        return self.target.kind

    @property
    def note_kind(self) -> str:
        """The note file's kind: artist / album / about."""
        return self.kind_override or self.target.note_kind

    @property
    def path(self) -> str:
        return note_path(self.target.folder, self.note_kind)

    @property
    def writable(self) -> bool:
        return self.confidence == Confidence.CONFIDENT and bool(self.note_text)

    def to_dict(self, with_text: bool = True) -> dict:
        out = {
            "folder": str(self.folder), "kind": self.kind, "note": self.path, "confidence": self.confidence.value,
            "title": self.title, "reasons": list(self.reasons), "sources": list(self.sources),
            "level": self.level, "level_reasons": list(self.level_reasons),
            "suggested_levels": list(self.suggested_levels), "note_kind": self.note_kind,
            "candidates": [c.to_dict() for c in self.candidates], "problems": list(self.problems),
        }
        if with_text:
            out["draft"] = self.note_text
        return out


@dataclass
class NotesSummary:
    total: int = 0
    confident: int = 0
    uncertain: int = 0
    none: int = 0
    written: int = 0
    would_write: int = 0
    exists: int = 0
    failed: int = 0
    not_written: int = 0  # uncertain / none: left for review
    cancelled: bool = False
    problems: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def summarize(
    proposals: list[Proposal], results: Optional[dict[str, NoteWriteResult]] = None, cancelled: bool = False,
) -> NotesSummary:
    """Counts for a report. `results` maps a note path to its write_proposal() result (dry-run results count as
    'would_write')."""
    summary = NotesSummary(total=len(proposals), cancelled=cancelled)
    for proposal in proposals:
        setattr(summary, proposal.confidence.value, getattr(summary, proposal.confidence.value) + 1)
        for problem in proposal.problems:
            if problem not in summary.problems:
                summary.problems.append(problem)
        result = (results or {}).get(proposal.path)
        if result is None:
            continue
        if result.status == STATUS_WRITTEN:
            if result.dry_run:
                summary.would_write += 1
            else:
                summary.written += 1
        elif result.status == "exists":
            summary.exists += 1
        elif result.status == STATUS_SKIPPED:
            summary.not_written += 1
        else:
            summary.failed += 1
    return summary


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
                 cache: Optional[ResponseCache] = None):
        self.fetchers = fetchers or Fetchers()
        self.loader = loader
        # The request layer (core/notes_cache.py): a per-engine memo (the same URL is never asked twice in a run) and,
        # when a ResponseCache is given, the persistent cache (a repeated or resumed run is answered from disk).
        self.stats = RequestStats()
        self._mb = CachedFetch(self.fetchers.mb or nmb._fetch(), "mb", cache, self.stats)
        self._wiki = CachedFetch(self.fetchers.wiki or wp.default_fetch(), "wiki", cache, self.stats)
        self._discogs = CachedDiscogs(self.fetchers.discogs, cache, self.stats) if self.fetchers.discogs else None
        self._artists: dict[tuple, _Resolution] = {}
        self._levels: dict[str, tuple[LocalArtist, LevelDecision]] = {}
        self._groups: dict[str, list[nmb.ReleaseGroup]] = {}
        self._all_groups: dict[str, list[nmb.ReleaseGroup]] = {}
        self._network_failures = 0
        self._seen_groups: dict[str, nmb.ReleaseGroup] = {}  # release groups offered as candidates, by id
        self._d_entries: dict[int, list[dict]] = {}  # Discogs artist release lists, by artist id

    # -- public ------------------------------------------------------------------------------------------

    def build_proposal(self, target: Target) -> Proposal:
        """One proposal; never raises for a service failure (the problem is in `problems`)."""
        if self._network_failures >= 2:
            return Proposal(target, Confidence.NONE, reasons=["skipped: the services could not be reached"],
                            problems=["could not reach MusicBrainz (repeated); the rest of the run was not looked up"])
        return self._dispatch(target)

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
            return "" if self.fetchers.discogs is not None else DISCOGS_MISSING
        if candidate.source == "wikipedia":
            return "" if self.fetchers.wikipedia else WIKIPEDIA_OFF
        return f"No note can be built from a {candidate.source} candidate."

    def best_guess(self, proposal: Proposal) -> Optional[Candidate]:
        """The candidate to use for an UNCERTAIN proposal when the person wants the engine's best guess: its top
        candidate the engine can build a note around. None (no guess) for a confident or nothing-found proposal,
        for a genre / collection folder, when the level is unclear (an artist folder whose level is not 'artist'),
        or when there is no usable candidate."""
        if proposal.confidence != Confidence.UNCERTAIN or proposal.kind not in (KIND_ARTIST, KIND_ALBUM):
            return None
        if proposal.kind == KIND_ARTIST and proposal.level != LEVEL_ARTIST:
            return None
        if proposal.kind == KIND_ALBUM and proposal.level not in (LEVEL_ALBUM, ""):
            return None
        for candidate in proposal.candidates:
            if self.can_choose(proposal, candidate):
                return candidate
        return None

    def rebuild(self, proposal: Proposal, candidate: Optional[Candidate] = None, level: str = "") -> Proposal:
        """The proposal again after a PERSON decided: `candidate` (one of proposal.candidates) is the artist /
        release group to use whatever the score said, and/or `level` (one of proposal.suggested_levels:
        'artist', 'album', 'genre-collection') is the level to build. A chosen candidate is believed: the result
        is 'confident' when the note could be built, and its reasons say it was chosen. Cached lookups are
        reused, so this is usually one or two requests. Never raises for a service failure. 'skip' is not an
        engine matter (the review page just leaves the folder out)."""
        self._network_failures = 0
        target = proposal.target
        kind = target.kind
        if level == LEVEL_LOCAL_TAGS:
            return self.local_note(proposal)
        if level == SUGGEST_ARTIST:
            kind = KIND_ARTIST
        elif level == SUGGEST_ALBUM:
            kind = KIND_ALBUM
        if kind != target.kind:
            target = Target(target.folder, kind, target.parent_name)
        return self._dispatch(target, candidate, level)

    def _dispatch(self, target: Target, force: Optional[Candidate] = None, force_level: str = "") -> Proposal:
        try:
            if target.kind == KIND_ARTIST:
                proposal = self._artist_proposal(target, force, force_level)
            elif target.kind == KIND_GENRE:
                proposal = self._genre_proposal(target)
            else:
                proposal = self._album_proposal(target, force, force_level)
        except nmb.MusicBrainzError as exc:
            proposal = Proposal(target, Confidence.NONE, reasons=["MusicBrainz failed"], problems=[str(exc)])
        except (wp.WikipediaError, DiscogsError) as exc:  # only reached when nothing was caught closer
            proposal = Proposal(target, Confidence.NONE, reasons=["a lookup failed"], problems=[str(exc)])
        if (proposal.kind in (KIND_ARTIST, KIND_ALBUM) and proposal.confidence != Confidence.CONFIDENT
                and not proposal.note_text and not proposal.candidates and proposal.level != LEVEL_UNCLEAR):
            if self.fetchers.discogs is None:
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
                self._all_groups[mbid] = nmb.browse_all_release_groups(mbid, self._mb)
            except nmb.MusicBrainzError:
                return fallback
        return self._all_groups[mbid]

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
                self._all_groups[mbid] = nmb.browse_all_release_groups(mbid, self._mb)
            self._groups[mbid] = [g for g in self._all_groups[mbid] if (g.primary or "").casefold() in ("album", "ep")]
        return self._groups[mbid]

    def resolve_artist(
        self, names: list[str], local_titles: list[list[str]], local_years: list[str],
        partial: Optional[list[str]] = None,
    ) -> _Resolution:
        """The artist behind a folder: candidates from MusicBrainz AND Discogs, each scored by how many of the
        local album titles it has in its discography (see core/notes_match.py)."""
        partial = [p for p in (partial or []) if p and fold_name(p) not in [fold_name(n) for n in names]]
        key = (tuple(fold_name(n) for n in names), tuple(fold_name(p) for p in partial), tuple(album_key(t) for names_ in local_titles for t in names_))
        if key in self._artists:
            return self._artists[key]
        res = _Resolution()
        self._artists[key] = res
        wanted = len([t for t in local_titles if t])
        # -- MusicBrainz
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
            for sim, _score, hit in scored[:MAX_CANDIDATES]:
                groups = self._discography(hit.id)
                found = discography_hits(local_titles, [g.title for g in groups])
                evaluation = Evaluation(
                    "musicbrainz", hit.id, hit.name, hits=found, name_sim=sim, search_score=hit.score,
                    era_conflict=era_conflict(hit.begin, hit.end, local_years),
                    detail=", ".join(x for x in (hit.disambiguation, hit.country, hit.begin[:4]) if x),
                )
                evaluations.append((evaluation, hit, groups))
                if sim == 1.0 and wanted and len(found) == wanted and not evaluation.era_conflict:
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
        # -- Discogs
        client = self._discogs
        if client is not None and not client.rate_limited:
            try:
                dhits = nd.search_artists(client, names[0])
                scored_d = sorted(
                    ((name_similarity(names, [h.name]), h) for h in dhits), key=lambda s: s[0], reverse=True)
                devals: list[tuple[Evaluation, nd.DiscogsArtistHit]] = []
                for sim, hit in [s for s in scored_d if s[0] >= 0.5][:3]:
                    entries = self._discogs_entries(client, hit.id)
                    found = discography_hits(local_titles, [e["title"] for e in entries])
                    evaluation = Evaluation("discogs", str(hit.id), hit.name, hits=found, name_sim=sim,
                                            search_score=0, detail=f"Discogs {hit.id}")
                    devals.append((evaluation, hit))
                    if sim == 1.0 and wanted and len(found) == wanted:
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

    def _artist_level(self, folder: Path) -> tuple[LocalArtist, LevelDecision]:
        """The artist folder read (cached) and the artist test applied to it (core/notes_level.py)."""
        key = os.path.normcase(str(folder))
        if key not in self._levels:
            local = read_artist(folder, self.loader)
            self._levels[key] = (local, artist_level(local.name, local.albums))
        return self._levels[key]

    def _forced_resolution(self, res: _Resolution, candidate: Candidate) -> _Resolution:
        """The resolution with the artist a person chose as its (confident) winner."""
        forced = _Resolution(candidates=list(res.candidates), problems=list(res.problems))
        evaluation = Evaluation(candidate.source, candidate.id, candidate.name, hits=list(candidate.hits),
                                name_sim=candidate.score, detail=candidate.detail)
        if candidate.source == "musicbrainz":
            forced.mb = _ArtistFound(evaluation, Confidence.CONFIDENT, groups=self._discography(candidate.id))
        else:
            forced.discogs = _ArtistFound(evaluation, Confidence.CONFIDENT)
        forced.reasons = [f"chosen by you: {candidate.name} ({candidate.source})"]
        if candidate.hits:
            forced.reasons.append(f"{len(candidate.hits)} local album(s) found in its discography: {', '.join(candidate.hits)}")
        return forced

    def _artist_proposal(self, target: Target, force: Optional[Candidate] = None, force_level: str = "") -> Proposal:
        local, decision = self._artist_level(target.folder)
        level = {SUGGEST_ARTIST: LEVEL_ARTIST, SUGGEST_COLLECTION: LEVEL_COLLECTION}.get(force_level, decision.level)
        proposal = Proposal(target, local=local, level=level, level_reasons=list(decision.reasons),
                            suggested_levels=list(decision.suggested))
        if level != decision.level:
            proposal.level_reasons.append(f"level chosen by you: {force_level}")
        if level == LEVEL_COLLECTION:
            return self._collection_proposal(proposal, local)
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
        if force is not None and force.source == "wikipedia":
            return self._wikipedia_artist(proposal, local, res, force, titles)
        if force is not None:
            res = self._forced_resolution(res, force)
        proposal.candidates = list(res.candidates)
        proposal.reasons += list(res.reasons)
        proposal.problems = list(res.problems)
        proposal.confidence = res.confidence
        if res.confidence != Confidence.CONFIDENT:
            if not res.candidates and not res.problems:
                proposal.reasons.append("no artist of that name was found")
            if not res.candidates and not res.problems:
                self._offer_wikipedia(proposal, "artist", local.name, "", [t for names_ in titles for t in names_])
            return proposal

        mb = res.mb_ok
        detail: dict = {}
        sources: list[str] = []
        footer: list[str] = []
        if mb is not None:
            try:
                detail = nmb.artist_detail(mb.evaluation.id, self._mb)
                sources.append("musicbrainz")
                footer.append(note_text.musicbrainz_credit())
            except nmb.MusicBrainzError as exc:
                proposal.problems.append(str(exc))
        info: Optional[nd.DiscogsArtist] = None
        if res.discogs_ok is not None and self.fetchers.discogs is not None:
            try:
                info = nd.artist_info(self._discogs, int(res.discogs_ok.evaluation.id))
                sources.append("discogs")
            except DiscogsError as exc:
                proposal.problems.append(str(exc))

        name = (detail.get("name") if detail else "") or (info.name if info else "") or local.name
        if fold_name(name) == fold_name(local.name):
            name = local.name
        # -- the introduction: Wikipedia, else Discogs' profile, else a sidecar's text
        intro, intro_credit, wiki_url = "", "", ""
        if self.fetchers.wikipedia:
            hint_title, wikidata = nmb.wikipedia_hint(detail) if detail else ("", "")
            try:
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

    def _genre_proposal(self, target: Target) -> Proposal:
        local = read_genre(target.folder)
        proposal = Proposal(target, local=local, level=LEVEL_GENRE, suggested_levels=[SUGGEST_COLLECTION, SUGGEST_SKIP])
        proposal.level_reasons = [f"{len(local.artists)} artist folder(s) directly below it"
                                  if local.artists else f"{len(local.albums)} album folder(s) directly below it"]
        return self._genre_like(proposal, local.name)

    def _collection_proposal(self, proposal: Proposal, local: LocalArtist) -> Proposal:
        """A folder found (from the tags / its name) to be a collection, not an artist: it gets about.md and each
        album folder below it is an album of its own, with no artist level."""
        proposal.kind_override = "about"
        return self._genre_like(proposal, local.name.lstrip("_").strip())

    def _genre_like(self, proposal: Proposal, name: str) -> Proposal:
        """Wikipedia's page about the music genre `name` (verified: its lead says it is a genre / style of music)
        (the note is that sentence and the credit; no list of what is below). No page verified = 'uncertain': never written in a batch run."""
        proposal.reasons = list(proposal.level_reasons)
        page = None
        if self.fetchers.wikipedia:
            try:
                page = wp.find_page("genre", name, fetch=self._wiki, notes=proposal.reasons)
            except wp.WikipediaError as exc:
                proposal.problems.append(str(exc))
        if page is None:
            proposal.confidence = Confidence.UNCERTAIN
            proposal.reasons.append(f"no Wikipedia page about a music genre called '{name}' could be verified")
            return proposal
        proposal.confidence = Confidence.CONFIDENT
        proposal.reasons.append(f"Wikipedia: '{page.title}' (found by {page.via})")
        proposal.sources = ["wikipedia"]
        proposal.title = name
        proposal.note_text = render_genre_note(
            name=name, intro=wp.trim_extract(page.extract), links=[page.url] if page.url else [],
            footer=[note_text.wikipedia_credit(page.title)])
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
        proposal.reasons.append(f"chosen by you: {force.name} (Discogs release {release.id})")
        local_titles = [t.title for t in local.tracks if t.title]
        if tracks and len(local_titles) >= 3:
            overlap = track_overlap(local_titles, [t.title for t in tracks])
            proposal.reasons.append(f"{round(overlap * 100)}% of the local track titles are on the release")
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
                       problems=list(proposal.problems), kind_override=proposal.kind_override)
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
                and names_agree(target.folder.name, local.artist)):
            proposal.level, proposal.suggested_levels = LEVEL_ARTIST, [SUGGEST_ARTIST, SUGGEST_ALBUM, SUGGEST_SKIP]
            proposal.level_reasons.append(f"{len(local.tag_titles)} different album tags in a folder named like the artist")
            proposal.confidence = Confidence.UNCERTAIN
            proposal.reasons = ["level unclear: loose tracks of one artist, not one album"] + proposal.level_reasons
            return proposal
        names = [n for n in _unique([local.artist, target.parent_name if parent_is_artist else ""]) if not is_generic_name(n)]
        soundtrack = is_soundtrack_like(local.title, local.genres, parent.name if not parent_is_artist else "")
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
        if client is not None and not client.rate_limited and names:
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
            proposal.reasons.append(f"chosen by you: {force.name}")
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
                                  mb_artist is None and not names, forced=chosen_group is not None)

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
    return hashlib.sha256(normalize_note_text(text).encode("utf-8")).hexdigest()


def write_proposal(proposal: Proposal, dry_run: bool = False, allow_uncertain: bool = False) -> NoteWriteResult:
    """Writes the draft of a CONFIDENT proposal (an uncertain / none one is refused with status 'skipped' unless
    a person chose it: `allow_uncertain`). Never overwrites (redactor_common's write_note: any existing .md note
    of any of the five names, any letter case, makes the result 'exists'). With the opt-in history on, a written
    note is recorded so it can be undone (undo_note_entry)."""
    if proposal.confidence != Confidence.CONFIDENT and not allow_uncertain:
        return NoteWriteResult(
            STATUS_SKIPPED, proposal.path, f"not written: the match is {proposal.confidence.value}", dry_run)
    if not proposal.note_text:
        return NoteWriteResult(STATUS_SKIPPED, proposal.path, "there is no draft to write", dry_run)
    result = write_note(proposal.folder, proposal.note_kind, proposal.note_text, dry_run=dry_run)
    if result.ok and not dry_run:
        record_note_created(result.path, proposal.note_text)
    return result


def record_note_created(path: str, text: str) -> None:
    """Journal entry for a created note when the opt-in history is on (otherwise nothing is touched). The entry
    keeps the SHA-256 of what was written so an undo can tell whether the person edited it since."""
    from core.history import _report, get_journal, history_enabled

    if not history_enabled():
        return
    try:
        get_journal().record(path, KIND_NOTE, {"note": ("", Path(path).name)}, note=f"sha256:{_sha(text)}")
    except Exception as exc:  # noqa: BLE001 -- the note is already written; a journal failure never undoes it
        _report(exc)


def _trash(path: str) -> None:
    """The Recycle Bin (redactor_common.core.trash); tests replace it."""
    from redactor_common.core.trash import move_to_trash

    move_to_trash(path)


def undo_note_entry(entry, journal=None) -> str:
    """Undoes a journal entry of a created note: the file goes to the Recycle Bin, but ONLY if it still holds
    exactly what was written (an edited note is the person's now). Returns '' or a plain-language problem."""
    if getattr(entry, "kind", "") != KIND_NOTE:
        return "this entry is not a created note"
    match = re.match(r"sha256:([0-9a-f]{64})$", getattr(entry, "note", "") or "")
    if not match:
        return "the entry has no checksum, so the file cannot be checked"
    path = entry.path
    if not os.path.isfile(path):
        return "the note is no longer there"
    try:
        with open(path, "rb") as handle:
            current = hashlib.sha256(handle.read()).hexdigest()
    except OSError as exc:
        return f"the note could not be read ({exc})"
    if current != match.group(1):
        return "the note was changed after it was created; it is left alone"
    try:
        _trash(path)
    except Exception as exc:  # noqa: BLE001 -- TrashError, OSError
        return f"the note could not be moved to the Recycle Bin ({exc})"
    if journal is None:
        from core.history import get_journal

        journal = get_journal()
    try:
        journal.mark_restored(entry.id, None)
    except Exception:  # noqa: BLE001 -- the undo itself happened
        pass
    return ""
