"""
A failed or empty lyrics fetch never touches the lyrics a file already has:
only a successful fetch replaces them, and only an explicit clear in the
Edit Lyrics dialog removes the USLT frame. (Regression: a "no lyrics found"
result used to blank mp3.lyrics, so the next Save deleted the frame.)
"""

import os
import shutil
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from mutagen.id3 import ID3, USLT  # noqa: E402

from core.mp3_file import MP3File, STATUS_ERROR, STATUS_OK, STATUS_TOOL_ERROR  # noqa: E402
from core.scan_service import run_lyrics_fetch  # noqa: E402
from core.tag_reader import load_tags  # noqa: E402
from core.tag_writer import save_tags  # noqa: E402
from tests.test_cover_gui import _load, _select_all, window  # noqa: E402,F401

FIXTURE = Path(__file__).parent / "fixtures" / "tiny.mp3"


def _with_lyrics(tmp_path, text="my old lyrics"):
    path = tmp_path / "song.mp3"
    shutil.copyfile(FIXTURE, path)
    tags = ID3(path)
    tags.add(USLT(encoding=3, lang="eng", desc="", text=text))
    tags.save(path)
    mp3 = MP3File(path=path, artist="Artist", title="Title")
    load_tags(mp3)
    mp3.artist, mp3.title = "Artist", "Title"
    return mp3


def _uslt(path):
    return [str(f.text) for k, f in ID3(path).items() if k.startswith("USLT")]


@pytest.mark.parametrize(
    "result",
    [
        ("", STATUS_ERROR, "no lyrics found"),
        ("", STATUS_TOOL_ERROR, "lyrics search failed: offline"),
        ("", STATUS_OK, ""),  # defensive: OK without text is not a replacement
    ],
)
def test_a_failed_fetch_keeps_the_lyrics_and_a_later_save_keeps_the_frame(tmp_path, result):
    mp3 = _with_lyrics(tmp_path)
    assert mp3.lyrics == "my old lyrics" and not mp3.dirty
    with patch("core.scan_service.fetch_lyrics", return_value=result):
        run_lyrics_fetch([mp3], max_workers=1)
    assert mp3.lyrics == "my old lyrics"
    assert mp3.dirty is False
    assert mp3.lyrics_status == result[1] and mp3.lyrics_message == result[2]  # still shown
    mp3.apply_tags({"title": "Another"})  # an unrelated edit, then Save
    assert save_tags(mp3)
    assert _uslt(mp3.path) == ["my old lyrics"]


def test_a_successful_fetch_replaces_the_lyrics_as_before(tmp_path):
    mp3 = _with_lyrics(tmp_path)
    with patch("core.scan_service.fetch_lyrics", return_value=("new words", STATUS_OK, "")):
        run_lyrics_fetch([mp3], max_workers=1)
    assert mp3.lyrics == "new words" and mp3.dirty
    assert save_tags(mp3)
    assert _uslt(mp3.path) == ["new words"]


def test_a_batch_only_replaces_the_files_that_were_found(tmp_path):
    a = _with_lyrics(tmp_path)
    b_path = tmp_path / "other.mp3"
    shutil.copyfile(FIXTURE, b_path)
    b = MP3File(path=b_path, artist="Other", title="Song")
    load_tags(b)
    b.artist, b.title = "Other", "Song"

    def fake(query, artist="", title=""):
        return ("found", STATUS_OK, "") if artist == "Other" else ("", STATUS_ERROR, "no lyrics found")

    with patch("core.scan_service.fetch_lyrics", side_effect=fake):
        run_lyrics_fetch([a, b], max_workers=2)
    assert a.lyrics == "my old lyrics" and not a.dirty
    assert b.lyrics == "found" and b.dirty


def test_clearing_the_lyrics_explicitly_still_removes_the_frame(tmp_path):
    mp3 = _with_lyrics(tmp_path)
    mp3.lyrics = ""  # what the Edit Lyrics dialog does on an explicit clear
    mp3.dirty = True
    assert save_tags(mp3)
    assert _uslt(mp3.path) == []


def test_edit_lyrics_dialog_clear_marks_dirty_and_save_removes_the_frame(window, tmp_path, monkeypatch):
    import gui.main_window as mw

    mp3 = _with_lyrics(tmp_path)
    window.files = [mp3]
    window._rebuild_table()

    class Cleared:
        def __init__(self, *a, **k):
            pass

        def exec(self):
            return mw.QDialog.DialogCode.Accepted

        def result_lyrics(self):
            return ""

    monkeypatch.setattr(mw, "LyricsDialog", Cleared)
    window.open_lyrics_dialog(mp3)
    assert mp3.lyrics == "" and mp3.dirty
    assert save_tags(mp3)
    assert _uslt(mp3.path) == []


def test_not_found_with_kept_lyrics_still_shows_the_lyrics_cell(window, tmp_path):
    mp3 = _with_lyrics(tmp_path)
    window.files = [mp3]
    window._rebuild_table()
    _select_all(window)
    with patch("core.scan_service.fetch_lyrics", return_value=("", STATUS_ERROR, "no lyrics found")):
        window.run_lyrics_fetch()
    item = window.table.item(0, window._col_index["lyrics"])
    assert item.text().startswith("Yes")
    assert "no lyrics found" in item.toolTip()
    assert mp3.lyrics == "my old lyrics" and not mp3.dirty
