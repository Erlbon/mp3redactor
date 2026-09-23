"""
Embedded cover art (ID3v2 APIC frames) -- reading, and the pending-change
model the GUI edits before Save writes it (core/tag_writer.py).

Covers are NOT held in memory for every loaded file: a music library can
easily be thousands of files at a few hundred KB of artwork each. On
load, core/tag_reader.py only records whether a cover exists
(MP3File.has_cover); the image itself is read from disk on demand
(read_cover()), in the background, and only for files actually on
screen or selected -- the same lazy approach cbzredactor uses for its
page-1 covers. Only a *pending* change (a new image, or a removal) lives
in memory until Save.

What "the cover" means: the front-cover picture (APIC type 3) if the
file has one, else its first picture of any type -- how most players
choose. Setting a cover replaces every picture frame with a single
front cover, and removing clears them all, so what the preview shows is
exactly what the file ends up with.

Pure logic, no Qt dependency.
"""

from __future__ import annotations

import itertools
from pathlib import Path

FRONT_COVER = 3  # ID3v2 APIC picture type "Cover (front)"
SUPPORTED_MIMES = ("image/jpeg", "image/png")

# Filenames checked, in order, by find_folder_image() -- the usual names
# rippers and download stores put next to an album's tracks.
FOLDER_IMAGE_NAMES = (
    "cover.jpg", "cover.jpeg", "cover.png",
    "folder.jpg", "folder.jpeg", "folder.png",
    "front.jpg", "front.jpeg", "front.png",
    "albumart.jpg", "albumartsmall.jpg",
)

# Process-wide, never reused: each change to a file's cover gets a fresh
# number, so an icon-cache key (path, mtime, version) can't collide
# after undo/redo brings back an older version.
_versions = itertools.count(1)


def next_version() -> int:
    return next(_versions)


def sniff_mime(data: bytes | None) -> str | None:
    """The image MIME type from the file signature, for the two formats
    ID3 players reliably display (JPEG, PNG); None for anything else."""
    if not data:
        return None
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    return None


def extension_for(mime: str) -> str:
    return ".png" if mime == "image/png" else ".jpg"


def pick_cover_frame(frames: list):
    """The front cover among APIC frames, else the first one; None if
    there are none. Frames are duck-typed on `.type`."""
    for frame in frames:
        if getattr(frame, "type", None) == FRONT_COVER:
            return frame
    return frames[0] if frames else None


def read_cover(path: str | Path) -> tuple[bytes, str] | None:
    """(image bytes, MIME type) of the file's cover as stored on disk, or
    None if it has none or can't be read. Never raises -- a failed read
    just shows no cover. Safe to call from a worker thread."""
    try:
        from mutagen.id3 import ID3, ID3NoHeaderError
    except ImportError:
        return None
    try:
        tags = ID3(str(path))
    except ID3NoHeaderError:
        return None
    except Exception:  # noqa: BLE001 -- unreadable file: no cover to show
        return None
    frame = pick_cover_frame(tags.getall("APIC"))
    if frame is None or not frame.data:
        return None
    return bytes(frame.data), (frame.mime or sniff_mime(frame.data) or "image/jpeg")


def find_folder_image(audio_path: str | Path) -> Path | None:
    """The album-art image next to `audio_path` (cover.jpg, folder.jpg,
    front.jpg, ... case-insensitively), or None."""
    folder = Path(audio_path).parent
    try:
        by_lower = {entry.name.lower(): entry for entry in folder.iterdir() if entry.is_file()}
    except OSError:
        return None
    for name in FOLDER_IMAGE_NAMES:
        if name in by_lower:
            return by_lower[name]
    return None
