"""
core/redact_steps.py

The steps behind the "Redact" button (redactor_common's pipeline engine,
core/pipeline.py): run a recipe on each loaded/selected file with no
operator input and leave a corrected file IN PLACE, the original in the
Recycle Bin. Qt-free; gui/main_window.py wires it to the menu/toolbar.

Every step wraps code the app already has (scan_service's tools, the
MusicBrainz/AcoustID and Discogs lookups, cover_art, the rename pattern) without the
dialogs. How one file flows:

  1. Mp3Ctx makes a WORKING COPY of the MP3File (what steps edit) and a
     scratch copy of the file in the same folder (what the external
     tools read and mp3val -f rewrites). The live row and the original
     file are not touched until the save step.
  2. Steps run. Measurements and fixes are APPLIED; guesses (online tag
     lookup, cover art) come back as SUGGESTIONs with a confidence, which
     the engine applies only at or above the recipe's threshold and lists
     as "Needs review" otherwise.
  3. The save stage (save_stage(), handed to the engine as `finalize`; it
     is not a step and is not listed in the recipe editor) writes the
     working copy's tags into the scratch copy
     (tag_writer.apply_tags_to_file), verifies it reloads with the same
     audio length and tags, then commit_in_place() swaps it in and sends
     the original to the Recycle Bin.
  4. The "last" steps (rename, move into folders) act on the finished
     file. The engine runs `finalize` after them, so they call
     Mp3Ctx.save() first; it saves once and the save stage reports it.

A scan-based step never acts on a failed tool: TOOL ERROR/TOOL MISSING is
never stamped (MP3File.record_scan) and never triggers mp3val -f.
Steps that cannot do their job because something is not installed or the
network is down answer StepResult.nothing(note=...), which the engine lists
under NOTES in the report. A file that must not be touched (unsaved edits,
unreadable) makes its first step answer StepResult.skipped(...).
"""

from __future__ import annotations

import dataclasses
import difflib
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from redactor_common.core.move_plan import execute_move, plan_moves, render_relative_path
from redactor_common.core.os_utils import rename_no_clobber
from redactor_common.core.filename_parser import normalize_field_value
from redactor_common.core.local_db import LocalDatabaseError, normalize_words
from redactor_common.core.path_parser import PathParseResult, folder_value_counts, parse_path_detailed
from redactor_common.core.pipeline import (
    CommitError,
    FileReport,
    OptionSpec,
    Recipe,
    Step,
    StepResult,
    commit_in_place,
    effective_option_source,
)
from redactor_common.core.rename_pattern import render_filename, unique_path, zero_pad_numeric_value
from redactor_common.core.save_errors import describe_save_error
from redactor_common.core.trash import move_to_trash

from core import discogs_lookup as discogs
from core.acoustid_lookup import FPCALC_EXE_NAME, AcoustIdError, candidate_releases, identify_files
from core.bpm_detector import detect_bpm
from core.cover_art import find_folder_image, read_cover, sniff_mime
from core.ffmpeg_probe import deep_check_integrity, measure_loudness, probe_format
from core.fields import FIELDS
from core.keyfinder_runner import detect_key
from core.lock_retry import lock_hint, retry_on_lock
from core.mp3_file import (
    BASELINE_KEYS,
    MP3File,
    STATUS_ERROR,
    STATUS_OK,
    STATUS_TOOL_ERROR,
    STATUS_TOOL_MISSING,
    STATUS_WARNING,
)
from core.mp3val_runner import check_integrity, fix_integrity
from core.musicbrainz_local import (
    LocalMatch,
    LocalQuery,
    agreement as local_agreement,
    find_album_local,
    find_track_local,
    local_fields_for,
    open_database as open_musicbrainz_database,
)
from core.musicbrainz_lookup import (
    MusicBrainzError,
    ReleaseMatch,
    album_query,
    facts_from_tags,
    fetch_front_cover,
    fields_for,
    find_album,
    find_album_by_recordings,
    match_release,
    rank_matches,
)
from core.scan_service import REDACT_SCRATCH_PREFIX, reload_from_disk
from core.settings import DEFAULT_MOVE_PATTERN, Settings
from core.tag_reader import load_tags
from core.tag_writer import apply_tags_to_file
from core.tool_locator import find_tool

# Confidence of the two cover sources (see CoverStep). A folder image
# sitting next to the audio is what "Set Cover from Folder Image" uses
# today; the Cover Art Archive is asked with the file's own release id.
FOLDER_COVER_CONFIDENCE = 0.92
ONLINE_COVER_CONFIDENCE = 0.95
COVER_ART_SIZE = 500  # px, Cover Art Archive "front-500"

# The text-lookup fields that, all present, mean there is nothing for
# the lookup step to fill (the MusicBrainz ids alone are not worth a
# network round trip per file).
_LOOKUP_TRIGGER_FIELDS = ("title", "artist", "albumartist", "album", "track", "year")


# --- run environment -------------------------------------------------------


@dataclass
class RedactEnv:
    """What the steps of one Redact run share: the app's settings, the
    rename log, the trash function (injectable for tests) and the files
    in the run (an album lookup works on a whole folder, once)."""

    settings: Settings
    rename_log: object | None = None  # anything with .record(label, [(old, new)], **move_details)
    trash: Callable[[str], None] | None = None  # None: the Recycle Bin (move_to_trash)
    items: list[MP3File] = field(default_factory=list)
    cleaned: int = 0  # scratch files of an earlier, interrupted run removed by begin()
    # Discogs (DiscogsStep): how its requests are made. None = the real
    # network and the process-wide pace; tests inject a mocked fetch/throttle.
    discogs_fetch: Callable | None = None
    discogs_throttle: object | None = None
    _albums: dict = field(default_factory=dict)
    _path_counts: dict = field(default_factory=dict)  # (pattern, root) -> folder_value_counts of the run
    _discogs: dict = field(default_factory=dict)  # "client" -> the run's client (None: no token); (folder, query) -> lookup
    _discogs_releases: dict = field(default_factory=dict)  # Discogs release id -> release, fetched once per run
    _local: dict = field(default_factory=dict)  # "db" -> (local MusicBrainz database or None, note); (folder, fingerprint) -> lookup

    def begin(self, items) -> None:
        """Call before each run."""
        self.items = list(items)
        self._albums.clear()
        self._path_counts.clear()
        self._discogs.clear()
        self._discogs_releases.clear()
        self._local.clear()
        self.cleaned = remove_stale_scratch_files(self.items)


def remove_stale_scratch_files(items) -> int:
    """Deletes the hidden scratch copies (and mp3val's .bak of them) that a
    crashed run left in the folders of `items`; returns how many files.
    Only names with Redact's own prefix are touched. Another running copy
    of the app mid-Redact in the same folder would lose its scratch copy,
    which fails that one file safely (the original is never touched)."""
    removed = 0
    for folder in {os.path.dirname(os.path.realpath(str(m.path))) for m in items}:
        try:
            names = os.listdir(folder)
        except OSError:
            continue
        for name in names:
            if name.startswith(REDACT_SCRATCH_PREFIX) and name.endswith((".mp3", ".mp3.bak")):
                try:
                    os.remove(os.path.join(folder, name))
                    removed += 1
                except OSError:
                    pass
    return removed


# --- per-file context ------------------------------------------------------


