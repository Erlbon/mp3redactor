"""
Writes an MP3File's tag fields back to disk using mutagen -- the write
counterpart to tag_reader.load_tags(). Uses the same raw ID3 frames
tag_reader reads, so a round-trip (load -> bulk-edit -> save -> load)
reads back exactly what was written. Three shapes of core.fields.FIELDS
entry, each handled differently:

  - Plain 4-letter frames (TIT2/TPE1/TALB/... -- see _SIMPLE_FRAME_IDS)
    go through one generic loop.
  - The two TXXX-based "advanced" fields (AcoustID Fingerprint,
    iTunesAdvisory -- see _TXXX_DESCRIPTIONS) aren't backed by a
    dedicated frame id; TXXX's own key is "TXXX:<description>", so
    these go through a second small loop using tags.delall()/tags.add()
    rather than setall(frame_id, ...).
  - Comment (COMM) needs its own function (_write_comment_frame()) --
    its ID3 key encodes description+language too, but unlike TXXX a
    file can carry several COMM frames from other software with
    different desc/lang combinations, which the loop above's
    single-key delall() can't clean up.

Also writes TBPM, TKEY, and TXXX:REPLAYGAIN_TRACK_GAIN (see
_write_bpm_frame()/_write_key_frame()/_write_loudness_frame() below) --
none of BPM/key/loudness is a core.fields.FIELDS entry the bulk-edit
panel exposes, they're detection results (core.scan_service.
run_bpm_check()/run_key_detection()/run_loudness_measurement()), so
none of them goes through the generic field loops above the way
title/artist/etc. do.

Lyrics (USLT) is a fourth case, written by _write_lyrics_frame() --
also not a FIELDS entry (it gets its own editor, gui/lyrics_dialog.py,
rather than a bulk-edit panel row), but unlike BPM/key/loudness it's a
plain user-facing value with the same blank-to-clear convention as
Comment, not a detection result -- see that function's own docstring.

mutagen is imported lazily/guarded, same reasoning as tag_reader.py and
bpm_detector.py: a missing dependency should degrade a specific
capability (here: saving), not crash the app.

Every failure path runs through redactor_common.core.save_errors.
describe_save_error() rather than a bare str(exc) -- recognizes
Windows' classic MAX_PATH (260-char) limit specifically and explains
it clearly, since that's a property of WHERE the file lives (retrying
the same save can't help), not a transient failure a raw exception
message would otherwise make look retriable.
"""

from redactor_common.core.save_errors import describe_save_error

from core.mp3_file import ACOUSTID_FINGERPRINT_DESC, ITUNESADVISORY_DESC, MP3File, STATUS_OK

# attribute_key -> ID3 frame id for the FIELDS entries backed by a
# plain 4-letter frame. A separate mapping from FIELDS itself (rather
# than reusing it directly) since not every attribute core.fields
# could ever list is necessarily backed by a single ID3 frame the same
# simple way -- keeping this explicit here means a future FIELDS entry
# doesn't silently need writer support it doesn't have.
_SIMPLE_FRAME_IDS = {
    "title": "TIT2",
    "artist": "TPE1",
    "albumartist": "TPE2",
    "album": "TALB",
    "track": "TRCK",
    "discnumber": "TPOS",
    "year": "TDRC",
    "genre": "TCON",
    "composer": "TCOM",
    "language": "TLAN",
    "albumsort": "TSOA",
    "artistsort": "TSOP",
    "albumartistsort": "TSO2",
}

# attribute_key -> TXXX description, for the FIELDS entries with no
# dedicated ID3 frame of their own. AcoustID Fingerprint's description
# matches MusicBrainz Picard's own convention (the de facto standard
# other taggers, including mp3tag, follow for interop); iTunesAdvisory
# matches mp3tag's own mapping table. Shared with core/mp3_file.py so
# the read side (core/tag_reader.py) can never drift onto a different
# description string.
_TXXX_DESCRIPTIONS = {
    "acoustid_fingerprint": ACOUSTID_FINGERPRINT_DESC,
    "itunesadvisory": ITUNESADVISORY_DESC,
}


