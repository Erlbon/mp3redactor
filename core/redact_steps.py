"""
core/redact_steps.py

The steps behind the "Redact" button (redactor_common's pipeline engine,
core/pipeline.py): run a recipe on each loaded/selected file with no
operator input and leave a corrected file IN PLACE, the original in the
Recycle Bin. Qt-free; gui/main_window.py wires it to the menu/toolbar.

Every step wraps code the app already has (scan_service's tools, the
MusicBrainz/AcoustID lookup, cover_art, the rename pattern) without the
dialogs. How one file flows:

  1. Mp3Ctx makes a WORKING COPY of the MP3File (what steps edit) and a
     scratch copy of the file in the same folder (what the external
     tools read and mp3val -f rewrites). The live row and the original
     file are not touched until the save step.
  2. Steps run. Measurements and fixes are APPLIED; guesses (online tag
     lookup, cover art) come back as SUGGESTIONs with a confidence, which
     the engine applies only at or above the recipe's threshold and lists
     as "Needs review" otherwise.
  3. SaveStep (always last of the file-changing steps; not listed in the
     recipe editor) writes the working copy's tags into the scratch copy
     (tag_writer.apply_tags_to_file), verifies it reloads with the same
     audio length and tags, then commit_in_place() swaps it in and sends
     the original to the Recycle Bin.
  4. RenameStep (pinned after the save) renames the finished file.

A scan-based step never acts on a failed tool: TOOL ERROR/TOOL MISSING is
never stamped (MP3File.record_scan) and never triggers mp3val -f.
Steps that cannot do their job because something is not installed or the
network is down answer NOTHING and leave a note in RedactEnv, shown under
the report (the engine's report has no place for a NOTHING message).
"""

from __future__ import annotations

import dataclasses
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from redactor_common.core.os_utils import rename_no_clobber
from redactor_common.core.pipeline import (
    CommitError,
    OptionSpec,
    Recipe,
    Step,
    StepResult,
    commit_in_place,
)
from redactor_common.core.rename_pattern import render_filename, unique_path, zero_pad_numeric_value
from redactor_common.core.save_errors import describe_save_error
from redactor_common.core.trash import move_to_trash

from core.acoustid_lookup import FPCALC_EXE_NAME, AcoustIdError, candidate_releases, identify_files
from core.bpm_detector import detect_bpm
from core.cover_art import find_folder_image, read_cover, sniff_mime
from core.ffmpeg_probe import deep_check_integrity, measure_loudness, probe_format
from core.fields import FIELDS
from core.keyfinder_runner import detect_key
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
from core.scan_service import reload_from_disk
from core.settings import Settings
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


class RedactSkip(Exception):
    """This file is not processed at all; the message is the report line."""


# --- run environment -------------------------------------------------------


@dataclass
class RedactEnv:
    """What the steps of one Redact run share: the app's settings, the
    rename log, the trash function (injectable for tests), the files in
    the run (an album lookup works on a whole folder, once) and the
    notes that end up under the report."""

    settings: Settings
    rename_log: object | None = None  # anything with .record(label, [(old, new)])
    trash: Callable[[str], None] | None = None  # None: the Recycle Bin (move_to_trash)
    items: list[MP3File] = field(default_factory=list)
    notes: list[tuple[str, str]] = field(default_factory=list)  # (file name, message)
    _albums: dict = field(default_factory=dict)

    def begin(self, items) -> None:
        """Call before each run."""
        self.items = list(items)
        self.notes.clear()
        self._albums.clear()

    def note(self, mp3: MP3File, text: str) -> None:
        self.notes.append((mp3.filename, text))

    def notes_text(self) -> str:
        """The notes grouped by message, so "mp3val not found" says so
        once (with how many files it affected), not once per file."""
        groups: dict[str, list[str]] = {}
        for name, text in self.notes:
            groups.setdefault(text, []).append(name)
        lines = []
        for text, names in groups.items():
            if len(names) == 1:
                lines.append(f"{names[0]}: {text}")
            else:
                lines.append(f"{text}  ({len(names)} files)")
        return "\n".join(lines)


