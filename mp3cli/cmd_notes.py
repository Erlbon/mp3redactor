"""
mp3cli/cmd_notes.py

  notes PATH...   draft the Markdown folder notes (artist.md in an artist folder, album.md in an album folder) that
                  Pluteus shows beside the folder, from what the folders hold and what MusicBrainz, Wikipedia and
                  (with the Discogs token saved in the app, or DISCOGS_TOKEN) Discogs say.

Creating notes for a whole library: `mp3redactor notes <library root> --kind artist --write` writes the confident
artist notes; add `--include-uncertain` to also write the uncertain ones from the engine's best candidate (drafts,
marked "from an uncertain match" in the report, never over an existing note, never for nothing found or an unclear
level).

A REPORT by default: every target folder is looked up and listed with how sure the match is and what would be
written, and nothing is written. --write writes the drafts (status: draft in the front matter) of the CONFIDENT
matches only -- a match is confident when at least one of the folder's own album titles is found in the candidate's
discography and nothing equally good competes with it. Uncertain and unmatched folders are listed with the reasons
and the candidates, never written: they are for a person to decide (Tools > Create Notes in the app has the review page). A folder
that already has artist.md / author.md / series.md / album.md / about.md, in any letter case, is never touched; a
note is never overwritten. The engine is core/notes_flow.py; the layout rule (Artist/Album (Year)/01 Song.mp3) is
core/notes_local.py.
"""

from __future__ import annotations

import argparse

from core.notes_flow import (
    STATUS_SKIPPED, Confidence, Fetchers, NotesEngine, NotesSummary, Proposal, build_proposals, live_fetchers, scan, summarize,
    write_proposal,
)
from core.notes_local import ALL_KINDS, KIND_ALBUM, KIND_ARTIST, KIND_GENRE
from redactor_common.cli import EXIT_INTERRUPTED, EXIT_OK, EXIT_PARTIAL, CliError, Output, add_common_options

KIND_CHOICES = ("both", KIND_ARTIST, KIND_ALBUM, KIND_GENRE, "all")


def make_fetchers(use_discogs: bool) -> Fetchers:
    """The real services; the tests replace this one function with recorded fixtures."""
    return live_fetchers(use_discogs=use_discogs)


def add_notes_parser(sub) -> None:
    parser = sub.add_parser(
        "notes", help="draft artist.md / album.md folder notes",
        description="Draft the Markdown notes Pluteus shows beside a folder (artist.md, album.md) from MusicBrainz, "
                    "Wikipedia and Discogs, checked against the albums really in the folder. A report unless "
                    "--write is given; --write writes only the confident matches (status: draft) and never "
                    "overwrites an existing note.",
    )
    parser.add_argument("paths", nargs="+", metavar="PATH", help="a library, artist or album folder (or MP3 files, wildcards)")
    parser.add_argument(
        "-R", "--no-recurse", action="store_true",
        help="look only at the folders named, not the folders below them",
    )
    parser.add_argument("--kind", choices=KIND_CHOICES, default="both", help="which notes: artist, album, genre (about.md of a genre or collection folder), both = artist + album "
                                                             "(default) or all = all three")
    parser.add_argument("--write", action="store_true", help="write the drafts of the confident matches (default: report only)")
    parser.add_argument(
        "--include-uncertain", action="store_true",
        help="with --write: ALSO write the uncertain matches, using the engine's best candidate, as drafts "
             "(status: draft; never over an existing note, never for 'nothing found', never when the level is "
             "unclear). The report marks them 'from an uncertain match' so they can be reviewed afterwards")
    parser.add_argument("-n", "--dry-run", action="store_true", help="never write, even with --write: show what would be written")
    parser.add_argument("--no-discogs", action="store_true", help="do not use Discogs even when a token is saved")
    parser.add_argument("--show-drafts", action="store_true", help="print each draft note (text mode) / include it (JSON)")
    add_common_options(parser)
    parser.set_defaults(handler=run_notes)


def _row(proposal: Proposal, status: str, message: str, show_draft: bool) -> dict:
    row = proposal.to_dict(with_text=show_draft)
    row.update({"status": status, "message": message, "path": proposal.path})
    return row


def _print(out: Output, row: dict) -> None:
    extra = f"  ({row['message']})" if row["message"] else ""
    out.line(f"{row['status']:<11} {row['kind']:<7} {row['folder']}{extra}")
    if row["status"] in ("review", "no-match"):
        for reason in row["reasons"][:4]:
            out.line(f"             - {reason}")
        if row.get("suggested_levels"):
            out.line(f"             level: {row.get('level', '')}; could be: {', '.join(row['suggested_levels'])}")
        for cand in row["candidates"][:3]:
            found = f", {len(cand['hits'])} local album(s) found" if cand["hits"] else ""
            out.line(f"             candidate: {cand['source']} {cand['name']} [{cand['detail']}]{found}")
    for problem in row["problems"][:3]:
        out.line(f"             ! {problem}")
    if row.get("draft"):
        out.line("")
        out.line(row["draft"].rstrip("\n"))
        out.line("")


