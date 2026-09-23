"""
MP3File: the per-row data object backing the file table, analogous to
EpubBook in the epub tool. Holds tag fields plus the results of the
file-integrity, BPM, key, and lyrics checks, and the embedded cover's
state (see core/cover_art.py for why the image itself isn't kept here).
"""

from dataclasses import dataclass, field
from pathlib import Path

from core.cover_art import next_version, read_cover


# Integrity/BPM status values, shared vocabulary with the table's status
# column so GUI code can switch on a small fixed set rather than parsing
# free text.
STATUS_UNCHECKED = "UNCHECKED"
STATUS_OK = "OK"
STATUS_WARNING = "WARNING"
STATUS_ERROR = "ERROR"
STATUS_TOOL_MISSING = "TOOL MISSING"

# TXXX frame descriptions for the two mp3tag "advanced" fields that
# aren't backed by a dedicated 4-letter ID3 frame -- shared between
# core/tag_reader.py and core/tag_writer.py so the two sides of the
# round trip can never drift onto different description strings.
# "Acoustid Fingerprint" matches MusicBrainz Picard's own convention
# (the de facto standard other taggers, including mp3tag, follow for
# interop); "ITUNESADVISORY" matches mp3tag's own mapping table.
ACOUSTID_FINGERPRINT_DESC = "Acoustid Fingerprint"
ITUNESADVISORY_DESC = "ITUNESADVISORY"


