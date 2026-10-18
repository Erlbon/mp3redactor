"""
core/conversion_safety.py

The shared conversion policy (redactor_common.core.conversion_policy) for Import and Convert to MP3: what is done
with the ORIGINAL once its MP3 exists. Qt-free.

Rules:
1. Never delete media. An original is KEPT, MOVED to a dated "converted originals YYYY-MM-DD" folder (QUARANTINE), or sent
   to the Recycle Bin (RECYCLE).
2. Only after the result is verified: the new .mp3 opens with mutagen and its duration is within
   DURATION_TOLERANCE_SECONDS of the original's. An original whose duration cannot be read is not touched.
3. A quarantine move is written to a TSV undo log (UTF-8, `new_path<TAB>old_path`, `path<TAB>CREATED` for the new mp3).
4. An original modified in the last `recent_minutes` minutes is left alone (quarantine and recycle only).
5. A dry run changes nothing: plan_originals() says what would happen.

A user who has not saved Preferences > Conversion gets legacy_policy(): ask on open, keep the original, no dry run:
exactly what the app did before the policy existed.
"""

from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable

from redactor_common.core import conversion_policy as policy_mod
from redactor_common.core.conversion_policy import ConversionPolicy

KIND_CONVERTED = "converted originals"
UNDO_CREATED = "CREATED"
DURATION_TOLERANCE_SECONDS = 1.5

KEPT = "kept"
QUARANTINED = "quarantined"
RECYCLED = "recycled"
LEFT = "left"  # the policy wanted to move it, but it was not safe to: the original stays (detail says why)


def legacy_policy() -> ConversionPolicy:
    """What the app did before the shared page existed: the original stays beside the new mp3, ask on open."""
    return ConversionPolicy(
        on_open=policy_mod.ON_OPEN_ASK, originals=policy_mod.ORIGINALS_KEEP, dry_run_first=False,
    )


def get_policy(settings) -> ConversionPolicy:
    """The saved policy (Settings.conversion_policy holds the [conversion_policy] ini section), else legacy_policy()."""
    values = settings.conversion_policy

    def get(section: str, key: str, default: str) -> str:
        return values.get(key, default)

    return policy_mod.load_policy(get, legacy_policy())


def save_policy(settings, policy: ConversionPolicy) -> None:
    """Puts the policy into `settings` (the caller then calls save_settings)."""

    def put(section: str, key: str, value: str) -> None:
        settings.conversion_policy[key] = value

    policy_mod.save_policy(policy, put)


@dataclass
class OriginalOutcome:
    src: Path
    action: str  # KEPT / QUARANTINED / RECYCLED / LEFT
    detail: str = ""
    moved_to: Path | None = None
    done: bool = True  # False: a dry run, nothing was touched


def _duration(path: Path) -> float | None:
    try:
        import mutagen

        found = mutagen.File(str(path))
        length = getattr(getattr(found, "info", None), "length", None)
        return float(length) if length else None
    except Exception:  # mutagen raises many kinds for an unreadable file
        return None


def verify_result(src: Path, dest: Path, tolerance: float = DURATION_TOLERANCE_SECONDS) -> str:
    """"" when `dest` is a readable MP3 whose duration matches `src`; else the reason it is not verified."""
    if not dest.is_file() or dest.stat().st_size == 0:
        return "the new MP3 is missing or empty"
    new_length = _duration(dest)
    if new_length is None:
        return "the new MP3 cannot be read"
    old_length = _duration(src)
    if old_length is None:
        return "the original's length cannot be read, so the result cannot be compared"
    if abs(new_length - old_length) > tolerance:
        return f"the new MP3 is {new_length:.1f} s, the original {old_length:.1f} s"
    return ""


def _free_name(path: Path) -> Path:
    if not os.path.lexists(path):
        return path
    n = 2
    while os.path.lexists(path.with_name(f"{path.stem} ({n}){path.suffix}")):
        n += 1
    return path.with_name(f"{path.stem} ({n}){path.suffix}")


def quarantine_folder_for(src: Path, policy: ConversionPolicy, day: date | None = None) -> Path:
    base = Path(policy.quarantine_folder) if policy.quarantine_folder else src.parent
    return base / f"{KIND_CONVERTED} {(day or date.today()).isoformat()}"


def undo_log_path(src: Path, policy: ConversionPolicy, day: date | None = None) -> Path:
    folder = quarantine_folder_for(src, policy, day)
    log_folder = Path(policy.undo_log_folder) if policy.undo_log_folder else folder.parent
    return log_folder / f"{folder.name}.undo.tsv"


