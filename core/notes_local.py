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
    # a place that holds the artists, not a genre or an artist: a folder you choose by this name is a library root
    "bands", "band", "artists", "media", "albums", "archive", "collection",
})
LIBRARY_ROOT_MESSAGE = (
    "This folder looks like a library root, not a genre: nothing to write here. "
    "Choose the genre folder itself or one level lower.")


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------

_YEAR_PREFIX = re.compile(r"^\s*\[?\s*((?:1[89]|20)\d{2})\s*\]?\s*[-\u2013\u2014:.]\s+(?=.*[^\W\d_])")


def split_year(folder_name: str) -> tuple[str, str]:
    """('Vienna', '1980') from 'Vienna (1980)' (also [1980]) or '1980 - Vienna'; the name as lookup_name() cleans it
    (numbering prefixes gone) and '' without a year."""
    match = _YEAR_SUFFIX.search(folder_name)
    if match:
        return lookup_name(folder_name[: match.start()].strip()), match.group(1)
    lead = _YEAR_PREFIX.match(folder_name)
    if lead:
        return lookup_name(folder_name[lead.end():].strip()), lead.group(1)
    return lookup_name(folder_name.strip()), ""


# A numbering prefix in front of a real name: '1.61 - Ska', '780 - Music', '0 - Bands', '02. Name', '[1981] - Name',
# '[1981] Name', '#12 Name', '1.61. Ska'. Digits and dots (in brackets, or after '#') FOLLOWED BY A SEPARATOR and then a name
# with a letter in it. '1975', '10cc', '311', '2 Unlimited', '3 Doors Down' and '5 Seconds of Summer' have no separator after
# their number, so they stay as they are.
_LETTER_AHEAD = r"(?=.*[^\W\d_])"
_NUMBERING = (
    re.compile(r"^\s*(?:\[\s*\d[\d.]*\s*\]|\d+(?:\.\d+)*\.?)\s*[-\u2013\u2014:]\s+" + _LETTER_AHEAD),
    re.compile(r"^\s*\d+(?:\.\d+)*\.\s+" + _LETTER_AHEAD),
    re.compile(r"^\s*\[\s*\d[\d.]*\s*\]\s+" + _LETTER_AHEAD),
    re.compile(r"^\s*#\s*\d+\s+" + _LETTER_AHEAD),
)
_YEAR_RANGE = re.compile(r"\s*[(\[]\s*\d{4}\s*[-\u2013\u2014]\s*(?:\d{4})?\s*[)\]]\s*$")


def lookup_name(folder_name: str) -> str:
    """The name a folder stands for, for every lookup and for the note's title: numbering / Dewey prefixes ('1.61 - Ska',
    '780 - Music', '02. Name', '[1981] - Name', '#12 Name') and a trailing year range ('Marvel (1961-2012)') taken off. Only
    when a real name is left (a letter in it): a name that is only a number, or that would become empty, is returned as it
    is. The folder keeps its own name for paths and display; this is only what is searched for and written as the title."""
    text = (folder_name or "").strip()
    cleaned = text
    for pattern in _NUMBERING:
        match = pattern.match(cleaned)
        if match:
            cleaned = cleaned[match.end():]
            break
    cleaned = _YEAR_RANGE.sub("", cleaned).strip()
    return cleaned if re.search(r"[^\W\d_]", cleaned) else text


