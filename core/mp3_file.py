"""
MP3File: the per-row data object backing the file table, analogous to
EpubBook in the epub tool. Holds tag fields plus the results of the
file-integrity, BPM, and key checks; cover/lyrics fields are reserved
here now so those later versions don't need a schema migration, but are
left unset/unused until those features land.
"""

from dataclasses import dataclass, field
from pathlib import Path


# Integrity/BPM status values, shared vocabulary with the table's status
# column so GUI code can switch on a small fixed set rather than parsing
# free text.
STATUS_UNCHECKED = "UNCHECKED"
STATUS_OK = "OK"
STATUS_WARNING = "WARNING"
STATUS_ERROR = "ERROR"
STATUS_TOOL_MISSING = "TOOL MISSING"


@dataclass
class MP3File:
    path: Path

    # Tag fields (populated by core.tag_reader via mutagen)
    title: str = ""
    artist: str = ""
    album: str = ""
    track: str = ""
    year: str = ""
    genre: str = ""
    language: str = ""
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

    # Reserved for later versions -- deliberately present but unused in v1
    has_cover: bool | None = None
    lyrics: str = ""

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

    def apply_tags(self, values: dict[str, str]) -> None:
        """Writes each field in `values` (attribute_key -> new value,
        from core.fields.FIELDS) onto this file's in-memory tag
        attributes and marks it dirty. Mirrors the epub tool's
        EpubBook.apply_metadata() -- does not touch disk, that's
        core.tag_writer.save_tags()'s job."""
        for key, value in values.items():
            setattr(self, key, value)
        self.dirty = True
