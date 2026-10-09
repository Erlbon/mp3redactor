"""
core/m4b_builder.py

Builds one chaptered .m4b audiobook from a set of MP3 files (one
chapter per file) with ffmpeg -- backs Tools > Create M4B Audiobook...
Qt-free; the dialog and the progress bar live in gui/.

How it works (our own implementation, nothing copied from other tools):
 1. Every MP3 is encoded to AAC (.m4a) in a temp folder, in parallel
    (ffmpeg processes on a small thread pool), all to the same sample
    rate and channel count so the pieces can be joined. A re-encode is
    unavoidable: M4B players expect AAC, so the result is not bit-exact.
 2. The real length of each encoded piece is measured with ffprobe, and
    the chapter marks are laid out from those lengths (not from the MP3
    tags, which can be a little off).
 3. The pieces are joined with ffmpeg's concat demuxer (stream copy)
    together with an ffmetadata file (book tags + chapters) and the cover
    image, into a hidden temp .m4b that replaces the destination only on
    success, so a failed or cancelled run never leaves a partial file.

Same external-tool conventions as core/mp3_converter.py: find_tool()
(bundled, PATH or a user override), run_tool() (no console window,
stdin closed, timeout).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from xml.sax.saxutils import escape as xml_escape
from xml.sax.saxutils import quoteattr

from core.cover_art import extension_for, find_folder_image, read_cover, sniff_mime
from core.ffmpeg_probe import FFPROBE_EXE_NAME, probe_format
from core.lock_retry import retry_on_lock
from core.mp3_file import STATUS_ERROR, STATUS_OK, STATUS_TOOL_MISSING
from core.tool_locator import find_tool
from redactor_common.core.rename_pattern import sanitize_filename
from redactor_common.core.subprocess_utils import run_tool

FFMPEG_EXE_NAME = "ffmpeg.exe"

# Speech needs far less than music: 64 kbps AAC is the usual audiobook choice.
BITRATE_CHOICES_KBPS: list[int] = [32, 48, 64, 96, 128]
DEFAULT_BITRATE_KBPS = 64
DEFAULT_GENRE = "Audiobook"

ENCODE_TIMEOUT_SECONDS = 3600  # one chapter file
JOIN_TIMEOUT_SECONDS = 3600
PROBE_TIMEOUT_SECONDS = 60
MAX_PARALLEL_ENCODES = 4
FALLBACK_SAMPLE_RATE = 44100
MAX_CHANNELS = 2

_BACKSLASH = chr(92)
_NATURAL_SPLIT = re.compile(r"(\d+)")


@dataclass
class BookChapter:
    path: Path
    title: str


@dataclass
class BookSpec:
    chapters: list[BookChapter]
    output: Path
    title: str
    author: str = ""
    narrator: str = ""
    year: str = ""
    genre: str = DEFAULT_GENRE
    series: str = ""
    series_index: str = ""
    publisher: str = ""
    language: str = ""
    cover: bytes | None = None
    cover_mime: str = ""
    bitrate_kbps: int = DEFAULT_BITRATE_KBPS
    write_sidecar: bool = False  # metadata.opf + cover beside the audiobook (Audiobookshelf and similar)


@dataclass
class BuildResult:
    status: str  # STATUS_OK / STATUS_ERROR / STATUS_TOOL_MISSING
    message: str = ""
    output: Path | None = None
    chapter_count: int = 0
    duration_seconds: float = 0.0
    cancelled: bool = False
    notes: list[str] = field(default_factory=list)  # things that went wrong after the audiobook was made


# --- ordering and chapter titles -------------------------------------------------


def _int_or(text: str, default: int) -> int:
    match = re.match(r"\s*(\d+)", text or "")
    return int(match.group(1)) if match else default


def natural_key(name: str) -> list:
    """"Chapter 2" before "Chapter 10": digit runs compare as numbers."""
    return [int(part) if part.isdigit() else part.lower() for part in _NATURAL_SPLIT.split(name)]


def order_files(files: list) -> list:
    """The reading order: disc number, then track number, then the file name
    (natural order). `files` are MP3File-like (.path, .discnumber, .track);
    a missing disc or track sorts by name alone."""
    return sorted(
        files,
        key=lambda f: (_int_or(f.discnumber, 1), _int_or(f.track, 10**9), natural_key(Path(f.path).name)),
    )


def chapter_title(file) -> str:
    """The file's own title tag, else its file name without the extension."""
    return (file.title or "").strip() or Path(file.path).stem


# --- one book per folder -----------------------------------------------------------


