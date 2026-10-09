"""
mp3cli/cmd_m4b.py

  m4b PATH...   one chaptered .m4b audiobook per folder of MP3 files (one chapter per file, in disc / track /
                name order), the same builder as File > Create M4B Audiobook.

Where the audiobooks go: beside the MP3 files (default), all into one folder (--into), or into a library in
Audiobookshelf's layout Author/[Series/]Title/Title.m4b (--library). One book can be named exactly (--file).
Existing audiobooks are skipped unless --replace. --lookup fills in narrator, series, publisher, year,
description and cover from Audible (then Open Library) when the best result is a confident match.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from core.audiobook_lookup import (
    AUDIBLE_REGIONS, DEFAULT_REGION, AudiobookLookupError, BookMatch, download_cover, score, search,
)
from core.cover_art import sniff_mime
from core.m4b_builder import (
    BITRATE_CHOICES_KBPS, DEFAULT_GENRE, BookChapter, BookSpec, build_m4b, chapter_title, defaults_for,
    group_by_folder, library_output,
)
from core.mp3_file import STATUS_OK
from redactor_common.cli import EXIT_OK, EXIT_PARTIAL, CliError, Output, add_common_options
from redactor_common.core.rename_pattern import sanitize_filename

from mp3cli.files import add_path_arguments, collect, load, settings

DEFAULT_MIN_SCORE = 2.8  # of 4: the title (2), the author (1) and the running time (1)


def add_m4b_parser(sub) -> None:
    parser = sub.add_parser(
        "m4b", help="make an M4B audiobook from MP3 files",
        description="Make one chaptered .m4b per folder of MP3 files (a chapter per file). The MP3s are re-encoded to "
                    "AAC and are never touched. A book that already exists is skipped unless --replace.",
    )
    add_path_arguments(parser)
    where = parser.add_mutually_exclusive_group()
    where.add_argument("--into", metavar="FOLDER", help="put every audiobook in this folder, named <Title>.m4b")
    where.add_argument("--library", metavar="FOLDER", help="library folder: <FOLDER>/<Author>/[<Series>/]<Title>/<Title>.m4b")
    where.add_argument("--file", metavar="FILE", help="the exact .m4b to write (one book only)")
    parser.add_argument("--bitrate", type=int, choices=BITRATE_CHOICES_KBPS, metavar="KBPS",
                        help=f"AAC bitrate, one of {', '.join(map(str, BITRATE_CHOICES_KBPS))} (default: the one saved in the app, 64)")
    parser.add_argument("--sidecar", action="store_true", default=None,
                        help="also write metadata.opf and a cover image beside each audiobook (Audiobookshelf, Calibre)")
    parser.add_argument("--replace", action="store_true", help="replace an audiobook that already exists")
    for flag, label in (
        ("title", "the book's title"), ("author", "the author"), ("narrator", "the narrator"), ("series", "the series"),
        ("series-number", "the book's number in the series"), ("year", "the year"), ("publisher", "the publisher"),
        ("language", "the language code (eng, nor, deu)"), ("description", "the description"),
    ):
        parser.add_argument(f"--{flag}", metavar="TEXT", help=f"{label} (applies to every book; use it with one book)")
    parser.add_argument("--cover", metavar="IMAGE", help="a cover image (JPEG or PNG) instead of the files' own")
    parser.add_argument("--lookup", action="store_true", help="look the book up on Audible (then Open Library) and fill in what it finds")
    parser.add_argument("--region", choices=AUDIBLE_REGIONS, metavar="STORE",
                        help=f"Audible store for --lookup (default: the one saved in the app, {DEFAULT_REGION})")
    parser.add_argument("--min-score", type=float, default=DEFAULT_MIN_SCORE, metavar="N",
                        help=f"how good a --lookup match must be to be used, 0-4 (default {DEFAULT_MIN_SCORE})")
    parser.add_argument("-n", "--dry-run", action="store_true", help="show the audiobooks that would be made, make nothing")
    add_common_options(parser)
    parser.set_defaults(handler=run_m4b)


def _overrides(args: argparse.Namespace) -> dict[str, str]:
    given = {
        "title": args.title, "author": args.author, "narrator": args.narrator, "series": args.series,
        "series_index": args.series_number, "year": args.year, "publisher": args.publisher,
        "language": args.language, "description": args.description,
    }
    return {key: value.strip() for key, value in given.items() if value is not None}


def _read_cover(path: str) -> tuple[bytes, str]:
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        raise CliError(f"cannot read the cover image: {exc}") from exc
    mime = sniff_mime(data)
    if mime not in ("image/jpeg", "image/png"):
        raise CliError("the cover must be a JPEG or PNG image")
    return data, mime


def _output_for(args: argparse.Namespace, spec: BookSpec, source_folder: Path) -> Path:
    if args.file:
        path = Path(args.file)
        return path if path.suffix.lower() == ".m4b" else path.with_name(path.name + ".m4b")
    if args.library:
        return library_output(args.library, spec.author, spec.title, spec.series)
    folder = Path(args.into) if args.into else source_folder
    return folder / f"{sanitize_filename(spec.title) or 'Audiobook'}.m4b"


def _apply_match(spec: BookSpec, match: BookMatch, overrides: dict[str, str]) -> None:
    """What a looked-up book fills in; anything the user gave explicitly wins."""
    for attr, value in (
        ("title", match.title), ("author", match.author_text), ("narrator", match.narrator_text),
        ("series", match.series), ("series_index", match.series_index), ("publisher", match.publisher),
        ("year", match.year), ("language", match.language), ("description", match.description),
    ):
        if value and attr not in overrides:
            setattr(spec, attr, value)


def run_m4b(args: argparse.Namespace, out: Output) -> int:
    app = settings()
    files = [m for m in load(collect(args.paths, out, recurse=not args.no_recurse)) if not _unreadable(m, out)]
    if not files:
        raise CliError("none of the files could be read")
    books = group_by_folder(files)
    if args.file and len(books) > 1:
        raise CliError(f"--file names one audiobook, but the files make {len(books)} (one per folder)")
    overrides = _overrides(args)
    cover = _read_cover(args.cover) if args.cover else None
    bitrate = args.bitrate or app.m4b_bitrate_kbps
    sidecar = app.m4b_sidecar if args.sidecar is None else args.sidecar
    region = args.region or app.m4b_audible_region

    failed = 0
    for number, book in enumerate(books, start=1):
        defaults = defaults_for(book)
        spec = BookSpec(
            chapters=[BookChapter(Path(f.path), chapter_title(f)) for f in book], output=Path(), title=defaults.title,
            author=defaults.author, year=defaults.year, genre=DEFAULT_GENRE, publisher=defaults.publisher,
            language=defaults.language, cover=defaults.cover, cover_mime=defaults.cover_mime, bitrate_kbps=bitrate,
            write_sidecar=sidecar,
        )
        for attr, value in overrides.items():
            setattr(spec, attr, value)
        row = {
            "folder": str(Path(book[0].path).parent), "title": "", "output": "", "status": "", "message": "",
            "chapters": len(book), "minutes": 0, "match": None, "notes": [],
        }
        minutes = sum(f.duration_seconds or 0 for f in book) / 60
        row["minutes"] = round(minutes)
        if args.lookup:
            _lookup(spec, overrides, minutes, args, region, row, out, cover is None)
        if cover is not None:
            spec.cover, spec.cover_mime = cover
        spec.output = _output_for(args, spec, Path(book[0].path).parent)
        row["title"], row["output"] = spec.title, str(spec.output)
        out.progress(number, len(books), spec.title)

        if spec.output.exists() and not args.replace:
            row["status"], row["message"] = "skipped", "already exists (use --replace to make it again)"
        elif args.dry_run:
            row["status"] = "planned"
        else:
            result = build_m4b(
                spec, ffmpeg_path=app.ffmpeg_path or None, ffprobe_path=app.ffprobe_path or None,
                progress=lambda done, total, title=spec.title: out.progress(done, total, title),
            )
            if result.status == STATUS_OK:
                row["status"], row["notes"] = "created", result.notes
                row["minutes"] = round(result.duration_seconds / 60)
            else:
                row["status"], row["message"] = "failed", result.message
                failed += 1
        out.record(row)
        match = f"  (matched {row['match']['source']}: {row['match']['title']})" if row["match"] else ""
        out.line(f"{row['status']:9} {row['output']}  [{row['chapters']} chapters, {row['minutes']} min]{match}"
                 + (f"  ({row['message']})" if row["message"] else ""))
        for note in row["notes"]:
            out.warn(f"{spec.title}: {note}")
    out.finish({"books": len(books), "failed": failed, "dry_run": args.dry_run, "bitrate_kbps": bitrate})
    return EXIT_PARTIAL if failed else EXIT_OK


def _unreadable(mp3, out: Output) -> bool:
    if mp3.load_error:
        out.warn(f"{mp3.path}: skipped, could not be read ({mp3.load_error})")
        return True
    return False


def _lookup(spec: BookSpec, overrides: dict[str, str], minutes: float, args, region: str, row: dict, out: Output,
            take_cover: bool) -> None:
    """Asks Audible / Open Library about the book and applies the best result when it is a confident match."""
    try:
        outcome = search(spec.title, spec.author, minutes or None, region=region)
    except AudiobookLookupError as exc:
        row["message"] = f"lookup failed: {exc}"
        out.warn(f"{spec.title}: lookup failed ({exc})")
        return
    if not outcome.matches:
        out.warn(f"{spec.title}: the lookup found nothing")
        return
    best = outcome.matches[0]
    value = score(best, spec.title, spec.author, minutes or None)
    if value < args.min_score:
        out.warn(f"{spec.title}: the best match ({best.title}, score {value:.1f}) is below --min-score {args.min_score}; not used")
        return
    _apply_match(spec, best, overrides)
    row["match"] = {"source": best.source, "title": best.title, "score": round(value, 2), "asin": best.asin}
    if take_cover and best.cover_url:
        try:
            spec.cover, spec.cover_mime = download_cover(best.cover_url)
        except AudiobookLookupError as exc:
            out.warn(f"{spec.title}: the cover could not be fetched ({exc})")
