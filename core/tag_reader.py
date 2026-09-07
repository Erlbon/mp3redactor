"""
Populates an MP3File's tag fields from disk using mutagen (the one tag
library used throughout this project -- no eyeD3, per project decision).

mutagen is imported lazily/guarded the same way aubio is in
bpm_detector.py: a missing dependency should degrade a specific
capability, not crash file loading entirely.

Also reads back TBPM/TKEY (see the bottom of load_tags() below) --
these used to be write-only from this app's perspective: a detected
BPM/key only ever showed up in the table for the rest of that session,
because nothing read an *existing* TBPM/TKEY tag back on load. That
made a successful save look like it silently failed -- reload the same
file (or just restart the app) and the value you just wrote vanishes
from the table again, even though it's genuinely sitting in the file's
ID3 tag the whole time. See core/tag_writer.py's _write_bpm_frame()/
_write_key_frame() for the write side of this same round trip.
"""

from pathlib import Path

from core.mp3_file import MP3File, STATUS_OK


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
        mp3.language = _first(tags, "TLAN")
        mp3.has_cover = any(key.startswith("APIC") for key in tags.keys())

        # Deliberately does NOT set mp3.dirty here -- reading back a
        # tag that's already on disk isn't an unsaved change, same as
        # title/artist/etc. above never marking dirty on load either.
        bpm_text = _first(tags, "TBPM")
        if bpm_text:
            try:
                mp3.bpm = float(bpm_text)
                mp3.bpm_status = STATUS_OK
                mp3.bpm_message = ""
            except ValueError:
                pass  # malformed TBPM from other software -- leave bpm unset rather than guess

        key_text = _first(tags, "TKEY")
        if key_text:
            mp3.key_value = key_text
            mp3.key_status = STATUS_OK
            mp3.key_message = ""

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
