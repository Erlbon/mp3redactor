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
 2. The real length of each encoded piece is measured (its AAC frames
    counted with ffprobe: the stream copy keeps every frame, including the
    encoder's priming and padding, which the container's own duration
    leaves out, so using that would make the chapter marks drift by
    about 35-45 ms per chapter), and the chapter marks are laid out from
    those lengths (not from the MP3 tags, which can also be a little off).
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
import threading
import time
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
from redactor_common.core.subprocess_utils import decode_output, popen_tool, run_tool

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
AAC_FRAME_SAMPLES = 1024  # an AAC-LC frame
STALE_BUILD_SECONDS = 6 * 3600  # a build folder this old was left by a crash or a kill

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
    description: str = ""
    cover: bytes | None = None
    cover_mime: str = ""
    bitrate_kbps: int = DEFAULT_BITRATE_KBPS
    write_sidecar: bool = False  # metadata.opf + cover beside the audiobook (Audiobookshelf and similar)
    # What takes a file this build would replace (an older .m4b, metadata.opf, cover.jpg): the Recycle Bin or a
    # --trash-dir. None: such a file is overwritten in place, as before.
    trash: Callable[[str], None] | None = None


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


def usable_cover(data: bytes | None) -> tuple[bytes | None, str]:
    """(data, MIME type) when the picture is a JPEG or PNG -- the two kinds an M4B can carry -- judged by its bytes,
    not by what a tag claims; (None, "") for anything else (a GIF or WebP would make the join fail)."""
    mime = sniff_mime(data)
    return (data, mime) if data and mime else (None, "")


