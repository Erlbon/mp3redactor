"""
mp3cli/cmd_tags.py

  info PATH...   what each MP3 is: length, bitrate, cover, and its tags
  set  PATH...   change tag fields (-s Artist=Queen -s Track=3, --clear Comment), saved in place
"""

from __future__ import annotations

import argparse

from core.mp3_file import MP3File
from core.tag_writer import save_tags
from redactor_common.cli import EXIT_OK, EXIT_PARTIAL, CliError, Output, add_common_options

from mp3cli.fields import DEFAULT_INFO_FIELDS, FIELD_KEYS, parse_assignment, resolve_field
from mp3cli.files import add_path_arguments, collect, load, skip_reason


def _duration_text(seconds) -> str:
    if not seconds:
        return ""
    total = int(round(seconds))
    return f"{total // 3600}:{total % 3600 // 60:02d}:{total % 60:02d}" if total >= 3600 else f"{total // 60}:{total % 60:02d}"


# --- info ---------------------------------------------------------------------------


def add_info_parser(sub) -> None:
    parser = sub.add_parser("info", help="show what the MP3 files are", description="Show each file's length, bitrate, cover and tags.")
    add_path_arguments(parser)
    parser.add_argument("--fields", metavar="LIST", help="comma-separated tag fields to show (default: title, artist, album, track, year)")
    parser.add_argument("--all", action="store_true", help="show every tag field that has a value")
    add_common_options(parser)
    parser.set_defaults(handler=run_info)


def run_info(args: argparse.Namespace, out: Output) -> int:
    files = collect(args.paths, out, recurse=not args.no_recurse)
    wanted = FIELD_KEYS if args.all else (
        [resolve_field(name) for name in args.fields.split(",") if name.strip()] if args.fields else DEFAULT_INFO_FIELDS
    )
    failed = 0
    for index, mp3 in enumerate(load(files), start=1):
        out.progress(index, len(files), str(mp3.path))
        fields = {k: getattr(mp3, k, "") for k in wanted if (getattr(mp3, k, "") or "").strip()}
        status = mp3.load_error or "ok"
        failed += bool(mp3.load_error)
        out.record({
            "path": str(mp3.path), "status": status, "duration_seconds": mp3.duration_seconds,
            "bitrate_kbps": mp3.bitrate_kbps, "has_cover": mp3.has_cover, "fields": fields,
        })
        details = ", ".join(p for p in (_duration_text(mp3.duration_seconds),
                                       f"{mp3.bitrate_kbps} kbps" if mp3.bitrate_kbps else "",
                                       "cover" if mp3.has_cover else "no cover", status) if p)
        out.line(f"{mp3.path}  [{details}]")
        for key, value in fields.items():
            out.line(f"  {key}: {value}")
    out.finish({"files": len(files), "failed": failed})
    return EXIT_PARTIAL if failed else EXIT_OK


# --- set ----------------------------------------------------------------------------


def add_set_parser(sub) -> None:
    parser = sub.add_parser(
        "set", help="change tag fields",
        description="Set or empty ID3 tag fields and save each file in place. An empty value removes the tag.",
    )
    add_path_arguments(parser)
    parser.add_argument("-s", "--set", dest="assignments", action="append", default=[], metavar="FIELD=VALUE",
                        help="set a field (repeat for several), e.g. -s Artist=Queen -s Track=3")
    parser.add_argument("--clear", action="append", default=[], metavar="FIELD", help="empty a field (repeatable)")
    parser.add_argument("-n", "--dry-run", action="store_true", help="show the changes, write nothing")
    add_common_options(parser)
    parser.set_defaults(handler=run_set)


def run_set(args: argparse.Namespace, out: Output) -> int:
    changes: dict[str, str] = {}
    for text in args.assignments:
        key, value = parse_assignment(text)
        changes[key] = value
    for name in args.clear:
        changes[resolve_field(name)] = ""
    if not changes:
        raise CliError("nothing to change: give at least one -s FIELD=VALUE or --clear FIELD")

    files = collect(args.paths, out, recurse=not args.no_recurse)
    failed = 0
    for index, mp3 in enumerate(load(files), start=1):
        path = str(mp3.path)
        out.progress(index, len(files), path)
        row = {"path": path, "status": "", "changes": {}, "message": ""}
        reason = skip_reason(mp3)
        if reason:
            row["status"], row["message"] = "failed", reason
            failed += 1
        else:
            for key, new in changes.items():
                old = getattr(mp3, key, "") or ""
                if old != new:
                    row["changes"][key] = {"old": old, "new": new}
            if not row["changes"]:
                row["status"] = "unchanged"
            elif args.dry_run:
                row["status"] = "planned"
            else:
                row["status"] = _save(mp3, changes, row)
                failed += row["status"] == "failed"
        out.record(row)
        out.line(f"{row['status']:9} {path}" + (f"  ({row['message']})" if row["message"] else ""))
        for key, change in row["changes"].items():
            out.line(f"          {key}: {change['old']!r} -> {change['new']!r}")
    out.finish({"files": len(files), "failed": failed, "dry_run": args.dry_run})
    return EXIT_PARTIAL if failed else EXIT_OK


def _save(mp3: MP3File, changes: dict[str, str], row: dict) -> str:
    mp3.apply_tags(changes)
    if save_tags(mp3):
        return "changed"
    row["message"] = mp3.save_error
    return "failed"