class Mp3Ctx:
    """make_context() result for one file (see the module docstring).
    A file that must not be touched -- it failed to load, has unsaved edits
    (Redact works on what is on disk and would otherwise silently replace
    the edits) or can't be copied -- gets `skip_reason`, and Mp3Step /
    save_stage answer StepResult.skipped(skip_reason) for it."""

    def __init__(self, mp3: MP3File, env: RedactEnv):
        self.mp3 = mp3
        self.env = env
        self.original = os.path.realpath(mp3.path)  # work through a symlink, don't replace it
        self.work = dataclasses.replace(mp3)
        self.step_options: dict = {}  # set by the engine; read through Step.options_for(ctx)
        self.release_confidence: float | None = None  # set when the lookup step's guess was applied
        self.saved = False  # the save stage committed a new file
        self.save_failed = False
        self._save_done = False
        self._save_result: StepResult | None = None
        self.temp = ""
        self.skip_reason = ""
        if mp3.load_error:
            self.skip_reason = f"the file could not be read ({mp3.load_error})"
        elif mp3.dirty:
            self.skip_reason = "it has unsaved edits (save or undo them first)"
        if self.skip_reason:
            return
        try:
            fd, self.temp = tempfile.mkstemp(
                prefix=REDACT_SCRATCH_PREFIX, suffix=".mp3", dir=os.path.dirname(self.original)
            )
            os.close(fd)
            retry_on_lock(lambda: shutil.copy2(self.original, self.temp))  # keeps the permissions
        except OSError as exc:
            self._discard_temp()
            self.skip_reason = f"couldn't make a working copy ({describe_save_error(exc)}{lock_hint(exc)})"

    def save(self) -> StepResult | None:
        """Writes the working copy in place, once (later calls return the
        same result). None: nothing to write. The steps that act on the
        finished file call this first; save_stage() returns the result to
        the engine so it is reported."""
        if not self._save_done:
            self._save_done = True
            self._save_result = _save_working_copy(self)
        return self._save_result

    def restore_temp(self) -> None:
        """Back to the original's bytes (after a fix that failed midway)."""
        retry_on_lock(lambda: shutil.copy2(self.original, self.temp))

    def _discard_temp(self) -> None:
        for path in (self.temp, self.temp + ".bak"):
            if path:
                try:
                    os.remove(path)
                except OSError:
                    pass

    def close(self) -> None:
        """Engine hook, called after every file. Removes the scratch copy
        (the save step's commit already moved it on success). mp3val
        leaves a ".bak" of the scratch copy when the "delete backups after
        fixing" setting is off; it is the original's content, so after a
        successful save it is kept beside the new file as "<name>.bak",
        the same thing Fix Integrity does."""
        bak = self.temp + ".bak"
        if self.saved and os.path.isfile(bak) and not self.env.settings.delete_backup_after_fix:
            dest = str(self.mp3.path) + ".bak"
            if not os.path.lexists(dest):
                try:
                    os.replace(bak, dest)
                except OSError:
                    pass
        self._discard_temp()


# --- helpers ---------------------------------------------------------------


def _first_line(text: str) -> str:
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


def _problem_count(message: str) -> int:
    return len([line for line in (message or "").splitlines() if line.strip()])


def _existing_frame_text(path: str, frame_id: str) -> str:
    """Text of an ID3 frame already in the file ("" if none/unreadable)."""
    try:
        from mutagen.id3 import ID3

        frame = ID3(path).get(frame_id)
        return str(frame.text[0]).strip() if frame is not None and frame.text else ""
    except Exception:  # noqa: BLE001 -- unreadable tags just mean "nothing there"
        return ""


class Mp3Step(Step):
    """Base of this app's steps: a file whose context says to skip it
    (Mp3Ctx.skip_reason) is answered with StepResult.skipped before the
    step's own process() runs; the engine then stops that file."""

    def run(self, ctx: Mp3Ctx) -> StepResult:
        if ctx.skip_reason:
            return StepResult.skipped(ctx.skip_reason)
        return self.process(ctx)

    def process(self, ctx: Mp3Ctx) -> StepResult:
        raise NotImplementedError


# --- steps: checks and measurements ------------------------------------------


class IntegrityStep(Mp3Step):
    key = "integrity"
    label = "Integrity check (mp3val)"
    description = (
        "Checks the file with mp3val and stamps the result inside the file. With 'fix problems' on, "
        "fixable problems are repaired (on a copy -- the original goes to the Recycle Bin) and the "
        "file is checked again. A tool failure is never stamped and never triggers a fix."
    )
    options = (
        OptionSpec("fix", "Fix problems", "bool", True, tooltip="Run mp3val -f when the check finds problems."),
    )

    def process(self, ctx: Mp3Ctx) -> StepResult:
        work, settings = ctx.work, ctx.env.settings
        mp3val = settings.mp3val_path or None
        status, message = check_integrity(ctx.temp, override_path=mp3val)
        work.record_scan("integrity", status, message)  # TOOL MISSING/ERROR: shown, never stamped
        if status == STATUS_TOOL_MISSING:
            return StepResult.nothing(note=message)
        if status == STATUS_TOOL_ERROR:
            return StepResult.failed(message)
        if status == STATUS_OK:
            return StepResult.applied("OK (stamped)")

        problems = _problem_count(message)
        if not self.options_for(ctx)["fix"]:
            return StepResult.applied(f"{status}: {problems} problem(s), not fixed ({_first_line(message)})")

        fix_status, fix_message = fix_integrity(
            ctx.temp, delete_backup=settings.delete_backup_after_fix, override_path=mp3val
        )
        if fix_status in (STATUS_TOOL_MISSING, STATUS_TOOL_ERROR):
            ctx.restore_temp()  # a crashed fix may have half-rewritten the copy
            return StepResult.failed(f"mp3val couldn't fix it ({fix_message}); the file was left as it was")
        status, message = check_integrity(ctx.temp, override_path=mp3val)
        if status in (STATUS_TOOL_MISSING, STATUS_TOOL_ERROR):
            ctx.restore_temp()
            return StepResult.failed(f"couldn't re-check the file after fixing ({message}); left as it was")
        work.record_scan("integrity", status, message)
        remaining = _problem_count(message)
        if status == STATUS_OK:
            return StepResult.applied(f"fixed {problems} problem(s) with mp3val; OK now (stamped)")
        return StepResult.applied(
            f"fixed {max(problems - remaining, 0)} of {problems} problem(s) with mp3val; "
            f"{status}: {remaining} remain ({_first_line(message)})"
        )


class BpmStep(Mp3Step):
    key = "bpm"
    label = "Detect BPM"
    description = "Measures the tempo with aubio and writes it to the BPM tag."
    options = (
        OptionSpec(
            "replace", "Replace an existing BPM tag", "bool", False,
            tooltip="Off: a file that already has a BPM tag keeps it (measuring is skipped).",
        ),
    )

    def process(self, ctx: Mp3Ctx) -> StepResult:
        if not self.options_for(ctx)["replace"] and _existing_frame_text(ctx.temp, "TBPM"):
            return StepResult.nothing()
        bpm, status, message = detect_bpm(Path(ctx.temp))
        if status == STATUS_TOOL_MISSING:
            return StepResult.nothing(note=message)
        if status == STATUS_TOOL_ERROR:
            return StepResult.failed(message)  # aubio itself failed: nothing is written
        if bpm is None or status != STATUS_OK:
            # Mostly "not enough beats" (ambient, spoken word): a miss, not a failure.
            return StepResult.nothing(note=f"BPM not detected: {message}")
        work = ctx.work
        work.bpm, work.bpm_status, work.bpm_message = bpm, status, message
        work.dirty = True
        return StepResult.applied(f"{bpm:.1f} BPM")


