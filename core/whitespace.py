"""
core/whitespace.py

Finds and tidies stray whitespace in tag values (decided 2026-11-25,
questions 1A-4A on the whitespace decision page). The case that started
it: every file of "[2005] - Bloodstains Across Switzerland" had Album =
"  Switzerland" (two leading spaces). Nothing in tag_reader/tag_writer
trims a value, so a file keeps exactly what is in it until it is tidied
here, either in Edit > Tidy Whitespace... (gui/whitespace_dialog.py) or by
the Redact step (core/redact_steps.TidyWhitespaceStep).

The rules:
- One-line fields (every editable text field except Comment and Lyrics):
  zero-width characters (U+200B, U+FEFF) go; every run of whitespace --
  spaces, tabs, line breaks, non-breaking spaces (U+00A0, U+202F) --
  becomes one plain space; the ends are trimmed.
- Comment and Lyrics, where line breaks are real: zero-width characters
  go, spaces at the end of each line go, and blank space at the very start
  and end of the value goes. Line breaks and spaces inside a line stay.

Qt-free.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from core.fields import FIELDS
from core.mp3_file import JOURNAL_KEYS

ZERO_WIDTH = ("​", "﻿")
_ZERO_WIDTH_RE = re.compile("[" + "".join(ZERO_WIDTH) + "]")
# Spaces/tabs (and non-breaking spaces) just before a line break in a multi-line value.
_LINE_END_SPACE_RE = re.compile(r"[ \t  ]+(?=\r\n|\r|\n)")

MULTILINE_KEYS = frozenset({"comment", "lyrics"})
_LABELS = {key: label for key, label, _multiline in FIELDS}
_LABELS["lyrics"] = "Lyrics"
# The editable text tags, in the table's order, then Lyrics.
_FIELD_KEYS = [key for key, _label, _multiline in FIELDS]
TIDY_KEYS: tuple[str, ...] = tuple(key for key in _FIELD_KEYS if key in JOURNAL_KEYS) + tuple(
    key for key in JOURNAL_KEYS if key not in _FIELD_KEYS
)


def field_label(key: str) -> str:
    return _LABELS.get(key, key)


def tidy_value(text: str, multiline: bool = False) -> str:
    """`text` with its stray whitespace tidied (see the module docstring)."""
    if not text:
        return text or ""
    text = _ZERO_WIDTH_RE.sub("", text)
    if multiline:
        return _LINE_END_SPACE_RE.sub("", text).strip()
    return " ".join(text.split())


def tidy_field(key: str, text: str) -> str:
    return tidy_value(text, key in MULTILINE_KEYS)


@dataclass
class WhitespaceFix:
    """One untidy value: `item` is the file (an MP3File), `key` its field."""

    item: object
    key: str
    old: str
    new: str

    @property
    def label(self) -> str:
        return field_label(self.key)


def fixes_for(item) -> list[WhitespaceFix]:
    """The untidy values of one file (none for a file that failed to load)."""
    if getattr(item, "load_error", ""):
        return []
    found = []
    for key in TIDY_KEYS:
        old = getattr(item, key, "") or ""
        new = tidy_field(key, old)
        if new != old:
            found.append(WhitespaceFix(item, key, old, new))
    return found


def find_untidy(items) -> list[WhitespaceFix]:
    """Every untidy value in `items`, file by file in the given order."""
    return [fix for item in items for fix in fixes_for(item)]


# --- showing the hidden characters ----------------------------------------------

_SHOW = {"\t": "→", " ": "⍽", " ": "⍽", "​": "¤", "﻿": "¤"}
_UNTIDY_SPACES_RE = re.compile(r"\A +| +\Z| {2,}| +(?=\r\n|\r|\n)")
_UNTIDY_SPACES_MULTILINE_RE = re.compile(r"\A[ \t\r\n]+|[ \t\r\n]+\Z| +(?=\r\n|\r|\n)")


def show_whitespace(text: str, multiline: bool = False) -> str:
    """`text` for display with the untidy whitespace made visible: a space at
    the start or end, a run of spaces (one-line fields only: indentation in
    a comment is kept) or a space before a line break as a middle dot, a tab
    as an arrow, a line break as a return sign, a non-breaking space as a
    visible-space sign and a zero-width character as a currency sign. A
    single space between two words stays a space."""
    pattern = _UNTIDY_SPACES_MULTILINE_RE if multiline else _UNTIDY_SPACES_RE
    text = pattern.sub(lambda m: re.sub(" ", "·", m.group(0)), text or "")
    text = "".join(_SHOW.get(ch, ch) for ch in text)
    return text.replace("\r\n", "↵").replace("\n", "↵").replace("\r", "↵")