def defaults_for(files: list) -> BookDefaults:
    """What to start a book's tags and cover from: the first file's album / album artist / year, its
    folder name when it has no album, and its cover (embedded picture, else an image beside it)."""
    first = files[0]
    cover, mime = None, ""
    found = read_cover(first.path)
    if found is not None:
        cover, mime = usable_cover(found[0])
    if cover is None:  # no embedded picture, or one an M4B cannot carry: try an image beside the files
        folder_image = find_folder_image(first.path)
        if folder_image is not None:
            try:
                cover, mime = usable_cover(Path(folder_image).read_bytes())
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
    add("description", spec.description)
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
    drops the picture), so they go in afterwards with mutagen -- into a COPY that replaces the audiobook only
    when it reads back with the tags, so a failed save can never leave a damaged file. Returns "" or a problem
    message."""
    wanted = {
        "series": spec.series, "series-part": spec.series_index if spec.series else "",
        "publisher": spec.publisher, "language": spec.language,
    }
    wanted = {name: value.strip() for name, value in wanted.items() if value and value.strip()}
    if not wanted:
        return ""
    copy = path.with_name(f".{path.stem}.tagging.m4b")
    try:
        from mutagen.mp4 import MP4, MP4FreeForm

        shutil.copyfile(path, copy)
        audio = MP4(str(copy))
        for name, value in wanted.items():
            audio[f"----:com.apple.iTunes:{name}"] = [MP4FreeForm(value.encode("utf-8"))]
        audio.save()
        again = MP4(str(copy))  # the copy must read back, tags and all
        if not all(f"----:com.apple.iTunes:{name}" in again for name in wanted):
            raise ValueError("the tags did not read back")
        os.replace(copy, path)
    except Exception as exc:  # noqa: BLE001 -- the audiobook is fine without them
        try:
            copy.unlink()
        except OSError:
            pass
        return f"could not add the series / publisher / language tags: {exc}"
    return ""


def _trash_existing(path: Path, spec: BookSpec) -> str:
    """Sends an existing file that is about to be replaced to the Recycle Bin / --trash-dir (when the build has
    one). "" or what went wrong; the new file is then NOT written over it."""
    if spec.trash is None or not path.exists():
        return ""
    try:
        spec.trash(str(path))
    except Exception as exc:  # noqa: BLE001 -- TrashError or OSError: keep the old file
        return f"{path.name} was kept, the old one could not be sent to the Recycle Bin ({exc})"
    return ""


def write_sidecars(spec: BookSpec) -> list[str]:
    """Writes metadata.opf and cover.jpg/.png beside the audiobook. An earlier one goes to the Recycle Bin
    (or --trash-dir) first when the build has a trash; otherwise it is replaced. Returns problems as messages;
    never raises."""
    folder = spec.output.parent
    notes: list[str] = []
    try:
        problem = _trash_existing(folder / OPF_NAME, spec)
        if problem:
            notes.append(problem)
        else:
            text = '<?xml version="1.0" encoding="utf-8"?>\n' + opf_text(spec) + "\n"
            (folder / OPF_NAME).write_text(text, encoding="utf-8", newline="\n")
    except OSError as exc:
        notes.append(f"could not write {OPF_NAME}: {exc}")
    if spec.cover:
        try:
            cover_path = folder / ("cover" + extension_for(spec.cover_mime))
            problem = _trash_existing(cover_path, spec)
            if problem:
                notes.append(problem)
            else:
                cover_path.write_bytes(spec.cover)
        except OSError as exc:
            notes.append(f"could not write the cover image: {exc}")
    return notes


# --- ffmetadata ------------------------------------------------------------------


def escape_ffmetadata(text: str, keep_newlines: bool = False) -> str:
    """ffmpeg's ffmetadata escaping: a backslash before = ; # and the backslash itself. Line breaks
    become spaces (a title is one line), or with `keep_newlines` (a description) stay as breaks,
    written as a backslash followed by the newline."""
    text = str(text)
    if keep_newlines:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
    elif any(c in text for c in "\r\n"):
        text = " ".join(text.splitlines())
    out = []
    for char in text:
        if char in ("=", ";", "#", _BACKSLASH):
            out.append(_BACKSLASH)
        elif char == "\n":
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
        "description": spec.description,
    }
    for key, value in tags.items():
        if value and value.strip():
            lines.append(f"{key}={escape_ffmetadata(value.strip(), keep_newlines=key == 'description')}")
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


class _Running:
    """The ffmpeg processes of one build. A Cancel stops them at once instead of waiting for a long encode or
    join to finish; the workers poll `stopped`, the calling thread may also pass its own should_cancel."""

    def __init__(self) -> None:
        self.stopped = threading.Event()
        self._lock = threading.Lock()
        self._processes: set[subprocess.Popen] = set()

    def stop(self) -> None:
        self.stopped.set()
        with self._lock:
            processes = list(self._processes)
        for process in processes:
            try:
                process.kill()
            except OSError:
                pass

    def run(self, command: list, timeout: float, cancelled: Callable[[], bool] | None = None) -> subprocess.CompletedProcess:
        """run_tool() that can be stopped: the same flags (no window, stdin closed, UTF-8 output), polled every
        quarter second. Raises subprocess.TimeoutExpired like run_tool; a stopped process returns a non-zero code."""
        process = popen_tool([str(a) for a in command], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        with self._lock:
            self._processes.add(process)
        deadline = time.monotonic() + timeout
        try:
            while True:
                try:
                    out, err = process.communicate(timeout=0.25)
                    break
                except subprocess.TimeoutExpired:
                    if self.stopped.is_set() or (cancelled is not None and cancelled()):
                        self.stop()
                        out, err = process.communicate()
                        break
                    if time.monotonic() > deadline:
                        process.kill()
                        process.communicate()
                        raise subprocess.TimeoutExpired(command, timeout) from None
        finally:
            with self._lock:
                self._processes.discard(process)
        return subprocess.CompletedProcess(command, process.returncode, decode_output(out), decode_output(err))


def _encode_piece(
    ffmpeg: Path, source: Path, dest: Path, bitrate_kbps: int, sample_rate: int, channels: int,
    running: _Running | None = None,
) -> str:
    """Encodes one MP3 to AAC; returns "" on success, else the error message."""
    run = running.run if running is not None else (lambda command, timeout: run_tool(command, timeout=timeout))
    try:
        result = run(
            [
                str(ffmpeg), "-y", "-i", str(source), "-map", "0:a:0", "-vn", "-map_metadata", "-1",
                "-c:a", "aac", "-b:a", f"{bitrate_kbps}k", "-ar", str(sample_rate), "-ac", str(channels),
                str(dest),
            ],
            ENCODE_TIMEOUT_SECONDS,
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


def probe_audio_length(path: Path, ffprobe: Path, sample_rate: int) -> float | None:
    """The length in seconds of an encoded AAC piece as the stream copy will lay it out: its frames counted
    (ffprobe reads the packets, it does not decode) times 1024 samples. This is longer than the container's
    own duration, which leaves out the encoder's priming and padding. None if it can't be told."""
    try:
        result = run_tool(
            [str(ffprobe), "-v", "quiet", "-select_streams", "a:0", "-count_packets",
             "-show_entries", "stream=nb_read_packets", "-of", "default=nw=1:nk=1", str(path)],
            timeout=PROBE_TIMEOUT_SECONDS,
        )
        frames = int(result.stdout.strip())
    except (subprocess.TimeoutExpired, OSError, ValueError):
        return None
    return frames * AAC_FRAME_SAMPLES / sample_rate if frames > 0 and sample_rate > 0 else None


def _sweep_stale_builds(folder: Path) -> None:
    """Removes .m4b-build-* folders (and *.building.m4b / *.replaced.m4b / *.tagging.m4b leftovers) an earlier,
    crashed or killed run left behind -- only old ones, so a build running at the same time is not touched."""
    try:
        entries = list(folder.iterdir())
    except OSError:
        return
    now = time.time()
    for entry in entries:
        name = entry.name
        if not (name.startswith(".m4b-build-") or (name.startswith(".") and name.endswith((".building.m4b", ".tagging.m4b")))):
            continue
        try:
            if now - entry.stat().st_mtime < STALE_BUILD_SECONDS:
                continue
            shutil.rmtree(entry, ignore_errors=True) if entry.is_dir() else entry.unlink()
        except OSError:
            pass


def _make_work_folder(output: Path) -> tuple[Path | None, list[Path], str]:
    """(the temp work folder, the folders this call had to create for the output, "" or an error message)."""
    created: list[Path] = []
    folder = output.parent
    missing = []
    while not folder.exists() and folder != folder.parent:
        missing.append(folder)
        folder = folder.parent
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        created = missing
        _sweep_stale_builds(output.parent)
        return Path(tempfile.mkdtemp(prefix=".m4b-build-", dir=str(output.parent))), created, ""
    except OSError as exc:
        _remove_empty(missing)
        return None, [], f"could not prepare {output.parent}: {exc}"


def _remove_empty(folders: list[Path]) -> None:
    """Removes the folders a failed build created (deepest first), but only the ones that are empty."""
    for folder in sorted(folders, key=lambda f: len(f.parts), reverse=True):
        try:
            folder.rmdir()
        except OSError:
            pass


def _install(final_tmp: Path, output: Path, spec: BookSpec) -> str:
    """Puts the finished file at `output`. An older audiobook there is first renamed aside, and goes to the
    Recycle Bin / --trash-dir only after the new one is in place; if the new one cannot be, the old one is
    put back. Returns "" or a message (what went wrong)."""
    if spec.trash is None or not output.exists():
        retry_on_lock(lambda: os.replace(final_tmp, output))
        return ""
    aside = output.with_name(f".{output.stem}.replaced.m4b")
    retry_on_lock(lambda: os.replace(output, aside))
    try:
        retry_on_lock(lambda: os.replace(final_tmp, output))
    except OSError:
        os.replace(aside, output)  # put the old one back
        raise
    try:
        spec.trash(str(aside))
    except Exception as exc:  # noqa: BLE001 -- TrashError or OSError
        kept = output.with_name(f"{output.stem} (previous).m4b")
        n = 2
        while kept.exists():
            kept = output.with_name(f"{output.stem} (previous {n}).m4b")
            n += 1
        try:
            os.replace(aside, kept)
        except OSError:
            kept = aside
        return f"the old audiobook could not be sent to the Recycle Bin ({exc}); it is kept as {kept.name}"
    return ""


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

    work, created, problem = _make_work_folder(spec.output)
    if work is None:
        return BuildResult(STATUS_ERROR, problem)
    final_tmp = spec.output.with_name(f".{spec.output.stem}.building.m4b")
    running = _Running()
    succeeded = False
    notes: list[str] = []
    try:
        if spec.cover and sniff_mime(spec.cover) is None:
            notes.append("the cover image is not a JPEG or PNG, so it was left out of the audiobook")
            spec.cover, spec.cover_mime = None, ""
        elif spec.cover:
            spec.cover_mime = sniff_mime(spec.cover) or spec.cover_mime
        pieces = [work / f"{index:05d}.m4a" for index in range(len(spec.chapters))]
        errors: list[str] = []
        done = 0
        report(0, total)
        with ThreadPoolExecutor(max_workers=min(MAX_PARALLEL_ENCODES, len(spec.chapters))) as pool:
            pending = {
                pool.submit(
                    _encode_piece, ffmpeg, chapter.path, piece, spec.bitrate_kbps, sample_rate, channel_count, running
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
                    running.stop()  # the running encodes are killed, the queued ones never start
                    for future in pending:
                        future.cancel()
                    break
        if cancelled():
            return BuildResult(STATUS_ERROR, "Cancelled.", cancelled=True)
        if errors:
            return BuildResult(STATUS_ERROR, errors[0] + (f" (and {len(errors) - 1} more)" if len(errors) > 1 else ""))

        durations: list[float] = []
        for chapter, piece in zip(spec.chapters, pieces):
            seconds = probe_audio_length(piece, ffprobe, sample_rate)
            if seconds is None:
                seconds = probe_duration(piece, ffprobe)  # an ffprobe that cannot count packets: the container's value
            if seconds is None or seconds <= 0:
                return BuildResult(STATUS_ERROR, f"{chapter.path.name}: could not measure the encoded length")
            durations.append(seconds)

        list_file = work / "pieces.txt"
        list_file.write_text("".join(f"file '{piece.name}'\n" for piece in pieces), encoding="utf-8")
        meta_file = work / "book.ffmetadata"
        # LF line ends on every platform: an escaped newline in a description must stay one "\n".
        meta_file.write_text(ffmetadata_text(spec, durations), encoding="utf-8", newline="\n")
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
            result = running.run(command, JOIN_TIMEOUT_SECONDS, cancelled)
        except subprocess.TimeoutExpired:
            return BuildResult(STATUS_ERROR, f"ffmpeg timed out joining the chapters after {JOIN_TIMEOUT_SECONDS}s")
        except OSError as exc:
            return BuildResult(STATUS_ERROR, f"failed to launch ffmpeg: {exc}")
        if cancelled():
            return BuildResult(STATUS_ERROR, "Cancelled.", cancelled=True)
        if result.returncode != 0:
            return BuildResult(STATUS_ERROR, _last_line(result.stderr, f"ffmpeg exited with code {result.returncode}"))
        if problem := add_series_tags(final_tmp, spec):
            notes.append(problem)
        try:
            kept_note = _install(final_tmp, spec.output, spec)
        except OSError as exc:
            return BuildResult(STATUS_ERROR, f"could not move the finished file into place: {exc}")
        if kept_note:
            notes.append(kept_note)
        succeeded = True
        report(total, total)
        return BuildResult(
            STATUS_OK, output=spec.output, chapter_count=len(spec.chapters), duration_seconds=sum(durations),
            notes=notes + (write_sidecars(spec) if spec.write_sidecar else []),
        )
    finally:
        running.stop()
        shutil.rmtree(work, ignore_errors=True)
        try:
            final_tmp.unlink()
        except OSError:
            pass
        if not succeeded:
            _remove_empty(created)  # a failed or cancelled build leaves no empty author/series folders behind
