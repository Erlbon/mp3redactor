"""
Single source of truth for which tag fields the app edits, their
display labels, and whether they're multi-line -- the file table and
the bulk-edit tag panel are both built from this list so they always
agree on order and naming. Same pattern as the epub tool's
core/fields.py, deliberately trimmed down: MP3 tags don't have an
equivalent to covers, genre/language pickers, or ISBN lookups (yet --
those stay roadmap items), so this v1 is plain text fields only.

Each tuple: (attribute_key, display_label, multiline). `attribute_key`
matches an MP3File attribute name directly (see core/mp3_file.py) and
an ID3 frame in core/tag_reader.py / core/tag_writer.py.
"""

FIELDS: list[tuple[str, str, bool]] = [
    ("title", "Title", False),
    ("artist", "Artist", False),
    ("album", "Album", False),
    ("track", "Track", False),
    ("year", "Year", False),
    ("genre", "Genre", False),
]
