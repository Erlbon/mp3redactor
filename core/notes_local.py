"""
core/notes_local.py

The LOCAL side of the folder notes (core/notes_flow.py): which folders of a library are at the artist level and
which at the album level (docs/library-layout.md of Pluteus: Music/Artist/Album (Year)/01 Song.mp3), which of
them still lack a note of our own, and what the folder actually holds (the albums, the files, their tags).

Qt-free. Finding the targets only lists directories (cheap, no tags are read); `read_artist` / `read_album` read
the tags of one target's files, through the app's own reader, when its proposal is built.

The layout rule, in short:
  - album folder  = has MP3 files directly in it (or in CD1/Disc 2 sub-folders) and no album folders below it;
  - artist folder = has at least one album folder directly below it (loose MP3 files beside them are ignored);
  - genre folder  = has at least one ARTIST folder directly below it and is not itself an artist or album folder,
                    and is not the library root (the folder the scan started from, when it recurses), or is an
                    underscore grouping folder (_Name: a grouping, not a series/artist, like in the comics layout).
                    The levels are decided by depth from the audio files, never by a folder's name: Artist/Album has no
                    genre level, Genre/Artist/Album has one. A folder that only holds genre folders (the library root
                    above a genre level) is none of these and gets no note;
  - anything else (a folder of scans) is neither: it gets no note.
"""

from __future__ import annotations

import glob
import os
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

from core.mp3_file import MP3File
from redactor_common.core.notes import NoteState, existing_note, existing_sidecar, note_status
from redactor_common.core.temp_names import is_app_temp_name

KIND_ARTIST = "artist"
KIND_ALBUM = "album"
KIND_GENRE = "genre"  # a folder above the artists (Music/Genre/Artist/Album): its note is about.md
KINDS = (KIND_ARTIST, KIND_ALBUM)  # what "both" means
ALL_KINDS = (KIND_ARTIST, KIND_ALBUM, KIND_GENRE)
NOTE_KIND_OF = {KIND_ARTIST: "artist", KIND_ALBUM: "album", KIND_GENRE: "about"}  # the note file's kind

_DISC_FOLDER = re.compile(r"^(cd|disc|disk|dvd)\s*[-_. ]?\s*\d+\b", re.IGNORECASE)
_YEAR_SUFFIX = re.compile(r"\s*[\(\[]\s*((?:1[89]|20)\d{2})\s*[\)\]]\s*$")
# Folder names that are a place to keep music in, not an artist.
GENERIC_FOLDER_NAMES = frozenset({
    "music", "musik", "musikk", "mp3", "mp3s", "audio", "songs", "library", "various artists", "various", "va",
    "compilations", "compilation", "soundtracks", "soundtrack", "unknown artist", "unknown", "incoming",
    "downloads", "new", "misc", "singles",
})


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------

def split_year(folder_name: str) -> tuple[str, str]:
    """('Vienna', '1980') from 'Vienna (1980)' (also [1980]); the name unchanged and '' without a year."""
    match = _YEAR_SUFFIX.search(folder_name)
    if not match:
        return folder_name.strip(), ""
    return folder_name[: match.start()].strip(), match.group(1)


