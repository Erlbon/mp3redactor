"""
core/notes_evidence.py -- "What is in this folder": the local evidence for a row of the Create Notes review page, so the person
can tell WHAT the folder is (cover, track list as the files carry it, tags), also in Cyrillic, Greek, CJK ... No network.

  read_evidence(folder, kind)  -> Evidence   (reads the tags and the cover; run it in a worker thread, the files may be on a share)
  album_evidence(local_album, cover)         -> EvidenceAlbum   (pure: a LocalAlbum -> what is shown)
  evidence_text(evidence)                    -> plain lines (the display model, also used by the CLI-free tests)

The texts are exactly what the files carry (never normalised). An album lists up to MAX_TRACKS tracks, then "... and N more";
an artist lists its albums (title, year, track count) and shows the covers of up to MAX_COVERS of them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from core.cover_art import find_folder_image, read_cover
from core.notes_local import (
    KIND_ALBUM, KIND_ARTIST, Loader, LocalAlbum, LocalTrack, album_children, album_files, album_from_tags, default_loader,
    split_year,
)

MAX_TRACKS = 60
MAX_COVERS = 6
MAX_COVER_BYTES = 8_000_000  # a larger picture is not read for a thumbnail


@dataclass
class EvidenceTrack:
    number: str = ""
    title: str = ""
    artist: str = ""
    length: str = ""


@dataclass
class EvidenceAlbum:
    folder_name: str = ""
    title: str = ""  # the folder's title (as the folder says it)
    year: str = ""
    album_tag: str = ""
    artist_tag: str = ""
    year_tag: str = ""
    files: int = 0
    tracks: list[EvidenceTrack] = field(default_factory=list)  # up to MAX_TRACKS
    more_tracks: int = 0  # the tracks not listed
    cover: Optional[bytes] = None  # the picture as read (the view scales it); None = no cover
    cover_from: str = ""  # 'embedded' or the image file's name
    thumb: Optional[bytes] = None  # set by the view: a scaled PNG, so a cached Evidence holds no big picture


@dataclass
class Evidence:
    kind: str  # 'album' or 'artist'
    folder_name: str = ""
    albums: list[EvidenceAlbum] = field(default_factory=list)  # an album row: one; an artist row: every album folder
    covers: int = 0  # how many of `albums` had their tags / cover read (the first MAX_COVERS)
    loose_files: int = 0
    error: str = ""


def seconds_text(seconds: Optional[float]) -> str:
    if not seconds or seconds < 0:
        return ""
    total = int(round(seconds))
    return f"{total // 60}:{total % 60:02d}"


def _number(track: LocalTrack) -> str:
    if track.track is None:
        return ""
    return f"{track.disc}-{track.track}" if track.disc and track.disc > 1 else str(track.track)


def album_evidence(local: LocalAlbum, cover: Optional[tuple[bytes, str]] = None, with_tracks: bool = True) -> EvidenceAlbum:
    """What is shown of an album: tags, folder name, tracks (original script), the cover as (bytes, where from)."""
    tracks = [EvidenceTrack(_number(t), t.title or Path(t.path).stem, t.artist, seconds_text(t.seconds)) for t in local.tracks]
    shown = tracks[:MAX_TRACKS] if with_tracks else []
    album = EvidenceAlbum(
        folder_name=local.folder.name, title=local.title, year=local.year,
        album_tag=local.tag_titles[0] if local.tag_titles else "", artist_tag=local.artist,
        year_tag=next((t.year for t in local.tracks if t.year), ""),
        files=len(local.tracks), tracks=shown, more_tracks=max(0, len(tracks) - len(shown)) if with_tracks else 0,
    )
    if cover is not None:
        album.cover, album.cover_from = cover[0], cover[1]
    return album


def read_cover_of(folder: Path, files: list[Path]) -> Optional[tuple[bytes, str]]:
    """(picture bytes, 'embedded' or the image file's name): the picture inside the first file that has one (up to 3 files are
    tried), else folder.jpg / cover.jpg beside the files. None when there is none."""
    for path in files[:3]:
        found = read_cover(path)
        if found and len(found[0]) <= MAX_COVER_BYTES:
            return found[0], "embedded"
    if files:
        image = find_folder_image(files[0])
        if image is not None:
            try:
                if image.stat().st_size <= MAX_COVER_BYTES:
                    return image.read_bytes(), image.name
            except OSError:
                return None
    return None


def read_evidence(folder: Path, kind: str, loader: Optional[Loader] = None) -> Evidence:
    """Read what the folder holds. An album: every file's tags and the cover. An artist: the albums by their folder names and
    file counts, and of the first MAX_COVERS the tags of one file plus the cover. Never raises."""
    folder = Path(folder)
    loader = loader or default_loader
    evidence = Evidence(kind=kind, folder_name=folder.name)
    try:
        if kind == KIND_ALBUM:
            files = album_files(folder)
            local = album_from_tags(folder, loader(files)) if files else LocalAlbum(folder=folder)
            evidence.albums = [album_evidence(local, read_cover_of(folder, files))]
            evidence.covers = 1
        elif kind == KIND_ARTIST:
            children = album_children(folder)
            for index, child in enumerate(children):
                files = album_files(child)
                title, year = split_year(child.name)
                if index < MAX_COVERS and files:
                    local = album_from_tags(child, loader(files[:1]))
                    one = album_evidence(local, read_cover_of(child, files), with_tracks=False)
                    one.files = len(files)
                    one.title, one.year = title, year or one.year
                    evidence.albums.append(one)
                    evidence.covers += 1
                else:
                    evidence.albums.append(EvidenceAlbum(folder_name=child.name, title=title, year=year, files=len(files)))
            try:
                evidence.loose_files = len([p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".mp3"])
            except OSError:
                pass
    except Exception as exc:  # noqa: BLE001 -- a folder that cannot be read is said, never a traceback
        evidence.error = f"The folder could not be read: {exc}"
    return evidence


def evidence_lines(evidence: Evidence) -> list[str]:
    """The text of the panel, line by line (the view renders these; tests read them)."""
    lines: list[str] = []
    if evidence.error:
        return [evidence.error]
    if evidence.kind == KIND_ALBUM and evidence.albums:
        a = evidence.albums[0]
        lines.append(f"Folder: {a.folder_name}")
        lines.append(f"Tags: album '{a.album_tag or '-'}', artist '{a.artist_tag or '-'}', year {a.year_tag or a.year or '-'}")
        lines.append(f"{a.files} MP3 file(s)")
        for t in a.tracks:
            lines.append(" ".join(x for x in (f"{t.number}." if t.number else "-", t.title,
                                              f"- {t.artist}" if t.artist else "", f"({t.length})" if t.length else "") if x))
        if a.more_tracks:
            lines.append(f"... and {a.more_tracks} more")
    elif evidence.kind == KIND_ARTIST:
        lines.append(f"Folder: {evidence.folder_name}")
        total = sum(a.files for a in evidence.albums) + evidence.loose_files
        lines.append(f"{len(evidence.albums)} album folder(s), {total} MP3 file(s)"
                     + (f", {evidence.loose_files} directly in the folder" if evidence.loose_files else ""))
        for a in evidence.albums:
            tag = f" (tagged '{a.album_tag}' by '{a.artist_tag}')" if a.album_tag or a.artist_tag else ""
            lines.append(f"{a.title or a.folder_name}" + (f" ({a.year})" if a.year else "") + f": {a.files} track(s){tag}")
    return lines
