"""
core/mp3_duplicates.py

Analyze > Find Duplicates...: groups the loaded MP3s that may be the same
track, for the user to REVIEW through redactor_common's shared Find
Duplicates dialog -- nothing here changes or deletes a file.

Duplicates are not errors in a music library: a track legitimately sits on
its album, a compilation and a "best of"; live and remastered versions
exist beside the original. So every group carries a tier and a plain
reason saying WHY it matched, and the dialog selects nothing by default.

Phase 1 (this module, no external tool) uses three kinds of evidence:

1. IDENTICAL -- the audio payload is the same, tags ignored. The payload
   is the file minus its ID3v2 tag(s) at the start and its trailing
   ID3v1 / APE / Lyrics3 blocks, so retagging a file never changes its
   identity (and an edited audio stream always does). Cheap first: files
   are bucketed by payload LENGTH and only a bucket with 2+ files is
   hashed (redactor_common's sampled content_fingerprint); the sampled
   hash is a change detector, not a checksum, so equal samples are then
   confirmed with a hash of the whole payload before the word "identical"
   is used.
2. POSSIBLE -- the same MusicBrainz recording id (UFID). Same RECORDING,
   not necessarily the same release: it may be a compilation copy.
3. By tags -- same primary artist + same title + lengths within 2 s.
   Version words (live, remix, remaster, acoustic, demo, instrumental,
   edit, radio, extended, mix, mono, unplugged) stay significant:
   "Song (Live)" is not "Song". Same album (and track number): STRONG;
   album missing on some files: POSSIBLE; different albums: WEAK ("probably
   a compilation or another release"). A remaster next to its original is
   a separate WEAK group.

Evidence is combined: files linked by any rule share one group, shown at
the strongest tier with every reason that applies.

Phase 2 (not built): Chromaprint audio fingerprints to find the same
recording at a different bitrate/encode; needs fpcalc.

Pure logic -- no Qt.
"""

from __future__ import annotations

import hashlib
import os
import re
from collections import defaultdict
from typing import Callable, Optional, Sequence

from redactor_common.core.duplicates import (
    TIER_IDENTICAL,
    TIER_POSSIBLE,
    TIER_STRONG,
    TIER_WEAK,
    DuplicateGroup,
    DuplicateMember,
    tier_strength,
)
from redactor_common.core.local_db import normalize_words
from redactor_common.core.scan_stamp import content_fingerprint

from core.mp3_file import MP3File

DURATION_TOLERANCE = 2.0  # seconds
ID3V1_SIZE = 128
ID3V1_ENHANCED_SIZE = 227   # a "TAG+" block sits directly before the ID3v1 one
APE_BLOCK = 32              # APEv2 header and footer are each 32 bytes
LYRICS3_END = 15            # 6 size digits + "LYRICS200"
_HASH_CHUNK = 1024 * 1024

IDENTICAL_REASON = "identical audio (tags ignored)"
RECORDING_REASON = "same MusicBrainz recording -- may be on a different release/compilation"
DIFFERENT_ALBUM_NOTE = "same track on different albums -- probably a compilation or another release"
REMASTER_REASON = "same track, but one is a remaster -- probably a different release of it"

# Words that make two titles different versions of a song. Canonical form
# on the right ("remastered" and "remaster" count as one).
VERSION_WORDS = {
    "live": "live", "remix": "remix", "remixed": "remix", "remaster": "remaster",
    "remastered": "remaster", "acoustic": "acoustic", "demo": "demo",
    "instrumental": "instrumental", "edit": "edit", "radio": "radio",
    "extended": "extended", "mix": "mix", "mono": "mono", "unplugged": "unplugged",
}
_FEAT_BRACKET = re.compile(r"[\(\[]\s*(?:feat\.?|ft\.?|featuring)\s[^\)\]]*[\)\]]", re.IGNORECASE)
_FEAT_TAIL = re.compile(r"\s+(?:feat\.?|ft\.?|featuring)\s.*$", re.IGNORECASE)
_YEAR = re.compile(r"(?:19|20)\d\d$")


# --- the audio payload (tags excluded) ------------------------------------


def _id3v2_length(head: bytes, available: int) -> int:
    """Total bytes of the ID3v2 tag that `head` (>= 10 bytes) starts, 0 if
    it isn't a well-formed one that fits in `available` bytes."""
    if len(head) < 10 or head[:3] != b"ID3" or head[3] not in (2, 3, 4) or head[4] == 0xFF:
        return 0
    if any(b & 0x80 for b in head[6:10]):
        return 0   # not a synchsafe size
    size = (head[6] << 21) | (head[7] << 14) | (head[8] << 7) | head[9]
    total = 10 + size + (10 if head[3] == 4 and head[5] & 0x10 else 0)   # v2.4 footer flag
    return total if total <= available else 0