def fold_name(text: str) -> str:
    """A name for comparing: accents and case gone, '&' as 'and', punctuation and spacing collapsed."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c)).casefold().replace("&", " and ")
    return re.sub(r"[^0-9a-z]+", " ", text).strip()


# Folders that hold many artists' work: a COLLECTION (about.md level), never an artist (artist.md).
COLLECTION_NAMES = frozenset({
    "various artists", "various", "va", "compilations", "compilation", "samplers", "sampler", "soundtracks",
    "soundtrack", "ost", "osts", "games", "game music", "film music", "movie soundtracks", "diverse",
    "verschiedene interpreten",
})
VARIOUS_ARTIST_NAMES = frozenset({"various artists", "various", "va", "v a", "diverse", "verschiedene interpreten"})


def is_collection_name(name: str) -> bool:
    return fold_name(name.lstrip("_")) in COLLECTION_NAMES


def is_generic_name(name: str) -> bool:
    return fold_name(name) in GENERIC_FOLDER_NAMES


# An ALPHABETICAL GROUPING folder (Bands/D/<Band>/<Album>): a single letter or digit, '#', a range such as 'A-C',
# 'A - F', '0-9', 'Aa-Az', or a bucket name. It only organises artist folders: it gets no note and no proposal and is
# listed in the scan summary ('skipped: alphabetical grouping'). Only a folder that sits ABOVE artist folders counts
# (a band really called 'D' or 'X' that holds albums is an artist folder).
GROUPING_BUCKET_NAMES = frozenset({"misc", "other", "others", "various"})
_GROUPING_RANGE = re.compile(r"^([0-9A-Za-zÀ-ɏ]{1,2})\s*[-‐-―]\s*([0-9A-Za-zÀ-ɏ]{1,2})$")
GROUPING_REASON = "alphabetical grouping"


def is_alphabetical_grouping(name: str) -> bool:
    """Case-insensitive, trailing punctuation ignored ('D', 'd.', '#', 'A-C', 'A - C', '0-9', 'Aa-Az', 'Misc')."""
    text = (name or "").strip()
    stripped = text.rstrip(" .,:;!") or text
    if len(stripped) == 1 and (stripped.isalnum() or stripped == "#"):
        return True
    if _GROUPING_RANGE.match(stripped):
        return True
    return fold_name(stripped) in GROUPING_BUCKET_NAMES


# ---------------------------------------------------------------------------
# Folders
# ---------------------------------------------------------------------------

def _entries(folder: Path) -> tuple[list[Path], list[Path]]:
    """(MP3 files, sub-folders) directly in `folder`, natural name order. Symlinked folders are not followed,
    the app's own leftovers and hidden names are skipped."""
    files: list[Path] = []
    dirs: list[Path] = []
    try:
        with os.scandir(folder) as entries:
            for entry in entries:
                if entry.name.startswith("."):
                    continue
                try:
                    if entry.is_dir(follow_symlinks=False):
                        dirs.append(Path(entry.path))
                    elif entry.name.lower().endswith(".mp3") and not is_app_temp_name(entry.name):
                        files.append(Path(entry.path))
                except OSError:
                    continue
    except OSError:
        return [], []
    key = lambda p: [int(t) if t.isdigit() else t.casefold() for t in re.split(r"(\d+)", p.name)]  # noqa: E731
    return sorted(files, key=key), sorted(dirs, key=key)


def disc_folders(folder: Path) -> list[Path]:
    """The CD1 / Disc 2 sub-folders of `folder` that hold MP3 files."""
    return [d for d in _entries(folder)[1] if _DISC_FOLDER.match(d.name) and _entries(d)[0]]


def album_files(folder: Path) -> list[Path]:
    """Every MP3 of an album folder: the ones directly in it, then those of its disc folders."""
    files, _ = _entries(folder)
    for disc in disc_folders(folder):
        files += _entries(disc)[0]
    return files


def _is_album_like(folder: Path) -> bool:
    files, _dirs = _entries(folder)
    return bool(files) or bool(disc_folders(folder))


def album_children(folder: Path) -> list[Path]:
    """The sub-folders of `folder` that are album folders (disc folders are not albums of their own)."""
    return [d for d in _entries(folder)[1] if not _DISC_FOLDER.match(d.name) and _is_album_like(d)]


def album_folder_of(file: str | os.PathLike) -> Path:
    """The album folder an MP3 file belongs to: its folder, or the folder above when that is a 'Disc 2' style
    sub-folder."""
    parent = Path(file).parent
    return parent.parent if _DISC_FOLDER.match(parent.name) else parent


def classify(folder: Path) -> str:
    """'artist', 'album' or '' (see the module text)."""
    if album_children(folder):
        return KIND_ARTIST
    return KIND_ALBUM if _is_album_like(folder) else ""


# ---------------------------------------------------------------------------
# Finding the targets
# ---------------------------------------------------------------------------

