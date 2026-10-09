"""
core/m4b_builder.py -- ordering, ffmetadata text, and a real build with the bundled ffmpeg
(MP3s made from generated tones; the result is read back with mutagen and ffprobe).
"""

import json
import wave
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from core import m4b_builder as mb
from core.ffmpeg_probe import FFPROBE_EXE_NAME
from core.mp3_converter import convert_to_mp3
from core.mp3_file import STATUS_ERROR, STATUS_OK, STATUS_TOOL_MISSING
from core.tool_locator import find_tool
from redactor_common.core.subprocess_utils import run_tool

_FFMPEG = find_tool("ffmpeg.exe")
_FFPROBE = find_tool(FFPROBE_EXE_NAME)
requires_ffmpeg = pytest.mark.skipif(_FFMPEG is None or _FFPROBE is None, reason="ffmpeg/ffprobe not available")


def f(name, title="", track="", disc=""):
    return SimpleNamespace(path=Path(name), title=title, track=track, discnumber=disc)


# --- ordering and titles ----------------------------------------------------------


def test_files_are_ordered_by_disc_then_track_then_natural_name():
    files = [f("b10.mp3", track="2", disc="2"), f("a.mp3", track="3", disc="1"), f("z.mp3", track="1", disc="1")]
    assert [x.path.name for x in mb.order_files(files)] == ["z.mp3", "a.mp3", "b10.mp3"]


def test_without_track_numbers_the_names_are_in_natural_order():
    files = [f("Chapter 10.mp3"), f("Chapter 2.mp3"), f("Chapter 1.mp3")]
    assert [x.path.name for x in mb.order_files(files)] == ["Chapter 1.mp3", "Chapter 2.mp3", "Chapter 10.mp3"]


def test_track_of_total_and_blank_tracks_are_handled():
    files = [f("b.mp3", track="2/12"), f("a.mp3", track="")]
    assert [x.path.name for x in mb.order_files(files)] == ["b.mp3", "a.mp3"]  # a tracked file before an untracked one


def test_a_chapter_title_is_the_tag_else_the_file_name():
    assert mb.chapter_title(f("x/01 Intro.mp3", title=" The Start ")) == "The Start"
    assert mb.chapter_title(f("x/01 Intro.mp3")) == "01 Intro"


# --- ffmetadata -------------------------------------------------------------------


def test_ffmetadata_escapes_the_special_characters():
    bs = chr(92)
    assert mb.escape_ffmetadata("a=b;c#d" + bs + "e") == f"a{bs}=b{bs};c{bs}#d{bs}{bs}e"
    assert mb.escape_ffmetadata("two\nlines") == "two lines"


def test_ffmetadata_lays_the_chapters_end_to_end_and_skips_blank_tags():
    spec = mb.BookSpec(
        chapters=[mb.BookChapter(Path("a.mp3"), "One"), mb.BookChapter(Path("b.mp3"), "Two; Part")],
        output=Path("book.m4b"), title="My Book", author="A. Writer",
    )
    text = mb.ffmetadata_text(spec, [1.5, 2.0])
    lines = text.splitlines()
    assert lines[0] == ";FFMETADATA1"
    assert "title=My Book" in lines and "artist=A. Writer" in lines and "genre=Audiobook" in lines
    assert not any(line.startswith(("composer=", "date=")) for line in lines[:8])  # blank narrator / year left out
    assert "START=0" in lines and "END=1500" in lines and "START=1500" in lines and "END=3500" in lines
    assert any(line.startswith("title=Two") and "Part" in line for line in lines)


# --- a real build -------------------------------------------------------------------


def _tone_mp3(path: Path, seconds: float, hz: int = 440, rate: int = 44100) -> Path:
    wav = path.with_suffix(".wav")
    n = int(rate * seconds)
    t = np.linspace(0, seconds, n, endpoint=False)
    pcm = (0.3 * np.sin(2 * np.pi * hz * t) * 32767).astype(np.int16)
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    status, message = convert_to_mp3(wav, path, bitrate_kbps=128)
    assert status == STATUS_OK, message
    wav.unlink()
    return path


