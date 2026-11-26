"""
Compares the in-process key detector (core.key_detector, the Python port
of libkeyfinder) with the keyfinder-cli tool, file by file.

    .venv/Scripts/python tools/compare_keyfinder.py <folder or file> [...] [options]

Options:
    --exe PATH       keyfinder-cli.exe to compare against (default: the one
                     find_tool() finds, as the app would)
    --ffmpeg-only    decode with ffmpeg in the port too, not aubio first
                     (shows how much of a difference is decoding)
    -j N             files analysed at once (default 4)
    --limit N        stop after N files
    --report PATH    write the Markdown report here (default:
                     key-comparison-<date>.md in the current folder)

Every audio file under the given folders is analysed by both. The report
gives the agreement rate, the time each took, and every file where they
differ, with how the two keys relate (relative major/minor, a fifth
apart, same tonic other mode, other): near misses matter less than
unrelated keys. Exit code 0 when both always agree, 1 when any file
differs or fails, 2 for a usage error.
"""

from __future__ import annotations

import argparse
import datetime
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import key_detector  # noqa: E402
from core.keyfinder_runner import KEYFINDER_EXE_NAME, detect_key_with_exe  # noqa: E402
from core.mp3_file import STATUS_OK  # noqa: E402
from core.tool_locator import find_tool  # noqa: E402

AUDIO_EXTENSIONS = {".mp3", ".flac", ".wav", ".m4a", ".m4b", ".ogg", ".opus", ".aac", ".wma", ".aiff", ".aif"}


def key_relation(a: str, b: str) -> str:
    """How two keys in standard notation relate."""
    if a == b:
        return "same"
    if not a or not b:
        return "silence vs key"
    names = key_detector.STANDARD_NOTATION
    ia, ib = names.index(a), names.index(b)
    # key_t: index = semitones above A * 2 + (1 if minor)
    ta, ma = ia // 2, ia % 2
    tb, mb = ib // 2, ib % 2
    # Express each as its relative major tonic (minor tonic + 3 semitones).
    ra = (ta + 3 * ma) % 12
    rb = (tb + 3 * mb) % 12
    if ma != mb and ra == rb:
        return "relative major/minor"
    if ma != mb and ta == tb:
        return "same tonic, other mode"
    if ma == mb and (ta - tb) % 12 in (5, 7):
        return "a fifth apart"
    if ma != mb and (ra - rb) % 12 in (5, 7):
        return "a fifth apart, other mode"
    return "unrelated"


def collect(paths: list[str]) -> list[Path]:
    files: list[Path] = []
    for p in paths:
        path = Path(p)
        if path.is_file():
            files.append(path)
        elif path.is_dir():
            for root, _dirs, names in os.walk(path):
                for name in sorted(names):
                    if Path(name).suffix.lower() in AUDIO_EXTENSIONS:
                        files.append(Path(root) / name)
        else:
            print(f"not found: {p}", file=sys.stderr)
    return files


