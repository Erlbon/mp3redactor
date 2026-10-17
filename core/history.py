"""
core/history.py

This app's side of the operations journal (redactor_common.core.journal):
every tag change the app writes to a file (a plain Save, a Redact save, a
Restore from Tools > History) is recorded with the old and new text of each
changed field, so an unwanted Save can be undone after the app was closed.

OPT-IN (owner, 2026-10-14): nothing is recorded, and no file is created,
unless the user turned it on (Preferences > History, or yes to the question
Tools > History asks): history_enabled() / set_history_enabled().

  get_journal()           the process-wide Journal, `mp3redactor_journal.jsonl`
                           next to the settings file (lazily created)
  fields_text(mp3)         the editable tag fields as plain text (no scan
                           stamps, no technical values, no cover bytes)
  begin_save / end_save    used by the save paths; a journal failure never
                           blocks or fails a save (see take_journal_problem())
  apply_restore(path, v)   the History dialog's writer: the app's own reader,
                           setters and save path, then a read-back check
  record_file_moves(pairs) the rename/move hook: a file's entries follow it
                           (make_rename_log() wires it into the RenameLog)

Covers are not stored (only "cover changed"); replaced or converted files
are covered by the Recycle Bin. Qt-free; safe on Redact's worker thread.
"""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

from redactor_common.core.journal import (
    KIND_REDACT,
    KIND_RESTORE,
    KIND_SAVE,
    Journal,
    default_journal_path,
    diff_fields,
)

from core.app_paths import APP_SLUG, base_dir
from core.mp3_file import JOURNAL_KEYS, MP3File

__all__ = [
    "KIND_REDACT", "KIND_RESTORE", "KIND_SAVE", "apply_restore", "begin_save", "delete_journal", "end_save",
    "JournalMoveLog", "fields_text", "get_journal", "history_enabled", "journal_exists", "journal_path",
    "make_rename_log", "record_file_moves", "set_history_enabled", "set_journal_for_tests", "take_journal_problem",
]

_lock = threading.Lock()
_journal: Journal | None = None
_enabled: bool | None = None  # None: not read from the settings file yet
_problem = ""  # the first journal failure, waiting to be shown once
_problem_reported = False


def journal_path() -> str:
    """Next to the settings file (core.settings: base_dir())."""
    return default_journal_path(APP_SLUG, str(base_dir()))


def get_journal() -> Journal:
    global _journal
    with _lock:
        if _journal is None:
            _journal = Journal(journal_path())
        return _journal


def history_enabled() -> bool:
    """Whether tag changes are recorded: the OPT-IN setting [history]
    enabled, off by default (a missing or garbled value is off). Read from
    the settings file once, then kept; set_history_enabled() changes it."""
    global _enabled
    with _lock:
        known = _enabled
    if known is not None:
        return known
    try:
        from core.settings import load_settings

        value = bool(load_settings().history_enabled)
    except Exception:  # noqa: BLE001 -- an unreadable settings file means off
        value = False
    with _lock:
        if _enabled is None:
            _enabled = value
        return _enabled


def set_history_enabled(enabled: bool | None) -> None:
    """The running app's switch (Preferences OK/Apply, Tools > History's
    question, the window's own settings at startup). Does not save the
    setting: the caller saves Settings. None: read the file again."""
    global _enabled
    with _lock:
        _enabled = None if enabled is None else bool(enabled)


def notes_backup_base() -> str:
    """The folder that holds `notes_backup/` (the text a refreshed note replaced): the folder of the journal, i.e. the
    folder next to the settings file (core.settings: base_dir()); never a library folder."""
    return os.path.dirname(os.path.abspath(get_journal().path))


def note_undo_handlers(journal: Journal | None = None) -> dict:
    """What Tools > History needs to undo a written note: kind -> handler(entry) -> (ok, message), for 'note' (created)
    and 'note-update' (refreshed). The note goes to the Recycle Bin, or to Preferences' backup folder on a drive
    without one."""
    from redactor_common.core.notes_model import KIND_NOTE, KIND_NOTE_UPDATE, make_note_undo_handler

    from core import recycle

    handler = make_note_undo_handler(journal or get_journal(), notes_backup_base(),
                                     fallback_folder=recycle.recycle_fallback_folder)
    return {KIND_NOTE: handler, KIND_NOTE_UPDATE: handler}


def delete_journal() -> str:
    """Sends the journal file to the Recycle Bin (Preferences > History >
    Delete History...; owner rule: deletes go to the Recycle Bin), the kept
    text of refreshed notes (notes_backup) with it, and removes a leftover
    prune temp file. Returns "" or a plain-language problem."""
    path = get_journal().path
    try:
        from redactor_common.core.notes_model import backup_folder

        backups = backup_folder(notes_backup_base())
        if os.path.isdir(backups):
            _trash(backups)
        if os.path.exists(path):
            _trash(path)
        if os.path.exists(path + ".tmp"):
            os.remove(path + ".tmp")  # a half-written prune copy, not data
    except Exception as exc:  # noqa: BLE001 -- TrashError, OSError: reported, not raised
        return f"the history file could not be deleted ({exc})"
    return ""


def _trash(path: str) -> None:
    """The Recycle Bin (redactor_common.core.trash); tests replace it."""
    from redactor_common.core.trash import move_to_trash

    move_to_trash(path)


