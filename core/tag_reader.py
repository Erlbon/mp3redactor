"""
Populates an MP3File's tag fields from disk using mutagen (the one tag
library used throughout this project -- no eyeD3, per project decision).

mutagen is imported lazily/guarded the same way aubio is in
bpm_detector.py: a missing dependency should degrade a specific
capability, not crash file loading entirely.
"""

from pathlib import Path

from core.mp3_file import MP3File


def load_tags(mp3: MP3File) -> None:
    """
    Mutates mp3 in place, filling its tag fields. On any failure, sets
    mp3.load_error and leaves tag fields at their defaults rather than
    raising -- callers loading a batch of files should not have one
    corrupt/unreadable file abort the whole load (same lesson as the
    epub tool's v52 zlib.error fix: catch broadly at the per-file
    boundary, not narrowly and hope).
    """
    try:
        from mutagen.mp3 import MP3
        from mutagen.id3 import ID3NoHeaderError
    except ImportError:
        mp3.load_error = "mutagen is not installed"
        return

    try:
        audio = MP3(mp3.path)
    except ID3NoHeaderError:
        # Valid MP3 with no ID3 tag at all -- not an error, just empty tags.
        audio = None
    except Exception as e:  # noqa: BLE001 -- any decode/tag-parse failure is a per-file load_error
        mp3.load_error = f"failed to read tags: {e}"
        return

    if audio is None:
        return

    tags = audio.tags
    if tags is not None:
        mp3.title = _first(tags, "TIT2")
        mp3.artist = _first(tags, "TPE1")
        mp3.album = _first(tags, "TALB")
        mp3.track = _first(tags, "TRCK")
        mp3.year = _first(tags, "TDRC")
        mp3.genre = _first(tags, "TCON")
        mp3.has_cover = any(key.startswith("APIC") for key in tags.keys())

    if audio.info is not None:
        mp3.duration_seconds = getattr(audio.info, "length", None)
        bitrate = getattr(audio.info, "bitrate", None)
        mp3.bitrate_kbps = int(bitrate / 1000) if bitrate else None


def _first(tags, frame_id: str) -> str:
    frame = tags.get(frame_id)
    if frame is None:
        return ""
    try:
        return str(frame.text[0]) if frame.text else ""
    except (AttributeError, IndexError):
        return str(frame)
