"""
mp3cli/cmd_analyze.py

  analyze PATH...   check the MP3 files: integrity (mp3val), a full decode (ffmpeg), BPM, musical key and
                    loudness -- the same checks as the Analyze menu. Nothing is written unless --save.

Exit code 1 when a check found a problem (WARNING or ERROR) or could not run (tool missing or failing), so a
script can act on it.
"""

from __future__ import annotations

import argparse

from core.mp3_file import STATUS_ERROR, STATUS_OK, STATUS_TOOL_ERROR, STATUS_TOOL_MISSING, STATUS_WARNING, MP3File
from core.scan_service import (
    run_bpm_check, run_deep_check, run_integrity_check, run_key_detection, run_loudness_measurement, save_dirty_tags,
)
from redactor_common.cli import EXIT_OK, EXIT_PARTIAL, Output, add_common_options

from mp3cli.files import add_path_arguments, collect, load, settings

PROBLEM_STATUSES = {STATUS_WARNING, STATUS_ERROR, STATUS_TOOL_ERROR, STATUS_TOOL_MISSING}


def add_analyze_parser(sub) -> None:
    parser = sub.add_parser(
        "analyze", help="check integrity, decode, BPM, key and loudness",
        description="Run the analysis checks on the MP3 files. With none of the check options, the integrity check "
                    "runs. Results are shown; nothing is written to the files unless --save.",
    )
    add_path_arguments(parser)
    parser.add_argument("--integrity", action="store_true", help="mp3val integrity check (the default check)")
    parser.add_argument("--deep", action="store_true", help="full ffmpeg decode, plus encoder / sample rate / channels")
    parser.add_argument("--bpm", action="store_true", help="detect the tempo")
    parser.add_argument("--key", action="store_true", help="detect the musical key (keyfinder-cli)")
    parser.add_argument("--loudness", action="store_true", help="measure the integrated loudness (LUFS) and the gain to the target")
    parser.add_argument("--save", action="store_true", help="write the results into the files' tags (BPM, key, scan stamps)")
    add_common_options(parser)
    parser.set_defaults(handler=run_analyze)


def _result(mp3: MP3File, checks: list[str]) -> dict:
    row: dict = {"path": str(mp3.path), "status": mp3.load_error or "ok", "checks": {}}
    if "integrity" in checks:
        row["checks"]["integrity"] = {"status": mp3.integrity_status, "message": mp3.integrity_message}
    if "deep" in checks:
        row["checks"]["deep"] = {
            "status": mp3.deep_check_status, "message": mp3.deep_check_message,
            "encoder": mp3.audio_encoder, "sample_rate_hz": mp3.sample_rate_hz, "channels": mp3.channels,
        }
    if "bpm" in checks:
        row["checks"]["bpm"] = {"status": mp3.bpm_status, "bpm": mp3.bpm, "message": mp3.bpm_message}
    if "key" in checks:
        row["checks"]["key"] = {"status": mp3.key_status, "key": mp3.key_value, "message": mp3.key_message}
    if "loudness" in checks:
        row["checks"]["loudness"] = {
            "status": mp3.loudness_status, "lufs": mp3.loudness_lufs, "gain_db": mp3.loudness_gain_db,
            "message": mp3.loudness_message,
        }
    return row


def _describe(name: str, check: dict) -> str:
    status = check["status"]
    detail = {
        "bpm": check.get("bpm"), "key": check.get("key"), "loudness": check.get("lufs"),
    }.get(name)
    if name == "loudness" and detail is not None:
        detail = f"{detail:.1f} LUFS (gain {check['gain_db']:+.1f} dB)"
    elif name == "deep" and status == STATUS_OK and check.get("encoder"):
        detail = f"{check['encoder']}, {check['sample_rate_hz']} Hz, {check['channels']} ch"
    text = f"{name}: {status}"
    if detail not in (None, ""):
        text += f" ({detail})"
    if check.get("message") and status != STATUS_OK:
        text += f" - {check['message']}"
    return text


def run_analyze(args: argparse.Namespace, out: Output) -> int:
    checks = [name for name in ("integrity", "deep", "bpm", "key", "loudness") if getattr(args, name)] or ["integrity"]
    app = settings()
    files = load(collect(args.paths, out, recurse=not args.no_recurse))
    readable = [m for m in files if not m.load_error]

    def step(label: str):
        return lambda done, total: out.progress(done, total, label)

    if "integrity" in checks:
        run_integrity_check(readable, progress=step("integrity"), mp3val_path=app.mp3val_path or None)
    if "deep" in checks:
        run_deep_check(readable, progress=step("deep check"), ffmpeg_path=app.ffmpeg_path or None, ffprobe_path=app.ffprobe_path or None)
    if "bpm" in checks:
        run_bpm_check(readable, progress=step("BPM"))
    if "key" in checks:
        run_key_detection(readable, progress=step("key"), keyfinder_cli_path=app.keyfinder_cli_path or None)
    if "loudness" in checks:
        run_loudness_measurement(readable, progress=step("loudness"), ffmpeg_path=app.ffmpeg_path or None)
    if args.save:
        save_dirty_tags([m for m in readable if m.dirty])

    problems = 0
    for mp3 in files:
        row = _result(mp3, checks)
        if args.save and mp3.save_error:
            row["save_error"] = mp3.save_error
        bad = bool(mp3.load_error) or bool(row.get("save_error")) or any(
            c["status"] in PROBLEM_STATUSES for c in row["checks"].values()
        )
        problems += bad
        row["problem"] = bad
        out.record(row)
        out.line(f"{'PROBLEM' if bad else 'ok':8} {mp3.path}")
        if mp3.load_error:
            out.line(f"         could not be read: {mp3.load_error}")
        for name, check in row["checks"].items():
            out.line(f"         {_describe(name, check)}")
        if row.get("save_error"):
            out.line(f"         not saved: {row['save_error']}")
    out.finish({"files": len(files), "problems": problems, "checks": checks, "saved": args.save})
    return EXIT_PARTIAL if problems else EXIT_OK