def record_file_moves(pairs) -> None:
    """The rename/move hook (RenameLog's on_move, the command line's rename and
    move): the journal's entries of each renamed or moved file follow it from
    old to new path, so Restore finds the file where it is now. History off:
    nothing is written. A journal failure never breaks the rename."""
    if not history_enabled():
        return
    pairs = [(os.fspath(old), os.fspath(new)) for old, new in pairs]
    if not pairs:
        return
    try:
        get_journal().record_moves(pairs)
    except Exception as exc:  # noqa: BLE001 -- the rename already happened
        _report(exc)


class JournalMoveLog:
    """A stand-in for the RenameLog where there is none (Redact from the
    command line): its record() only lets the journal follow the file."""

    def record(self, _label, renames, **_move_details) -> None:
        record_file_moves(renames)


def make_rename_log(path: str):
    """The app's RenameLog (File > Undo Last Rename) with the journal hook."""
    from redactor_common.core.rename_log import RenameLog

    return RenameLog(path, on_move=record_file_moves)


def journal_exists() -> bool:
    return os.path.isfile(get_journal().path)


class _ReadOnlyJournal(Journal):
    """The journal as the History dialog sees it while recording is off: it
    can be read and restored from, but nothing is written to it (not even
    the restore's own entry)."""

    def begin(self, *_args, **_kwargs):
        return None

    def finish(self, *_args, **_kwargs) -> None:
        return None

    def mark_restored(self, *_args, **_kwargs) -> None:
        return None


def journal_for_dialog() -> Journal:
    """get_journal() when history is on, else a read-only view of the same file."""
    journal = get_journal()
    return journal if history_enabled() else _ReadOnlyJournal(journal.path)


def set_journal_for_tests(journal: Journal | None, enabled: bool | None = None) -> None:
    """Points the app at another journal (tests: one in tmp_path); None goes
    back to the default one. `enabled` sets the switch (None: read the
    settings file again). Also forgets an earlier journal problem."""
    global _journal, _enabled, _problem, _problem_reported
    with _lock:
        _journal = journal
        _enabled = enabled
        _problem = ""
        _problem_reported = False


def fields_text(mp3: MP3File) -> dict[str, str]:
    """The editable tag fields as plain text (JOURNAL_KEYS: the FIELDS
    entries, MusicBrainz ids, comment and lyrics)."""
    return {key: str(getattr(mp3, key, "") or "") for key in JOURNAL_KEYS}


def _report(exc: BaseException) -> None:
    """A journal failure (disk full, unwritable folder): remembered for the
    status bar and printed to stderr, once per run."""
    global _problem, _problem_reported
    with _lock:
        if _problem_reported or _problem:
            return
        _problem = f"The change history could not be written ({exc}); saving goes on without it."
        message = _problem
    try:
        print(f"mp3redactor: {message}", file=sys.stderr)
    except Exception:  # noqa: BLE001 -- no console (windowed exe)
        pass


def take_journal_problem() -> str:
    """The journal failure to show, once ("" when there is none or it was
    already shown)."""
    global _problem, _problem_reported
    with _lock:
        message, _problem = _problem, ""
        if message:
            _problem_reported = True
        return message


def begin_save(mp3: MP3File, kind: str = KIND_SAVE, path: str | os.PathLike | None = None) -> str | None:
    """Announces the write of `mp3`'s changed fields (old values from its
    baseline). Returns the entry id, or None when nothing worth recording
    changed, history is off (the opt-in setting) or the journal failed
    (never raises). History off: nothing is computed, no file is touched."""
    if not history_enabled():
        return None
    try:
        changes = diff_fields(mp3.baseline, fields_text(mp3))
        cover = mp3.cover_change_pending
        if not changes and not cover:
            return None
        return get_journal().begin(os.fspath(path if path is not None else mp3.path), kind, changes, cover_changed=cover)
    except Exception as exc:  # noqa: BLE001 -- the journal must never block a save
        _report(exc)
        return None


def end_save(entry_id: str | None, ok: bool, error: str = "") -> None:
    if entry_id is None:
        return
    try:
        get_journal().finish(entry_id, ok, error)
    except Exception as exc:  # noqa: BLE001
        _report(exc)


def apply_restore(path: str, values: dict[str, str]) -> str:
    """Writes {field: text} onto the file at `path`: loads its CURRENT tags
    with the app's reader, sets the fields, saves through the app's save
    path (scratch copy swapped in) and reads the file back to check. Returns
    "" or a plain-language problem. Runs on a worker thread (no widgets) and
    writes no journal entry itself: the History dialog records the restore."""
    from core.tag_reader import load_tags
    from core.tag_writer import save_tags

    unknown = [name for name in values if name not in JOURNAL_KEYS]
    if unknown:
        return f"this program does not know the field(s) {', '.join(unknown)}"
    mp3 = MP3File(path=Path(path))
    load_tags(mp3)
    if mp3.load_error:
        return f"the file could not be read ({mp3.load_error})"
    mp3.apply_tags({name: values[name] for name in values})
    if not save_tags(mp3, journal_kind=None):
        return mp3.save_error or "the file could not be saved"
    check = MP3File(path=Path(path))
    load_tags(check)
    if check.load_error:
        return f"the file could not be read back after the restore ({check.load_error})"
    wrong = [name for name, value in values.items() if (getattr(check, name, "") or "").strip() != (value or "").strip()]
    if wrong:
        return f"the file does not read back the restored value of {', '.join(wrong)}"
    return ""