def fold_name(text: str) -> str:
    """A name for comparing: accents and case gone, '&' as 'and', punctuation and spacing collapsed. Letters of every script
    stay ('Дачники' is 'дачники', not nothing: the albums of a Russian band are matched by their Cyrillic titles)."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c)).casefold().replace("&", " and ")
    return re.sub(r"[\W_]+", " ", text).strip()  # letters and digits of ANY script stay (Cyrillic, Greek, CJK ...)


# Folders that hold many artists' work: a COLLECTION (about.md level), never an artist (artist.md).
COLLECTION_NAMES = frozenset({
    "various artists", "various", "va", "compilations", "compilation", "samplers", "sampler", "soundtracks",
    "soundtrack", "ost", "osts", "games", "game music", "film music", "movie soundtracks", "diverse",
    "verschiedene interpreten",
})
VARIOUS_ARTIST_NAMES = frozenset({"various artists", "various", "va", "v a", "diverse", "verschiedene interpreten"})


def is_collection_name(name: str) -> bool:
    """A collection folder name, raw or cleaned of its numbering ('2 - Various Artists')."""
    return any(fold_name(n.lstrip("_")) in COLLECTION_NAMES for n in {name, lookup_name(name)})


def is_generic_name(name: str) -> bool:
    """A place-to-keep-music name, raw or cleaned of its numbering ('780 - Music', '0 - Bands')."""
    return any(fold_name(n) in GENERIC_FOLDER_NAMES for n in {name, lookup_name(name)})


# An ALPHABETICAL GROUPING folder (Bands/D/<Band>/<Album>): a single letter or digit, '#', a range such as 'A-C',
# 'A - F', '0-9', 'Aa-Az', or a bucket name. It only organises artist folders: it gets no note and no proposal and is
# listed in the scan summary ('skipped: alphabetical grouping'). Only a folder that sits ABOVE artist folders counts
# (a band really called 'D' or 'X' that holds albums is an artist folder).
GROUPING_BUCKET_NAMES = frozenset({"misc", "other", "others", "various"})
_GROUPING_RANGE = re.compile(r"^([0-9A-Za-zÀ-ɏ]{1,2})\s*[-‐-―]\s*([0-9A-Za-zÀ-ɏ]{1,2})$")
GROUPING_REASON = "alphabetical grouping"


def is_alphabetical_grouping(name: str) -> bool:
    """Case-insensitive, trailing punctuation ignored ('D', 'd.', '#', 'A-C', 'A - C', '0-9', 'Aa-Az', 'Misc')."""
    for text in dict.fromkeys([(name or "").strip(), lookup_name(name or "")]):  # raw, then without its numbering
        stripped = text.rstrip(" .,:;!") or text
        if len(stripped) == 1 and (stripped.isalnum() or stripped == "#"):
            return True
        if _GROUPING_RANGE.match(stripped) or fold_name(stripped) in GROUPING_BUCKET_NAMES:
            return True
    return False


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
    library_root: bool = False  # a folder the scan started from was left out as a generic place name / a grouping
    missing: list[str] = field(default_factory=list)  # arguments that named nothing

    @property
    def only_a_library_root(self) -> bool:
        """Nothing to write, and the reason is the folder itself: it is empty, or a generic place / grouping (a library
        root), not that its folders already have notes. The message is LIBRARY_ROOT_MESSAGE."""
        if self.targets or self.missing:
            return False
        has_notes = any(s.reason.startswith("already has") for s in self.skipped)
        return not has_notes and (self.library_root or not (self.skipped or self.groupings))


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
    artist folders directly below it is a genre folder. The folder a scan STARTS from is judged like any other: it gets
    its own note when it is eligible (a generic place name such as Music or Bands, and an alphabetical grouping, never
    get one: scan() leaves them out and says it looked like a library root)."""
    if folder.name.startswith("_") and _has_audio_below(folder):
        return KIND_GENRE
    kind = classify(folder)
    if kind:
        return kind
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
    root_keys = {os.path.normcase(str(r)) for r in roots}
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
            result.library_root = result.library_root or key in root_keys
            return
        if kind == KIND_ARTIST and is_collection_name(folder.name):
            kind = KIND_GENRE  # 'Various Artists', 'Soundtracks'...: a collection, its note is about.md
            if kind not in wanted:
                return
        if kind in (KIND_ARTIST, KIND_GENRE) and is_generic_name(folder.name.lstrip("_")) and not is_collection_name(folder.name):
            result.skipped.append(Skipped(folder, kind, f"'{folder.name}' is not a {kind} folder name"))
            result.library_root = result.library_root or key in root_keys
            return
        found = existing_note(folder)
        if found:
            if include_outdated and Path(found).name.lower() == f"{NOTE_KIND_OF[kind]}.md":
                state = note_status(found, CURRENT_NOTE_FORMAT)
                if state.replaceable:
                    result.targets.append(Target(folder, kind, parent_name=lookup_name(folder.parent.name), update=state))
                    return
                result.skipped.append(Skipped(folder, kind, f"already has {Path(found).name} ({state.kind})", state.kind))
                return
            result.skipped.append(Skipped(folder, kind, f"already has {Path(found).name}"))
            return
        result.targets.append(Target(folder, kind, parent_name=lookup_name(folder.parent.name)))

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
    sampled: bool = False  # True: only some of the tags were read (read_artist with max_albums / files_per_album)

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
    return LocalGenre(folder=folder, name=lookup_name(folder.name.lstrip("_").strip()), artists=artists, albums=albums)


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
TAG_WORKERS = 4  # tags are read by this many threads: on a network share the time is waiting, not work