def group_by_folder(files: list) -> list[list]:
    """The files split into books by their folder (a folder of chapter files is one book), books in
    folder-name order, each book's files in reading order."""
    groups: dict[str, list] = {}
    for f in files:
        groups.setdefault(os.path.normcase(str(Path(f.path).parent)), []).append(f)
    return [order_files(group) for _key, group in sorted(groups.items(), key=lambda kv: natural_key(kv[0]))]


@dataclass
class BookDefaults:
    title: str
    author: str
    year: str
    cover: bytes | None
    cover_mime: str
    publisher: str = ""
    language: str = ""


def defaults_for(files: list) -> BookDefaults:
    """What to start a book's tags and cover from: the first file's album / album artist / year, its
    folder name when it has no album, and its cover (embedded picture, else an image beside it)."""
    first = files[0]
    cover, mime = None, ""
    found = read_cover(first.path)
    if found is not None:
        cover, mime = found
    else:
        folder_image = find_folder_image(first.path)
        if folder_image is not None:
            try:
                cover = Path(folder_image).read_bytes()
                mime = sniff_mime(cover) or "image/jpeg"
            except OSError:
                cover, mime = None, ""
    return BookDefaults(
        title=(first.album or "").strip() or Path(first.path).parent.name,
        author=(first.albumartist or first.artist or "").strip(),
        year=(first.year or "").strip(),
        cover=cover, cover_mime=mime,
        publisher=(getattr(first, "publisher", "") or "").strip(),
        language=(getattr(first, "language", "") or "").strip(),
    )


def library_output(root: str | Path, author: str, title: str, series: str = "") -> Path:
    """root/Author/[Series/]Title/Title.m4b -- Audiobookshelf's `{Author}/{Series}/{Book}` or
    `{Author}/{Book}`, with a folder of its own per book."""
    book = sanitize_filename(title) or "Audiobook"
    folder = Path(root)
    for part in (author, series):
        if sanitize_filename(part):
            folder = folder / sanitize_filename(part)
    return folder / book / f"{book}.m4b"


# --- sidecar files (metadata.opf + cover) --------------------------------------------

_OPF_NS = "http://www.idpf.org/2007/opf"
_DC_NS = "http://purl.org/dc/elements/1.1/"
OPF_NAME = "metadata.opf"


def opf_text(spec: BookSpec) -> str:
    """The OPF Audiobookshelf documents (and Calibre writes): title, author (role aut), narrator
    (role nrt), publisher, date, language, genre, and the series as calibre:series / series_index.
    All namespaces on the package, as in Audiobookshelf's own example."""
    lines = [
        f'<package xmlns="{_OPF_NS}" xmlns:dc="{_DC_NS}" xmlns:opf="{_OPF_NS}" version="3.0">',
        "  <metadata>",
    ]

    def add(tag: str, text: str, role: str = "") -> None:
        if text and text.strip():
            attribute = f' opf:role="{role}"' if role else ""
            lines.append(f"    <dc:{tag}{attribute}>{xml_escape(text.strip())}</dc:{tag}>")

    add("title", spec.title)
    add("creator", spec.author, "aut")
    add("creator", spec.narrator, "nrt")
    add("publisher", spec.publisher)
    add("date", spec.year)
    add("language", spec.language)
    add("subject", spec.genre)
    if spec.series.strip():
        lines.append(f'    <meta name="calibre:series" content={quoteattr(spec.series.strip())} />')
        if spec.series_index.strip():
            lines.append(f'    <meta name="calibre:series_index" content={quoteattr(spec.series_index.strip())} />')
    lines += ["  </metadata>", "</package>"]
    return "\n".join(lines)


def add_series_tags(path: Path, spec: BookSpec) -> str:
    """Adds the tags Audiobookshelf reads by name (series, series-part, publisher, language) as
    freeform atoms. ffmpeg's mp4 writer can only keep them without the cover (its custom-tag mode
    drops the picture), so they go in afterwards with mutagen. Returns "" or a problem message."""
    wanted = {
        "series": spec.series, "series-part": spec.series_index if spec.series else "",
        "publisher": spec.publisher, "language": spec.language,
    }
    wanted = {name: value.strip() for name, value in wanted.items() if value and value.strip()}
    if not wanted:
        return ""
    try:
        from mutagen.mp4 import MP4, MP4FreeForm

        audio = MP4(str(path))
        for name, value in wanted.items():
            audio[f"----:com.apple.iTunes:{name}"] = [MP4FreeForm(value.encode("utf-8"))]
        audio.save()
    except Exception as exc:  # noqa: BLE001 -- the audiobook is fine without them
        return f"could not add the series / publisher / language tags: {exc}"
    return ""