class KeyStep(Mp3Step):
    key = "key"
    label = "Detect key"
    description = "Detects the musical key with keyfinder-cli and writes it to the Key tag."
    options = (
        OptionSpec(
            "replace", "Replace an existing Key tag", "bool", False,
            tooltip="Off: a file that already has a Key tag keeps it (detection is skipped).",
        ),
    )

    def process(self, ctx: Mp3Ctx) -> StepResult:
        if not self.options_for(ctx)["replace"] and _existing_frame_text(ctx.temp, "TKEY"):
            return StepResult.nothing()
        key, status, message = detect_key(
            Path(ctx.temp), override_path=ctx.env.settings.keyfinder_cli_path or None
        )
        if status == STATUS_TOOL_MISSING:
            return StepResult.nothing(note=message)
        if status != STATUS_OK:  # TOOL ERROR: nothing is written
            return StepResult.failed(message)
        if not key:
            return StepResult.nothing()  # silent audio: no key to write (nothing is cleared either)
        work = ctx.work
        work.key_value, work.key_status, work.key_message = key, status, message
        work.dirty = True
        return StepResult.applied(f"key {key}")


class LoudnessStep(Mp3Step):
    key = "loudness"
    label = "Measure loudness (ReplayGain)"
    description = "Measures loudness with ffmpeg and writes the ReplayGain track gain."

    def process(self, ctx: Mp3Ctx) -> StepResult:
        lufs, gain, status, message = measure_loudness(
            Path(ctx.temp), override_path=ctx.env.settings.ffmpeg_path or None
        )
        if status == STATUS_TOOL_MISSING:
            return StepResult.nothing(note=message)
        if status != STATUS_OK:  # TOOL ERROR: nothing is written
            return StepResult.failed(message)
        if gain is None:
            return StepResult.nothing()  # silent audio
        work = ctx.work
        work.loudness_lufs, work.loudness_gain_db = lufs, gain
        work.loudness_status, work.loudness_message = status, message
        work.dirty = True
        return StepResult.applied(f"{lufs:.1f} LUFS, gain {gain:+.2f} dB")


class DeepCheckStep(Mp3Step):
    key = "deep_check"
    label = "Deep check (ffmpeg full decode)"
    description = "Decodes the whole file with ffmpeg to find corrupt audio and stamps the result. Slow."
    default_enabled = False

    def process(self, ctx: Mp3Ctx) -> StepResult:
        settings, work = ctx.env.settings, ctx.work
        status, message = deep_check_integrity(Path(ctx.temp), override_path=settings.ffmpeg_path or None)
        work.record_scan("deep_check", status, message)
        if status == STATUS_TOOL_MISSING:
            return StepResult.nothing(note=message)
        if status == STATUS_TOOL_ERROR:
            return StepResult.failed(message)
        encoder, rate, channels, probe_status, _msg = probe_format(
            Path(ctx.temp), override_path=settings.ffprobe_path or None
        )
        if probe_status == STATUS_OK:
            work.audio_encoder, work.sample_rate_hz, work.channels = encoder, rate, channels
        if status == STATUS_OK:
            return StepResult.applied("OK (stamped)")
        return StepResult.applied(f"{status} (stamped): {_first_line(message)}")


# --- steps: guesses ----------------------------------------------------------


@dataclass
class TagFill:
    """What the tag lookup would write: field key -> new value."""

    fields: dict[str, str]

    def __str__(self) -> str:
        return ", ".join(f"{key}={value!r}" for key, value in self.fields.items())


@dataclass
class CoverSuggestion:
    data: bytes
    mime: str
    source: str

    def __str__(self) -> str:
        return f"embed {self.source} ({len(self.data) // 1024} KB)"


@dataclass
class FolderLookup:
    """One folder's MusicBrainz result, computed once per run."""

    files: list[MP3File]
    facts: list
    match: ReleaseMatch | None = None
    error: str = ""


def _norm(text: str) -> str:
    return re.sub(r"[^0-9a-z]+", " ", (text or "").casefold()).strip()


def _agreement(mp3: MP3File, release, track) -> float:
    """How far the file's own tags back up the match (1.0 = album and
    artist both agree). Together with the title/length/track score this
    is the confidence of a match made by tags; only an exact agreement
    can clear the default 90% threshold."""
    factor = 1.0 if _norm(mp3.album) == _norm(release.title) else 0.6
    artist = mp3.artist or mp3.albumartist
    if not artist:
        factor *= 0.85
    elif _norm(artist) not in (_norm(track.artist), _norm(release.artist)):
        factor *= 0.5
    return factor


def _lookup_folder(env: RedactEnv, folder: str, use_fingerprint: bool) -> FolderLookup:
    """Mirrors the Look Up via MusicBrainz dialog's search for one folder
    (tags/folder name first, AcoustID fingerprints when fpcalc is there),
    without the review. A failure is kept in .error so the run doesn't
    retry it for every file of the folder."""
    key = (folder, use_fingerprint)
    if key in env._albums:
        return env._albums[key]
    files = sorted(
        (m for m in env.items if not m.load_error and os.path.dirname(os.path.realpath(m.path)) == folder),
        key=lambda m: m.filename.casefold(),
    )
    result = FolderLookup(files=files, facts=[])
    env._albums[key] = result
    try:
        fpcalc = find_tool(FPCALC_EXE_NAME, override=env.settings.fpcalc_path or None) if use_fingerprint else None
        hits = [[] for _ in files]
        if fpcalc:
            hits, _problems = identify_files([m.path for m in files], fpcalc)
        facts = [facts_from_tags(m.title, m.track, m.discnumber, m.duration_seconds) for m in files]
        for fact, file_hits in zip(facts, hits):
            fact.recordings = {h.recording_id: h.score for h in file_hits}
        result.facts = facts
        query = album_query(
            [m.album for m in files], [m.albumartist or m.artist for m in files], Path(folder)
        )
        matches: list[ReleaseMatch] = []
        if query.artist or query.album:
            matches = find_album(facts, query)
        if any(hits) and (not matches or matches[0].matched < len(facts)):
            seen = {m.release.id for m in matches}
            ids = [rid for rid in candidate_releases(hits, limit=5) if rid not in seen]
            if ids:
                matches = rank_matches(matches + find_album_by_recordings(facts, ids), len(facts))
        if matches and matches[0].matched:
            result.match = matches[0]
    except (MusicBrainzError, AcoustIdError, OSError) as exc:
        result.error = str(exc)
    return result


# Field keys and numeric handling the path pattern shares with Parse Filename.
_PATH_FIELD_KEYS = {key for key, _label, _multiline in FIELDS}
_PATH_NUMERIC_FIELDS = {"track", "discnumber", "year"}


def _is_under(path: str, root: str) -> bool:
    try:
        path, root = os.path.normcase(os.path.abspath(path)), os.path.normcase(os.path.abspath(root))
        return os.path.commonpath([path, root]) == root and path != root
    except ValueError:  # another drive
        return False


def _parse_path(mp3: MP3File, pattern: str, root: str, corroborate=None) -> PathParseResult:
    return parse_path_detailed(
        str(mp3.path), pattern, root, _PATH_FIELD_KEYS, _PATH_NUMERIC_FIELDS,
        strip_leading_zeros_fields={"track"}, corroborate=corroborate,
    )


def _run_folder_counts(env: RedactEnv, pattern: str, root: str) -> dict:
    """First pass over the run's files (cheap string work, once per run):
    how many files share each folder value, so a folder that several files
    agree on scores higher."""
    key = (pattern, root)
    if key not in env._path_counts:
        results = [_parse_path(m, pattern, root) for m in env.items if not m.load_error]
        env._path_counts[key] = folder_value_counts(results)
    return env._path_counts[key]