@dataclass
class Target:
    """One folder that should get a note."""

    folder: Path
    kind: str  # KIND_ARTIST / KIND_ALBUM
    parent_name: str = ""  # the folder above (the artist, in the library layout), a hint for an album
    update: Optional["NoteState"] = None  # set when the folder has an older note of ours to refresh (scan include_outdated)

    @property
    def note_kind(self) -> str:
        """The kind of note file this level gets (a genre folder gets about.md)."""
        return NOTE_KIND_OF[self.kind]

    @property
    def note_file_name(self) -> str:
        return f"{self.note_kind}.md"

    @property
    def label(self) -> str:
        return self.folder.name


@dataclass
class Skipped:
    folder: Path
    kind: str
    reason: str
    state: str = ""  # with scan(include_outdated=True): the existing note's state (core: redactor_common STATE_*)


@dataclass
class ScanResult:
    targets: list[Target] = field(default_factory=list)
    skipped: list[Skipped] = field(default_factory=list)
    groupings: list[Skipped] = field(default_factory=list)  # alphabetical grouping folders: no note, no proposal
    missing: list[str] = field(default_factory=list)  # arguments that named nothing


def _resolve_roots(paths: Iterable[str | os.PathLike]) -> tuple[list[Path], list[str]]:
    roots: list[Path] = []
    missing: list[str] = []
    seen: set[str] = set()

    def add(path: Path) -> None:
        key = os.path.normcase(str(path.resolve()))
        if key not in seen:
            seen.add(key)
            roots.append(path)

    for argument in paths:
        text = os.fspath(argument)
        if os.path.lexists(text):
            matches = [text]
        elif any(c in text for c in "*?["):
            matches = sorted(glob.glob(text))
        else:
            matches = []
        if not matches:
            missing.append(text)
            continue
        for match in matches:
            path = Path(os.path.abspath(match))
            if path.is_dir():
                add(path)
            elif path.suffix.lower() == ".mp3":
                add(path.parent)  # a file stands for the folder it lives in
            else:
                missing.append(match)
    return roots, missing


def _has_audio_below(folder: Path, depth: int = 4) -> bool:
    files, dirs = _entries(folder)
    return bool(files) or (depth > 1 and any(_has_audio_below(d, depth - 1) for d in dirs))


def classify_level(folder: Path, is_root: bool = False, include_subfolders: bool = True) -> str:
    """classify() plus the genre level: 'genre', 'artist', 'album' or ''. An underscore folder with audio
    anywhere below it is a grouping (genre/about level) whatever else it looks like. Otherwise a folder with
    artist folders directly below it is a genre folder, except the library root: the folder a recursive scan
    started from is never given a note (with `include_subfolders` False the named folder is taken as meant)."""
    if folder.name.startswith("_") and _has_audio_below(folder):
        return KIND_GENRE
    kind = classify(folder)
    if kind:
        return kind
    if is_root and include_subfolders:
        return ""
    if any(classify(child) == KIND_ARTIST for child in _entries(folder)[1]):
        return KIND_GENRE
    return ""