# --- per-file context ------------------------------------------------------


class Mp3Ctx:
    """make_context() result for one file (see the module docstring).
    Raises RedactSkip for a file that must not be touched: it failed to
    load, or has unsaved edits (Redact works on what is on disk and would
    otherwise silently replace the edits)."""

    def __init__(self, mp3: MP3File, env: RedactEnv):
        if mp3.load_error:
            raise RedactSkip(f"skipped -- the file could not be read ({mp3.load_error})")
        if mp3.dirty:
            raise RedactSkip("skipped -- it has unsaved edits (save or undo them first)")
        self.mp3 = mp3
        self.env = env
        self.original = os.path.realpath(mp3.path)  # work through a symlink, don't replace it
        self.work = dataclasses.replace(mp3)
        self.step_options: dict = {}
        self.release_confidence: float | None = None  # set when the lookup step's guess was applied
        self.saved = False  # the save step committed a new file
        self.save_failed = False
        self.temp = ""
        try:
            fd, self.temp = tempfile.mkstemp(
                prefix=".mp3redactor-redact-", suffix=".mp3", dir=os.path.dirname(self.original)
            )
            os.close(fd)
            shutil.copy2(self.original, self.temp)  # keeps the permissions
        except OSError as exc:
            self._discard_temp()
            raise RedactSkip(f"skipped -- couldn't make a working copy ({describe_save_error(exc)})") from exc

    def note(self, text: str) -> None:
        self.env.note(self.mp3, text)

    def restore_temp(self) -> None:
        """Back to the original's bytes (after a fix that failed midway)."""
        shutil.copy2(self.original, self.temp)

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


def _tool_missing(ctx: Mp3Ctx, message: str) -> StepResult:
    ctx.note(message)
    return StepResult.nothing()


# --- steps: checks and measurements ------------------------------------------


class IntegrityStep(Step):
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

    def run(self, ctx: Mp3Ctx) -> StepResult:
        work, settings = ctx.work, ctx.env.settings
        mp3val = settings.mp3val_path or None
        status, message = check_integrity(ctx.temp, override_path=mp3val)
        work.record_scan("integrity", status, message)  # TOOL MISSING/ERROR: shown, never stamped
        if status == STATUS_TOOL_MISSING:
            return _tool_missing(ctx, message)
        if status == STATUS_TOOL_ERROR:
            return StepResult.failed(message)
        if status == STATUS_OK:
            return StepResult.applied("OK (stamped)")

        problems = _problem_count(message)
        if not ctx.step_options["fix"]:
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


class BpmStep(Step):
    key = "bpm"
    label = "Detect BPM"
    description = "Measures the tempo with aubio and writes it to the BPM tag."
    options = (
        OptionSpec(
            "replace", "Replace an existing BPM tag", "bool", False,
            tooltip="Off: a file that already has a BPM tag keeps it (measuring is skipped).",
        ),
    )

    def run(self, ctx: Mp3Ctx) -> StepResult:
        if not ctx.step_options["replace"] and _existing_frame_text(ctx.temp, "TBPM"):
            return StepResult.nothing()
        bpm, status, message = detect_bpm(Path(ctx.temp))
        if status == STATUS_TOOL_MISSING:
            return _tool_missing(ctx, message)
        if bpm is None or status != STATUS_OK:
            # Mostly "not enough beats" (ambient, spoken word): a miss, not a failure.
            ctx.note(f"BPM not detected: {message}")
            return StepResult.nothing()
        work = ctx.work
        work.bpm, work.bpm_status, work.bpm_message = bpm, status, message
        work.dirty = True
        return StepResult.applied(f"{bpm:.1f} BPM")