@dataclass
class MP3File:
    path: Path

    # Tag fields (populated by core.tag_reader via mutagen)
    title: str = ""
    artist: str = ""
    albumartist: str = ""
    album: str = ""
    track: str = ""
    discnumber: str = ""
    year: str = ""
    genre: str = ""
    composer: str = ""
    comment: str = ""
    language: str = ""
    # mp3tag's "advanced" field set -- see core/fields.py's 2026-09-13
    # note. Real, editable fields (not detection results), just less
    # commonly needed than the ones above -- hidden by default in the
    # table (see gui/main_window.py's DEFAULT_HIDDEN_COLUMNS).
    albumsort: str = ""
    artistsort: str = ""
    albumartistsort: str = ""
    acoustid_fingerprint: str = ""
    itunesadvisory: str = ""
    duration_seconds: float | None = None
    bitrate_kbps: int | None = None

    # File-integrity check (mp3val)
    integrity_status: str = STATUS_UNCHECKED
    integrity_message: str = ""

    # BPM detection (aubio)
    bpm: float | None = None
    bpm_status: str = STATUS_UNCHECKED
    bpm_message: str = ""

    # Key detection (keyfinder-cli)
    key_status: str = STATUS_UNCHECKED
    key_value: str = ""
    key_message: str = ""

    # Deep integrity check (ffmpeg, full decode) -- catches corrupt/
    # truncated audio data mp3val's header-only scan can miss. Kept
    # separate from integrity_status/message above (not merged into
    # the same fields) since the two checks can legitimately disagree
    # -- clean headers with bad audio data, or vice versa -- and a user
    # should be able to see both results, not have one silently
    # overwrite the other.
    deep_check_status: str = STATUS_UNCHECKED
    deep_check_message: str = ""

    # Loudness measurement (ffmpeg's loudnorm filter, single-pass).
    # loudness_lufs is the measured integrated loudness; loudness_gain_db
    # is the ReplayGain-style track gain relative to the -18 LUFS
    # reference (core.ffmpeg_probe.REPLAYGAIN_REFERENCE_LUFS) -- what
    # actually gets written to the TXXX:REPLAYGAIN_TRACK_GAIN frame on
    # Save (core/tag_writer.py).
    loudness_lufs: float | None = None
    loudness_gain_db: float | None = None
    loudness_status: str = STATUS_UNCHECKED
    loudness_message: str = ""

    # Format details (ffprobe) -- richer than what mutagen surfaces
    # above (duration_seconds/bitrate_kbps): the actual encoder used,
    # sample rate, channel count. Gathered via core.scan_service.
    # run_deep_check(), which already shells out to the same ffmpeg/
    # ffprobe toolchain for the deep decode check.
    audio_encoder: str = ""
    sample_rate_hz: int | None = None
    channels: int | None = None

    # Embedded cover art (ID3v2 APIC) -- see core/cover_art.py. has_cover
    # is set on load (None = not read yet); the image itself is read from
    # disk on demand (cover_bytes()), never kept for every file. A cover
    # change made in the app is held here until Save writes it:
    # cover_pending (new image + mime) or cover_remove_pending.
    # cover_version changes on every cover change, for cache keys.
    has_cover: bool | None = None
    cover_pending: bytes | None = None
    cover_pending_mime: str = ""
    cover_remove_pending: bool = False
    cover_version: int = 0

    # Lyrics (core.lyrics_fetcher.fetch_lyrics(), via the `lyricy`
    # package's LRCLIB provider -- free, no API key) -- read back from
    # an existing ID3v2 USLT frame on load same as BPM/key/loudness
    # (core/tag_reader.py), written back on Save (core/tag_writer.py's
    # _write_lyrics_frame()). Not a core.fields.FIELDS entry -- full
    # song lyrics are long, multi-line text, so this gets its own
    # dedicated editor (gui/lyrics_dialog.py) instead of a single-line
    # bulk-edit panel row. lyrics_status/lyrics_message follow the same
    # STATUS_* vocabulary as bpm_status/key_status above.
    lyrics: str = ""
    lyrics_status: str = STATUS_UNCHECKED
    lyrics_message: str = ""

    # Load/save error tracking, same distinction the epub tool draws:
    # a well-formed file can still fail to WRITE for reasons unrelated
    # to its own content (permissions, path length), so this is kept
    # separate from the tag/check fields above.
    load_error: str = ""
    save_error: str = ""

    # True once a bulk-edit has been applied in memory but not yet
    # written to disk -- set by apply_tags(), cleared by
    # core.tag_writer.save_tags() on a successful write. Left set (so
    # the file stays visibly unsaved) if a save attempt fails.
    dirty: bool = False

    @property
    def filename(self) -> str:
        return self.path.name

    def display_bpm(self) -> str:
        if self.bpm is None:
            return ""
        return f"{self.bpm:.1f}"

    def display_loudness(self) -> str:
        if self.loudness_lufs is None:
            return ""
        return f"{self.loudness_lufs:.1f} LUFS"

    def display_sample_rate(self) -> str:
        if self.sample_rate_hz is None:
            return ""
        return f"{self.sample_rate_hz} Hz"

    @property
    def cover_change_pending(self) -> bool:
        return self.cover_pending is not None or self.cover_remove_pending

    def set_cover(self, data: bytes, mime: str) -> None:
        """Stages `data` (JPEG or PNG) as this file's cover; written on
        Save. Marks the file dirty."""
        self.cover_pending = data
        self.cover_pending_mime = mime
        self.cover_remove_pending = False
        self.has_cover = True
        self.cover_version = next_version()
        self.dirty = True

    def remove_cover(self) -> None:
        """Stages removal of every embedded picture; written on Save."""
        self.cover_pending = None
        self.cover_pending_mime = ""
        self.cover_remove_pending = True
        self.has_cover = False
        self.cover_version = next_version()
        self.dirty = True

    def cover_bytes(self) -> tuple[bytes, str] | None:
        """(bytes, mime) of the cover as it currently stands -- a pending
        change if there is one, else what's on disk. Reads the file, so
        call it off the GUI thread for anything more than one file."""
        if self.cover_pending is not None:
            return self.cover_pending, self.cover_pending_mime
        if self.cover_remove_pending or self.has_cover is False:
            return None
        return read_cover(self.path)

    def cover_image_bytes(self) -> bytes | None:
        """Just the bytes of cover_bytes() -- the loader shape the shared
        async image helpers take."""
        cover = self.cover_bytes()
        return cover[0] if cover else None

    def mark_cover_saved(self) -> None:
        """Called after a successful write: the pending change is now
        what's on disk."""
        self.cover_pending = None
        self.cover_pending_mime = ""
        self.cover_remove_pending = False

    def apply_tags(self, values: dict[str, str]) -> None:
        """Writes each field in `values` (attribute_key -> new value,
        from core.fields.FIELDS) onto this file's in-memory tag
        attributes and marks it dirty. Mirrors the epub tool's
        EpubBook.apply_metadata() -- does not touch disk, that's
        core.tag_writer.save_tags()'s job."""
        for key, value in values.items():
            setattr(self, key, value)
        self.dirty = True
