"""
Orchestrates running checks across a batch of MP3Files, reporting
progress via a callback so the GUI can drive a QProgressDialog the same
way the epub tool does for its load/save/rebuild operations.

Kept deliberately separate from any specific check (tags, integrity,
BPM) so a later addition -- key detection, cover, lyrics -- is just
another function with the same (mp3, ) -> None mutate-in-place shape,
plugged into load_folder()/run_integrity_check()/run_bpm_check() sitting
alongside these two rather than a rewrite.
"""

from collections.abc import Callable, Iterable
from pathlib import Path

from core.bpm_detector import detect_bpm
from core.mp3_file import MP3File
from core.mp3val_runner import check_integrity, fix_integrity
from core.tag_reader import load_tags
from core.tag_writer import save_tags

ProgressCallback = Callable[[int, int], None]  # (completed, total) -> None

MP3_EXTENSIONS = {".mp3"}


def find_mp3_files(paths: Iterable[Path]) -> list[Path]:
    """
    Expands a mix of files and folders (as dropped/selected in the GUI)
    into a flat, de-duplicated, sorted list of .mp3 file paths.
    """
    found: set[Path] = set()
    for p in paths:
        if p.is_dir():
            for child in p.rglob("*"):
                if child.is_file() and child.suffix.lower() in MP3_EXTENSIONS:
                    found.add(child.resolve())
        elif p.is_file() and p.suffix.lower() in MP3_EXTENSIONS:
            found.add(p.resolve())
    return sorted(found)


def load_files(paths: Iterable[Path], progress: ProgressCallback | None = None) -> list[MP3File]:
    """Creates an MP3File per path and populates its tag fields."""
    path_list = list(paths)
    total = len(path_list)
    results = []
    for i, p in enumerate(path_list, start=1):
        mp3 = MP3File(path=p)
        load_tags(mp3)
        results.append(mp3)
        if progress is not None:
            progress(i, total)
    return results


def run_integrity_check(
    files: list[MP3File],
    progress: ProgressCallback | None = None,
    mp3val_path: str | None = None,
) -> None:
    """
    Mutates each file's integrity_status/integrity_message in place.
    mp3val_path is the user's manual override from Settings > Locate
    External Tools (core.settings.Settings.mp3val_path), if set.
    """
    total = len(files)
    for i, mp3 in enumerate(files, start=1):
        status, message = check_integrity(mp3.path, override_path=mp3val_path)
        mp3.integrity_status = status
        mp3.integrity_message = message
        if progress is not None:
            progress(i, total)


def run_integrity_fix(
    files: list[MP3File],
    progress: ProgressCallback | None = None,
    delete_backup: bool = False,
    mp3val_path: str | None = None,
) -> None:
    """
    Attempts to fix each file via mp3val -f, mutating integrity_status/
    integrity_message with the post-fix result (same fields
    run_integrity_check() writes -- a fix is just a check that also
    mutates the file, so there's no separate set of fields to track).

    delete_backup forwards to fix_integrity()'s -nb toggle, mp3val_path
    forwards to its override_path -- see core.settings for where both
    preferences live.

    Deliberately takes an explicit list rather than "all loaded files"
    -- callers should pass only the files the user actually selected for
    fixing, mirroring the epub tool's Rebuild Manifest being a targeted,
    not blanket, action.
    """
    total = len(files)
    for i, mp3 in enumerate(files, start=1):
        status, message = fix_integrity(
            mp3.path, delete_backup=delete_backup, override_path=mp3val_path
        )
        mp3.integrity_status = status
        mp3.integrity_message = message
        if progress is not None:
            progress(i, total)


def run_bpm_check(files: list[MP3File], progress: ProgressCallback | None = None) -> None:
    """Mutates each file's bpm/bpm_status/bpm_message in place."""
    total = len(files)
    for i, mp3 in enumerate(files, start=1):
        bpm, status, message = detect_bpm(mp3.path)
        mp3.bpm = bpm
        mp3.bpm_status = status
        mp3.bpm_message = message
        if progress is not None:
            progress(i, total)


def save_dirty_tags(files: list[MP3File], progress: ProgressCallback | None = None) -> None:
    """
    Writes every dirty file's tags to disk via core.tag_writer.save_tags(),
    which itself clears each file's dirty flag on success or sets its
    save_error and leaves it dirty on failure -- same per-file error
    isolation as the integrity/BPM checks, one bad file doesn't abort
    the rest of the batch. Non-dirty files are skipped entirely (no-op,
    not even re-saved) since they have nothing new to write.

    Deliberately takes an explicit list rather than filtering "all
    loaded files" internally -- callers should already have filtered to
    dirty files before calling (see MainWindow.save_changed()), so the
    progress-dialog item count reflects only the actual work.
    """
    total = len(files)
    for i, mp3 in enumerate(files, start=1):
        save_tags(mp3)
        if progress is not None:
            progress(i, total)