class PathTagsStep(Mp3Step):
    key = "path_tags"
    label = "Fill empty tags from the folder path"
    description = (
        "Reads tags back out of the file's folders, the mirror of Move into folders: with the pattern "
        "%albumartist%/%album%/%track% - %title% and the library root set in Import > Parse Filename (or "
        "Rename / Export Files > Move into folders), Music/Queen/Jazz/03 - Fat Bottomed Girls.mp3 gives "
        "album artist, album, track and title. Only EMPTY tags are filled; existing values are never "
        "replaced. The confidence reflects how well the path fits the pattern (a folder shared by several "
        "files of the run counts for more); below the threshold the match is listed under Needs review. "
        "Does nothing until a library root is set, and only for files under it. Runs before the online "
        "tag lookup."
    )

    def __init__(self, settings: Settings | None = None, *, default_enabled: bool | None = None):
        super().__init__(default_enabled=True if default_enabled is None else default_enabled)
        self.options = (
            _pattern_option(
                "pattern", "Folder path pattern",
                "Kept as saved; clear it (Use fallback) to follow the last folder pattern used in Parse Filename or Move into folders. The last part matches the file name, the others the folders above it.",
                settings, (lambda: settings.saved_path_pattern()) if settings else (lambda: DEFAULT_MOVE_PATTERN),
                "the last folder pattern used in Parse Filename / Move into folders", _preview_path(settings),
            ),
        )

    def process(self, ctx: Mp3Ctx) -> StepResult:
        settings = ctx.env.settings
        root = settings.library_root
        if not root or not os.path.isdir(root):
            return StepResult.nothing(
                note="Folder-path tags skipped: no library root folder "
                "(choose one in Parse Filename with a path pattern, or in Rename / Export Files > Move into folders)"
            )
        if not _is_under(str(ctx.mp3.path), root):
            return StepResult.nothing()
        pattern, _source = resolve_pattern(self, ctx, settings.saved_path_pattern)
        counts = _run_folder_counts(ctx.env, pattern, root)
        result = _parse_path(
            ctx.mp3, pattern, root, lambda name, value: counts.get((name, normalize_field_value(value)), 0)
        )
        if not result.matched:
            return StepResult.nothing()
        fill = {key: value for key, value in result.values.items() if value and not getattr(ctx.work, key, "")}
        if not fill:
            return StepResult.nothing()
        reason = f"folder path matched {pattern!r}"
        if result.missing_segments:
            reason += f"; no match for {', '.join(result.missing_segments)}"
        if result.matched_segments:
            reason += "; matched " + ", ".join(f"{seg} = {name!r}" for seg, name in result.matched_segments)
        return StepResult.suggestion(TagFill(fill), result.confidence, reason)

    def apply_suggestion(self, ctx: Mp3Ctx, result: StepResult) -> list[str]:
        ctx.work.apply_tags(result.value.fields)
        return [f"{key} = {value!r}" for key, value in result.value.fields.items()]


# Confidence of a match made in the LOCAL MusicBrainz database (see TagLookupStep):
LOCAL_MBID_CONFIDENCE = 0.97  # the file's own MusicBrainz Album Id names this release
LOCAL_RECORDING_TAG_CONFIDENCE = 0.95  # the file's own recording id tag is on this release
LOCAL_FUZZY_CAP = 0.85  # a tag-only match that isn't artist+album exact with an equal track count: review
LOCAL_TRACK_ONLY_CAP = 0.8  # a single track found by title, no album to back it up
# The local database also knows these; a file with all of them (and the text fields) has nothing to fill.
_LOCAL_TRIGGER_FIELDS = _LOOKUP_TRIGGER_FIELDS + ("publisher", "catalognumber", "releasecountry")


@dataclass
class LocalFolderLookup:
    """One folder's result from the local MusicBrainz database, computed once per run."""

    files: list[MP3File]
    facts: list
    match: LocalMatch | None = None
    error: str = ""
    year: str = ""
    note: str = ""  # e.g. AcoustID unreachable: fingerprints skipped, the rest still works


@dataclass
class LocalFill:
    """The local lookup's suggestion for one file, before it is merged with an online one."""

    fields: dict[str, str]
    confidence: float
    reason: str
    note: str = ""  # an FYI for the report (AcoustID unreachable...)


def _local_database(env: RedactEnv):
    """(the local MusicBrainz database or None, note). None with no note when none is configured
    (the step then behaves exactly as before); a configured but unusable one is a note, not a failure."""
    if "db" not in env._local:
        path = env.settings.musicbrainz_database
        db, note = None, ""
        if path:
            try:
                db = open_musicbrainz_database(path)
            except LocalDatabaseError as exc:
                note = f"Local MusicBrainz database not used ({exc}); using online MusicBrainz"
        env._local["db"] = (db, note)
    return env._local["db"]


def _local_lookup_folder(env: RedactEnv, folder: str, use_fingerprint: bool, db) -> LocalFolderLookup:
    """The Look Up via MusicBrainz (Local Database) search for one folder, without the review:
    the files' own release ids first, then AcoustID recordings (online; skipped quietly when
    unreachable), then artist + album text -- all answered offline. A failure is kept in .error so
    the run doesn't retry it for every file of the folder."""
    key = (folder, use_fingerprint)
    if key in env._local:
        return env._local[key]
    files = sorted(
        (m for m in env.items if not m.load_error and os.path.dirname(os.path.realpath(m.path)) == folder),
        key=lambda m: m.filename.casefold(),
    )
    result = LocalFolderLookup(files=files, facts=[])
    env._local[key] = result
    try:
        hits = [[] for _ in files]
        fpcalc = find_tool(FPCALC_EXE_NAME, override=env.settings.fpcalc_path or None) if use_fingerprint else None
        if fpcalc:
            try:
                hits, _problems = identify_files([m.path for m in files], fpcalc)
            except (AcoustIdError, OSError) as exc:
                hits = [[] for _ in files]
                result.note = f"AcoustID unavailable ({exc}): fingerprints skipped"
        facts = [facts_from_tags(m.title, m.track, m.discnumber, m.duration_seconds) for m in files]
        for fact, file_hits in zip(facts, hits):
            fact.recordings = {h.recording_id: h.score for h in file_hits}
        result.facts = facts
        base = album_query([m.album for m in files], [m.albumartist or m.artist for m in files], Path(folder))
        years = [m.year[:4] for m in files if m.year]
        result.year = max(set(years), key=years.count) if years else ""
        query = LocalQuery(artist=base.artist, album=base.album, year=result.year)
        matches = find_album_local(
            db, facts, query,
            album_ids=tuple(m.musicbrainz_albumid for m in files if m.musicbrainz_albumid),
            recording_ids=[m.musicbrainz_trackid for m in files],
        )
        if matches and matches[0].matched:
            result.match = matches[0]
    except (LocalDatabaseError, OSError) as exc:
        result.error = str(exc)
    return result