def default_loader(paths: list[Path]) -> list[MP3File]:
    """The tags of `paths`, in order. Four files at a time from a handful of files on (waiting for a share overlaps)."""
    from core.scan_service import load_files

    paths = list(paths)
    if len(paths) < TAG_WORKERS:
        return load_files(paths)
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=TAG_WORKERS, thread_name_prefix="notes-tags") as pool:
        return list(pool.map(lambda path: load_files([path])[0], paths))


def album_from_tags(folder: Path, mp3s: list[MP3File]) -> LocalAlbum:
    """The LocalAlbum of a folder from the tags already read of (some of) its files."""
    tracks = []
    for mp3 in mp3s:
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


def read_album(folder: Path, loader: Optional[Loader] = None) -> LocalAlbum:
    """The album folder's files and tags."""
    loader = loader or default_loader
    return album_from_tags(folder, loader(album_files(folder)))


def read_artist(
    folder: Path, loader: Optional[Loader] = None, max_albums: Optional[int] = None,
    files_per_album: Optional[int] = None,
) -> LocalArtist:
    """The artist folder, its album folders and their tags. With `max_albums` / `files_per_album` only a SAMPLE of the tags
    is read: the first files of the first albums (all of them in ONE call of the loader, so a pooled reader works on all
    at once); the other albums are listed by their folder names alone (title and year from the name, no tags, no tracks),
    and `sampled` says so."""
    loader = loader or default_loader
    children = album_children(folder)
    read_these = children if max_albums is None else children[:max_albums]
    sampled = max_albums is not None and len(children) > max_albums
    file_lists = []
    for child in read_these:
        files = album_files(child)
        if files_per_album is not None and len(files) > files_per_album:
            files, sampled = files[:files_per_album], True
        file_lists.append(files)
    everything = [f for files in file_lists for f in files]
    loaded = loader(everything) if everything else []
    albums = []
    if len(loaded) == len(everything):
        position = 0
        for child, files in zip(read_these, file_lists):
            albums.append(album_from_tags(child, loaded[position:position + len(files)]))
            position += len(files)
    else:  # a loader that drops files: one call per album, as before
        albums = [album_from_tags(child, loader(files)) for child, files in zip(read_these, file_lists)]
    for child in children[len(read_these):]:  # the rest: names only
        title, year = split_year(child.name)
        albums.append(LocalAlbum(folder=child, title=title, year=year))
    names = _common(a.artist for a in albums) if albums else []
    return LocalArtist(folder=folder, name=lookup_name(folder.name), tag_names=names, albums=albums,
                       loose_tracks=len(_entries(folder)[0]), sampled=sampled)


def sidecar_for(folder: Path) -> Optional[str]:
    """An artist.nfo / album.nfo / metadata.opf in the folder (any case): a source of text, never a note."""
    return existing_sidecar(folder)
