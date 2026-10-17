"""
Export/Import Settings for mp3redactor: the SettingsAdapter that maps
core.settings.Settings onto redactor_common's shared settings-bundle file
(`mp3redactor-settings.json`).

The ini stores everything as strings; this adapter reads each key as a real
JSON type (bool, int, list, str) and, on import, coerces whatever a file
carries back to the field's type before saving. A value of the wrong type is
skipped (the key keeps its current value) rather than failing the section.

Sections and their keys are the ONLY things an imported file may touch --
read_section() returns every key of a section, so an unknown key in a file
is ignored by redactor_common, and nothing outside SECTION_KEYS is ever
written. This app stores no secrets; redactor_common's looks_secret() guard
would drop any secret-looking key anyway.

Portable sections are ticked by default; the machine-specific ones (tool
paths, last-used folders, library root) are opt-in, labelled "this computer
only". Zoom, panel visibility and window geometry are not persisted by this
app today, so there is nothing to export for them.

Live refresh is the caller's job (MainWindow._on_settings_imported); the
adapter reads and writes through `get_settings`, so it always sees the
window's current Settings object (which the Preferences dialog replaces).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

from core.audio_quality import MAX_KBPS, MIN_KBPS, normalise_limits
from core.settings import Settings, save_settings
from redactor_common.core import settings_bundle as sb
from redactor_common.core import settings_coerce as sc

APP_SLUG = "mp3redactor"

# section -> the Settings fields it carries, with the JSON type each is coerced to.
# "str" = string, "bool", "int", "strlist" = list of strings, "pairs" = list of
# [code, name] pairs (custom_languages).
SECTION_KEYS: dict[str, dict[str, str]] = {
    "recipe": {"redact_recipe": "str"},
    "patterns": {
        "pattern_history": "strlist",
        "rename_pattern": "str",
        "move_pattern": "str",
    },
    "columns": {
        "hidden_columns": "strlist",
        "column_order": "strlist",
        "panel_field_order": "strlist",
        "has_column_preference": "bool",
    },
    "field_defaults": {
        "ascii_filenames": "bool",
        "rename_zero_pad": "bool",
        "rename_zero_pad_width": "int",
        "auto_number_padding": "int",
        "delete_backup_after_fix": "bool",
    },
    # Preferences > Table colours: the Bitrate column's poor and good limits (kbps, 32..320, poor < good).
    "table_colours": {"band_poor_kbps": "kbps", "band_good_kbps": "kbps"},
    "genres_languages": {
        "custom_genres": "strlist",
        "hidden_default_genres": "strlist",
        "custom_languages": "pairs",
        "hidden_default_languages": "strlist",
    },
    # The opt-in change history switch (core/history.py), stored as [history] enabled in the ini and the
    # bundle. Importing enabled=true turns recording on only because the user ticked this section in the
    # import preview, whose label says so; a bundle without it leaves the current value.
    "history": {"enabled": "bool"},
    "tools": {
        "mp3val_path": "str",
        "keyfinder_cli_path": "str",
        "ffmpeg_path": "str",
        "ffprobe_path": "str",
        "fpcalc_path": "str",
    },
    "folders": {
        "last_directory": "str",
        "library_root": "str",
        "recycle_fallback_folder": "str",  # a folder on this computer (the backup folder for drives without a Recycle Bin)
        # The offline MusicBrainz database and the dump it is built from: files on
        # this computer, so an imported path only counts if the file is there.
        "musicbrainz_database": "str",
        "musicbrainz_dump": "str",
    },
}

# (section, bundle key) -> Settings field, where the two names differ.
_FIELD_BY_KEY = {("history", "enabled"): "history_enabled"}


def _field(section: str, name: str) -> str:
    return _FIELD_BY_KEY.get((section, name), name)


# Keys whose value is a file on this computer: an import skips one that doesn't exist here.
_EXISTING_FILE_KEYS = frozenset({"musicbrainz_database", "musicbrainz_dump"})

_LABEL_PORTABLE = [
    ("recipe", "Redact recipe"),
    ("patterns", "Rename, move and path patterns (history and saved)"),
    ("columns", "Column layout, visibility and order, and the side-panel field order"),
    ("field_defaults", "Field defaults (ASCII, zero-padding, numbering, backups)"),
    ("genres_languages", "Custom genres and languages"),
    ("table_colours", "Table colours (the Bitrate column's poor and good limits)"),
    ("history", "Change history on/off (importing 'on' starts recording tag changes on this computer)"),
]
_LABEL_MACHINE = [
    ("tools", "External tool paths (mp3val, ffmpeg, keyfinder, fpcalc)"),
    ("folders", "Last-used folder, library root, backup folder, MusicBrainz database and dump"),
]

# Integer fields: a sane floor so an imported 0/negative can't break padding.
_INT_MIN = 0
_INT_MAX = 99


# kind -> (redactor_common's coercer, the ValueError message when it gives None).
# The coercers never let a JSON true pass as the int 1 (bool is an int subclass).
_COERCERS: dict[str, tuple[Callable[[Any], Any], str]] = {
    "str": (sc.coerce_str, "expected text"),
    "bool": (sc.coerce_bool, "expected true/false"),
    "int": (lambda v: sc.coerce_int(v, _INT_MIN, _INT_MAX), "expected a small whole number"),
    "kbps": (lambda v: sc.coerce_int(v, MIN_KBPS, MAX_KBPS), f"expected a whole number of kbps, {MIN_KBPS} to {MAX_KBPS}"),
    "strlist": (sc.coerce_str_list, "expected a list of text"),
    "pairs": (sc.coerce_pairs, "expected a list of [code, name] pairs"),
}


def _coerce(kind: str, value: Any) -> Any:
    """The value as the field's type, or raises ValueError if it can't be."""
    if kind not in _COERCERS:
        raise ValueError(f"unknown kind {kind}")
    coerce, message = _COERCERS[kind]
    return sc.required(coerce(value), message)


def _to_json(kind: str, value: Any) -> Any:
    if kind == "pairs":
        return [[code, name] for code, name in value]
    if kind == "strlist":
        return list(value)
    return value


class Mp3SettingsAdapter(sb.SettingsAdapter):
    app_slug = APP_SLUG

    def __init__(
        self,
        get_settings: Callable[[], Settings],
        app_version: str = "",
        redetect_tools: Callable[[], None] | None = None,
        target_dir: Path | None = None,
    ) -> None:
        self._get_settings = get_settings
        self.app_version = app_version
        self._target_dir = target_dir  # None = the app's normal settings location
        # Only offered after an import when the app supplies one (base class
        # leaves it None, which disables the "Re-detect tools" question).
        if redetect_tools is not None:
            self.redetect_tools = redetect_tools

    def sections(self) -> list[sb.SectionSpec]:
        return (
            [sb.SectionSpec(k, label) for k, label in _LABEL_PORTABLE]
            + [sb.SectionSpec(k, label + " -- this computer only", portable=False)
               for k, label in _LABEL_MACHINE]
        )

    def read_section(self, key: str) -> dict[str, Any]:
        settings = self._get_settings()
        return {
            name: _to_json(kind, getattr(settings, _field(key, name)))
            for name, kind in SECTION_KEYS[key].items()
        }

    def write_section(self, key: str, values: dict[str, Any]) -> None:
        spec = SECTION_KEYS[key]
        settings = self._get_settings()
        # Coerce everything first so a bad value can't leave a half-written section.
        coerced = {}
        for name, value in values.items():
            kind = spec.get(name)
            if kind is None:
                continue  # unknown key: ignored, never written
            try:
                coerced[name] = _coerce(kind, value)
            except ValueError:
                continue  # wrong type: keep the current value
            if name in _EXISTING_FILE_KEYS and not (coerced[name] and os.path.isfile(coerced[name])):
                del coerced[name]  # empty or not on this computer: keep the current value
        for name, value in coerced.items():
            setattr(settings, _field(key, name), value)
        if key == "table_colours":  # one bad pair must not leave poor >= good
            settings.band_poor_kbps, settings.band_good_kbps = normalise_limits(
                settings.band_poor_kbps, settings.band_good_kbps
            )
        if coerced:
            save_settings(settings, self._target_dir)
        if key == "history" and "enabled" in coerced:
            from core import history

            history.set_history_enabled(settings.history_enabled)  # the running app follows at once
        if key == "folders" and "recycle_fallback_folder" in coerced:
            from core import recycle

            recycle.set_recycle_fallback_folder(settings.recycle_fallback_folder)
