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

from core.settings import Settings, save_settings
from redactor_common.core import settings_bundle as sb

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
        "has_column_preference": "bool",
    },
    "field_defaults": {
        "ascii_filenames": "bool",
        "rename_zero_pad": "bool",
        "rename_zero_pad_width": "int",
        "auto_number_padding": "int",
        "delete_backup_after_fix": "bool",
    },
    "genres_languages": {
        "custom_genres": "strlist",
        "hidden_default_genres": "strlist",
        "custom_languages": "pairs",
        "hidden_default_languages": "strlist",
    },
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
        # The offline MusicBrainz database and the dump it is built from: files on
        # this computer, so an imported path only counts if the file is there.
        "musicbrainz_database": "str",
        "musicbrainz_dump": "str",
    },
}

# Keys whose value is a file on this computer: an import skips one that doesn't exist here.
_EXISTING_FILE_KEYS = frozenset({"musicbrainz_database", "musicbrainz_dump"})

_LABEL_PORTABLE = [
    ("recipe", "Redact recipe"),
    ("patterns", "Rename, move and path patterns (history and saved)"),
    ("columns", "Column layout, visibility and order"),
    ("field_defaults", "Field defaults (ASCII, zero-padding, numbering, backups)"),
    ("genres_languages", "Custom genres and languages"),
]
_LABEL_MACHINE = [
    ("tools", "External tool paths (mp3val, ffmpeg, keyfinder, fpcalc)"),
    ("folders", "Last-used folder, library root, MusicBrainz database and dump"),
]

# Integer fields: a sane floor so an imported 0/negative can't break padding.
_INT_MIN = 0
_INT_MAX = 99


def _coerce(kind: str, value: Any) -> Any:
    """The value as the field's type, or raises ValueError if it can't be."""
    if kind == "str":
        if isinstance(value, str):
            return value
        raise ValueError("expected text")
    if kind == "bool":
        if isinstance(value, bool):
            return value
        raise ValueError("expected true/false")
    if kind == "int":
        # bool is an int subclass in Python; don't let true become 1.
        if isinstance(value, int) and not isinstance(value, bool) and _INT_MIN <= value <= _INT_MAX:
            return value
        raise ValueError("expected a small whole number")
    if kind == "strlist":
        if isinstance(value, list) and all(isinstance(v, str) for v in value):
            return list(value)
        raise ValueError("expected a list of text")
    if kind == "pairs":
        if isinstance(value, list) and all(
            isinstance(p, (list, tuple)) and len(p) == 2 and all(isinstance(x, str) for x in p)
            for p in value
        ):
            return [(p[0], p[1]) for p in value]
        raise ValueError("expected a list of [code, name] pairs")
    raise ValueError(f"unknown kind {kind}")


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
            name: _to_json(kind, getattr(settings, name))
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
            setattr(settings, name, value)
        if coerced:
            save_settings(settings, self._target_dir)
