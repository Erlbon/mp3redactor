"""
Single source of truth for which tag fields the app edits, their
display labels, and whether they're multi-line -- the file table and
the bulk-edit tag panel are both built from this list so they always
agree on order and naming. Same pattern as the epub tool's
core/fields.py, deliberately trimmed down: MP3 tags don't have an
equivalent to covers or ISBN lookups (yet -- those stay roadmap
items), so this v1 is plain text fields only. Genre and Language do
get the family's quick-pick "+" button (see gui/tag_panel.py,
core/mp3_genres.py, core/mp3_languages.py) -- that's a Settings-
managed convenience list layered on top of a plain text field, not a
structurally different kind of field, so it doesn't need its own
column in this tuple.

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
    ("language", "Language", False),
]

# Fields with a quick-pick "+" button in the bulk-edit panel, backed by
# a Settings-managed hideable-defaults-plus-custom list (see
# core/mp3_genres.py, core/mp3_languages.py). Picking a value REPLACES
# the field -- unlike epub's/cbz's semicolon/comma-joined multi-value
# Genre, MP3's TCON is conventionally single-valued in practice and
# nothing else in this project's tag reading/writing treats it as a
# delimited list.
QUICK_PICK_FIELDS: frozenset[str] = frozenset({"genre", "language"})
