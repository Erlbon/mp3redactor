"""
Compares the in-process integrity check and fix (core.mp3_validator, the
Python port of mp3val 0.1.8) with mp3val.exe, file by file.

    .venv/Scripts/python tools/compare_mp3val.py <folder or file> [...] [options]

Options:
    --exe PATH       mp3val.exe to compare against (default: the one
                     find_tool() finds, as the app would)
    --fix            also compare the fix: both rebuild their own copy of
                     each file, and the rebuilt files and the .bak backups
                     must be byte-identical
    --nb             with --fix: delete the backup (-nb) in both
    --all-files      take every file, not only .mp3/.mp2/.mp1
    -j N             files compared at once (default 4)
    --limit N        stop after N files
    --work-dir PATH  where the copies for --fix (and for names mp3val.exe
                     cannot open) go (default: a new folder in the system
                     temp folder, deleted afterwards)
    --report PATH    write the Markdown report here (default:
                     mp3val-comparison-<date>.md in the current folder)

The original files are only ever read: the check reads them in place and
the fix works on copies. mp3val.exe cannot open a path with letters
outside the Windows code page, so for such a file it gets a copy with a
plain name, while the port reads the original (which shows the port
opening it). The file path inside the quoted part of each line is
replaced by <file> before the lines are compared.

What is compared: every WARNING, ERROR, INFO and FIXED line, in order.
A file the exe calls empty must be one the port calls empty; a file the
exe hangs on (timeout) must be one the port stops on (ValidatorLoopError).
Exit code 0 when the two always agree, 1 when any file differs, 2 for a
usage error.
"""

from __future__ import annotations

import argparse
import datetime
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import mp3_validator  # noqa: E402
from core.mp3val_runner import MP3VAL_EXE_NAME  # noqa: E402
from core.tool_locator import find_tool  # noqa: E402

MPEG_EXTENSIONS = {".mp3", ".mp2", ".mp1"}
EXE_TIMEOUT = 60
_KEPT = ("WARNING:", "ERROR:", "INFO:", "FIXED:")
_QUOTED_PATH = re.compile(r'^(\w+): "[^"]*"')

EMPTY = "<empty file>"
LOOP = "<hangs: mp3val.exe timed out / the port stopped a loop>"
CRASH = "<crash: mp3val.exe access violation / the port stopped a read outside the file>"
ACCESS_VIOLATION = 0xC0000005


def normalise(lines: list[str]) -> list[str]:
    return [_QUOTED_PATH.sub(r'\1: "<file>"', line) for line in lines if line.startswith(_KEPT)]


def exe_can_open(path: Path) -> bool:
    """mp3val.exe takes its arguments in the ANSI code page."""
    if sys.platform != "win32":
        return True
    try:
        str(path.resolve()).encode("mbcs", errors="strict")
    except (UnicodeEncodeError, LookupError):
        return False
    return True


