"""
core.lyrics_fetcher -- build_query() (pure string logic) and
fetch_lyrics() (wraps the `lyricy` package). lyricy genuinely is
installed in this sandbox (see requirements.txt), so fetch_lyrics()'s
happy/error paths are tested by mocking lyricy.Lyricy.search directly
(never a real network call) -- the ImportError/STATUS_TOOL_MISSING path
uses the same sys.modules injection trick test_bpm_detector.py uses for
aubio, so it's exercised regardless of whether lyricy happens to be
installed in whatever environment the tests run in.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

from core.lyrics_fetcher import build_query, fetch_lyrics
from core.mp3_file import MP3File, STATUS_ERROR, STATUS_OK, STATUS_TOOL_MISSING


def test_build_query_combines_artist_and_title():
    mp3 = MP3File(path=Path("song.mp3"), artist="Test Artist", title="Test Title")
    assert build_query(mp3) == "Test Artist Test Title"


def test_build_query_falls_back_to_filename_stem_when_untagged():
    mp3 = MP3File(path=Path("Some Song.mp3"))
    assert build_query(mp3) == "Some Song"


def test_fetch_lyrics_reports_tool_missing_when_lyricy_not_installed(monkeypatch):
    monkeypatch.setitem(sys.modules, "lyricy", None)

    lyrics, status, message = fetch_lyrics("Artist Title")

    assert lyrics == ""
    assert status == STATUS_TOOL_MISSING
    assert "lyricy" in message


def test_fetch_lyrics_returns_error_for_a_blank_query():
    lyrics, status, message = fetch_lyrics("   ")

    assert lyrics == ""
    assert status == STATUS_ERROR


def _fake_match(link: str, lyrics_without_lrc_tags: str = ""):
    match = MagicMock()
    match.link = link
    match.lyrics_without_lrc_tags = lyrics_without_lrc_tags

    def _fetch():
        match.lyrics_without_lrc_tags = lyrics_without_lrc_tags

    match.fetch.side_effect = _fetch
    return match


@patch("lyricy.Lyricy.search")
def test_fetch_lyrics_returns_plain_lyrics_on_a_match(mock_search):
    mock_search.return_value = [_fake_match("lrclib:123", "Line one\nLine two")]

    lyrics, status, message = fetch_lyrics("Artist Title")

    assert lyrics == "Line one\nLine two"
    assert status == STATUS_OK
    assert message == ""


@patch("lyricy.Lyricy.search")
def test_fetch_lyrics_reports_error_when_nothing_found(mock_search):
    # lyricy's own "nothing found" sentinel is a result with an empty
    # link (e.g. title="No result found"), not an exception or an empty
    # list -- see core/lyrics_fetcher.py's own docstring.
    mock_search.return_value = [_fake_match("", "")]

    lyrics, status, message = fetch_lyrics("Nonexistent Song")

    assert lyrics == ""
    assert status == STATUS_ERROR


@patch("lyricy.Lyricy.search")
def test_fetch_lyrics_reports_error_when_search_raises(mock_search):
    mock_search.side_effect = ConnectionError("network unreachable")

    lyrics, status, message = fetch_lyrics("Artist Title")

    assert lyrics == ""
    assert status == STATUS_ERROR
    assert "network unreachable" in message


@patch("lyricy.Lyricy.search")
def test_fetch_lyrics_reports_error_when_the_matched_result_fetch_fails(mock_search):
    match = _fake_match("lrclib:123")
    match.fetch.side_effect = ValueError("bad response")
    mock_search.return_value = [match]

    lyrics, status, message = fetch_lyrics("Artist Title")

    assert lyrics == ""
    assert status == STATUS_ERROR
    assert "bad response" in message
