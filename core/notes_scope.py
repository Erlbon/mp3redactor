"""
core/notes_scope.py -- the choices of the first question of Tools > Create Notes ("which folder?").

Pure logic, no Qt: gui/notes_dialog.py shows what scope_choices() returns. The selected files are only ever a
CHOICE in the list (and the pre-ticked one when there is a selection), never used without the person pressing
Continue.

Order and captions:
  1 the folder you opened            last Open Folder (last_directory), else where the loaded files come from
  2 the artist / album of the selected files
  3 one level up                     the folder above (1), never a drive root
  4 the folder you used last time    settings.notes_last_folder
  5 the library root                 settings.library_root
Folders that do not exist are left out; the same folder (compared case-insensitively, resolved) appears once with
its captions joined. At most MAX_CHOICES.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from core.notes_flow import album_folder_of, artist_folder_of_file
from core.notes_kinds import parse_kinds
from core.notes_local import ALL_KINDS, KIND_ALBUM, KIND_ARTIST, KIND_GENRE

MAX_CHOICES = 5

def count_targets(folder: str, include_subfolders: bool = True) -> tuple[int, int, int]:
    """(artist, album, genre) folders under `folder` that would be looked up (no note yet): what the scope dialog's time
    hint is based on. A quick directory walk (no tags are read); it can take a moment on a very large library."""
    from core.notes_local import KIND_GENRE, scan

    found = scan([folder], include_subfolders=include_subfolders, kinds=ALL_KINDS)
    kinds = [t.kind for t in found.targets]
    return kinds.count(KIND_ARTIST), kinds.count(KIND_ALBUM), kinds.count(KIND_GENRE)


def kinds_sentence(kinds) -> str:
    """The review page's header sentence: which kinds of notes were looked up."""
    kinds = parse_kinds(kinds)
    if kinds == (KIND_ARTIST,):
        return "Looked up: artist notes only (no album lookups)."
    if kinds == (KIND_ALBUM,):
        return "Looked up: album notes only."
    if kinds == (KIND_GENRE,):
        return "Looked up: genre / collection notes only (no artist or album lookups, no tags read)."
    names = [{KIND_ARTIST: "artist", KIND_ALBUM: "album", KIND_GENRE: "genre / collection"}[k] for k in kinds]
    return "Looked up: " + (", ".join(names[:-1]) + " and " + names[-1]) + " notes."

CAP_OPENED = "the folder you opened"
CAP_ARTIST = "the artist of the selected files"
CAP_ALBUM = "the album of the selected files"
CAP_UP = "one level up: also the other artists next to it"
CAP_LAST = "the folder you used last time"
CAP_LIBRARY = "the library folder from Preferences"


@dataclass
class ScopeChoice:
    path: str
    caption: str
    default: bool = False


def _key(path: str) -> str:
    try:
        return os.path.normcase(str(Path(path).resolve()))
    except OSError:
        return os.path.normcase(os.path.abspath(path))


def _is_root(path: str) -> bool:
    p = Path(path)
    return p.parent == p


def opened_folder(loaded: list[str], last_directory: str) -> tuple[str, int]:
    """(the folder the person opened, how many loaded files are in it; 0 when no list is loaded)."""
    parents = [str(Path(f).parent) for f in loaded]
    if last_directory and os.path.isdir(last_directory):
        if not parents:
            return last_directory, 0
        base = _key(last_directory)
        if all(_key(p) == base or _key(p).startswith(base.rstrip("\\/") + os.sep) for p in parents):
            return last_directory, len(loaded)
    if not parents:
        return "", 0
    try:
        common = os.path.commonpath(parents)
    except ValueError:  # several drives
        return "", 0
    return common, len(loaded)


def scope_choices(
    loaded: Iterable[str], selected: Iterable[str], last_directory: str = "", notes_last_folder: str = "",
    library_root: str = "",
) -> list[ScopeChoice]:
    loaded, selected = [str(p) for p in loaded], [str(p) for p in selected]
    found: list[ScopeChoice] = []
    index: dict[str, ScopeChoice] = {}

    def add(path: str, caption: str, default: bool = False) -> Optional[ScopeChoice]:
        if not path or not os.path.isdir(path):
            return None
        key = _key(path)
        if key in index:
            existing = index[key]
            if caption not in existing.caption:
                existing.caption += "; " + caption
            existing.default = existing.default or default
            return existing
        choice = ScopeChoice(path, caption, default)
        index[key] = choice
        found.append(choice)
        return choice

    opened, count = opened_folder(loaded, last_directory)
    cap = CAP_OPENED if not count else (
        f"{CAP_OPENED}: all {count} loaded files are in it" if count > 1 else f"{CAP_OPENED}: the loaded file is in it")
    add(opened, cap)

    artists: list[str] = []
    albums: list[str] = []
    for file in selected:
        for target, bucket in ((str(artist_folder_of_file(file)), artists), (str(album_folder_of(file)), albums)):
            if _key(target) not in {_key(b) for b in bucket}:
                bucket.append(target)
    for i, a in enumerate(artists[:2]):
        add(a, CAP_ARTIST, default=(i == 0))
    for album in albums[:2]:
        if _key(album) not in {_key(a) for a in artists}:
            add(album, CAP_ALBUM)

    if opened and not _is_root(opened):
        above = str(Path(opened).parent)
        if not _is_root(above):
            add(above, CAP_UP)
    add(notes_last_folder, CAP_LAST)
    add(library_root, CAP_LIBRARY)

    if len(found) > MAX_CHOICES:
        keep = [c for c in found if c.default]
        rest = [c for c in found if not c.default]
        keep_set = {id(c) for c in keep}
        room = MAX_CHOICES - len(keep)
        allowed = {id(c) for c in rest[:room]} | keep_set
        found = [c for c in found if id(c) in allowed]
    if found and not any(c.default for c in found):
        found[0].default = True
    return found
