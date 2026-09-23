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

from redactor_common.core.languages import language_pairs
from redactor_common.core.managed_list import exclude_hidden_codes, merge_pairs

# (ISO 639-2/T code, English name) from redactor_common's shared ISO 639
# table -- the same table epub and cbz build their 2-letter lists from.
DEFAULT_LANGUAGES: list[tuple[str, str]] = language_pairs(
    ["eng", "nor", "swe", "dan", "fin", "deu", "fra", "spa", "ita", "por",
     "nld", "rus", "jpn", "kor", "zho", "ara", "pol", "und"],
    "alpha3",
)

# The hideable-defaults-plus-custom merge rules are shared too
# (redactor_common.core.managed_list); kept under their original names.
merge_languages = merge_pairs
exclude_hidden = exclude_hidden_codes