def _log(path: Path, new_path: Path, old: str) -> None:
    for text in (str(new_path), old):
        if any(c in text for c in "\t\r\n"):
            return  # cannot be written as TSV; the move itself was already refused by the caller
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(f"{new_path}\t{old}\n")
        f.flush()
        os.fsync(f.fileno())


def _loggable(*paths: Path) -> bool:
    return not any(c in str(p) for p in paths for c in "\t\r\n")


def handle_original(
    src: Path, dest: Path, policy: ConversionPolicy, *, dry_run: bool = False, day: date | None = None,
    trash: Callable[[str], None] | None = None, now: Callable[[], float] = time.time,
) -> OriginalOutcome:
    """Does (or, with dry_run, only describes) what the policy says to the original `src` of the new `dest`."""
    if policy.originals == policy_mod.ORIGINALS_KEEP or os.path.normcase(os.path.abspath(src)) == os.path.normcase(
            os.path.abspath(dest)):
        return OriginalOutcome(src, KEPT, "the original stays beside the new MP3", done=not dry_run)
    if dry_run:
        # The result does not exist yet: a dry run states the intent.
        if policy.originals == policy_mod.ORIGINALS_RECYCLE:
            return OriginalOutcome(src, RECYCLED, "would be sent to the Recycle Bin", done=False)
        target = quarantine_folder_for(src, policy, day)
        return OriginalOutcome(src, QUARANTINED, f"would be moved to {target}", target, done=False)
    problem = verify_result(src, dest)
    if problem:
        return OriginalOutcome(src, LEFT, f"original left where it is: {problem}")
    if policy.recent_minutes > 0:
        try:
            age = now() - src.stat().st_mtime
        except OSError:
            age = None
        if age is not None and age < policy.recent_minutes * 60:
            return OriginalOutcome(src, LEFT, f"original left where it is: modified in the last {policy.recent_minutes} minutes")
    if policy.originals == policy_mod.ORIGINALS_RECYCLE:
        if trash is None:
            from redactor_common.core.trash import move_to_trash as trash
        try:
            trash(str(src))
        except Exception as exc:  # TrashError, TrashUnavailable
            return OriginalOutcome(src, LEFT, f"original left where it is: {exc}")
        return OriginalOutcome(src, RECYCLED, "sent to the Recycle Bin")
    folder = quarantine_folder_for(src, policy, day)
    target = _free_name(folder / src.name)
    if not _loggable(target, src, dest):
        return OriginalOutcome(src, LEFT, "original left where it is: a tab or line break in a path cannot be logged")
    try:
        folder.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(target))
    except OSError as exc:
        return OriginalOutcome(src, LEFT, f"original left where it is: {exc}")
    log = undo_log_path(src, policy, day)
    _log(log, dest, UNDO_CREATED)
    _log(log, target, str(src))
    return OriginalOutcome(src, QUARANTINED, f"moved to {target}", target)


def undo_conversion(log_path: Path) -> list[str]:
    """Moves the quarantined originals of an undo log back, last first. A new MP3 (CREATED) is left in place: media is
    never deleted. Returns the problems (nothing is overwritten)."""
    problems: list[str] = []
    entries = []
    try:
        for raw in Path(log_path).read_text(encoding="utf-8").split("\n"):
            if "\t" in raw:
                a, b = raw.split("\t", 1)
                entries.append((a, b))
    except OSError as exc:
        return [str(exc)]
    for new, old in reversed(entries):
        if old == UNDO_CREATED:
            continue
        if not os.path.lexists(new):
            problems.append(f"{new} is gone; cannot move it back to {old}")
        elif os.path.lexists(old):
            problems.append(f"{old} exists now; {new} left where it is")
        else:
            try:
                Path(old).parent.mkdir(parents=True, exist_ok=True)
                shutil.move(new, old)
            except OSError as exc:
                problems.append(f"{new}: {exc}")
    return problems


def plan_originals(conversions: list[tuple[Path, Path]], policy: ConversionPolicy) -> list[str]:
    """The dry-run report: one line per conversion, what would be converted and what would happen to the original."""
    lines = []
    for src, dest in conversions:
        out = handle_original(src, dest, policy, dry_run=True)
        lines.append(f"{src}  ->  {dest}\n    original: {out.detail}")
    return lines
