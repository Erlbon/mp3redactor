"""
MP3File: the per-row data object backing the file table, analogous to
EpubBook in the epub tool. Holds tag fields plus the results of the
file-integrity, BPM, key, and lyrics checks, and the embedded cover's
state (see core/cover_art.py for why the image itself isn't kept here).
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
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
# The external tool itself failed (timeout, launch/OS failure, crash with
# no verdict) -- NOT a finding about the file, so it is never stamped and
# never reported as a bad file. See core/mp3val_runner.py / ffmpeg_probe.py.
STATUS_TOOL_ERROR = "TOOL ERROR"

# TXXX frame descriptions for the two mp3tag "advanced" fields that
# aren't backed by a dedicated 4-letter ID3 frame -- shared between
# core/tag_reader.py and core/tag_writer.py so the two sides of the
# round trip can never drift onto different description strings.
# "Acoustid Fingerprint" matches MusicBrainz Picard's own convention
# (the de facto standard other taggers, including mp3tag, follow for
# interop); "ITUNESADVISORY" matches mp3tag's own mapping table.
ACOUSTID_FINGERPRINT_DESC = "Acoustid Fingerprint"
ITUNESADVISORY_DESC = "ITUNESADVISORY"
# MusicBrainz identifiers, as MusicBrainz Picard writes them (and other
# taggers read them): the release's id in a TXXX frame, the recording's
# id in a UFID frame owned by "http://musicbrainz.org". Written by
# Import > Look Up via MusicBrainz (core/musicbrainz_lookup.py) so a
# file permanently records which release/recording it was matched to.
MUSICBRAINZ_ALBUM_ID_DESC = "MusicBrainz Album Id"
# Release details Look Up via Discogs fills: the label (TPUB, a plain
# frame), its catalogue number and the release country (TXXX frames named
# as MusicBrainz Picard writes them, so other taggers read them too).
CATALOG_NUMBER_DESC = "CATALOGNUMBER"
RELEASE_COUNTRY_DESC = "MusicBrainz Album Release Country"
MUSICBRAINZ_UFID_OWNER = "http://musicbrainz.org"
# TXXX frames recording the last validation scans (mp3val integrity,
# ffmpeg deep check) INSIDE the file, so the record survives copies of
# the file. Value: "<STATUS>;<ISO-8601 UTC time>", e.g.
# "OK;2026-09-30T14:05:11Z" -- see parse_scan_stamp()/MP3File.record_scan().
INTEGRITY_SCAN_DESC = "REDACTOR_INTEGRITY"
DEEP_CHECK_SCAN_DESC = "REDACTOR_DEEP_CHECK"
# Only a scan that actually looked at the file is worth recording;
# TOOL MISSING / TOOL ERROR / UNCHECKED say nothing about it.
STAMPABLE_STATUSES = frozenset({STATUS_OK, STATUS_WARNING, STATUS_ERROR})
_STAMP_TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def format_scan_stamp(status: str, when: datetime) -> str:
    return f"{status};{when.astimezone(timezone.utc).strftime(_STAMP_TIME_FORMAT)}"


def parse_scan_stamp(text: str) -> tuple[str, datetime] | None:
    """(status, UTC time) from a stamp value, or None for anything
    unknown or garbled -- a hand-edited or foreign frame must read as
    "never scanned", never raise."""
    try:
        status, _, stamp = (text or "").partition(";")
        status = status.strip()
        if status not in STAMPABLE_STATUSES:
            return None
        when = datetime.strptime(stamp.strip(), _STAMP_TIME_FORMAT).replace(tzinfo=timezone.utc)
        return status, when
    except (ValueError, TypeError):
        return None


def scan_display(status: str, stamp: str) -> str:
    """Scan-column text: "<STATUS> · 2026-09-30 14:05" (local time) when
    `status` is the stamped result, else the bare status word."""
    parsed = parse_scan_stamp(stamp)
    if parsed is None or parsed[0] != status:
        return status
    return f"{status} · {parsed[1].astimezone().strftime('%Y-%m-%d %H:%M')}"


def scan_tooltip(status: str, stamp: str, message: str) -> str:
    """Full local timestamp (when `status` is the stamped result) plus
    the scan's message."""
    parsed = parse_scan_stamp(stamp)
    lines = []
    if parsed is not None and parsed[0] == status:
        lines.append(f"Last scanned {parsed[1].astimezone().strftime('%Y-%m-%d %H:%M:%S %Z').strip()}")
    if message:
        lines.append(message)
    return "\n".join(lines)