def audio_skip_ranges(path: str, size: Optional[int] = None) -> list[tuple[int, int]]:
    """The (start, end) byte ranges of `path` that are tags, not audio:
    ID3v2 tag(s) at the front, ID3v1 (+ "TAG+"), APEv2 and Lyrics3v2 blocks
    at the back. Raises OSError if the file can't be read."""
    size = os.path.getsize(path) if size is None else size
    ranges: list[tuple[int, int]] = []
    with open(path, "rb") as fh:
        start = 0
        for _ in range(4):   # some tools stack several ID3v2 tags
            fh.seek(start)
            length = _id3v2_length(fh.read(10), size - start)
            if not length:
                break
            start += length
        end = size
        for _ in range(8):
            if end - start >= ID3V1_SIZE:
                fh.seek(end - ID3V1_SIZE)
                if fh.read(3) == b"TAG":
                    end -= ID3V1_SIZE
                    if end - start >= ID3V1_ENHANCED_SIZE:
                        fh.seek(end - ID3V1_ENHANCED_SIZE)
                        if fh.read(4) == b"TAG+":
                            end -= ID3V1_ENHANCED_SIZE
                    continue
            if end - start >= APE_BLOCK:
                fh.seek(end - APE_BLOCK)
                footer = fh.read(APE_BLOCK)
                if footer[:8] == b"APETAGEX":
                    tag_size = int.from_bytes(footer[12:16], "little")   # items + footer
                    has_header = bool(int.from_bytes(footer[20:24], "little") & 0x80000000)
                    total = tag_size + (APE_BLOCK if has_header else 0)
                    if APE_BLOCK <= tag_size and total <= end - start:
                        end -= total
                        continue
            if end - start >= LYRICS3_END:
                fh.seek(end - LYRICS3_END)
                tail = fh.read(LYRICS3_END)
                if tail[6:] == b"LYRICS200" and tail[:6].isdigit():
                    total = int(tail[:6]) + LYRICS3_END
                    if total <= end - start:
                        end -= total
                        continue
            break
    if start:
        ranges.append((0, start))
    if end < size:
        ranges.append((end, size))
    return ranges


def audio_length(size: int, ranges: Sequence[tuple[int, int]]) -> int:
    return size - sum(end - start for start, end in ranges)


def audio_fingerprint(path: str, ranges: Sequence[tuple[int, int]]) -> str:
    """redactor_common's sampled content fingerprint of the audio payload
    ("" if unreadable). A tag edit leaves it unchanged."""
    return content_fingerprint(path, ranges)


def full_audio_hash(path: str, ranges: Sequence[tuple[int, int]], cancelled: Callable[[], bool] = lambda: False) -> str:
    """SHA-256 over the whole audio payload ("" if unreadable or cancelled)."""
    try:
        size = os.path.getsize(path)
        spans, cursor = [], 0
        for start, end in sorted(ranges):
            if start > cursor:
                spans.append((cursor, start))
            cursor = max(cursor, end)
        if cursor < size:
            spans.append((cursor, size))
        digest = hashlib.sha256()
        with open(path, "rb") as fh:
            for start, end in spans:
                fh.seek(start)
                left = end - start
                while left > 0:
                    if cancelled():
                        return ""
                    chunk = fh.read(min(_HASH_CHUNK, left))
                    if not chunk:
                        return ""
                    digest.update(chunk)
                    left -= len(chunk)
        return digest.hexdigest()
    except OSError:
        return ""


# --- tag normalisation ------------------------------------------------------


def primary_artist_key(artist: str) -> str:
    """The comparison key of the main artist: "Artist feat. X" -> "artist",
    a leading "The" ignored, punctuation/case/accents folded."""
    text = _FEAT_BRACKET.sub(" ", artist or "")
    text = _FEAT_TAIL.sub("", text)
    key = normalize_words(text)
    return key[4:] if key.startswith("the ") else key


def title_key(title: str) -> tuple[str, frozenset]:
    """(base words, version words) of a title. "(feat. X)" is dropped; the
    version words (live, remix, remastered ...) are separated out but kept
    significant -- compare BOTH parts. A year next to a version word
    ("Remastered 2011", "2009 Remaster") belongs to the version."""
    text = _FEAT_BRACKET.sub(" ", title or "")
    text = _FEAT_TAIL.sub("", text)
    words = normalize_words(text).split()
    versions, drop = set(), set()
    for i, word in enumerate(words):
        canon = VERSION_WORDS.get(word)
        if canon:
            versions.add(canon)
            drop.add(i)
            for j in (i - 1, i + 1):
                if 0 <= j < len(words) and _YEAR.fullmatch(words[j]):
                    drop.add(j)
    return " ".join(w for i, w in enumerate(words) if i not in drop), frozenset(versions)


