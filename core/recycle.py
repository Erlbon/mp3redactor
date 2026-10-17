"""
core/recycle.py

Where a file goes when it must be replaced but the drive has NO Recycle Bin (a network share, a mapped network drive,
a removable disk): the backup folder of Preferences > Library (redactor_common.core.trash.RECYCLE_FALLBACK_FOLDER_KEY,
Settings.recycle_fallback_folder). Without it such a replacement is refused and the older file stays where it is.

Read from the settings file once, then kept (like core/history.py's switch); Preferences and an imported settings bundle
change it for the running app with set_recycle_fallback_folder(). Qt-free.
"""

from __future__ import annotations

import threading
from typing import Callable

from redactor_common.core.trash import RECYCLE_FALLBACK_FOLDER_KEY, trash_with_fallback  # noqa: F401 -- the shared key

BACKUP_FOLDER_HELP = (
    "On a network drive there is no Recycle Bin: set a backup folder here or updates of notes there are refused. "
    "When a note is refreshed (Tools > Create Notes > review older notes) the older note goes to the Recycle Bin; on a "
    "network share or a removable disk, which has none, it goes into this folder instead, under a dated subfolder that "
    "mirrors its path. Leave blank for none: a refresh on such a drive is then refused and the older note stays."
)

_lock = threading.Lock()
_folder: str | None = None  # None: not read from the settings file yet


def recycle_fallback_folder() -> str:
    """The backup folder ("" = none)."""
    global _folder
    with _lock:
        known = _folder
    if known is not None:
        return known
    try:
        from core.settings import load_settings

        value = (load_settings().recycle_fallback_folder or "").strip()
    except Exception:  # noqa: BLE001 -- an unreadable settings file means none
        value = ""
    with _lock:
        if _folder is None:
            _folder = value
        return _folder


def set_recycle_fallback_folder(folder: str | None) -> None:
    """The running app's value (Preferences OK/Apply, an imported bundle). Does not save: the caller saves Settings.
    None: read the settings file again."""
    global _folder
    with _lock:
        _folder = None if folder is None else str(folder).strip()


def note_mover() -> Callable[[str], None]:
    """The mover for a replaced / undone note: Recycle Bin with retries, else the backup folder, else refused."""
    return trash_with_fallback(recycle_fallback_folder)