def analyse(path: Path, exe: Path, ffmpeg_only: bool, ffmpeg: str | None) -> dict:
    t0 = time.perf_counter()
    if ffmpeg_only:
        try:
            rate, blocks, finish = key_detector._ffmpeg_blocks(ffmpeg, str(path))
            index = key_detector._analyse_blocks(rate, blocks)
            problem = finish()
            port = (key_detector.key_name(index), STATUS_OK if not problem else "error", problem)
        except Exception as e:  # noqa: BLE001
            port = ("", "error", str(e))
    else:
        port = key_detector.detect_key_native(path)
    t1 = time.perf_counter()
    exe_result = detect_key_with_exe(path, tool_path=exe)
    t2 = time.perf_counter()
    return {
        "path": path, "port": port, "exe": exe_result,
        "port_s": t1 - t0, "exe_s": t2 - t1,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare the Python key detector with keyfinder-cli.")
    parser.add_argument("paths", nargs="+")
    parser.add_argument("--exe")
    parser.add_argument("--ffmpeg-only", action="store_true")
    parser.add_argument("-j", type=int, default=4)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--report")
    args = parser.parse_args(argv)

    exe = Path(args.exe) if args.exe else find_tool(KEYFINDER_EXE_NAME)
    if exe is None or not Path(exe).is_file():
        print("keyfinder-cli.exe not found: pass --exe", file=sys.stderr)
        return 2
    ffmpeg = None
    if args.ffmpeg_only:
        from core.ffmpeg_probe import FFMPEG_EXE_NAME

        found = find_tool(FFMPEG_EXE_NAME)
        if found is None:
            print("--ffmpeg-only needs ffmpeg on PATH or in tools/", file=sys.stderr)
            return 2
        ffmpeg = str(found)

    files = collect(args.paths)
    if args.limit:
        files = files[: args.limit]
    if not files:
        print("no audio files found", file=sys.stderr)
        return 2

    results = []
    with ThreadPoolExecutor(max_workers=max(1, args.j)) as pool:
        futures = [pool.submit(analyse, f, Path(exe), args.ffmpeg_only, ffmpeg) for f in files]
        for i, fut in enumerate(futures, 1):
            r = fut.result()
            results.append(r)
            mark = "=" if r["port"][:2] == r["exe"][:2] else "X"
            print(f"[{i}/{len(files)}] {mark} port={r['port'][0] or '-':4} exe={r['exe'][0] or '-':4} {r['path'].name}")

    both_ok = [r for r in results if r["port"][1] == STATUS_OK and r["exe"][1] == STATUS_OK]
    agree = [r for r in both_ok if r["port"][0] == r["exe"][0]]
    differ = [r for r in both_ok if r["port"][0] != r["exe"][0]]
    failed = [r for r in results if r not in both_ok]
    relations: dict[str, int] = {}
    for r in differ:
        rel = key_relation(r["port"][0], r["exe"][0])
        relations[rel] = relations.get(rel, 0) + 1
    port_time = sum(r["port_s"] for r in results)
    exe_time = sum(r["exe_s"] for r in results)
    pct = 100.0 * len(agree) / len(both_ok) if both_ok else 0.0

    lines = [
        f"# Key detection: Python port vs keyfinder-cli ({datetime.date.today().isoformat()})",
        "",
        f"- Files: {len(results)} ({', '.join(args.paths)})",
        f"- Port decoding: {'ffmpeg only' if args.ffmpeg_only else 'aubio, ffmpeg fallback (as the app does)'}",
        f"- keyfinder-cli: {exe}",
        f"- **Same key: {len(agree)} of {len(both_ok)} ({pct:.1f}%)**",
        f"- Different key: {len(differ)}" + (
            " (" + ", ".join(f"{n} {rel}" for rel, n in sorted(relations.items(), key=lambda kv: -kv[1])) + ")"
            if differ else ""),
        f"- Failed in one or both: {len(failed)}",
        f"- Time per file: port {port_time / len(results):.2f} s, keyfinder-cli {exe_time / len(results):.2f} s",
        "",
    ]
    if differ:
        lines += ["## Different keys", "", "| File | Port | keyfinder-cli | Relation |", "|---|---|---|---|"]
        for r in differ:
            lines.append(f"| {r['path']} | {r['port'][0] or '(silence)'} | {r['exe'][0] or '(silence)'} | "
                         f"{key_relation(r['port'][0], r['exe'][0])} |")
        lines.append("")
    if failed:
        lines += ["## Failures", "", "| File | Port | keyfinder-cli |", "|---|---|---|"]
        for r in failed:
            p = r["port"][2] if r["port"][1] != STATUS_OK else r["port"][0] or "(silence)"
            e = r["exe"][2] if r["exe"][1] != STATUS_OK else r["exe"][0] or "(silence)"
            lines.append(f"| {r['path']} | {p.replace('|', '/')} | {e.replace('|', '/')} |")
        lines.append("")

    report = Path(args.report) if args.report else Path(f"key-comparison-{datetime.date.today().isoformat()}.md")
    report.write_text("\n".join(lines), encoding="utf-8")
    print()
    print("\n".join(lines[3:9]))
    print(f"Report: {report.resolve()}")
    return 0 if not differ and not failed else 1


if __name__ == "__main__":
    sys.exit(main())