# --- grouping ---------------------------------------------------------------


class _Evidence:
    """Links between files (union-find) plus the (tier, reason) behind each
    link; `components()` yields the connected sets with their evidence."""

    def __init__(self, count: int):
        self.parent = list(range(count))
        self.links: list[tuple[list[int], str, str]] = []

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def link(self, members: Sequence[int], tier: str, reason: str) -> None:
        if len(members) < 2:
            return
        self.links.append((list(members), tier, reason))
        root = self.find(members[0])
        for m in members[1:]:
            self.parent[self.find(m)] = root

    def components(self):
        evidence: dict[int, list[tuple[str, str]]] = defaultdict(list)
        sets: dict[int, set[int]] = defaultdict(set)
        for members, tier, reason in self.links:
            root = self.find(members[0])
            sets[root].update(members)
            evidence[root].append((tier, reason))
        for root, members in sets.items():
            yield sorted(members), evidence[root]


def _combined(evidence: list[tuple[str, str]]) -> tuple[str, str]:
    """The strongest tier and a reason sentence naming every distinct
    reason: the top tier's first, the others after "also"."""
    best = min(tier_strength(t) for t, _r in evidence)
    top: list[str] = []
    rest: list[str] = []
    for tier, reason in evidence:
        bucket = top if tier_strength(tier) == best else rest
        if reason not in bucket:
            bucket.append(reason)
    rest = [r for r in rest if r not in top]
    tier = next(t for t, _r in evidence if tier_strength(t) == best)
    reason = "; ".join(top)
    if rest:
        reason += " (also: " + "; ".join(rest) + ")"
    return tier, reason


def _duration_clusters(indexes: list[int], files: Sequence[MP3File]) -> list[list[int]]:
    """Runs of files whose lengths follow each other within the tolerance
    (files with no known length can't be compared and are left out)."""
    timed = sorted((i for i in indexes if files[i].duration_seconds is not None), key=lambda i: files[i].duration_seconds)
    clusters: list[list[int]] = []
    for i in timed:
        if clusters and files[i].duration_seconds - files[clusters[-1][-1]].duration_seconds <= DURATION_TOLERANCE:
            clusters[-1].append(i)
        else:
            clusters.append([i])
    return [c for c in clusters if len(c) >= 2]


def _tag_link(cluster: list[int], files: Sequence[MP3File]) -> tuple[str, str]:
    """Tier and reason for files with the same artist, title and length."""
    albums = [normalize_words(files[i].album) for i in cluster]
    tracks = [(files[i].track or "").split("/")[0].strip().lstrip("0") for i in cluster]
    known = {a for a in albums if a}
    if len(known) > 1:
        commonest = max(known, key=albums.count)
        if albums.count(commonest) >= 2:
            # two copies on one album plus others elsewhere: judge the pair
            same_track = len({t for a, t in zip(albums, tracks) if a == commonest} - {""}) <= 1
            return (TIER_STRONG if same_track else TIER_POSSIBLE), (
                "same artist, title, album and length; some copies are on a different album -- "
                "probably a compilation or another release"
            )
        return TIER_WEAK, DIFFERENT_ALBUM_NOTE
    if len(known) == 1 and "" not in albums:
        if len({t for t in tracks if t}) > 1:
            return TIER_POSSIBLE, "same artist, title, album and length, but different track numbers"
        return TIER_STRONG, "same artist, title, album and length"
    if len(known) == 1:
        return TIER_POSSIBLE, "same artist, title and length; the album is missing on some files"
    return TIER_POSSIBLE, "same artist, title and length; no album tag"


def _duration_text(seconds: Optional[float]) -> str:
    if seconds is None:
        return ""
    total = int(round(seconds))
    return f"{total // 60}:{total % 60:02d}"


