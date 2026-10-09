"""
mp3cli/fields.py

Tag field names for the command line: any spelling the user is likely to type ("albumartist", "Album Artist",
"album_artist") finds the field, and a value is checked before anything is written.
"""

from __future__ import annotations

import re

from core.fields import FIELDS
from redactor_common.cli import CliError
from redactor_common.cli.values import check_text

FIELD_KEYS = [key for key, _label, _multiline in FIELDS]

_LOOKUP: dict[str, str] = {}
for _key, _label, _multiline in FIELDS:
    for _spelling in (
        _key, _label, _label.replace(" ", ""), _label.replace(" ", "_"), _label.replace(" ", "-"), _key.replace("_", ""),
    ):
        _LOOKUP[_spelling.lower()] = _key

_TRACK = re.compile(r"^[0-9]+(/[0-9]+)?$")  # ASCII digits only: \d would also take Arabic-Indic digits
_YEAR = re.compile(r"^[0-9]{4}(-[0-9]{2}(-[0-9]{2})?)?$")
_LANGUAGE = re.compile(r"^[A-Za-z]{3}$")
MULTILINE_KEYS = {"comment"}  # every other tag is one line
ADVISORY_VALUES = ("0", "1", "2")  # iTunes: none, explicit, clean

# What `info` shows when no --fields or --all is given.
DEFAULT_INFO_FIELDS = ["title", "artist", "album", "track", "year"]


def resolve_field(name: str) -> str:
    """The MP3File attribute for what the user typed. Raises CliError listing the valid names."""
    key = _LOOKUP.get(name.strip().lower())
    if key is None:
        raise CliError(f"unknown field {name!r}. Fields: {', '.join(FIELD_KEYS)}")
    return key


def check_value(key: str, value: str) -> str:
    """The value to store (stripped), or a CliError when the tag would not take it. "" clears the field."""
    value = check_text(key, value, multiline=key in MULTILINE_KEYS)
    if not value:
        return ""
    if key in ("track", "discnumber") and not _TRACK.match(value):
        raise CliError(f"{key} must be a number or number/total (3 or 3/12), not {value!r}")
    if key == "year" and not _YEAR.match(value):
        raise CliError(f"year must be YYYY, YYYY-MM or YYYY-MM-DD, not {value!r}")
    if key == "language":
        if not _LANGUAGE.match(value):
            raise CliError(f"language must be a three-letter code (eng, nor, deu), not {value!r}")
        return value.lower()  # ID3 language codes are lower case
    if key == "itunesadvisory" and value not in ADVISORY_VALUES:
        raise CliError("itunesadvisory must be 0 (none), 1 (explicit) or 2 (clean)")
    return value


def parse_assignment(text: str) -> tuple[str, str]:
    """"artist=Queen" -> (key, checked value). Splits on the first "="."""
    if "=" not in text:
        raise CliError(f"expected FIELD=VALUE, got {text!r}")
    name, _, value = text.partition("=")
    key = resolve_field(name)
    return key, check_value(key, value)