def write_sidecars(spec: BookSpec) -> list[str]:
    """Writes metadata.opf and cover.jpg/.png beside the audiobook (replacing earlier ones). Returns
    problems as messages; never raises."""
    folder = spec.output.parent
    notes: list[str] = []
    try:
        text = '<?xml version="1.0" encoding="utf-8"?>\n' + opf_text(spec) + "\n"
        (folder / OPF_NAME).write_text(text, encoding="utf-8")
    except OSError as exc:
        notes.append(f"could not write {OPF_NAME}: {exc}")
    if spec.cover:
        try:
            (folder / ("cover" + extension_for(spec.cover_mime))).write_bytes(spec.cover)
        except OSError as exc:
            notes.append(f"could not write the cover image: {exc}")
    return notes


# --- ffmetadata ------------------------------------------------------------------


def escape_ffmetadata(text: str) -> str:
    """ffmpeg's ffmetadata escaping: a backslash before = ; # and the backslash itself;
    line breaks become spaces (a title is one line)."""
    text = " ".join(str(text).splitlines()) if any(c in str(text) for c in "\r\n") else str(text)
    out = []
    for char in text:
        if char in ("=", ";", "#", _BACKSLASH):
            out.append(_BACKSLASH)
        out.append(char)
    return "".join(out)


def ffmetadata_text(spec: BookSpec, durations_seconds: list[float]) -> str:
    """The ffmetadata file: the book's tags, then one [CHAPTER] per chapter laid end to end."""
    lines = [";FFMETADATA1"]
    tags = {
        "title": spec.title,
        "album": spec.title,
        "artist": spec.author,
        "album_artist": spec.author,
        "composer": spec.narrator,
        "genre": spec.genre,
        "date": spec.year,
    }
    for key, value in tags.items():
        if value and value.strip():
            lines.append(f"{key}={escape_ffmetadata(value.strip())}")
    start_ms = 0
    for chapter, seconds in zip(spec.chapters, durations_seconds):
        end_ms = start_ms + max(1, round(seconds * 1000))
        lines += [
            "[CHAPTER]", "TIMEBASE=1/1000", f"START={start_ms}", f"END={end_ms}",
            f"title={escape_ffmetadata(chapter.title)}",
        ]
        start_ms = end_ms
    return "\n".join(lines) + "\n"


# --- ffmpeg steps ----------------------------------------------------------------


def _last_line(stderr: str, fallback: str) -> str:
    lines = (stderr or "").strip().splitlines()
    return lines[-1] if lines else fallback