def _local_confidence(mp3: MP3File, match: LocalMatch, track, fact) -> tuple[float, str]:
    """How sure a local match is for one file, and why:
    - AcoustID recording hit on the track: AcoustID's score (as online);
    - the file's own MusicBrainz Album Id is this release: 97%, scaled down only if the track pairing is weak;
    - the file's own recording id tag is this track: 95%;
    - otherwise title/length/track number x how far the album and artist tags agree ("Beatles, The" =
      "The Beatles"), and never above LOCAL_FUZZY_CAP unless artist and album are exact AND the
      release has as many tracks as the folder has files."""
    release = match.release
    score = fact.recordings.get(track.recording_id) if track.recording_id else None
    if score is not None:
        return float(score), f"AcoustID fingerprint matched the recording on '{release.title}' (score {score:.0%})"
    single = match_release([fact], release)
    pair = single.score if single.assignment else 0.0
    if (mp3.musicbrainz_albumid or "").strip().lower() == release.id.lower():
        return (
            min(LOCAL_MBID_CONFIDENCE, LOCAL_MBID_CONFIDENCE * pair / 0.8),
            f"the file's own MusicBrainz Album Id is '{release.title}' (track pairing {pair:.0%})",
        )
    if track.recording_id and (mp3.musicbrainz_trackid or "").strip().lower() == track.recording_id.lower():
        return (
            LOCAL_RECORDING_TAG_CONFIDENCE,
            f"the file's own MusicBrainz recording id is track {track.position} of '{release.title}'",
        )
    confidence = pair * local_agreement(mp3.album, mp3.artist or mp3.albumartist, release, track)
    exact = match.exact and match.count_equal
    if not exact:
        confidence = min(confidence, LOCAL_FUZZY_CAP)
    return confidence, (
        f"matched track {track.position} of '{release.title}' by title/length/track number ({pair:.0%}), scaled by "
        f"how far the album and artist tags agree"
        + ("" if exact else "; artist and album are not both exact with an equal track count, so at most "
           f"{LOCAL_FUZZY_CAP:.0%}")
    )


class TagLookupStep(Mp3Step):
    key = "tags"
    label = "Fill missing tags (MusicBrainz / AcoustID)"
    description = (
        "Finds the file's album on MusicBrainz (by its tags or folder name, and by sound when fpcalc is "
        "installed) and fills tags that are EMPTY. Existing values are never replaced unless the option "
        "says so. A match made by fingerprint has AcoustID's score as its confidence; one made by tags "
        "needs the album and artist tags to agree exactly to reach 90%. Below the threshold the match is "
        "listed under Needs review and nothing is written. Needs network access. If a local MusicBrainz "
        "database is set up (Tools > MusicBrainz Database) it is consulted FIRST, offline, and also fills "
        "label, catalogue number and country: the file's own MusicBrainz Album Id names its release at 97%, "
        "a recording id tag at 95%, an AcoustID hit keeps its score, and a match by tags needs artist and "
        "album exact and as many tracks as files to reach 90% (otherwise at most 85%). Online MusicBrainz and "
        "AcoustID are then asked only for what is still missing, and if the network is down the local "
        "answer is used as it is."
    )
    options = (
        OptionSpec("overwrite", "Also replace different existing values", "bool", False),
        OptionSpec("fingerprint", "Identify by sound (needs fpcalc)", "bool", True),
    )

    def process(self, ctx: Mp3Ctx) -> StepResult:
        work, overwrite = ctx.work, self.options_for(ctx)["overwrite"]
        db, db_note = _local_database(ctx.env)
        triggers = _LOCAL_TRIGGER_FIELDS if db is not None else _LOOKUP_TRIGGER_FIELDS
        if not overwrite and all(getattr(work, k) for k in triggers):
            return StepResult.nothing()  # complete already: no lookup at all
        if db is None:
            result = self._online(ctx, overwrite)
            if db_note:  # a database is configured but unusable: say so, carry on online
                result.note = "; ".join(b for b in (db_note, result.note) if b)
            return result
        # The local database first: offline, and the only thing consulted when it answers everything
        # the network could add.
        local = self._local_fill(ctx, db, overwrite)
        missing = [k for k in _LOOKUP_TRIGGER_FIELDS if not getattr(work, k) and not (local and k in local.fields)]
        if overwrite or not missing:
            return self._suggest(local, local.fields, local.confidence, local.reason) if local else StepResult.nothing()
        # What is still missing might be on MusicBrainz proper: ask only now.
        online = self._online(ctx, overwrite)
        if local is None:
            return online
        fields, confidence, reason, note = dict(local.fields), local.confidence, local.reason, local.note
        if isinstance(online.value, TagFill):
            extra = {k: v for k, v in online.value.fields.items() if k not in fields}
            if extra:
                fields.update(extra)
                confidence = min(confidence, online.confidence)
                reason += f"; online MusicBrainz added {', '.join(extra)}: {online.reason}"
        elif online.note:
            note = "; ".join(b for b in (note, f"online MusicBrainz skipped: {online.note}") if b)
        return self._suggest(local, fields, confidence, reason, note)

    @staticmethod
    def _suggest(local: LocalFill, fields: dict, confidence: float, reason: str, note: str | None = None) -> StepResult:
        result = StepResult.suggestion(TagFill(fields), min(confidence, 1.0), "local MusicBrainz database: " + reason)
        result.note = local.note if note is None else note
        return result

    def _local_fill(self, ctx: Mp3Ctx, db, overwrite: bool) -> LocalFill | None:
        """What the local database would fill for this file, or None when it has no answer."""
        work = ctx.work
        lookup = _local_lookup_folder(ctx.env, os.path.dirname(ctx.original), self.options_for(ctx)["fingerprint"], db)
        if lookup.error:
            return None
        index = next((i for i, m in enumerate(lookup.files) if m is ctx.mp3), None)
        if index is None:
            return None
        fact = lookup.facts[index]
        track = lookup.match.assignment.get(index) if lookup.match is not None else None
        if track is not None:
            match = lookup.match
            confidence, reason = _local_confidence(ctx.mp3, match, track, fact)
            release = match.release
        else:
            # no album found for the folder (or this file isn't on it): a single track by title, if the
            # database has the track search index
            singles = find_track_local(db, fact, ctx.mp3.artist or ctx.mp3.albumartist, lookup.year)
            if not singles:
                return None
            match = singles[0]
            release, track = match.release, match.assignment[0]
            confidence = min(
                match_release([fact], release).score * local_agreement(ctx.mp3.album, ctx.mp3.artist or ctx.mp3.albumartist, release, track),
                LOCAL_TRACK_ONLY_CAP,
            )
            reason = f"found as a single track ('{track.title}' on '{release.title}'), with no album to confirm it"
        fields = {
            key: value
            for key, value in local_fields_for(release, track).items()
            if not getattr(work, key, "") or (overwrite and getattr(work, key, "") != value)
        }
        return LocalFill(fields, confidence, reason, lookup.note) if fields else None

    def _online(self, ctx: Mp3Ctx, overwrite: bool) -> StepResult:
        """The online MusicBrainz / AcoustID lookup (what this step always did)."""
        work = ctx.work
        folder = os.path.dirname(ctx.original)
        lookup = _lookup_folder(ctx.env, folder, self.options_for(ctx)["fingerprint"])
        if lookup.error:
            return StepResult.nothing(note=f"Tag lookup unavailable: {lookup.error}")
        if lookup.match is None:
            return StepResult.nothing()
        index = next((i for i, m in enumerate(lookup.files) if m is ctx.mp3), None)
        track = lookup.match.assignment.get(index) if index is not None else None
        if track is None:
            return StepResult.nothing()
        release = lookup.match.release

        recording_score = lookup.facts[index].recordings.get(track.recording_id) if track.recording_id else None
        if recording_score is not None:
            confidence = float(recording_score)
            reason = f"AcoustID fingerprint matched the recording on '{release.title}' (score {confidence:.0%})"
        else:
            single = match_release([lookup.facts[index]], release)
            pair = single.score if single.assignment else 0.0
            confidence = pair * _agreement(ctx.mp3, release, track)
            reason = (
                f"matched track {track.position} of '{release.title}' by title/length/track number "
                f"({pair:.0%}), scaled by how far the album and artist tags agree"
            )
        fill = {
            key: value
            for key, value in fields_for(release, track).items()
            if not getattr(work, key, "") or (overwrite and getattr(work, key, "") != value)
        }
        if not fill:
            return StepResult.nothing()
        return StepResult.suggestion(TagFill(fill), min(confidence, 1.0), reason)

    def apply_suggestion(self, ctx: Mp3Ctx, result: StepResult) -> list[str]:
        ctx.work.apply_tags(result.value.fields)
        ctx.release_confidence = result.confidence
        return [f"{key} = {value!r}" for key, value in result.value.fields.items()]


