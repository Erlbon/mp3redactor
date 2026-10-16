"""
core/notes_phases.py -- where the time of a Create Notes run goes, phase by phase. The timer is redactor_common's
(redactor_common/core/notes_phases.py); this file names mp3redactor's phases: walked folders (scan), tags read from the
files, requests to MusicBrainz, Discogs and Wikipedia (seconds INCLUDING the throttle waits and the waits for a busy
service), and the notes written.
"""

from __future__ import annotations

from redactor_common.core.notes_phases import BUSY, PhaseSet, mmss  # noqa: F401 -- re-exported
from redactor_common.core.notes_phases import PhaseTimer as _SharedTimer

SCAN, TAGS, MUSICBRAINZ, DISCOGS, WIKIPEDIA, WRITE = "scan", "tags", "musicbrainz", "discogs", "wikipedia", "write"
PHASES = PhaseSet(
    {SCAN: "scan", TAGS: "tags", MUSICBRAINZ: "MusicBrainz", DISCOGS: "Discogs", WIKIPEDIA: "Wikipedia", WRITE: "writing"},
    {SCAN: "folders", TAGS: "files", MUSICBRAINZ: "requests", DISCOGS: "requests", WIKIPEDIA: "requests", WRITE: "notes"},
    busy_phase=MUSICBRAINZ)
ORDER = PHASES.order
LABELS, UNITS = PHASES.labels, PHASES.units


class PhaseTimer(_SharedTimer):
    def __init__(self) -> None:
        super().__init__(PHASES)
