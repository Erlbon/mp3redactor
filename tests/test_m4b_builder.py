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


@requires_ffmpeg
def test_a_multi_line_description_reaches_the_tags_and_the_opf(tmp_path):
    chapters = [mb.BookChapter(_tone_mp3(tmp_path / "01.mp3", 1.0), "One")]
    out = tmp_path / "Book.m4b"
    text = "First paragraph; with = and # marks.\n\nSecond paragraph."
    result = mb.build_m4b(mb.BookSpec(chapters, out, "Book", description=text, write_sidecar=True))
    assert result.status == STATUS_OK, result.message
    tags = {k.lower(): v for k, v in _probe(out)["format"]["tags"].items()}
    assert tags["description"] == text
    assert "<dc:description>First paragraph; with = and # marks." in (tmp_path / "metadata.opf").read_text(encoding="utf-8")


# --- second review --------------------------------------------------------------------------------------------


@requires_ffmpeg
def test_the_chapter_marks_follow_the_real_length_of_every_piece(tmp_path):
    """The stream copy keeps every AAC frame, so each piece is about 35 ms longer than the container's own
    duration says; marks laid out from that duration drifted by that much per chapter."""
    chapters = [mb.BookChapter(_tone_mp3(tmp_path / f"{i}.mp3", 3.3, 300 + 100 * i), f"C{i}") for i in range(6)]
    out = tmp_path / "Book.m4b"
    result = mb.build_m4b(mb.BookSpec(chapters, out, "Book"))
    assert result.status == STATUS_OK, result.message
    info = _probe(out)
    assert abs(float(info["chapters"][-1]["end_time"]) - float(info["format"]["duration"])) < 0.01
    assert abs(result.duration_seconds - float(info["format"]["duration"])) < 0.01


def test_a_cancel_stops_the_running_ffmpeg_at_once():
    import sys
    import time

    running = mb._Running()
    started = time.monotonic()
    result = running.run([sys.executable, "-c", "import time; time.sleep(60)"], 120, cancelled=lambda: True)
    assert time.monotonic() - started < 15 and result.returncode != 0


def test_a_timeout_kills_the_process_and_raises():
    import subprocess
    import sys

    running = mb._Running()
    with pytest.raises(subprocess.TimeoutExpired):
        running.run([sys.executable, "-c", "import time; time.sleep(60)"], 0.5)


@requires_ffmpeg
def test_a_failed_build_leaves_no_empty_library_folders(tmp_path):
    bad = tmp_path / "bad.mp3"
    bad.write_bytes(b"this is not audio")
    out = tmp_path / "lib" / "Author" / "Series" / "Book" / "Book.m4b"
    result = mb.build_m4b(mb.BookSpec([mb.BookChapter(bad, "Bad")], out, "Book"))
    assert result.status == STATUS_ERROR
    assert not (tmp_path / "lib").exists()


def test_an_output_folder_that_cannot_be_made_is_an_error_not_a_crash(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_bytes(b"x")
    src = tmp_path / "a.mp3"
    src.write_bytes(b"x")
    result = mb.build_m4b(mb.BookSpec([mb.BookChapter(src, "A")], blocker / "sub" / "Book.m4b", "Book"))
    assert result.status in (STATUS_ERROR, STATUS_TOOL_MISSING)  # no ffmpeg at all is also an answer, never an exception


def test_old_build_folders_are_swept_but_a_fresh_one_is_left(tmp_path):
    import os
    import time

    old, fresh = tmp_path / ".m4b-build-old", tmp_path / ".m4b-build-new"
    for folder in (old, fresh):
        folder.mkdir()
        (folder / "00000.m4a").write_bytes(b"x")
    stale = time.time() - mb.STALE_BUILD_SECONDS - 60
    os.utime(old, (stale, stale))
    leftover = tmp_path / ".Book.building.m4b"
    leftover.write_bytes(b"x")
    os.utime(leftover, (stale, stale))
    mb._sweep_stale_builds(tmp_path)
    assert not old.exists() and not leftover.exists() and fresh.exists()


@requires_ffmpeg
def test_a_cover_an_m4b_cannot_carry_is_left_out_with_a_note(tmp_path):
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (32, 32), (1, 2, 3)).save(buf, format="GIF")
    chapters = [mb.BookChapter(_tone_mp3(tmp_path / "01.mp3", 1.0), "One")]
    out = tmp_path / "Book.m4b"
    result = mb.build_m4b(mb.BookSpec(chapters, out, "Book", cover=buf.getvalue(), cover_mime="image/gif"))
    assert result.status == STATUS_OK, result.message
    assert any("not a JPEG or PNG" in n for n in result.notes)
    assert not [s for s in _probe(out)["streams"] if s["codec_type"] == "video"]


def test_usable_cover_judges_the_bytes_not_the_claim():
    assert mb.usable_cover(b"\xff\xd8\xff\xe0rest") == (b"\xff\xd8\xff\xe0rest", "image/jpeg")
    assert mb.usable_cover(b"GIF89a....") == (None, "")
    assert mb.usable_cover(None) == (None, "")


def test_series_tags_never_leave_a_damaged_file_behind(tmp_path, monkeypatch):
    mutagen_mp4 = pytest.importorskip("mutagen.mp4")
    path = tmp_path / "Book.m4b"
    path.write_bytes(b"original bytes")  # not a real m4b: MP4() fails, as a save that dies half way would
    spec = mb.BookSpec([], tmp_path / "Book.m4b", "Book", series="S", series_index="1")
    message = mb.add_series_tags(path, spec)
    assert "could not add" in message and path.read_bytes() == b"original bytes"
    assert [p.name for p in tmp_path.iterdir()] == ["Book.m4b"]


@requires_ffmpeg
def test_replacing_an_audiobook_sends_the_old_one_to_the_trash_and_the_sidecars_too(tmp_path):
    trashed = []

    def trash(path):
        trashed.append(Path(path).name)
        Path(path).unlink()

    src = _tone_mp3(tmp_path / "01.mp3", 1.0)
    out = tmp_path / "lib" / "Book.m4b"
    first = mb.build_m4b(mb.BookSpec([mb.BookChapter(src, "One")], out, "Book", author="A", write_sidecar=True))
    assert first.status == STATUS_OK, first.message
    assert trashed == []  # nothing was replaced yet
    again = mb.build_m4b(mb.BookSpec([mb.BookChapter(src, "One")], out, "Book", author="A", write_sidecar=True, trash=trash))
    assert again.status == STATUS_OK, again.message
    assert sorted(trashed) == [".Book.replaced.m4b", "metadata.opf"]
    assert sorted(p.name for p in out.parent.iterdir()) == ["Book.m4b", "metadata.opf"]


@requires_ffmpeg
def test_when_the_old_audiobook_cannot_be_trashed_it_is_kept_beside_the_new_one(tmp_path):
    from redactor_common.core.trash import TrashError

    def refuse(path):
        raise TrashError("no bin")

    src = _tone_mp3(tmp_path / "01.mp3", 1.0)
    out = tmp_path / "Book.m4b"
    assert mb.build_m4b(mb.BookSpec([mb.BookChapter(src, "One")], out, "Book")).status == STATUS_OK
    result = mb.build_m4b(mb.BookSpec([mb.BookChapter(src, "One")], out, "Book", trash=refuse))
    assert result.status == STATUS_OK and any("kept as Book (previous).m4b" in n for n in result.notes)
    assert sorted(p.name for p in tmp_path.iterdir() if p.suffix == ".m4b") == ["Book (previous).m4b", "Book.m4b"]
