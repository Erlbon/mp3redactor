"""
Writes an MP3File's tag fields back to disk using mutagen -- the write
counterpart to tag_reader.load_tags(). Uses the same raw ID3 frames
tag_reader reads (TIT2/TPE1/TALB/TRCK/TDRC/TCON/TLAN), so a round-trip
(load -> bulk-edit -> save -> load) reads back exactly what was written.
Also writes TBPM (see _write_bpm_frame() below) -- a detected BPM
(core.scan_service.run_bpm_check) isn't a core.fields.FIELDS entry the
bulk-edit panel exposes, it's a numeric detection result, so it doesn't
go through the generic string-field loop the way title/artist/etc. do.

mutagen is imported lazily/guarded, same reasoning as tag_reader.py and
bpm_detector.py: a missing dependency should degrade a specific
capability (here: saving), not crash the app.
"""

from core.mp3_file import MP3File

# attribute_key -> ID3 frame id for the fields core.fields.FIELDS
# defines. A separate mapping from FIELDS itself (rather than reusing
# it directly) since not every attribute core.fields could ever list
# is necessarily backed by a single ID3 frame the same simple way --
# keeping this explicit here means a future FIELDS entry doesn't
# silently need writer support it doesn't have.
_FRAME_IDS = {
    "title": "TIT2",
    "artist": "TPE1",
    "album": "TALB",
    "track": "TRCK",
    "year": "TDRC",
    "genre": "TCON",
    "language": "TLAN",
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
        from mutagen.id3 import TALB, TBPM, TCON, TDRC, TIT2, TLAN, TPE1, TRCK
        from mutagen.mp3 import MP3
    except ImportError:
        mp3.save_error = "mutagen is not installed"
        return False

    frame_classes = {
        "TIT2": TIT2, "TPE1": TPE1, "TALB": TALB, "TRCK": TRCK,
        "TDRC": TDRC, "TCON": TCON, "TLAN": TLAN,
    }

    try:
        audio = MP3(mp3.path)
    except Exception as e:  # noqa: BLE001 -- any open/parse failure blocks writing too
        mp3.save_error = f"failed to open file: {e}"
        return False

    if audio.tags is None:
        try:
            audio.add_tags()
        except Exception as e:  # noqa: BLE001
            mp3.save_error = f"failed to add a tag header: {e}"
            return False

    tags = audio.tags
    for key, frame_id in _FRAME_IDS.items():
        value = getattr(mp3, key, "") or ""
        if value:
            tags.setall(frame_id, [frame_classes[frame_id](encoding=3, text=value)])
        else:
            tags.delall(frame_id)

    _write_bpm_frame(tags, mp3, TBPM)

    try:
        audio.save()
    except Exception as e:  # noqa: BLE001
        mp3.save_error = f"failed to write tags: {e}"
        return False

    mp3.dirty = False
    mp3.save_error = ""
    return True


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