def save_tags(mp3: MP3File) -> bool:
    """
    Writes mp3's current in-memory tag fields to its file on disk.
    Returns True on success. On any failure, sets mp3.save_error and
    returns False rather than raising -- callers saving a batch should
    not have one bad file abort the whole save (same lesson as
    tag_reader.load_tags()). Clears mp3.dirty and mp3.save_error on
    success; leaves mp3.dirty set (so it stays visibly unsaved) on
    failure.

    A field with an empty value has its frame removed entirely rather
    than written as an empty frame -- this is also how you clear a tag:
    blank the field in the bulk-edit panel, tick it, Apply, Save.
    """
    try:
        from mutagen.id3 import (
            COMM, TALB, TBPM, TCOM, TCON, TDRC, TIT2, TKEY, TLAN, TPE1, TPE2,
            TPOS, TRCK, TSO2, TSOA, TSOP, TXXX, USLT,
        )
        from mutagen.mp3 import MP3
    except ImportError:
        mp3.save_error = "mutagen is not installed"
        return False

    frame_classes = {
        "TIT2": TIT2, "TPE1": TPE1, "TPE2": TPE2, "TALB": TALB, "TRCK": TRCK,
        "TPOS": TPOS, "TDRC": TDRC, "TCON": TCON, "TCOM": TCOM, "TLAN": TLAN,
        "TSOA": TSOA, "TSOP": TSOP, "TSO2": TSO2,
    }

    try:
        audio = MP3(mp3.path)
    except Exception as e:  # noqa: BLE001 -- any open/parse failure blocks writing too
        mp3.save_error = f"failed to open file: {describe_save_error(e)}"
        return False

    if audio.tags is None:
        try:
            audio.add_tags()
        except Exception as e:  # noqa: BLE001
            mp3.save_error = f"failed to add a tag header: {describe_save_error(e)}"
            return False

    tags = audio.tags
    for key, frame_id in _SIMPLE_FRAME_IDS.items():
        value = getattr(mp3, key, "") or ""
        if value:
            tags.setall(frame_id, [frame_classes[frame_id](encoding=3, text=value)])
        else:
            tags.delall(frame_id)

    for key, desc in _TXXX_DESCRIPTIONS.items():
        value = getattr(mp3, key, "") or ""
        frame_key = f"TXXX:{desc}"
        tags.delall(frame_key)
        if value:
            tags.add(TXXX(encoding=3, desc=desc, text=[value]))

    _write_comment_frame(tags, mp3, COMM)
    _write_lyrics_frame(tags, mp3, USLT)
    _write_bpm_frame(tags, mp3, TBPM)
    _write_key_frame(tags, mp3, TKEY)
    _write_loudness_frame(tags, mp3, TXXX)

    try:
        audio.save()
    except Exception as e:  # noqa: BLE001
        mp3.save_error = f"failed to write tags: {describe_save_error(e)}"
        return False

    mp3.dirty = False
    mp3.save_error = ""
    return True


def _write_comment_frame(tags, mp3: MP3File, comm_cls) -> None:
    """Comment is a regular core.fields.FIELDS entry (bulk-editable,
    blank-to-clear, like title/artist/...), but COMM's own ID3 key
    encodes description+language (e.g. "COMM::eng"), so it can't go
    through the plain setall(frame_id, ...)/delall(frame_id) loop the
    way TIT2/TPE1/etc. do -- and unlike the TXXX-based fields above, a
    file can carry more than one COMM frame at once (different
    languages/descriptions) from other software, which a single-key
    delall() wouldn't fully clean up.

    This app treats Comment as one field, same as everywhere else in
    the bulk-edit panel -- so ALL existing COMM frames (any desc/lang)
    are cleared first, then at most one is written back (lang="eng",
    empty description), rather than trying to preserve multiple
    variants this app has no UI to distinguish between. See
    core.tag_reader._first_comm()'s docstring for the read side of
    this same simplification.
    """
    for key in [k for k in tags.keys() if k.startswith("COMM")]:
        tags.delall(key)
    value = mp3.comment or ""
    if value:
        tags.add(comm_cls(encoding=3, lang="eng", desc="", text=[value]))


