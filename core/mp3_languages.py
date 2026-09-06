"""
core/mp3_languages.py

A curated list of (ISO 639-2 code, display name) pairs for the
quick-pick "+" button next to the Language field (see gui/tag_panel.py
and core/tag_writer.py's TLAN frame), plus the pure hideable-defaults-
plus-custom merge logic behind it. Same role as epubredactor's/
cbzredactor's own language lists, but 3-letter ISO 639-2 codes rather
than 2-letter ISO 639-1 -- that's what ID3's TLAN frame actually
specifies (see https://id3.org/id3v2.4.0-frames), even though real-
world taggers are often lenient about it.

Deliberately a modest common set, not the full ISO 639-2 registry (a
few hundred entries) -- same "convenience shortlist, always free text"
philosophy as core/mp3_genres.py; anything not listed can still be
typed directly, or added as a custom entry via Settings > Add/Remove
Languages.
"""

DEFAULT_LANGUAGES: list[tuple[str, str]] = [
    ("eng", "English"),
    ("nor", "Norwegian"),
    ("swe", "Swedish"),
    ("dan", "Danish"),
    ("fin", "Finnish"),
    ("deu", "German"),
    ("fra", "French"),
    ("spa", "Spanish"),
    ("ita", "Italian"),
    ("por", "Portuguese"),
    ("nld", "Dutch"),
    ("rus", "Russian"),
    ("jpn", "Japanese"),
    ("kor", "Korean"),
    ("zho", "Chinese"),
    ("ara", "Arabic"),
    ("pol", "Polish"),
    ("und", "Undetermined"),
]


def merge_languages(
    defaults: list[tuple[str, str]], custom: list[tuple[str, str]]
) -> list[tuple[str, str]]:
    """Visible defaults plus custom entries whose code isn't already
    present, custom entries appended in the order given."""
    seen_codes = {code for code, _name in defaults}
    result = list(defaults)
    for code, name in custom:
        code = code.strip()
        name = name.strip()
        if not code or not name or code in seen_codes:
            continue
        seen_codes.add(code)
        result.append((code, name))
    return result


def exclude_hidden(
    defaults: list[tuple[str, str]], hidden_codes: list[str]
) -> list[tuple[str, str]]:
    hidden = set(hidden_codes)
    return [(code, name) for code, name in defaults if code not in hidden]
