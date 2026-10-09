"""
mp3cli/cmd_redact.py

  redact PATH...   run the Redact recipe on the MP3 files: the same steps as Edit > Redact in the app
                   (integrity, BPM, key, loudness, deep check, tags from the path, lookups on MusicBrainz and
                   Discogs, cover, rename, move), saved in place with each original in the Recycle Bin (or
                   moved to --trash-dir).

The recipe is the one saved in the app (Edit > Edit Redact Recipe) unless --recipe FILE names a JSON recipe;
--enable / --disable / --threshold adjust it for this run only. The Discogs token comes from the
DISCOGS_TOKEN environment variable, else the app's saved one. Running the recipe and the report are the
shared ones in redactor_common.cli.commands.
"""

from __future__ import annotations

import argparse

from core import redact_steps
from core.redact_steps import (
    FINALIZE_LABEL, Mp3Ctx, RedactEnv, build_catalogue, recipe_from_setting, save_stage,
)
from redactor_common.cli import CliError, Output, add_common_options
from redactor_common.cli.commands import (
    add_redact_options, build_recipe, list_steps, read_recipe_file, redact_items, trash_to, trash_with_retries,
)

from mp3cli.files import collect, load, settings


def add_redact_parser(sub) -> None:
    parser = sub.add_parser(
        "redact", help="run the Redact recipe",
        description="Run the Redact recipe on the MP3 files. Each file is saved in place and its original goes to the "
                    "Recycle Bin (or --trash-dir). Guesses below the confidence threshold are listed, not applied.",
    )
    parser.add_argument("paths", nargs="*", metavar="PATH", help="MP3 files, folders or wildcards")
    parser.add_argument("-R", "--no-recurse", action="store_true", help="for a folder, look only at the files directly in it")
    add_redact_options(parser)
    add_common_options(parser)
    parser.set_defaults(handler=run_redact)


def run_redact(args: argparse.Namespace, out: Output) -> int:
    app_settings = settings()
    catalogue = build_catalogue(app_settings)
    text = read_recipe_file(args.recipe) if args.recipe else app_settings.redact_recipe
    recipe = build_recipe(args, recipe_from_setting(text, catalogue), catalogue)
    if args.list_steps:
        return list_steps(recipe, catalogue, out)
    if not args.paths:
        raise CliError("give the MP3 files to redact (or --list-steps)")

    files = load(collect(args.paths, out, recurse=not args.no_recurse))
    env = RedactEnv(
        app_settings, rename_log=None, trash=trash_to(args.trash_dir) if args.trash_dir else trash_with_retries(lambda path: redact_steps.move_to_trash(path)),
    )
    env.begin(files)
    return redact_items(
        files, recipe, catalogue, make_context=lambda mp3: Mp3Ctx(mp3, env), describe=lambda mp3: mp3.filename,
        finalize=save_stage, finalize_label=FINALIZE_LABEL, path_of=lambda mp3: str(mp3.path), out=out,
    )
