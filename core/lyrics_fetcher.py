"""
Fetches song lyrics via the `lyricy` package's LRCLIB provider -- a
free, keyless REST API at lrclib.net (https://github.com/yogeshwaran01/
lyricy for the wrapper itself). Implements README.md's roadmap item 7
("Lyrics fetch + write -- via `lyricy` (fetch) + `mutagen` (write)").

lyricy is treated as an optional dependency, same guarded-import
convention as core/bpm_detector.py's aubio: a missing package degrades
lyrics fetching specifically (STATUS_TOOL_MISSING), not file loading as
a whole.

Only LRCLIB's free-text search is used (Lyricy.search(query), the
library's own default provider) -- not its per-field track/artist/
album/duration match, which lyricy only wires up for its alternate
BetterLyrics provider (which needs an API key for anything not already
cached upstream). A plain "<artist> <title>" query already matches
LRCLIB's own index well in practice and needs no key.

Plain lyrics only (Lyrics.lyrics_without_lrc_tags) -- this app has no
synced-lyrics display or .lrc export, so LRCLIB's [mm:ss.xx] time tags
would just be embedded as noise in the USLT frame and the Lyrics dialog
(see core/tag_writer.py's _write_lyrics_frame(), gui/lyrics_dialog.py).
"""

import re
import unicodedata

from core.mp3_file import MP3File, STATUS_ERROR, STATUS_OK, STATUS_TOOL_MISSING


def build_query(mp3: MP3File) -> str:
    """"<artist> <title>" from the file's current tags, falling back to
    the filename (minus extension) when neither is tagged -- an
    untagged file is still worth a best-effort search rather than
    refusing outright. Shared by core.scan_service.run_lyrics_fetch()
    (bulk fetch) and gui/lyrics_dialog.py (single-file Fetch button) so
    both build the exact same query for the same file."""
    query = f"{mp3.artist} {mp3.title}".strip()
    return query if query else mp3.path.stem


def _normalize(text: str) -> str:
    """Case/accents/punctuation-insensitive form for comparing names."""
    text = unicodedata.normalize("NFKD", text or "").casefold()
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[\W_]+", " ", text).strip()


def _title_core(title: str) -> str:
    """The title without "(Remastered 2011)" / "[Live]" style suffixes."""
    return _normalize(re.sub(r"[\(\[][^)\]]*[\)\]]", " ", title)) or _normalize(title)


def result_matches(result_title: str, artist: str, title: str) -> bool:
    """Does an LRCLIB result ("<track> - <artist> (<album>)") belong to
    this artist/title? The search is free text, so its first hit can be a
    different song entirely. Each name given must appear in the result
    (normalized; a multi-artist tag matches if any one artist does).
    With neither given there's nothing to check against."""
    haystack = f" {_normalize(result_title)} "
    if title and f" {_title_core(title)} " not in haystack:
        return False
    if artist:
        names = [_normalize(n) for n in re.split(r"[;,/&]| feat\.? | ft\.? ", artist, flags=re.IGNORECASE)]
        if not any(n and f" {n} " in haystack for n in names):
            return False
    return True


def fetch_lyrics(query: str, artist: str = "", title: str = "") -> tuple[str, str, str]:
    """
    artist/title (the file's own tags), when given, must match the result
    -- the first search hit that does is used, and if none does the result
    is "no lyrics found" rather than another song's lyrics.

    Returns (lyrics, status, message). lyrics is "" unless status is
    STATUS_OK. status is one of STATUS_OK / STATUS_ERROR /
    STATUS_TOOL_MISSING -- lyricy has no WARNING-equivalent, same
    vocabulary as core.keyfinder_runner.detect_key().
    """
    try:
        from lyricy import Lyricy
    except ImportError:
        return "", STATUS_TOOL_MISSING, "lyricy is not installed"

    query = query.strip()
    if not query:
        return "", STATUS_ERROR, "nothing to search with (no artist/title/filename)"

    try:
        results = Lyricy.search(query)
    except Exception as e:  # noqa: BLE001 -- any network/parse failure is a per-file result, not a crash
        return "", STATUS_ERROR, f"lyrics search failed: {e}"

    # lyricy's own "nothing found" sentinel is a result with an empty
    # link (e.g. title="No result found"), not an exception or an empty
    # list -- see LrcLib.search_lyrics() in the lyricy package itself.
    candidates = [r for r in results if r.link]
    if not candidates:
        return "", STATUS_ERROR, "no lyrics found"
    if artist or title:
        match = next(
            (r for r in candidates if result_matches(getattr(r, "title", "") or "", artist, title)),
            None,
        )
        if match is None:
            return "", STATUS_ERROR, (
                f'no lyrics found for this song (closest result: "{candidates[0].title}")'
            )
    else:
        match = candidates[0]

    try:
        match.fetch()
    except Exception as e:  # noqa: BLE001
        return "", STATUS_ERROR, f"lyrics fetch failed: {e}"

    lyrics = (match.lyrics_without_lrc_tags or "").strip()
    if not lyrics:
        return "", STATUS_ERROR, "no lyrics found"

    return lyrics, STATUS_OK, ""