def _write_lyrics_frame(tags, mp3: MP3File, uslt_cls) -> None:
    """Lyrics (fetched via core/lyrics_fetcher.py, or hand-edited/
    cleared in gui/lyrics_dialog.py) is stored in the standard ID3v2
    USLT ("Unsynchronised lyrics/text transcription") frame -- the same
    multi-desc/lang key shape as COMM (e.g. "USLT::eng"), so this
    follows _write_comment_frame()'s approach: clear every existing
    USLT frame first (a file can carry more than one from other
    software), then write at most one back (lang="eng", empty
    description), since this app has no UI to distinguish multiple
    variants -- same simplification Comment already makes.

    A blank mp3.lyrics clears the frame entirely rather than writing an
    empty one -- same blank-to-clear convention as every FIELDS entry,
    even though lyrics isn't itself a FIELDS entry (see
    gui/lyrics_dialog.py's docstring for why: full song lyrics don't
    fit a single-line field the way every FIELDS entry does)."""
    for key in [k for k in tags.keys() if k.startswith("USLT")]:
        tags.delall(key)
    value = mp3.lyrics or ""
    if value:
        tags.add(uslt_cls(encoding=3, lang="eng", desc="", text=value))


def _write_bpm_frame(tags, mp3: MP3File, tbpm_cls) -> None:
    """mp3.bpm is a float (aubio's raw estimate, e.g. 127.6) but the
    ID3v2 TBPM frame's own spec defines it as "an integer... represented
    as a numerical string" -- so this rounds rather than reusing the
    generic string-field loop above, which would otherwise write a
    non-conformant "127.6".

    Only ever touches TBPM when mp3.bpm actually holds a detected value
    (see core.scan_service.run_bpm_check) -- unlike the FIELDS loop
    above, there's no bulk-edit UI to blank this field back out, and
    tag_reader.load_tags() never populates mp3.bpm from an existing
    TBPM frame either (BPM detection is aubio-driven, not tag-driven),
    so mp3.bpm is None both for a file that was never analyzed this
    session AND one that already carries a TBPM tag from other software.
    Leaving that pre-existing frame alone in the None case avoids
    silently deleting a tag this app never actually looked at.
    """
    if mp3.bpm is not None:
        tags.setall("TBPM", [tbpm_cls(encoding=3, text=str(round(mp3.bpm)))])


def _write_key_frame(tags, mp3: MP3File, tkey_cls) -> None:
    """mp3.key_value/key_status are core.scan_service.run_key_detection()'s
    result, not a bulk-edit field -- written to the standard ID3v2 TKEY
    ("Initial key") frame.

    Unlike BPM, an empty key_value can be a genuine, positive result
    here: keyfinder-cli returns STATUS_OK with "" for audio it
    determined has no key (silence), not just for a failure -- see
    core.keyfinder_runner.detect_key()'s own docstring. So the gate is
    key_status == STATUS_OK, not "key_value is truthy": a successful
    "no key" result still clears/skips the frame the same way blanking
    a text field does (a stale TKEY from other software genuinely
    should go, once this app has actually re-measured "there isn't
    one"). STATUS_ERROR/STATUS_TOOL_MISSING (key_status still its
    default STATUS_UNCHECKED, or a failed run) leaves any existing
    TKEY frame alone -- same "we don't know, so don't touch it" reasons
    as _write_bpm_frame()."""
    if mp3.key_status != STATUS_OK:
        return
    if mp3.key_value:
        tags.setall("TKEY", [tkey_cls(encoding=3, text=mp3.key_value)])
    else:
        tags.delall("TKEY")


def _write_loudness_frame(tags, mp3: MP3File, txxx_cls) -> None:
    """mp3.loudness_gain_db is core.scan_service.run_loudness_measurement()'s
    result -- written to the de facto standard TXXX:REPLAYGAIN_TRACK_GAIN
    frame (there's no dedicated ID3v2 frame for ReplayGain; TXXX with
    this exact description is what every ReplayGain-aware player and
    tagger already looks for). TXXX's own key is "TXXX:<desc>", not a
    plain 4-letter frame id, so this uses tags.delall()/tags.add()
    rather than the setall(frame_id, ...) pattern the plain frames use.

    Gated on loudness_status == STATUS_OK, same "only touch this once
    we actually know something this session" reasoning as
    _write_bpm_frame()/_write_key_frame(). loudness_gain_db can also be
    None on an otherwise-successful measurement -- genuinely silent
    audio has no meaningful gain to compute (see
    core.ffmpeg_probe.measure_loudness()'s docstring) -- so that case
    is also left alone rather than writing or clearing anything,
    same as a None mp3.bpm.
    """
    if mp3.loudness_status != STATUS_OK or mp3.loudness_gain_db is None:
        return
    tags.delall("TXXX:REPLAYGAIN_TRACK_GAIN")
    tags.add(txxx_cls(encoding=3, desc="REPLAYGAIN_TRACK_GAIN", text=[f"{mp3.loudness_gain_db:+.2f} dB"]))
