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


def fetch_lyrics(query: str) -> tuple[str, str, str]:
    """
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
    match = results[0] if results else None
    if match is None or not match.link:
        return "", STATUS_ERROR, "no lyrics found"

    try:
        match.fetch()
    except Exception as e:  # noqa: BLE001
        return "", STATUS_ERROR, f"lyrics fetch failed: {e}"

    lyrics = (match.lyrics_without_lrc_tags or "").strip()
    if not lyrics:
        return "", STATUS_ERROR, "no lyrics found"

    return lyrics, STATUS_OK, ""