def _size_text(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{int(value)} B" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


COLUMNS = [
    ("artist", "Artist"), ("title", "Title"), ("album", "Album"), ("track", "Track"),
    ("duration", "Duration"), ("bitrate", "Bitrate"), ("size", "Size"), ("path", "Folder / File"),
]


def find_duplicate_groups(
    files: Sequence[MP3File],
    progress: Optional[Callable[..., None]] = None,
    cancelled: Optional[Callable[[], bool]] = None,
) -> list[DuplicateGroup]:
    """The groups of loaded files that may be duplicates (see the module
    docstring). Files that failed to load are ignored. `progress(done,
    total, label)` and `cancelled()` as redactor_common's
    run_find_duplicates supplies them; a cancelled run returns []."""
    progress = progress or (lambda *a, **k: None)
    cancelled = cancelled or (lambda: False)
    usable = [mp3 for mp3 in files if not mp3.load_error]
    n = max(1, len(usable))

    def report(fraction: float, label: str) -> None:
        progress(int(fraction * n), n, label)

    # 1. where the audio starts/ends, per file (a few small reads)
    sizes: list[int] = []
    ranges: list[Optional[list[tuple[int, int]]]] = []
    for pos, mp3 in enumerate(usable):
        if cancelled():
            return []
        report(0.4 * pos / n, f"Reading {mp3.filename}")
        try:
            size = os.path.getsize(mp3.path)
            sizes.append(size)
            ranges.append(audio_skip_ranges(str(mp3.path), size))
        except OSError:
            sizes.append(0)
            ranges.append(None)

    evidence = _Evidence(len(usable))

    # 2. identical audio: bucket by payload length, then sampled hash,
    #    then confirm with a full hash
    by_length: dict[int, list[int]] = defaultdict(list)
    for i, rng in enumerate(ranges):
        if rng is not None:
            by_length[audio_length(sizes[i], rng)].append(i)
    candidates = [i for bucket in by_length.values() if len(bucket) >= 2 for i in bucket]
    sampled: dict[str, list[int]] = defaultdict(list)
    for pos, i in enumerate(candidates):
        if cancelled():
            return []
        report(0.4 + 0.3 * pos / max(1, len(candidates)), f"Comparing audio of {usable[i].filename}")
        fp = audio_fingerprint(str(usable[i].path), ranges[i])
        if fp:
            sampled[fp].append(i)
    for same in (b for b in sampled.values() if len(b) >= 2):
        by_full: dict[str, list[int]] = defaultdict(list)
        for i in same:
            if cancelled():
                return []
            digest = full_audio_hash(str(usable[i].path), ranges[i], cancelled)
            if digest:
                by_full[digest].append(i)
        for exact in by_full.values():
            evidence.link(exact, TIER_IDENTICAL, IDENTICAL_REASON)

    # 3. same MusicBrainz recording
    by_recording: dict[str, list[int]] = defaultdict(list)
    for i, mp3 in enumerate(usable):
        rid = (mp3.musicbrainz_trackid or "").strip().lower()
        if rid:
            by_recording[rid].append(i)
    for same in by_recording.values():
        evidence.link(same, TIER_POSSIBLE, RECORDING_REASON)

    # 4. same artist + title + length
    buckets: dict[tuple, list[int]] = defaultdict(list)
    keys: dict[int, tuple[str, str, frozenset]] = {}
    for i, mp3 in enumerate(usable):
        if not (mp3.title or "").strip() or not (mp3.artist or "").strip():
            continue
        artist, (base, versions) = primary_artist_key(mp3.artist), title_key(mp3.title)
        if not artist or not base:
            continue
        keys[i] = (artist, base, versions)
        buckets[(artist, base, versions)].append(i)
    for same in buckets.values():
        for cluster in _duration_clusters(same, usable):
            tier, reason = _tag_link(cluster, usable)
            evidence.link(cluster, tier, reason)
    # a remaster beside its original: only the "remaster" word differs
    loose: dict[tuple, list[int]] = defaultdict(list)
    for i, (artist, base, versions) in keys.items():
        loose[(artist, base, versions - {"remaster"})].append(i)
    for same in loose.values():
        if len({"remaster" in keys[i][2] for i in same}) == 2:
            for cluster in _duration_clusters(same, usable):
                if len({"remaster" in keys[i][2] for i in cluster}) == 2:
                    evidence.link(cluster, TIER_WEAK, REMASTER_REASON)

    # 5. build the groups (fingerprints only for the files that matter)
    components = list(evidence.components())
    groups: list[DuplicateGroup] = []
    fingerprints: dict[int, str] = {}
    for done, (members, links) in enumerate(components):
        if cancelled():
            return []
        report(0.7 + 0.3 * done / max(1, len(components)), "Preparing the list")
        tier, reason = _combined(links)
        built = []
        for i in members:
            mp3 = usable[i]
            if i not in fingerprints:
                fingerprints[i] = audio_fingerprint(str(mp3.path), ranges[i]) if ranges[i] is not None else ""
            built.append(DuplicateMember(
                mp3, str(mp3.path),
                {
                    "artist": mp3.artist, "title": mp3.title, "album": mp3.album, "track": mp3.track,
                    "duration": _duration_text(mp3.duration_seconds),
                    "bitrate": f"{mp3.bitrate_kbps} kbps" if mp3.bitrate_kbps else "",
                    "size": _size_text(sizes[i]), "path": str(mp3.path),
                },
                fingerprints[i],
            ))
        groups.append(DuplicateGroup(f"mp3:{min(members)}", tier, reason, built))
    return groups
