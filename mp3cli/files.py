"""
mp3cli/files.py

Finding and loading the MP3 files a command works on, and the app's own settings and log, read without
any window.
"""

from __future__ import annotations

import os
from pathlib import Path

from core.app_paths import base_dir
from core.mp3_file import MP3File
from core.scan_service import load_files
from core.settings import Settings, load_settings
from redactor_common.cli import CliError, Output, expand_paths
from redactor_common.core.rename_log import RenameLog

EXTENSIONS = (".mp3",)


def add_path_arguments(parser) -> None:
    parser.add_argument("paths", nargs="+", metavar="PATH", help="MP3 files, folders or wildcards")
    parser.add_argument(
        "-R", "--no-recurse", action="store_true", help="for a folder, look only at the files directly in it"
    )


def collect(paths: list[str], out: Output, recurse: bool = True, extensions=EXTENSIONS, noun: str = "MP3") -> list[str]:
    """The files the arguments name. An argument that matches nothing is an error; if that leaves no files at
    all the command ends with a usage error."""
    files, missing = expand_paths(paths, extensions, recursive=recurse)
    for argument in missing:
        out.error(f"nothing found for {argument}")
    if not files:
        raise CliError(f"no {noun} files found")
    return files


def load(files: list[str]) -> list[MP3File]:
    return load_files(Path(f) for f in files)


def settings() -> Settings:
    return load_settings()


def rename_log() -> RenameLog:
    """The same log File > Undo Last Rename reads, so a rename done here can be undone from the app."""
    return RenameLog(os.path.join(str(base_dir()), "mp3redactor_rename_log.json"))


def skip_reason(mp3: MP3File) -> str:
    return f"the file could not be read ({mp3.load_error})" if mp3.load_error else ""
