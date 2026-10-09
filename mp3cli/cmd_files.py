"""
mp3cli/cmd_files.py

  rename  PATH...   rename by a tag pattern ("%track% - %artist% - %title%")
  move    PATH...   move (or copy) into folders under a library root by a pattern ("%albumartist%/%album%/...")
  convert PATH...   FLAC/WAV/OGG/M4A/... to MP3 beside the original (ffmpeg's libmp3lame)

Rename and move are the shared implementations in redactor_common.cli.commands; this file only says how a
file's fields and path are read. There is no undo for the command line (it is not recorded in the app's rename log).
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from core.fields import FIELDS
from core.mp3_converter import (
    BITRATE_CHOICES_KBPS, DEFAULT_BITRATE_KBPS, IMPORTABLE_EXTENSIONS, convert_to_mp3, plan_conversions,
)
from core.mp3_file import STATUS_OK, MP3File
from redactor_common.cli import Output, add_common_options, commands
from redactor_common.cli.commands import add_pattern_options, new_row, say
from redactor_common.core.rename_pattern import zero_pad_numeric_value
from redactor_common.core.trash import TrashError, move_to_trash

from mp3cli.files import add_path_arguments, collect, load, settings, skip_reason

DEFAULT_RENAME_PATTERN = "%track% - %artist% - %title%"


def _zero_pad(args: argparse.Namespace) -> int:
    """--zero-pad N, else the choice saved in the app's Rename window (on: its width), else none."""
    if args.zero_pad is not None:
        return args.zero_pad
    app = settings()
    return app.rename_zero_pad_width if app.rename_zero_pad else 0


def _ascii(args: argparse.Namespace) -> bool:
    return bool(args.ascii) or settings().ascii_filenames


def _values_for(mp3: MP3File, zero_pad: int) -> dict[str, str]:
    values = {key: getattr(mp3, key, "") or "" for key, _label, _multiline in FIELDS}
    if zero_pad > 0 and values.get("track"):
        values["track"] = zero_pad_numeric_value(values["track"], zero_pad)
    return values


# --- rename -------------------------------------------------------------------------


def add_rename_parser(sub) -> None:
    parser = sub.add_parser(
        "rename", help="rename files by a tag pattern",
        description="Rename each MP3 from its tags. Never overwrites: a name that is taken gets (2), (3)...",
    )
    add_path_arguments(parser)
    add_pattern_options(parser, pattern_required=False)
    add_common_options(parser)
    parser.set_defaults(handler=run_rename)


def run_rename(args: argparse.Namespace, out: Output) -> int:
    pattern = args.pattern or DEFAULT_RENAME_PATTERN
    zero_pad = _zero_pad(args)
    files = load(collect(args.paths, out, recurse=not args.no_recurse))
    failed = commands.rename_items(
        files, pattern=pattern, values_for=lambda m: _values_for(m, zero_pad), path_of=lambda m: str(m.path),
        skip_reason=skip_reason, out=out, dry_run=args.dry_run, ascii_only=_ascii(args),
    )
    return commands.finish_run(out, failed, files=len(files), dry_run=args.dry_run, pattern=pattern)


# --- move ---------------------------------------------------------------------------


def add_move_parser(sub) -> None:
    parser = sub.add_parser(
        "move", help="move files into folders under a library root",
        description="Move (or copy) each MP3 to <root>/<pattern>, the pattern may contain / to make sub-folders, "
                    "e.g. \"%albumartist%/%album%/%track% - %title%\". Never overwrites.",
    )
    add_path_arguments(parser)
    add_pattern_options(parser, pattern_required=True)
    parser.add_argument("--root", metavar="FOLDER", help="the library folder (default: the one saved in the app)")
    parser.add_argument("--copy", action="store_true", help="copy instead of move, leaving the originals")
    add_common_options(parser)
    parser.set_defaults(handler=run_move)


def run_move(args: argparse.Namespace, out: Output) -> int:
    root = args.root or settings().library_root
    zero_pad = _zero_pad(args)
    files = load(collect(args.paths, out, recurse=not args.no_recurse))
    failed = commands.move_items(
        files, root=root, pattern=args.pattern, values_for=lambda m: _values_for(m, zero_pad),
        path_of=lambda m: str(m.path), skip_reason=skip_reason, out=out, dry_run=args.dry_run, copy=args.copy,
        ascii_only=_ascii(args),
    )
    return commands.finish_run(out, failed, files=len(files), dry_run=args.dry_run, root=root)


# --- convert ------------------------------------------------------------------------


def add_convert_parser(sub) -> None:
    parser = sub.add_parser(
        "convert", help="convert FLAC/WAV/OGG/M4A/... to MP3",
        description="Convert other audio formats to MP3 with ffmpeg, beside the original (same name, .mp3). "
                    "Never overwrites: if the .mp3 already exists the file is skipped.",
    )
    add_path_arguments(parser)
    parser.add_argument("--bitrate", type=int, choices=BITRATE_CHOICES_KBPS, default=DEFAULT_BITRATE_KBPS,
                        metavar="KBPS", help=f"MP3 bitrate, one of {', '.join(map(str, BITRATE_CHOICES_KBPS))} (default {DEFAULT_BITRATE_KBPS})")
    parser.add_argument("--trash-original", action="store_true",
                        help="send the original to the Recycle Bin after the .mp3 is made (never deleted for good)")
    parser.add_argument("-n", "--dry-run", action="store_true", help="show what would be converted, change nothing")
    add_common_options(parser)
    parser.set_defaults(handler=run_convert)


def run_convert(args: argparse.Namespace, out: Output) -> int:
    files = collect(args.paths, out, recurse=not args.no_recurse, extensions=IMPORTABLE_EXTENSIONS, noun="audio")
    ffmpeg = settings().ffmpeg_path or None
    rows: dict[str, dict] = {}
    todo: list[Path] = []
    for path in files:
        row = rows[path] = new_row(path)
        source = Path(path)
        if source.suffix.lower() == ".mp3":
            row["status"], row["message"] = "skipped", "already an MP3"
        elif source.with_suffix(".mp3").exists():
            row["status"], row["message"], row["new_path"] = "skipped", "an .mp3 of that name already exists; left alone", str(source.with_suffix(".mp3"))
        else:
            todo.append(source)
    conversions, _skipped = plan_conversions(todo)
    failed = 0
    done = 0
    for source, dest in conversions:
        row = rows[str(source)]
        done += 1
        out.progress(done, len(conversions), source.name)
        row["new_path"] = str(dest)
        if args.dry_run:
            row["status"] = "planned"
            continue
        status, message = convert_to_mp3(source, dest, bitrate_kbps=args.bitrate, override_path=ffmpeg)
        if status != STATUS_OK:
            row["status"], row["message"], row["new_path"] = "failed", message, ""
            failed += 1
            continue
        row["status"] = "converted"
        if args.trash_original:
            try:
                commands.trash_with_retries(move_to_trash)(str(source))
            except TrashError as exc:
                row["message"] = f"converted, but the original was kept: {exc}"
                out.warn(f"{source.name}: the original was kept ({exc})")
    for path in files:
        out.record(rows[path])
        say(out, rows[path])
    return commands.finish_run(out, failed, files=len(files), dry_run=args.dry_run, bitrate_kbps=args.bitrate)