def _probe(path: Path) -> dict:
    result = run_tool(
        [str(_FFPROBE), "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", "-show_chapters",
         str(path)],
        timeout=60,
    )
    return json.loads(result.stdout)


@requires_ffmpeg
def test_builds_a_chaptered_m4b_with_tags(tmp_path):
    chapters = [
        mb.BookChapter(_tone_mp3(tmp_path / "01.mp3", 2.0, 440), "Opening"),
        mb.BookChapter(_tone_mp3(tmp_path / "02.mp3", 3.0, 660, rate=22050), "The Middle"),  # a different rate
    ]
    out = tmp_path / "out" / "My Book.m4b"
    seen = []
    result = mb.build_m4b(
        mb.BookSpec(
            chapters, out, "My Book", author="A. Writer", narrator="N. Reader", year="2024", bitrate_kbps=48,
            series="The Series", series_index="2", publisher="Pub House", language="eng",
        ),
        progress=lambda done, total: seen.append((done, total)),
    )
    assert result.status == STATUS_OK, result.message
    assert out.exists() and result.chapter_count == 2
    assert seen[0] == (0, 3) and seen[-1] == (3, 3)  # two chapters plus the join

    info = _probe(out)
    assert 4.0 < float(info["format"]["duration"]) < 6.5
    assert [c["tags"]["title"] for c in info["chapters"]] == ["Opening", "The Middle"]
    first, second = info["chapters"]
    assert float(first["start_time"]) == 0.0
    assert 1.7 < float(first["end_time"]) < 2.5 and abs(float(second["start_time"]) - float(first["end_time"])) < 0.01
    tags = {k.lower(): v for k, v in info["format"]["tags"].items()}
    assert tags["title"] == "My Book" and tags["artist"] == "A. Writer" and tags["album"] == "My Book"
    assert tags["composer"] == "N. Reader" and tags["genre"] == "Audiobook"
    # the tags Audiobookshelf reads by name
    assert tags["series"] == "The Series" and tags["series-part"] == "2"
    assert tags["publisher"] == "Pub House" and tags["language"] == "eng"
    assert any(s["codec_type"] == "video" for s in info["streams"]) is False  # no cover was given here
    audio = next(s for s in info["streams"] if s["codec_type"] == "audio")
    assert audio["codec_name"] == "aac" and int(audio["sample_rate"]) == 44100  # the first file's rate, for all
    # nothing is left beside the result
    assert sorted(p.name for p in out.parent.iterdir()) == ["My Book.m4b"]


@requires_ffmpeg
def test_the_cover_is_embedded(tmp_path):
    from PIL import Image
    import io

    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (200, 30, 30)).save(buf, format="JPEG")
    chapters = [mb.BookChapter(_tone_mp3(tmp_path / "01.mp3", 1.5), "One")]
    out = tmp_path / "Book.m4b"
    result = mb.build_m4b(mb.BookSpec(chapters, out, "Book", cover=buf.getvalue(), cover_mime="image/jpeg"))
    assert result.status == STATUS_OK, result.message
    video = [s for s in _probe(out)["streams"] if s["codec_type"] == "video"]
    assert video and video[0]["disposition"]["attached_pic"] == 1


@requires_ffmpeg
def test_a_cancelled_build_leaves_nothing_behind(tmp_path):
    chapters = [mb.BookChapter(_tone_mp3(tmp_path / f"{i}.mp3", 1.0), f"C{i}") for i in range(3)]
    out = tmp_path / "Book.m4b"
    result = mb.build_m4b(mb.BookSpec(chapters, out, "Book"), should_cancel=lambda: True)
    assert result.cancelled and not out.exists()
    assert sorted(p.name for p in tmp_path.iterdir() if p.suffix != ".mp3") == []


@requires_ffmpeg
def test_a_broken_input_is_reported_and_leaves_nothing_behind(tmp_path):
    bad = tmp_path / "bad.mp3"
    bad.write_bytes(b"this is not audio")
    out = tmp_path / "Book.m4b"
    result = mb.build_m4b(mb.BookSpec([mb.BookChapter(bad, "Bad")], out, "Book"))
    assert result.status == STATUS_ERROR and "bad.mp3" in result.message
    assert not out.exists() and sorted(p.name for p in tmp_path.iterdir()) == ["bad.mp3"]