# Every attribute core/tag_writer.py writes from plain in-memory text (the
# FIELDS entries plus MusicBrainz ids, comment and lyrics). load_tags()
# and a successful save_tags() record their values in
# MP3File.tag_baseline, and Save only rewrites a frame whose value now
# differs from it -- so a multi-valued TPE1, several COMM/USLT frames
# or a comment's language survive an edit of some other field.
BASELINE_KEYS = (
    "title", "artist", "albumartist", "album", "track", "discnumber", "year",
    "genre", "composer", "comment", "language", "albumsort", "artistsort",
    "albumartistsort", "acoustid_fingerprint", "itunesadvisory",
    "musicbrainz_albumid", "musicbrainz_trackid", "publisher", "catalognumber",
    "releasecountry", "lyrics", "integrity_stamp", "deep_check_stamp",
)


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
    musicbrainz_albumid: str = ""  # TXXX:MusicBrainz Album Id (release)
    musicbrainz_trackid: str = ""  # UFID:http://musicbrainz.org (recording)
    publisher: str = ""  # TPUB (the record label)
    catalognumber: str = ""  # TXXX:CATALOGNUMBER
    releasecountry: str = ""  # TXXX:MusicBrainz Album Release Country
    duration_seconds: float | None = None
    bitrate_kbps: int | None = None

    # File-integrity check (mp3val)
    integrity_status: str = STATUS_UNCHECKED
    integrity_message: str = ""
    # The persisted record of the last completed scan ("STATUS;time", see
    # parse_scan_stamp()); "" = never. integrity_status above is the
    # displayed result -- equal to the stamp's status for a real scan or
    # a stamp loaded from disk (the "last known" result), but it can also
    # be TOOL MISSING with an older stamp still standing.
    integrity_stamp: str = ""

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
    deep_check_stamp: str = ""  # as integrity_stamp

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

    # What the file on disk held for each BASELINE_KEYS attribute at the
    # last load/save (empty for a file never loaded). Replaced, never
    # mutated in place, so undo snapshots sharing it stay valid.
    tag_baseline: dict = field(default_factory=dict)

    def snapshot_tag_baseline(self) -> None:
        """Records the current text fields as "what's on disk"."""
        self.tag_baseline = {key: getattr(self, key, "") or "" for key in BASELINE_KEYS}

    def tag_changed(self, key: str) -> bool:
        """True if `key` differs from what was last loaded/saved."""
        return (getattr(self, key, "") or "") != self.tag_baseline.get(key, "")

    @property
    def filename(self) -> str:
        return self.path.name

    @property
    def integrity_scanned_at(self) -> str:
        """ISO-8601 UTC time of the last completed mp3val scan, "" = never."""
        return self._scanned_at(self.integrity_stamp)

    @property
    def deep_check_scanned_at(self) -> str:
        return self._scanned_at(self.deep_check_stamp)

    @staticmethod
    def _scanned_at(stamp: str) -> str:
        parsed = parse_scan_stamp(stamp)
        return parsed[1].strftime(_STAMP_TIME_FORMAT) if parsed else ""

    def load_scan_stamp(self, kind: str, text: str) -> None:
        """Adopts a stamp read from disk as the last-known result. Not an
        unsaved change, so never marks dirty. Garbled text is ignored."""
        parsed = parse_scan_stamp(text)
        if parsed is None:
            return
        setattr(self, f"{kind}_stamp", format_scan_stamp(*parsed))
        setattr(self, f"{kind}_status", parsed[0])
        setattr(self, f"{kind}_message", "")

    def record_scan(self, kind: str, status: str, message: str) -> None:
        """Stores a scan result ("integrity" or "deep_check"). A completed
        scan (OK/WARNING/ERROR) is stamped with the current UTC time and
        marks the file dirty -- Save writes the stamp into the file.
        TOOL MISSING and TOOL ERROR change the displayed status only:
        nothing was judged, so the stamp (and dirty flag) stay as they
        were -- a previous good stamp survives a failed re-scan."""
        setattr(self, f"{kind}_status", status)
        setattr(self, f"{kind}_message", message)
        if status in STAMPABLE_STATUSES and not self.load_error:
            setattr(self, f"{kind}_stamp", format_scan_stamp(status, datetime.now(timezone.utc)))
            self.dirty = True

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
        core.tag_writer.save_tags()'s job. A file that failed to load
        is left alone (its fields are blank defaults, not its tags)."""
        if self.load_error:
            return
        for key, value in values.items():
            setattr(self, key, value)
        self.dirty = True