def scan(
    paths: Iterable[str | os.PathLike], include_subfolders: bool = True, kinds: Iterable[str] = KINDS,
    include_outdated: bool = False,
) -> ScanResult:
    """The folders under `paths` (folders, MP3 files, wildcards) that are at the artist or album level and
    have no note of our own yet.

    A folder with an artist.md / author.md / series.md / album.md / about.md of ANY letter case is skipped
    (`skipped`, with the file's name): Pluteus shows the first of those, so a new one would hide the person's
    own. .nfo / .opf sidecars do not count. With `include_subfolders` False only the named folders themselves
    are looked at (their children are still examined to tell an artist folder from an album folder).

    `include_outdated` (default OFF): a folder whose existing note is OUR note of the right kind in the state
    'outdated-untouched' (stamped, an older format, nobody edited it) or 'legacy' (written by the first releases, no
    stamp) becomes a target with `update` set, to be refreshed on request. 'edited', 'foreign' and 'current' notes are
    never targets: they are in `skipped` with their state, so the summary can say they were left alone."""
    from core.notes_render import CURRENT_NOTE_FORMAT

    wanted = set(kinds)
    result = ScanResult()
    roots, result.missing = _resolve_roots(paths)
    seen: set[str] = set()

    def consider(folder: Path, kind: str) -> None:
        key = os.path.normcase(str(folder))
        if key in seen:
            return
        if kind not in wanted and not (kind == KIND_ARTIST and is_collection_name(folder.name)):
            return
        seen.add(key)
        if kind == KIND_GENRE and is_alphabetical_grouping(folder.name):
            result.groupings.append(Skipped(folder, kind, GROUPING_REASON))
            return
        if kind == KIND_ARTIST and is_collection_name(folder.name):
            kind = KIND_GENRE  # 'Various Artists', 'Soundtracks'...: a collection, its note is about.md
            if kind not in wanted:
                return
        if kind in (KIND_ARTIST, KIND_GENRE) and is_generic_name(folder.name.lstrip("_")) and not is_collection_name(folder.name):
            result.skipped.append(Skipped(folder, kind, f"'{folder.name}' is not a {kind} folder name"))
            return
        found = existing_note(folder)
        if found:
            if include_outdated and Path(found).name.lower() == f"{NOTE_KIND_OF[kind]}.md":
                state = note_status(found, CURRENT_NOTE_FORMAT)
                if state.replaceable:
                    result.targets.append(Target(folder, kind, parent_name=folder.parent.name, update=state))
                    return
                result.skipped.append(Skipped(folder, kind, f"already has {Path(found).name} ({state.kind})", state.kind))
                return
            result.skipped.append(Skipped(folder, kind, f"already has {Path(found).name}"))
            return
        result.targets.append(Target(folder, kind, parent_name=folder.parent.name))

    def walk(folder: Path, is_root: bool = False) -> None:
        kind = classify_level(folder, is_root, True)
        if kind:
            consider(folder, kind)
        if kind == KIND_ALBUM:
            return  # nothing below an album folder is a note level
        for child in _entries(folder)[1]:
            walk(child)

    for root in roots:
        if include_subfolders:
            walk(root, True)
        else:
            kind = classify_level(root, True, False)
            if kind:
                consider(root, kind)
    return result


# ---------------------------------------------------------------------------
# What the folders hold
# ---------------------------------------------------------------------------

@dataclass
class LocalTrack:
    path: Path
    title: str = ""
    track: Optional[int] = None
    disc: Optional[int] = None
    seconds: Optional[float] = None
    artist: str = ""
    albumartist: str = ""
    album: str = ""
    year: str = ""
    composer: str = ""
    genre: str = ""
    load_error: str = ""

    @property
    def name(self) -> str:
        return self.path.name


@dataclass
class LocalAlbum:
    folder: Path
    title: str = ""  # from the folder name, without ' (Year)'
    year: str = ""  # folder name first, else the tags
    tag_titles: list[str] = field(default_factory=list)  # the album tag(s), most common first
    artist: str = ""  # album-artist / artist tag, most common
    tracks: list[LocalTrack] = field(default_factory=list)
    album_artist: str = ""  # the album-artist tag alone, most common
    track_artists: list[str] = field(default_factory=list)  # the artist tags, most common first
    composers: list[str] = field(default_factory=list)
    genres: list[str] = field(default_factory=list)

    @property
    def folder_name(self) -> str:
        return self.folder.name

    @property
    def track_count(self) -> int:
        return len(self.tracks)

    @property
    def titles(self) -> list[str]:
        """Every name this album goes by locally (folder title, then the tags'), de-duplicated by folded form."""
        out: list[str] = []
        for title in [self.title, *self.tag_titles]:
            if title and fold_name(title) not in [fold_name(t) for t in out]:
                out.append(title)
        return out