# The fields the Discogs step can fill; a file with all of them has nothing for it.
_DISCOGS_FIELDS = (
    "title", "artist", "albumartist", "album", "track", "year", "genre", "publisher", "catalognumber", "releasecountry",
)
DISCOGS_NO_TOKEN_NOTE = "Discogs lookup skipped: no Discogs token. " + discogs.TOKEN_HELP
DISCOGS_STOPPED_NOTE = "Discogs lookup skipped: Discogs' rate limit (HTTP 429) was reached earlier in this run"
# A file that already has a title which barely resembles its paired track's
# is probably on another edition: its match is capped at the "fuzzy" level.
_DISCOGS_TITLE_CLASH = 0.5


@dataclass
class DiscogsFolderLookup:
    """One folder's Discogs result, computed once per run and query."""

    files: list[MP3File]
    facts: list
    matches: list = field(default_factory=list)
    error: str = ""


def _discogs_client(env: RedactEnv):
    """The run's Discogs client (one pace and one rate-limit flag for the
    whole run), or None when no token is stored. The token is read once
    per run."""
    if "client" not in env._discogs:
        token = discogs.load_token()
        env._discogs["client"] = (
            discogs.DiscogsClient(token, fetch=env.discogs_fetch, throttle=env.discogs_throttle) if token else None
        )
    return env._discogs["client"]


def _discogs_lookup_folder(ctx: "Mp3Ctx", client, folder: str) -> tuple[DiscogsFolderLookup, discogs.DiscogsQuery]:
    """Searches Discogs for the file's folder like the Look Up via Discogs
    dialog does, without the review. The query uses the folder's files'
    tags, with THIS file's working copy (what the earlier steps filled in)
    standing in for itself. Results are kept per (folder, query), and a
    failure is kept too, so the run doesn't retry it for every file."""
    env, me = ctx.env, ctx.mp3
    files = sorted(
        (m for m in env.items if not m.load_error and os.path.dirname(os.path.realpath(m.path)) == folder),
        key=lambda m: m.filename.casefold(),
    )
    tags = [ctx.work if m is me else m for m in files]
    query = discogs.discogs_query(
        [t.album for t in tags], [t.albumartist or t.artist for t in tags], [t.year for t in tags],
        Path(folder), [t.title for t in tags],
    )
    # No year in the key: it is a weak part of the query, and the run's own
    # earlier fills would change it mid-folder and cost a second search.
    key = (folder, normalize_words(query.artist), normalize_words(query.album))
    if key in env._discogs:
        return env._discogs[key], query
    result = DiscogsFolderLookup(files=files, facts=[facts_from_tags(t.title, t.track, t.discnumber, t.duration_seconds) for t in tags])
    env._discogs[key] = result
    try:
        result.matches = discogs.find_release(client, result.facts, query, cache=env._discogs_releases)
    except discogs.DiscogsRateLimited as exc:
        result.error = f"{exc} Discogs is skipped for the rest of this run."
    except discogs.DiscogsError as exc:
        result.error = str(exc)
    return result, query


class DiscogsStep(Mp3Step):
    key = "discogs"
    label = "Fill missing tags (Discogs)"
    description = (
        "Finds the file's album on Discogs (by its tags or folder name, after the MusicBrainz step has "
        "filled what it could) and fills tags that are EMPTY: title, artist, album, track, year, genre (with "
        "Discogs' styles), label, catalogue number and country. Existing values are never replaced. The match "
        "is 93% confident only when the artist and album match exactly, the release has as many tracks as the "
        "folder has files and one release clearly fits best; an exact artist and album with a different track "
        "count (or several equally good pressings) is 70%; anything looser is 60% at most. Below the threshold "
        "the match is listed under Needs review and nothing is written. Needs a Discogs token (Tools > API "
        "Keys) and network access; on by default only when a token is set. Discogs is asked about once a "
        "second; if it answers HTTP 429 the step is skipped for the rest of the run."
    )
    options = (
        OptionSpec("styles", "Add Discogs styles to Genre", "bool", True,
                   tooltip="On: the Genre tag gets the genres then the styles (Rock; Prog Rock). Off: genres only."),
    )

    def process(self, ctx: Mp3Ctx) -> StepResult:
        work = ctx.work
        if all(getattr(work, k) for k in _DISCOGS_FIELDS):
            return StepResult.nothing()  # complete already: no network round trip
        client = _discogs_client(ctx.env)
        if client is None:
            return StepResult.nothing(note=DISCOGS_NO_TOKEN_NOTE)
        if client.rate_limited:
            return StepResult.nothing(note=DISCOGS_STOPPED_NOTE)
        lookup, query = _discogs_lookup_folder(ctx, client, os.path.dirname(ctx.original))
        if lookup.error:
            return StepResult.nothing(note=f"Discogs lookup unavailable: {lookup.error}")
        index = next((i for i, m in enumerate(lookup.files) if m is ctx.mp3), None)
        if index is None or not lookup.matches:
            return StepResult.nothing()
        top = lookup.matches[0]
        track = top.assignment.get(index)
        if track is None:
            return StepResult.nothing()
        release = top.release

        trust = discogs.confidence(lookup.matches)
        reason = (
            f"Discogs release '{release.title}' ({', '.join(b for b in (release.year, release.label, release.catno) if b)}): "
            f"track {track.number} of {release.track_count}; "
            + ("artist and album match exactly" if top.exact else f"a fuzzy match ({top.score:.0%}) on artist and album")
            + ("" if top.count_equal else f"; the release has {release.track_count} tracks, the folder {top.file_count} files")
        )
        if work.title.strip() and normalize_words(work.title) != normalize_words(track.title):
            similarity = difflib.SequenceMatcher(None, normalize_words(work.title), normalize_words(track.title)).ratio()
            if similarity < _DISCOGS_TITLE_CLASH:
                trust = min(trust, discogs.FUZZY_CAP)
                reason += f"; the file's title {work.title!r} does not resemble {track.title!r}"
        fill = {
            key: value
            for key, value in discogs.fields_for(release, track, self.options_for(ctx)["styles"]).items()
            if not getattr(work, key, "")
        }
        if not fill:
            return StepResult.nothing()
        return StepResult.suggestion(TagFill(fill), trust, reason)

    def apply_suggestion(self, ctx: Mp3Ctx, result: StepResult) -> list[str]:
        ctx.work.apply_tags(result.value.fields)
        return [f"{key} = {value!r}" for key, value in result.value.fields.items()]


