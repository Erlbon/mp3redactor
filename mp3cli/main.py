"""
mp3cli/main.py

The mp3redactor command line, run through the app's own exe (main.py dispatches here when its first
argument is a command; the window never starts):

    mp3redactor info    PATH...                      what the files are
    mp3redactor set     PATH... -s Artist=Queen      change tag fields
    mp3redactor rename  PATH... -p "%track% - %title%"
    mp3redactor move    PATH... -p "%albumartist%/%album%/..." --root LIBRARY
    mp3redactor convert PATH...                      FLAC/WAV/OGG/M4A/... -> MP3
    mp3redactor redact  PATH...                      run the saved Redact recipe
    mp3redactor analyze PATH... [--deep --bpm ...]   integrity, decode, BPM, key, loudness
    mp3redactor m4b     PATH...                      chaptered M4B audiobook per folder

Every command takes --json (one JSON document), --quiet and --output FILE (the result goes to a file: the
reliable way to read it from a script, since a windowed exe cannot be waited for by an interactive shell),
and the ones that change files take --dry-run. Exit codes: 0 done, 1 some files failed or have problems,
2 bad arguments, 70 internal error, 130 interrupted. It reads the same settings file as the app.
"""

from __future__ import annotations

from core.version import APP_VERSION
from redactor_common.cli.main_factory import make_main

from mp3cli import COMMANDS, cmd_analyze, cmd_files, cmd_m4b, cmd_redact, cmd_tags

PROG = "mp3redactor"

# redactor_common's make_main: --version, the subcommands in this order (checked
# against COMMANDS), --json/--quiet/--output via make_output, Output always closed.
build_parser, main = make_main(
    PROG, APP_VERSION,
    "The MP3 Redactor on the command line: inspect, tag, rename, convert, analyze and bind MP3 files.",
    [
        cmd_tags.add_info_parser,
        cmd_tags.add_set_parser,
        cmd_files.add_rename_parser,
        cmd_files.add_move_parser,
        cmd_files.add_convert_parser,
        cmd_redact.add_redact_parser,
        cmd_analyze.add_analyze_parser,
        cmd_m4b.add_m4b_parser,
    ],
    commands=COMMANDS,
)