class KeyStep(Step):
    key = "key"
    label = "Detect key"
    description = "Detects the musical key with keyfinder-cli and writes it to the Key tag."
    options = (
        OptionSpec(
            "replace", "Replace an existing Key tag", "bool", False,
            tooltip="Off: a file that already has a Key tag keeps it (detection is skipped).",
        ),
    )

    def run(self, ctx: Mp3Ctx) -> StepResult:
        if not ctx.step_options["replace"] and _existing_frame_text(ctx.temp, "TKEY"):
            return StepResult.nothing()
        key, status, message = detect_key(
            Path(ctx.temp), override_path=ctx.env.settings.keyfinder_cli_path or None
        )
        if status == STATUS_TOOL_MISSING:
            return _tool_missing(ctx, message)
        if status != STATUS_OK:
            return StepResult.failed(message)
        if not key:
            return StepResult.nothing()  # silent audio: no key to write (nothing is cleared either)
        work = ctx.work
        work.key_value, work.key_status, work.key_message = key, status, message
        work.dirty = True
        return StepResult.applied(f"key {key}")


class LoudnessStep(Step):
    key = "loudness"
    label = "Measure loudness (ReplayGain)"
    description = "Measures loudness with ffmpeg and writes the ReplayGain track gain."

    def run(self, ctx: Mp3Ctx) -> StepResult:
        lufs, gain, status, message = measure_loudness(
            Path(ctx.temp), override_path=ctx.env.settings.ffmpeg_path or None
        )
        if status == STATUS_TOOL_MISSING:
            return _tool_missing(ctx, message)
        if status != STATUS_OK:
            return StepResult.failed(message)
        if gain is None:
            return StepResult.nothing()  # silent audio
        work = ctx.work
        work.loudness_lufs, work.loudness_gain_db = lufs, gain
        work.loudness_status, work.loudness_message = status, message
        work.dirty = True
        return StepResult.applied(f"{lufs:.1f} LUFS, gain {gain:+.2f} dB")


class DeepCheckStep(Step):
    key = "deep_check"
    label = "Deep check (ffmpeg full decode)"
    description = "Decodes the whole file with ffmpeg to find corrupt audio and stamps the result. Slow."
    default_enabled = False

    def run(self, ctx: Mp3Ctx) -> StepResult:
        settings, work = ctx.env.settings, ctx.work
        status, message = deep_check_integrity(Path(ctx.temp), override_path=settings.ffmpeg_path or None)
        work.record_scan("deep_check", status, message)
        if status == STATUS_TOOL_MISSING:
            return _tool_missing(ctx, message)
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


class TagLookupStep(Step):
    key = "tags"
    label = "Fill missing tags (MusicBrainz / AcoustID)"
    description = (
        "Finds the file's album on MusicBrainz (by its tags or folder name, and by sound when fpcalc is "
        "installed) and fills tags that are EMPTY. Existing values are never replaced unless the option "
        "says so. A match made by fingerprint has AcoustID's score as its confidence; one made by tags "
        "needs the album and artist tags to agree exactly to reach 90%. Below the threshold the match is "
        "listed under Needs review and nothing is written. Needs network access."
    )
    options = (
        OptionSpec("overwrite", "Also replace different existing values", "bool", False),
        OptionSpec("fingerprint", "Identify by sound (needs fpcalc)", "bool", True),
    )

    def run(self, ctx: Mp3Ctx) -> StepResult:
        work, overwrite = ctx.work, ctx.step_options["overwrite"]
        if not overwrite and all(getattr(work, k) for k in _LOOKUP_TRIGGER_FIELDS):
            return StepResult.nothing()  # complete already: no network round trip
        folder = os.path.dirname(ctx.original)
        lookup = _lookup_folder(ctx.env, folder, ctx.step_options["fingerprint"])
        if lookup.error:
            ctx.note(f"Tag lookup unavailable: {lookup.error}")
            return StepResult.nothing()
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


class CoverStep(Step):
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

    def run(self, ctx: Mp3Ctx) -> StepResult:
        work = ctx.work
        if work.cover_change_pending:
            return StepResult.nothing()
        has_cover = work.has_cover if work.has_cover is not None else read_cover(ctx.temp) is not None
        if has_cover:
            return StepResult.nothing()
        if ctx.step_options["folder"]:
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
        if ctx.step_options["online"] and work.musicbrainz_albumid:
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


# --- steps: save and rename ----------------------------------------------------


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