class CoverStep(Mp3Step):
    key = "cover"
    label = "Add missing cover art"
    description = (
        "For a file with no embedded cover: a cover.jpg/folder.jpg next to it, else the release's front "
        "cover from the Cover Art Archive (needs the MusicBrainz Album Id tag, which the tag lookup "
        "can fill in first). A cover is a guess, so it is embedded only at or above the confidence "
        "threshold; otherwise it is listed under Needs review."
    )
    options = (
        OptionSpec("folder", "Use a folder image (cover.jpg...)", "bool", True),
        OptionSpec("online", "Ask the Cover Art Archive", "bool", True),
    )

    def process(self, ctx: Mp3Ctx) -> StepResult:
        work = ctx.work
        if work.cover_change_pending:
            return StepResult.nothing()
        has_cover = work.has_cover if work.has_cover is not None else read_cover(ctx.temp) is not None
        if has_cover:
            return StepResult.nothing()
        if self.options_for(ctx)["folder"]:
            image = find_folder_image(ctx.original)
            if image is not None:
                try:
                    data = image.read_bytes()
                except OSError:
                    data = b""
                mime = sniff_mime(data)
                if mime:
                    return StepResult.suggestion(
                        CoverSuggestion(data, mime, f"folder image {image.name}"),
                        FOLDER_COVER_CONFIDENCE,
                        "an image named like album art sits next to the file",
                    )
        if self.options_for(ctx)["online"] and work.musicbrainz_albumid:
            data = fetch_front_cover(work.musicbrainz_albumid, size=COVER_ART_SIZE)
            mime = sniff_mime(data)
            if data and mime:
                confidence = ctx.release_confidence if ctx.release_confidence is not None else ONLINE_COVER_CONFIDENCE
                return StepResult.suggestion(
                    CoverSuggestion(data, mime, "Cover Art Archive front cover"),
                    min(confidence, ONLINE_COVER_CONFIDENCE),
                    f"front cover of release {work.musicbrainz_albumid}",
                )
        return StepResult.nothing()

    def apply_suggestion(self, ctx: Mp3Ctx, result: StepResult) -> str:
        suggestion: CoverSuggestion = result.value
        ctx.work.set_cover(suggestion.data, suggestion.mime)
        return f"embedded {suggestion.source}"


# --- save stage, then the steps that act on the finished file -------------------


FINALIZE_LABEL = "Final save"  # run_redact(finalize_label=...); names the save stage in the report


def verify_written_file(path: str, work: MP3File, expected_seconds: float | None) -> bool:
    """The commit's gate: the new file must open, have the audio length
    the original had (a little slack: mp3val -f may trim junk) and read
    back every tag this run changed."""
    try:
        from mutagen.mp3 import MP3

        length = float(MP3(path).info.length)
    except Exception:  # noqa: BLE001
        return False
    if length <= 0:
        return False
    if expected_seconds and abs(length - expected_seconds) > max(1.0, 0.02 * expected_seconds):
        return False
    fresh = MP3File(path=Path(path))
    load_tags(fresh)
    if fresh.load_error:
        return False
    for key in BASELINE_KEYS:
        if work.tag_changed(key) and (getattr(fresh, key, "") or "") != (getattr(work, key, "") or ""):
            return False
    if work.cover_pending is not None and not fresh.has_cover:
        return False
    return True


def _save_working_copy(ctx: Mp3Ctx) -> StepResult | None:
    """Mp3Ctx.save(): the working copy's tags into the scratch copy,
    verified, then swapped in for the original."""
    work = ctx.work
    if not work.dirty:
        return None  # nothing to write (every step found nothing / was off)
    expected = ctx.mp3.duration_seconds
    error = apply_tags_to_file(work, ctx.temp)
    if error:
        ctx.save_failed = True
        return StepResult.failed(f"NOT SAVED, the original is untouched: {error}")
    try:
        result = commit_in_place(
            ctx.original, ctx.temp, trash=ctx.env.trash or move_to_trash,
            verify=lambda path: verify_written_file(path, work, expected),
        )
    except CommitError as exc:
        ctx.save_failed = True
        return StepResult.failed(f"NOT SAVED, the original is untouched: {exc}")
    ctx.saved = True
    _adopt_saved_state(ctx)
    if result.backup_kept:
        saved = StepResult.applied(f"saved; the original is kept at {result.backup}")
        saved.note = result.warning
        return saved
    return StepResult.applied("saved in place; the original is in the Recycle Bin")


def _adopt_saved_state(ctx: Mp3Ctx) -> None:
    """The live row takes on the finished state (what save_tags does
    for a normal Save), then re-reads the file so the table shows
    exactly what is on disk."""
    work, live = ctx.work, ctx.mp3
    work.snapshot_tag_baseline()
    work.mark_cover_saved()
    work.dirty = False
    work.save_error = ""
    for f in dataclasses.fields(MP3File):
        setattr(live, f.name, getattr(work, f.name))
    reload_from_disk(live)


def save_stage(ctx: Mp3Ctx, _file_report: FileReport) -> StepResult | None:
    """The engine's `finalize` hook: saves the file once all steps have run
    (or reports the save a rename/move step already did)."""
    if ctx.skip_reason:  # a recipe with no step at all never asked the context
        return StepResult.skipped(ctx.skip_reason)
    return ctx.save()


def _filename_values(ctx: Mp3Ctx) -> dict[str, str]:
    """The pattern values of the finished file, zero-padded like Rename / Export."""
    settings = ctx.env.settings
    values = {key: getattr(ctx.work, key, "") or "" for key, _label, _multiline in FIELDS}
    if settings.rename_zero_pad:
        values["track"] = zero_pad_numeric_value(values["track"], settings.rename_zero_pad_width)
    return values


# --- pattern trail -----------------------------------------------------------------
# A saved recipe KEEPS the pattern it was saved with; an EMPTY stored pattern
# follows the app's current one (the fallback). The editor shows the trail:
# the pattern in effect and where it came from, recent patterns, a preview.

# What the pattern previews are rendered on.
_SAMPLE_VALUES = {
    "artist": "Queen", "albumartist": "Queen", "album": "A Night at the Opera", "title": "Bohemian Rhapsody",
    "track": "11", "disc": "1", "year": "1975", "genre": "Rock",
}


def _sample_values(settings: Settings | None) -> dict[str, str]:
    values = {key: "" for key, _label, _multiline in FIELDS}
    values.update(_SAMPLE_VALUES)
    if settings is not None and settings.rename_zero_pad:
        values["track"] = zero_pad_numeric_value(values["track"], settings.rename_zero_pad_width)
    return values


def _preview_filename(settings: Settings | None) -> Callable[[str], str]:
    def preview(pattern: str) -> str:
        if not pattern.strip():
            return ""
        try:
            ascii_only = bool(settings and settings.ascii_filenames)
            return render_filename(_sample_values(settings), pattern, fallback="untitled", ascii_only=ascii_only) + ".mp3"
        except Exception:
            return ""

    return preview


def _preview_path(settings: Settings | None) -> Callable[[str], str]:
    def preview(pattern: str) -> str:
        if not pattern.strip():
            return ""
        try:
            ascii_only = bool(settings and settings.ascii_filenames)
            parts = render_relative_path(_sample_values(settings), pattern, fallback_segment="untitled", ascii_only=ascii_only)
            return "/".join(parts) + ".mp3"
        except Exception:
            return ""

    return preview


def _pattern_option(key: str, label: str, tooltip: str, settings: Settings | None,
                    fallback: Callable[[], str], fallback_label: str, preview) -> OptionSpec:
    """A pattern option: stored "" follows `fallback`; a non-empty stored
    pattern is always kept."""
    return OptionSpec(
        key, label, "str", "", tooltip=tooltip,
        suggestions=lambda: list(settings.pattern_history) if settings else [],
        fallback=fallback, fallback_label=fallback_label, preview=preview,
    )


def resolve_pattern(step: Step, ctx, fallback: Callable[[], str]) -> tuple[str, str]:
    """(pattern, source) of a step's pattern option at run time: the stored
    value wins, an empty one follows the app's CURRENT setting (taken from
    the run's own settings, not the catalogue's)."""
    spec = next(o for o in step.options if o.key == "pattern")
    spec = dataclasses.replace(spec, fallback=fallback)
    value, source = effective_option_source(spec, (step.options_for(ctx)["pattern"] or "").strip())
    return value.strip(), source