def run_exe(exe: Path, path: Path, *flags: str) -> list[str]:
    try:
        result = subprocess.run(
            [str(exe), *flags, str(path)], capture_output=True, stdin=subprocess.DEVNULL,
            timeout=EXE_TIMEOUT, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        return [LOOP]
    stdout = result.stdout.decode("mbcs" if sys.platform == "win32" else "utf-8", errors="replace")
    stderr = result.stderr.decode("mbcs" if sys.platform == "win32" else "utf-8", errors="replace")
    if "or it is empty" in stderr:
        return [EMPTY]
    if "Cannot open input file" in stderr:
        return ["<mp3val.exe could not open the file>"]
    if result.returncode & 0xFFFFFFFF == ACCESS_VIOLATION:
        return [CRASH]
    if result.returncode != 0:
        return [f"<mp3val.exe exit code {result.returncode}>"] + normalise(stdout.splitlines())
    return normalise(stdout.splitlines())


def run_port_check(path: Path) -> list[str]:
    try:
        return normalise(mp3_validator.check_file(path))
    except ValueError:
        return [EMPTY]
    except mp3_validator.ValidatorLoopError:
        return [LOOP]
    except mp3_validator.ValidatorCrashError:
        return [CRASH]


def run_port_fix(path: Path, delete_backup: bool) -> list[str]:
    try:
        return normalise(mp3_validator.fix_file(path, delete_backup=delete_backup))
    except ValueError:
        return [EMPTY]
    except mp3_validator.ValidatorLoopError:
        return [LOOP]
    except mp3_validator.ValidatorCrashError:
        return [CRASH]


@dataclass
class Outcome:
    path: Path
    same: bool = True
    problems: list[str] = field(default_factory=list)
    exe_lines: list[str] = field(default_factory=list)
    port_lines: list[str] = field(default_factory=list)
    verdict: str = "OK"
    fixed: bool = False
    exe_seconds: float = 0.0
    port_seconds: float = 0.0


def _verdict(lines: list[str]) -> str:
    if any(line.startswith("ERROR:") for line in lines):
        return "ERROR"
    if any(line.startswith("WARNING:") for line in lines):
        return "WARNING"
    if lines and lines[0].startswith("<"):
        return lines[0]
    return "OK"


def compare_one(path: Path, exe: Path, work: Path, index: int, fix: bool, delete_backup: bool) -> Outcome:
    out = Outcome(path)
    slot = work / f"{index:06d}"
    try:
        # -- check --
        start = time.perf_counter()
        out.port_lines = run_port_check(path)
        out.port_seconds = time.perf_counter() - start
        if exe_can_open(path):
            target = path
        else:
            slot.mkdir(parents=True, exist_ok=True)
            target = slot / ("check" + path.suffix)
            shutil.copyfile(path, target)
        start = time.perf_counter()
        out.exe_lines = run_exe(exe, target)
        out.exe_seconds = time.perf_counter() - start
        out.verdict = _verdict(out.exe_lines)
        if out.exe_lines != out.port_lines:
            out.same = False
            out.problems.append("check output differs")

        # -- fix, on two copies --
        if fix:
            a = slot / "exe"
            b = slot / "port"
            a.mkdir(parents=True, exist_ok=True)
            b.mkdir(parents=True, exist_ok=True)
            name = "track" + path.suffix
            shutil.copyfile(path, a / name)
            shutil.copyfile(path, b / name)
            flags = ("-f", "-nb") if delete_backup else ("-f",)
            exe_fix = run_exe(exe, a / name, *flags)
            port_fix = run_port_fix(b / name, delete_backup)
            out.fixed = any(line.startswith("FIXED:") for line in exe_fix)
            if exe_fix != port_fix:
                out.same = False
                out.problems.append("fix output differs")
                out.exe_lines += ["-- fix --"] + exe_fix
                out.port_lines += ["-- fix --"] + port_fix
            for fname in (name, name + ".bak"):
                ea, eb = (a / fname).exists(), (b / fname).exists()
                if ea != eb:
                    out.same = False
                    out.problems.append(f"{fname}: {'only the exe' if ea else 'only the port'} left it")
                elif ea and (a / fname).read_bytes() != (b / fname).read_bytes():
                    out.same = False
                    out.problems.append(f"{fname}: bytes differ")
            leftovers = sorted({p.name for p in a.iterdir()} ^ {p.name for p in b.iterdir()})
            if leftovers:
                out.same = False
                out.problems.append(f"files only one side left: {', '.join(leftovers)}")
    except Exception as exc:  # report it, keep going
        out.same = False
        out.problems.append(f"comparison failed: {type(exc).__name__}: {exc}")
    finally:
        shutil.rmtree(slot, ignore_errors=True)
    return out


def collect(paths: list[str], all_files: bool) -> list[Path]:
    files: list[Path] = []
    for p in paths:
        path = Path(p)
        if path.is_file():
            files.append(path)
        elif path.is_dir():
            for root, _dirs, names in os.walk(path):
                for name in sorted(names):
                    if all_files or Path(name).suffix.lower() in MPEG_EXTENSIONS:
                        files.append(Path(root) / name)
    return files


def write_report(outcomes: list[Outcome], args, exe: Path, report: Path, seconds: float) -> None:
    same = sum(1 for o in outcomes if o.same)
    verdicts: dict[str, int] = {}
    for o in outcomes:
        verdicts[o.verdict] = verdicts.get(o.verdict, 0) + 1
    fixed = sum(1 for o in outcomes if o.fixed)
    lines = [
        f"# mp3val port comparison, {datetime.date.today().isoformat()}",
        "",
        f"- Folders: {', '.join(args.paths)}",
        f"- mp3val.exe: {exe}",
        f"- Mode: check{' + fix' + (' -nb' if args.nb else '') if args.fix else ''}",
        f"- Files: {len(outcomes)}; **identical: {same}**; different: {len(outcomes) - same}",
        f"- mp3val.exe's verdicts: " + ", ".join(f"{k} {v}" for k, v in sorted(verdicts.items())),
    ]
    if args.fix:
        lines.append(f"- Files the fix rebuilt: {fixed} (rebuilt file and .bak compared byte for byte)")
    exe_time = sum(o.exe_seconds for o in outcomes)
    port_time = sum(o.port_seconds for o in outcomes)
    lines.append(f"- Check time: mp3val.exe {exe_time:.1f} s, port {port_time:.1f} s; whole run {seconds:.0f} s")
    lines.append("")
    different = [o for o in outcomes if not o.same]
    if different:
        lines += ["## Differences", ""]
        for o in different:
            lines.append(f"### {o.path}")
            lines.append("")
            lines.append("- " + "; ".join(o.problems))
            lines.append("")
            lines.append("mp3val.exe:")
            lines.append("```")
            lines += o.exe_lines or ["(nothing)"]
            lines.append("```")
            lines.append("port:")
            lines.append("```")
            lines += o.port_lines or ["(nothing)"]
            lines.append("```")
            lines.append("")
    else:
        lines.append("No differences.")
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("paths", nargs="+")
    parser.add_argument("--exe")
    parser.add_argument("--fix", action="store_true")
    parser.add_argument("--nb", action="store_true")
    parser.add_argument("--all-files", action="store_true")
    parser.add_argument("-j", type=int, default=4)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--work-dir")
    parser.add_argument("--report")
    args = parser.parse_args(argv)

    exe = Path(args.exe) if args.exe else find_tool(MP3VAL_EXE_NAME)
    if exe is None or not Path(exe).is_file():
        print("mp3val.exe not found; pass --exe", file=sys.stderr)
        return 2
    files = collect(args.paths, args.all_files)
    if args.limit:
        files = files[: args.limit]
    if not files:
        print("no files found", file=sys.stderr)
        return 2

    own_work = args.work_dir is None
    work = Path(tempfile.mkdtemp(prefix="mp3val-compare-")) if own_work else Path(args.work_dir)
    work.mkdir(parents=True, exist_ok=True)
    start = time.perf_counter()
    try:
        with ThreadPoolExecutor(max_workers=max(1, args.j)) as pool:
            futures = [
                pool.submit(compare_one, f, Path(exe), work, i, args.fix, args.nb) for i, f in enumerate(files)
            ]
            outcomes = []
            for i, future in enumerate(futures, start=1):
                outcome = future.result()
                outcomes.append(outcome)
                mark = "same" if outcome.same else "DIFFERENT: " + "; ".join(outcome.problems)
                print(f"[{i}/{len(files)}] {outcome.verdict:8} {mark}  {outcome.path}", flush=True)
    finally:
        if own_work:
            shutil.rmtree(work, ignore_errors=True)
    seconds = time.perf_counter() - start
    report = Path(args.report) if args.report else Path(f"mp3val-comparison-{datetime.date.today().isoformat()}.md")
    write_report(outcomes, args, Path(exe), report, seconds)
    same = sum(1 for o in outcomes if o.same)
    print(f"{same} of {len(outcomes)} identical; report: {report.resolve()}")
    return 0 if same == len(outcomes) else 1


if __name__ == "__main__":
    sys.exit(main())