class SaveStep(Step):
    """Internal, always last of the file-changing steps (see recipe_for_run);
    not offered in the recipe editor."""

    key = "save"
    label = "Save file"
    description = "Writes the result in place; the original goes to the Recycle Bin."

    def run(self, ctx: Mp3Ctx) -> StepResult:
        work = ctx.work
        if not work.dirty:
            return StepResult.nothing()  # nothing to write (every step found nothing / was off)
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
        self._adopt(ctx)
        if result.backup_kept:
            ctx.note(result.warning)
            return StepResult.applied(f"saved; the original is kept at {result.backup}")
        return StepResult.applied("saved in place; the original is in the Recycle Bin")

    @staticmethod
    def _adopt(ctx: Mp3Ctx) -> None:
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


class RenameStep(Step):
    """Pinned after the save (see recipe_for_run): renames the finished
    file with the pattern last used in Rename / Export Files."""

    key = "rename"
    label = "Rename by saved pattern"
    description = (
        "Renames the file with the pattern you last used in File > Rename / Export Files (and its "
        "zero-pad/ASCII choices), without overwriting anything; logged for Undo Last Rename. Always runs "
        "last, after the file is saved. Off until a pattern has been used."
    )

    def __init__(self, settings: Settings | None = None):
        self.default_enabled = bool(settings and settings.pattern_history)

    def run(self, ctx: Mp3Ctx) -> StepResult:
        settings, work = ctx.env.settings, ctx.work
        if ctx.save_failed:
            return StepResult.nothing()
        if not settings.pattern_history:
            ctx.note("Rename skipped: no rename pattern saved yet (use File > Rename / Export Files once)")
            return StepResult.nothing()
        if not work.title.strip():
            ctx.note("Rename skipped: the file has no title")
            return StepResult.nothing()
        pattern = settings.pattern_history[0]
        old_path = str(ctx.mp3.path)
        stem, ext = os.path.splitext(os.path.basename(old_path))
        values = {key: getattr(work, key, "") or "" for key, _label, _multiline in FIELDS}
        if settings.rename_zero_pad:
            values["track"] = zero_pad_numeric_value(values["track"], settings.rename_zero_pad_width)
        new_stem = render_filename(values, pattern, fallback=stem, ascii_only=settings.ascii_filenames)
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


# --- catalogue, recipe ---------------------------------------------------------


def build_catalogue(settings: Settings | None = None) -> list[Step]:
    """The steps the recipe editor offers, in default order. `settings`
    decides whether Rename starts enabled (only once a pattern exists)."""
    return [
        IntegrityStep(),
        BpmStep(),
        KeyStep(),
        LoudnessStep(),
        DeepCheckStep(),
        TagLookupStep(),
        CoverStep(),
        RenameStep(settings),
    ]


def run_catalogue(settings: Settings | None = None) -> list[Step]:
    """build_catalogue() plus the internal save step, for the engine."""
    return build_catalogue(settings) + [SaveStep()]


def recipe_for_run(recipe: Recipe, catalogue: list[Step] | None = None) -> Recipe:
    """The recipe as the engine runs it: the user's steps in their order
    (steps an older saved recipe lacks, from `catalogue`, before the
    end), then Save, then Rename. Save is always on and Rename always
    last whatever a hand-edited settings file says."""
    pinned = ("save", "rename")
    user_order = [k for k in recipe.order if k not in pinned]
    user_order += [s.key for s in (catalogue or []) if s.key not in pinned and s.key not in user_order]
    enabled = {k: v for k, v in recipe.enabled.items() if k != "save"}
    return Recipe(
        order=user_order + list(pinned),
        enabled=enabled,
        options={k: dict(v) for k, v in recipe.options.items()},
        confidence_threshold=recipe.confidence_threshold,
    )


def recipe_to_setting(recipe: Recipe) -> str:
    """One-line JSON for the ini value (Settings.redact_recipe)."""
    import json

    return json.dumps(recipe.to_dict(), separators=(",", ":"))


def recipe_from_setting(text: str, catalogue: list[Step]) -> Recipe:
    """The saved recipe; nothing saved (or unreadable) means the defaults."""
    if not (text or "").strip():
        return Recipe.default_for(catalogue)
    return Recipe.from_json(text)