def pin_patterns(recipe: Recipe, catalogue: list[Step]) -> Recipe:
    """First-save pinning: write each pattern option's CURRENT effective
    value into a never-saved recipe, so saving it keeps that pattern even
    when Rename / Export or Parse Filename change later."""
    for step in catalogue:
        for spec in step.options:
            if spec.kind == "str" and spec.fallback is not None:
                stored = recipe.options.setdefault(step.key, {})
                if not (stored.get(spec.key) or ""):
                    stored[spec.key] = effective_option_source(spec, "")[0]
    return recipe


class RenameStep(Mp3Step):
    """A "last" step: renames the finished file with the pattern last used
    in Rename / Export Files."""

    key = "rename"
    label = "Rename by saved pattern"
    description = (
        "Renames the file with the pattern you last used in File > Rename / Export Files (and its "
        "zero-pad/ASCII choices), without overwriting anything; logged for Undo Last Rename. Always runs "
        "last, after the file is saved. Off until a pattern has been used."
    )
    position = "last"

    def __init__(self, settings: Settings | None = None, *, default_enabled: bool | None = None):
        saved = settings.saved_rename_pattern() if settings else ""
        super().__init__(default_enabled=bool(saved) if default_enabled is None else default_enabled)
        self.options = (
            _pattern_option(
                "pattern", "Filename pattern",
                "Kept as saved; clear it (Use fallback) to follow the pattern last used in Rename / Export Files. Placeholders like %artist% and %title%.",
                settings, (lambda: settings.saved_rename_pattern()) if settings else (lambda: ""),
                "the last Rename / Export pattern", _preview_filename(settings),
            ),
        )

    def process(self, ctx: Mp3Ctx) -> StepResult:
        settings, work = ctx.env.settings, ctx.work
        pattern, _source = resolve_pattern(self, ctx, settings.saved_rename_pattern)
        if not pattern:
            return StepResult.nothing(
                note="Rename skipped: no rename pattern saved yet (use File > Rename / Export Files once)"
            )
        if not work.title.strip():
            return StepResult.nothing(note="Rename skipped: the file has no title")
        ctx.save()  # the engine's save stage comes after the "last" steps
        if ctx.save_failed:
            return StepResult.nothing()
        old_path = str(ctx.mp3.path)
        stem, ext = os.path.splitext(os.path.basename(old_path))
        new_stem = render_filename(_filename_values(ctx), pattern, fallback=stem, ascii_only=settings.ascii_filenames)
        new_path = unique_path(os.path.dirname(old_path), new_stem, ext, set(), own_path=old_path)
        if new_path == old_path:
            return StepResult.nothing()
        try:
            rename_no_clobber(old_path, new_path)
        except OSError as exc:
            return StepResult.failed(f"couldn't rename to {os.path.basename(new_path)!r}: {exc}")
        ctx.mp3.path = Path(new_path)
        if ctx.env.rename_log is not None:
            ctx.env.rename_log.record("Redact", [(old_path, new_path)])
        return StepResult.applied(f"renamed to {os.path.basename(new_path)!r}")


class MoveIntoFoldersStep(Mp3Step):
    """A "last" step, after Rename: files the finished file under the
    library root by a folder pattern (the Rename / Export dialog's "Move
    into folders" mode, one file at a time)."""

    key = "move_into_folders"
    label = "Move into library folders"
    description = (
        "Moves the file into the library root (chosen in File > Rename / Export Files > Move into folders) "
        "by a pattern such as %albumartist%/%album%/%track% - %title%; missing folders are created, nothing "
        "is overwritten, and it is logged for Undo Last Rename. Runs last, after the file is saved. Off by "
        "default. Empty folders left behind are not removed."
    )
    position = "last"

    def __init__(self, settings: Settings | None = None, *, default_enabled: bool | None = None):
        super().__init__(default_enabled=False if default_enabled is None else default_enabled)
        self.options = (
            _pattern_option(
                "pattern", "Folder and filename pattern",
                "Relative to the library root; / starts a sub-folder. Kept as saved; clear it (Use fallback) to follow the last Move into folders pattern. Placeholders like %albumartist% and %album%.",
                settings, (lambda: settings.saved_move_pattern()) if settings else (lambda: DEFAULT_MOVE_PATTERN),
                "the last Move into folders pattern", _preview_path(settings),
            ),
        )

    def process(self, ctx: Mp3Ctx) -> StepResult:
        settings = ctx.env.settings
        root = settings.library_root
        if not root or not os.path.isdir(root):
            return StepResult.nothing(
                note="Move skipped: no library root folder (choose one in File > Rename / Export Files > Move into folders)"
            )
        pattern, _source = resolve_pattern(self, ctx, settings.saved_move_pattern)
        ctx.save()  # the engine's save stage comes after the "last" steps
        if ctx.save_failed:
            return StepResult.nothing()
        old_path = str(ctx.mp3.path)
        stem = os.path.splitext(os.path.basename(old_path))[0]
        values = _filename_values(ctx)
        (plan,) = plan_moves(
            [ctx.mp3], root, pattern, lambda _mp3: values, lambda mp3: str(mp3.path),
            ascii_only=settings.ascii_filenames, fallback_segment=stem,
        )
        if plan.blocking:
            return StepResult.failed(f"couldn't move: {plan.warning}")
        if plan.is_noop:
            return StepResult.nothing()
        try:
            moved = execute_move(old_path, plan.new_path, copy=False, trash=ctx.env.trash or move_to_trash)
        except OSError as exc:
            return StepResult.failed(f"couldn't move to {plan.new_path!r}: {exc}")
        ctx.mp3.path = Path(moved.new_path)
        if ctx.env.rename_log is not None and not moved.original_kept:
            trashed = [(old_path, moved.new_path)] if moved.original_trashed else []
            ctx.env.rename_log.record(
                "Redact", [(old_path, moved.new_path)],
                created_dirs=moved.created_dirs, trashed=trashed, root=plan.root,
            )
        result = StepResult.applied(f"moved to {os.path.relpath(moved.new_path, plan.root)!r}")
        result.note = moved.warning
        return result


# --- catalogue, recipe ---------------------------------------------------------


def build_catalogue(settings: Settings | None = None) -> list[Step]:
    """The steps the recipe editor offers, in default order. `settings`
    gives Rename and Move their starting patterns and decides whether
    Rename starts enabled (only once a pattern exists) and whether the
    Discogs step does (only once a token is stored). The save stage is
    not a step: pass save_stage / FINALIZE_LABEL to run_redact."""
    return [
        IntegrityStep(),
        BpmStep(),
        KeyStep(),
        LoudnessStep(),
        DeepCheckStep(),
        PathTagsStep(settings),
        TagLookupStep(),
        DiscogsStep(default_enabled=discogs.has_token()),  # on only when a token is set
        CoverStep(),
        RenameStep(settings),
        MoveIntoFoldersStep(settings),
    ]


def recipe_to_setting(recipe: Recipe) -> str:
    """One-line JSON for the ini value (Settings.redact_recipe)."""
    import json

    return json.dumps(recipe.to_dict(), separators=(",", ":"))


def recipe_from_setting(text: str, catalogue: list[Step]) -> Recipe:
    """The saved recipe; nothing saved (or unreadable) means the defaults."""
    if not (text or "").strip():
        return Recipe.default_for(catalogue)
    return Recipe.from_json(text)
