"""
core/acoustid_lookup.py

Identifies MP3s by their SOUND -- stage 2 of Import > Look Up via
MusicBrainz, for folders whose tags and names are too poor for the text
search: each file is fingerprinted with Chromaprint's fpcalc and looked
up on AcoustID (acoustid.org), which returns the MusicBrainz recordings
with that fingerprint and the releases they appear on.

fpcalc is an external tool like mp3val and keyfinder-cli: never bundled,
found on PATH or located via Tools > External Tools (official
builds per platform: github.com/acoustid/chromaprint/releases). Without
it the lookup simply works as before, from tags and folder names.

ACOUSTID_APP_KEY is this application's AcoustID key. Application keys
identify the app, not a user, and are meant to ship inside it (as
MusicBrainz Picard and beets ship theirs) -- no account access.
AcoustID asks for at most 3 requests per second.

AcoustID's links aren't perfect (a fingerprint can carry a wrongly
merged recording -- seen on the first real lookup: "Delia's Gone" under
Big River), so nothing here trusts one file's top hit: release
candidates are ranked by how many of the folder's files they contain,
and core/musicbrainz_lookup.py then matches file to track.
"""

from __future__ import annotations

import json
import subprocess
import urllib.parse
import urllib.request
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from core.subprocess_utils import run_tool
from core.version import APP_VERSION

ACOUSTID_APP_KEY = "1SFIKRwE2H"
API_URL = "https://api.acoustid.org/v2/lookup"
USER_AGENT = f"mp3redactor/{APP_VERSION} ( https://github.com/Erlbon/mp3redactor )"
FPCALC_EXE_NAME = "fpcalc.exe"
FPCALC_TIMEOUT_SECONDS = 120
MIN_REQUEST_INTERVAL = 0.34  # AcoustID: at most 3 requests per second
MIN_SCORE = 0.5  # AcoustID's own match score (0..1) below which a hit is ignored


class AcoustIdError(Exception):
    """fpcalc failed, or AcoustID couldn't be reached or refused the request."""


@dataclass
class Fingerprint:
    duration: int
    fingerprint: str


@dataclass
class RecordingHit:
    recording_id: str
    score: float
    release_ids: set[str] = field(default_factory=set)
    title: str = ""
    artists: str = ""


def fingerprint_file(path: Path, fpcalc: Path) -> Fingerprint:
    """fpcalc's fingerprint of one file (~0.25 s for a song)."""
    try:
        # run_tool(): UTF-8 output decoded with errors="replace" (text=True's
        # locale decoding could raise UnicodeDecodeError, aborting the batch).
        result = run_tool([str(fpcalc), "-json", str(path)], timeout=FPCALC_TIMEOUT_SECONDS)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AcoustIdError(f"fpcalc couldn't run: {exc}") from exc
    if result.returncode != 0:
        raise AcoustIdError(f"fpcalc failed: {(result.stderr or '').strip()[:200]}")
    try:
        data = json.loads(result.stdout)
        return Fingerprint(duration=int(round(float(data["duration"]))), fingerprint=data["fingerprint"])
    except (ValueError, KeyError, TypeError) as exc:
        raise AcoustIdError(f"fpcalc gave no fingerprint: {exc}") from exc


def _default_post(url: str, data: bytes) -> bytes:
    _POST_THROTTLE.wait()
    request = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def _make_throttle():
    from core.musicbrainz_lookup import _Throttle

    return _Throttle(MIN_REQUEST_INTERVAL)


_POST_THROTTLE = _make_throttle()


def lookup(fp: Fingerprint, post: Optional[Callable[[str, bytes], bytes]] = None,
           key: str = ACOUSTID_APP_KEY) -> list[RecordingHit]:
    """The MusicBrainz recordings AcoustID links to this fingerprint,
    best score first (hits under MIN_SCORE dropped)."""
    data = urllib.parse.urlencode({
        "client": key, "format": "json", "duration": fp.duration,
        "fingerprint": fp.fingerprint, "meta": "recordings releaseids",
    }).encode()
    try:
        raw = (post or _default_post)(API_URL, data)
    except OSError as exc:  # includes urllib's URLError/HTTPError
        body = getattr(exc, "read", lambda: b"")()
        raise AcoustIdError(f"AcoustID couldn't be reached: {_error_message(body) or exc}") from exc
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise AcoustIdError("AcoustID sent an unreadable answer") from exc
    if payload.get("status") != "ok":
        raise AcoustIdError(f"AcoustID: {_error_message(raw) or 'request refused'}")

    best: dict[str, RecordingHit] = {}
    for result in payload.get("results") or []:
        score = float(result.get("score") or 0)
        if score < MIN_SCORE:
            continue
        for recording in result.get("recordings") or []:
            rid = recording.get("id")
            if not rid:
                continue
            hit = best.get(rid)
            if hit is None or score > hit.score:
                hit = best[rid] = RecordingHit(
                    recording_id=rid, score=score, title=recording.get("title", ""),
                    artists=", ".join(a.get("name", "") for a in recording.get("artists") or []),
                )
            hit.release_ids |= {r["id"] for r in recording.get("releases") or [] if r.get("id")}
    return sorted(best.values(), key=lambda h: -h.score)


def _error_message(raw: bytes) -> str:
    try:
        return (json.loads(raw).get("error") or {}).get("message", "")
    except (ValueError, AttributeError):
        return ""


def identify_files(
    paths: list[Path], fpcalc: Path, post: Optional[Callable] = None,
) -> tuple[list[list[RecordingHit]], list[str]]:
    """Fingerprints and looks up every file: (hits per file, problems).
    A file that can't be fingerprinted or looked up gets no hits (the
    others still count); an unusable key stops at once."""
    hits, problems = [], []
    for path in paths:
        try:
            hits.append(lookup(fingerprint_file(path, fpcalc), post))
        except AcoustIdError as exc:
            if "invalid API key" in str(exc):
                raise
            hits.append([])
            problems.append(f"{path.name}: {exc}")
    return hits, problems


def candidate_releases(hits_per_file: list[list[RecordingHit]], limit: int = 4) -> list[str]:
    """MusicBrainz release ids most likely to be the folder's album:
    those containing the most of its files, then the highest total
    fingerprint score."""
    covered: dict[str, int] = defaultdict(int)
    strength: dict[str, float] = defaultdict(float)
    for hits in hits_per_file:
        best_per_release: dict[str, float] = {}
        for hit in hits:
            for release_id in hit.release_ids:
                best_per_release[release_id] = max(best_per_release.get(release_id, 0.0), hit.score)
        for release_id, score in best_per_release.items():
            covered[release_id] += 1
            strength[release_id] += score
    ranked = sorted(covered, key=lambda rid: (-covered[rid], -strength[rid], rid))
    return ranked[:limit]