def run_notes(args: argparse.Namespace, out: Output) -> int:
    kinds = {"both": (KIND_ARTIST, KIND_ALBUM), "all": ALL_KINDS}.get(args.kind, (args.kind,))
    found = scan(args.paths, include_subfolders=not args.no_recurse, kinds=kinds)
    for argument in found.missing:
        out.error(f"nothing found for {argument}")
    if not found.targets and not found.skipped and not found.groupings:
        raise CliError("no artist or album folders found")
    dry = args.dry_run or not args.write
    for skipped in found.skipped:
        row = {"status": "has-note", "kind": skipped.kind, "folder": str(skipped.folder), "message": skipped.reason,
               "reasons": [], "candidates": [], "problems": [], "confidence": "", "path": "", "sources": []}
        out.record(row)
        _print(out, row)

    for grouping in found.groupings:
        row = {"status": "grouping", "kind": grouping.kind, "folder": str(grouping.folder),
               "message": f"skipped: {grouping.reason}", "reasons": [], "candidates": [], "problems": [],
               "confidence": "", "path": "", "sources": []}
        out.record(row)
        _print(out, row)

    results = {}
    proposals: list[Proposal] = []
    guessed = 0  # notes written (or that would be) from an uncertain match
    failed = 0
    cancelled = False

    engine = NotesEngine(make_fetchers(not args.no_discogs))

    def handle(proposal: Proposal) -> None:
        nonlocal failed, guessed
        guess = None
        unwritten = ""
        if args.include_uncertain and proposal.confidence == Confidence.UNCERTAIN:
            guess = engine.best_guess(proposal)
            if guess is None:
                unwritten = ("the level is unclear" if proposal.level in ("unclear", "collection", "genre")
                             or proposal.kind == KIND_GENRE else "no usable candidate")
            else:
                reasons = list(proposal.reasons)
                proposal = engine.rebuild(proposal, candidate=guess)
                proposal.reasons = [f"best guess from an UNCERTAIN match: {guess.name} ({guess.source})", *reasons[:3]]
        proposals.append(proposal)
        if guess is not None and not proposal.writable:
            guess = None  # the rebuild did not give a note: it is reported like any other uncertain one
        if proposal.writable:
            result = write_proposal(proposal, dry_run=dry)
            results[proposal.path] = result
            if result.ok:
                status, message = ("would-write", ", ".join(proposal.sources)) if dry else ("written", proposal.path)
                if guess is not None:
                    guessed += 1
                    message += f"  [from an uncertain match: {guess.name} ({guess.source}) - review it]"
            elif result.status == "exists":
                status, message = "has-note", result.message
            else:
                status, message = "failed", result.message
                failed += 1
        elif proposal.confidence == Confidence.NONE:
            status = "no-match"
            message = "; ".join(proposal.reasons[:1])
            if proposal.problems:
                failed += 1
        else:
            status, message = "review", "uncertain: not written" + (f" ({unwritten})" if unwritten else "")
        out.record(_row(proposal, status, message, args.show_drafts))
        out.records[-1]["from_uncertain"] = guess is not None
        _print(out, out.records[-1])

    def progress(done: int, total: int, label: str) -> None:
        if label:
            out.progress(done + 1, total, label)

    try:
        build_proposals(found.targets, progress=progress, on_proposal=handle, engine=engine)
    except KeyboardInterrupt:
        cancelled = True
        out.warn("interrupted: the folders not yet looked at were left alone")

    summary: NotesSummary = summarize(proposals, results, cancelled=cancelled)
    summary_dict = summary.to_dict()
    summary_dict.update({"has_note": len(found.skipped), "groupings": len(found.groupings), "dry_run": dry, "failed": failed, "from_uncertain": guessed})
    out.line("")
    out.line(
        f"{summary.total} folder(s) looked up: {summary.confident} confident, {summary.uncertain} uncertain, "
        f"{summary.none} not found; {summary.written} written, {summary.would_write} would be written, "
        f"{len(found.skipped)} already had a note."
        + (f" {len(found.groupings)} alphabetical grouping folder(s) skipped." if found.groupings else "")
    )
    if guessed:
        out.line(f"{guessed} note(s) {'would be ' if dry else 'were '}written from an UNCERTAIN match (best guess, still "
                 "status: draft): look at them before you trust them.")
    left = summary.uncertain + summary.none
    if left > 0:
        out.line("Uncertain and unmatched folders were not written; review them (the candidates are listed above).")
    out.finish(summary_dict)
    if cancelled:
        return EXIT_INTERRUPTED
    return EXIT_PARTIAL if failed else EXIT_OK
