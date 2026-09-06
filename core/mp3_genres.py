"""
core/mp3_genres.py

A curated list of genre labels for the quick-pick "+" button next to
the Genre field (see gui/tag_panel.py), plus the pure hideable-
defaults-plus-custom merge logic behind it. Same role as
epubredactor's core/genres.py / cbzredactor's core/comic_genres.py,
but for music -- drawn from the standard ID3v1 genre list (the closest
thing MP3 tagging has to a canonical vocabulary; WinAmp's later
extensions past index 79 are deliberately omitted here as much less
universally recognized) rather than either of those projects' own
domains.

Unlike epub's Genre (semicolon list) or cbz's Genre (comma list),
this field is treated as single-valued -- ID3's TCON frame is
conventionally one genre per file in practice (even though the spec
technically allows more), and nothing elsewhere in this project's tag
reading/writing treats it as delimited. Picking from this list
therefore REPLACES the field, same as core/mp3_languages.py's
Language, not append-with-dedupe the way epub/cbz's multi-valued
Genre works.
"""

COMMON_MP3_GENRES: list[str] = sorted([
    "Blues", "Classic Rock", "Country", "Dance", "Disco", "Funk",
    "Grunge", "Hip-Hop", "Jazz", "Metal", "New Age", "Oldies", "Other",
    "Pop", "R&B", "Rap", "Reggae", "Rock", "Techno", "Industrial",
    "Alternative", "Ska", "Death Metal", "Soundtrack", "Ambient",
    "Trip-Hop", "Vocal", "Fusion", "Trance", "Classical",
    "Instrumental", "Acid", "House", "Game", "Sound Clip", "Gospel",
    "Noise", "AlternRock", "Bass", "Soul", "Punk", "Space",
    "Meditative", "Gothic", "Darkwave", "Electronic", "Pop-Folk",
    "Eurodance", "Dream", "Southern Rock", "Comedy", "Cult", "Gangsta",
    "Top 40", "Christian Rap", "Jungle", "Native US", "Cabaret",
    "New Wave", "Psychedelic", "Rave", "Showtunes", "Trailer", "Lo-Fi",
    "Tribal", "Acid Punk", "Acid Jazz", "Polka", "Retro", "Musical",
    "Rock & Roll", "Hard Rock", "Folk", "Ballad", "Chanson", "Opera",
    "Chamber Music", "Sonata", "Symphony", "Booty Bass", "Primus",
    "Porn Groove", "Satire", "Slow Jam", "Club", "Tango", "Samba",
    "Folklore", "Ballroom", "Power Ballad", "Rhythmic Soul", "Freestyle",
    "Duet", "Punk Rock", "Drum Solo", "A Capella", "Euro-House",
    "Dance Hall",
])


def merge_genres(defaults: list[str], custom: list[str]) -> list[str]:
    """Visible defaults plus custom entries not already present
    (case-insensitively), custom entries appended in the order given."""
    seen = {g.lower() for g in defaults}
    result = list(defaults)
    for genre in custom:
        genre = genre.strip()
        if not genre or genre.lower() in seen:
            continue
        seen.add(genre.lower())
        result.append(genre)
    return result


def exclude_hidden(defaults: list[str], hidden: list[str]) -> list[str]:
    hidden_lower = {g.lower() for g in hidden}
    return [g for g in defaults if g.lower() not in hidden_lower]