def _encode_piece(
    ffmpeg: Path, source: Path, dest: Path, bitrate_kbps: int, sample_rate: int, channels: int
) -> str:
    """Encodes one MP3 to AAC; returns "" on success, else the error message."""
    try:
        result = run_tool(
            [
                str(ffmpeg), "-y", "-i", str(source), "-map", "0:a:0", "-vn", "-map_metadata", "-1",
                "-c:a", "aac", "-b:a", f"{bitrate_kbps}k", "-ar", str(sample_rate), "-ac", str(channels),
                str(dest),
            ],
            timeout=ENCODE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return f"{source.name}: ffmpeg timed out after {ENCODE_TIMEOUT_SECONDS}s"
    except OSError as exc:
        return f"{source.name}: failed to launch ffmpeg: {exc}"
    if result.returncode != 0:
        return f"{source.name}: {_last_line(result.stderr, f'ffmpeg exited with code {result.returncode}')}"
    return ""


def probe_duration(path: Path, ffprobe: Path) -> float | None:
    """The length in seconds of an audio file, or None if it can't be told."""
    try:
        result = run_tool(
            [str(ffprobe), "-v", "quiet", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
            timeout=PROBE_TIMEOUT_SECONDS,
        )
        return float(result.stdout.strip())
    except (subprocess.TimeoutExpired, OSError, ValueError):
        return None


def build_m4b(
    spec: BookSpec,
    ffmpeg_path: str | None = None,
    ffprobe_path: str | None = None,
    progress: Callable[[int, int], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> BuildResult:
    """Builds spec.output. `progress(done, total)` counts the encoded chapters plus the final
    join (total = chapters + 1) and is only ever called from the calling thread, so it may touch
    a progress dialog. `should_cancel()` is checked between steps; a cancel leaves nothing
    behind. Never raises for tool trouble: the result carries the status and message."""
    if not spec.chapters:
        return BuildResult(STATUS_ERROR, "There are no files to put in the audiobook.")
    ffmpeg = find_tool(FFMPEG_EXE_NAME, override=ffmpeg_path or None)
    ffprobe = find_tool(FFPROBE_EXE_NAME, override=ffprobe_path or None)
    if ffmpeg is None:
        return BuildResult(STATUS_TOOL_MISSING, "ffmpeg not found (not bundled and not on PATH)")
    if ffprobe is None:
        return BuildResult(STATUS_TOOL_MISSING, "ffprobe not found (not bundled and not on PATH)")

    total = len(spec.chapters) + 1
    cancelled = should_cancel or (lambda: False)
    report = progress or (lambda done, total: None)

    # One format for every piece, taken from the first file (the join needs them identical).
    _enc, rate, channels, status, _msg = probe_format(spec.chapters[0].path, tool_path=ffprobe)
    sample_rate = rate if status == STATUS_OK and rate else FALLBACK_SAMPLE_RATE
    channel_count = min(channels, MAX_CHANNELS) if status == STATUS_OK and channels else MAX_CHANNELS

    spec.output.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=".m4b-build-", dir=str(spec.output.parent)))
    final_tmp = spec.output.with_name(f".{spec.output.stem}.building.m4b")
    try:
        pieces = [work / f"{index:05d}.m4a" for index in range(len(spec.chapters))]
        errors: list[str] = []
        done = 0
        report(0, total)
        with ThreadPoolExecutor(max_workers=min(MAX_PARALLEL_ENCODES, len(spec.chapters))) as pool:
            pending = {
                pool.submit(
                    _encode_piece, ffmpeg, chapter.path, piece, spec.bitrate_kbps, sample_rate, channel_count
                ): chapter
                for chapter, piece in zip(spec.chapters, pieces)
            }
            while pending:
                finished, still = wait(pending, timeout=0.25, return_when=FIRST_COMPLETED)
                for future in finished:
                    done += 1
                    error = future.result()
                    if error:
                        errors.append(error)
                pending = {f: pending[f] for f in still}
                report(done, total)
                if errors or cancelled():
                    for future in pending:
                        future.cancel()  # not yet started; the running ones finish their file
                    break
        if cancelled() and not errors:
            return BuildResult(STATUS_ERROR, "Cancelled.", cancelled=True)
        if errors:
            return BuildResult(STATUS_ERROR, errors[0] + (f" (and {len(errors) - 1} more)" if len(errors) > 1 else ""))

        durations: list[float] = []
        for chapter, piece in zip(spec.chapters, pieces):
            seconds = probe_duration(piece, ffprobe)
            if seconds is None or seconds <= 0:
                return BuildResult(STATUS_ERROR, f"{chapter.path.name}: could not measure the encoded length")
            durations.append(seconds)

        list_file = work / "pieces.txt"
        list_file.write_text("".join(f"file '{piece.name}'\n" for piece in pieces), encoding="utf-8")
        meta_file = work / "book.ffmetadata"
        meta_file.write_text(ffmetadata_text(spec, durations), encoding="utf-8")
        command = [
            str(ffmpeg), "-y", "-f", "concat", "-safe", "0", "-i", str(list_file), "-i", str(meta_file),
        ]
        if spec.cover:
            cover_file = work / ("cover" + extension_for(spec.cover_mime))
            cover_file.write_bytes(spec.cover)
            command += ["-i", str(cover_file)]
        command += ["-map", "0:a", "-map_metadata", "1", "-map_chapters", "1"]
        if spec.cover:
            command += ["-map", "2:v", "-c:v", "copy", "-disposition:v:0", "attached_pic"]
        command += ["-c:a", "copy", "-f", "ipod", str(final_tmp)]
        try:
            result = run_tool(command, timeout=JOIN_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            return BuildResult(STATUS_ERROR, f"ffmpeg timed out joining the chapters after {JOIN_TIMEOUT_SECONDS}s")
        except OSError as exc:
            return BuildResult(STATUS_ERROR, f"failed to launch ffmpeg: {exc}")
        if result.returncode != 0:
            return BuildResult(STATUS_ERROR, _last_line(result.stderr, f"ffmpeg exited with code {result.returncode}"))
        if cancelled():
            return BuildResult(STATUS_ERROR, "Cancelled.", cancelled=True)
        notes = [problem] if (problem := add_series_tags(final_tmp, spec)) else []
        try:
            retry_on_lock(lambda: os.replace(final_tmp, spec.output))
        except OSError as exc:
            return BuildResult(STATUS_ERROR, f"could not move the finished file into place: {exc}")
        report(total, total)
        return BuildResult(
            STATUS_OK, output=spec.output, chapter_count=len(spec.chapters), duration_seconds=sum(durations),
            notes=notes + (write_sidecars(spec) if spec.write_sidecar else []),
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)
        try:
            final_tmp.unlink()
        except OSError:
            pass