def test_nothing_to_build_and_missing_tools_are_reported(tmp_path, monkeypatch):
    assert mb.build_m4b(mb.BookSpec([], tmp_path / "x.m4b", "X")).status == STATUS_ERROR
    monkeypatch.setattr(mb, "find_tool", lambda *a, **k: None)
    result = mb.build_m4b(mb.BookSpec([mb.BookChapter(Path("a.mp3"), "A")], tmp_path / "x.m4b", "X"))
    assert result.status == STATUS_TOOL_MISSING


# --- Audiobookshelf conventions: folders, OPF, sidecar --------------------------------


def test_group_by_folder_makes_one_book_per_folder_in_reading_order():
    files = [
        SimpleNamespace(path=Path("lib/Book B/02.mp3"), discnumber="", track="2"),
        SimpleNamespace(path=Path("lib/Book A/10.mp3"), discnumber="", track=""),
        SimpleNamespace(path=Path("lib/Book B/01.mp3"), discnumber="", track="1"),
        SimpleNamespace(path=Path("lib/Book A/2.mp3"), discnumber="", track=""),
    ]
    books = mb.group_by_folder(files)
    assert [[f.path.name for f in book] for book in books] == [["2.mp3", "10.mp3"], ["01.mp3", "02.mp3"]]


def test_library_output_follows_author_series_book():
    root = Path("lib")
    assert mb.library_output(root, "Terry Goodkind", "Wizards First Rule", "Sword of Truth") == (
        root / "Terry Goodkind" / "Sword of Truth" / "Wizards First Rule" / "Wizards First Rule.m4b"
    )
    assert mb.library_output(root, "Steven Levy", "Hackers") == root / "Steven Levy" / "Hackers" / "Hackers.m4b"
    assert mb.library_output(root, "", "Loose: Book?") == root / "Loose Book" / "Loose Book.m4b"  # illegal characters go


def test_the_opf_matches_audiobookshelfs_documented_shape():
    spec = mb.BookSpec(
        [], Path("b.m4b"), "Dune & Co", author="F. Herbert", narrator="S. Brick", year="1965", publisher="Chilton",
        language="eng", series="Dune Chronicles", series_index="1",
    )
    text = mb.opf_text(spec)
    assert text.startswith('<package xmlns="http://www.idpf.org/2007/opf" xmlns:dc="http://purl.org/dc/elements/1.1/"')
    for line in (
        "<dc:title>Dune &amp; Co</dc:title>",
        '<dc:creator opf:role="aut">F. Herbert</dc:creator>',
        '<dc:creator opf:role="nrt">S. Brick</dc:creator>',
        "<dc:publisher>Chilton</dc:publisher>", "<dc:date>1965</dc:date>", "<dc:language>eng</dc:language>",
        '<meta name="calibre:series" content="Dune Chronicles" />',
        '<meta name="calibre:series_index" content="1" />',
    ):
        assert line in text
    import xml.dom.minidom
    xml.dom.minidom.parseString(text)  # well-formed


@requires_ffmpeg
def test_the_sidecar_files_are_written_beside_the_audiobook_only_when_asked(tmp_path):
    from PIL import Image
    import io

    buf = io.BytesIO()
    Image.new("RGB", (32, 32), (10, 120, 200)).save(buf, format="PNG")
    src = _tone_mp3(tmp_path / "01.mp3", 1.0)
    for flag, folder in ((False, "plain"), (True, "with")):
        spec = mb.BookSpec(
            [mb.BookChapter(src, "One")], tmp_path / folder / "Book.m4b", "Book", author="A",
            cover=buf.getvalue(), cover_mime="image/png", write_sidecar=flag,
        )
        result = mb.build_m4b(spec)
        assert result.status == STATUS_OK, result.message
    assert sorted(p.name for p in (tmp_path / "plain").iterdir()) == ["Book.m4b"]
    assert sorted(p.name for p in (tmp_path / "with").iterdir()) == ["Book.m4b", "cover.png", "metadata.opf"]
    assert "<dc:title>Book</dc:title>" in (tmp_path / "with" / "metadata.opf").read_text(encoding="utf-8")