@dataclass
class LocalArtist:
    folder: Path
    name: str = ""  # the folder name
    tag_names: list[str] = field(default_factory=list)  # artist / album-artist tags, most common first
    albums: list[LocalAlbum] = field(default_factory=list)
    loose_tracks: int = 0  # MP3 files directly in the artist folder (singles, no album folder)

    @property
    def names(self) -> list[str]:
        out: list[str] = []
        for name in [self.name, *self.tag_names]:
            if name and fold_name(name) not in [fold_name(n) for n in out]:
                out.append(name)
        return out


@dataclass
class LocalGenre:
    folder: Path
    name: str = ""
    artists: list[tuple[Path, int]] = field(default_factory=list)  # (artist folder, number of album folders)
    albums: list[tuple[Path, int]] = field(default_factory=list)  # (album folder, number of files): a collection


def read_genre(folder: Path) -> LocalGenre:
    """The artist folders directly under a genre folder and how many albums each holds (folder structure only:
    no tags are read for a genre note)."""
    artists = []
    for child in _entries(folder)[1]:
        albums = album_children(child)
        if albums:
            artists.append((child, len(albums)))
    albums = [(child, len(album_files(child))) for child in album_children(folder)]
    return LocalGenre(folder=folder, name=folder.name.lstrip("_").strip(), artists=artists, albums=albums)


def _leading_int(text: str) -> Optional[int]:
    match = re.match(r"\s*(\d+)", text or "")
    return int(match.group(1)) if match else None


def _common(values: Iterable[str]) -> list[str]:
    """The distinct non-empty values, most frequent first (compared by folded form, first spelling kept)."""
    counts: Counter = Counter()
    spelling: dict[str, str] = {}
    for value in values:
        value = (value or "").strip()
        key = fold_name(value)
        if key:
            counts[key] += 1
            spelling.setdefault(key, value)
    return [spelling[k] for k, _n in counts.most_common()]


Loader = Callable[[list[Path]], list[MP3File]]


def default_loader(paths: list[Path]) -> list[MP3File]:
    from core.scan_service import load_files

    return load_files(paths)


def read_album(folder: Path, loader: Optional[Loader] = None) -> LocalAlbum:
    """The album folder's files and tags."""
    loader = loader or default_loader
    files = album_files(folder)
    tracks = []
    for mp3 in loader(files):
        tracks.append(LocalTrack(
            path=Path(mp3.path), title=mp3.title, track=_leading_int(mp3.track), disc=_leading_int(mp3.discnumber),
            seconds=mp3.duration_seconds, artist=mp3.artist, albumartist=mp3.albumartist, album=mp3.album,
            year=mp3.year, composer=getattr(mp3, "composer", "") or "", genre=getattr(mp3, "genre", "") or "",
            load_error=mp3.load_error or "",
        ))
    title, year = split_year(folder.name)
    tag_titles = _common(t.album for t in tracks)
    tag_years = [y for y in (str(_leading_int(t.year) or "") for t in tracks) if len(y) == 4]
    artist = (_common(t.albumartist for t in tracks) or _common(t.artist for t in tracks) or [""])[0]
    return LocalAlbum(
        folder=folder, title=title, year=year or (Counter(tag_years).most_common(1)[0][0] if tag_years else ""),
        tag_titles=tag_titles, artist=artist, tracks=tracks,
        album_artist=(_common(t.albumartist for t in tracks) or [""])[0],
        track_artists=_common(t.artist for t in tracks), composers=_common(t.composer for t in tracks),
        genres=_common(t.genre for t in tracks),
    )


def read_artist(folder: Path, loader: Optional[Loader] = None) -> LocalArtist:
    """The artist folder, its album folders and their tags."""
    albums = [read_album(child, loader) for child in album_children(folder)]
    names = _common(a.artist for a in albums) if albums else []
    return LocalArtist(folder=folder, name=folder.name, tag_names=names, albums=albums,
                       loose_tracks=len(_entries(folder)[0]))


def sidecar_for(folder: Path) -> Optional[str]:
    """An artist.nfo / album.nfo / metadata.opf in the folder (any case): a source of text, never a note."""
    return existing_sidecar(folder)
