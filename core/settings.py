"""
Persisted app settings. Deliberately built on configparser rather than
QSettings so this stays testable without PyQt6 installed, same as every
other module under core/ -- only gui/ should need Qt at all.

Stored as an .ini file next to the app (core.app_paths.base_dir()),
matching the epub tool's Registry->ini migration (v31) -- ini survives
version upgrades and installer reruns in a way the Windows Registry
didn't for that project.

Filename is app-prefixed ("mp3redactor_settings.ini"), not a bare
"settings.ini" -- the whole Redactor family writes its ini file to the
same "next to the exe" location, and a generic name would collide the
moment two of these exes (e.g. this one and videoredactor's) end up in
the same folder, silently corrupting whichever one wrote last.

List/tuple-list fields (hidden_columns, column_order, custom_genres,
etc.) are JSON-encoded into a single ini value -- same technique the
epub/cbz tools' QSettings-based equivalents use, just persisted via
configparser instead of QSettings.setValue()/.value(). Unlike those
tools, this project loads/saves the *whole* Settings object as one
unit rather than exposing a load_X()/save_X() function per setting --
callers mutate a field on their live self.settings instance and call
save_settings(self.settings), same pattern already used for
mp3val_path/keyfinder_cli_path.
"""

import configparser
import json
from dataclasses import dataclass, field
from pathlib import Path

from core.app_paths import base_dir

SETTINGS_FILENAME = "mp3redactor_settings.ini"
SECTION = "general"


def _dump_list(value: list) -> str:
    return json.dumps(value)


def _load_list(raw: str) -> list:
    if not raw:
        return []
    try:
        loaded = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return []
    return loaded if isinstance(loaded, list) else []


def _load_str_list(raw: str) -> list[str]:
    return [v for v in _load_list(raw) if isinstance(v, str)]


def _load_pair_list(raw: str) -> list[tuple[str, str]]:
    """For custom_languages: a JSON list of [code, name] pairs, loaded
    back as (code, name) tuples. A malformed entry is skipped rather
    than failing the whole list -- a hand-edited or corrupted ini value
    for one entry shouldn't cost every other saved language."""
    result = []
    for item in _load_list(raw):
        if isinstance(item, list) and len(item) == 2 and all(isinstance(x, str) for x in item):
            result.append((item[0], item[1]))
    return result


@dataclass
class Settings:
    # When fixing file-integrity issues (mp3val -f), mp3val always
    # leaves a .bak copy of the original behind unless -nb is passed.
    # Defaults OFF (i.e. backups are kept) -- the safer choice, since
    # fixing mutates the file on disk. Users can opt into auto-deleting
    # backups via the Settings dialog.
    delete_backup_after_fix: bool = False

    # Manual overrides for external tool locations, set via Settings >
    # Locate External Tools. Empty string means "auto-detect" (bundled
    # tools/ dir, then PATH -- see core.tool_locator.find_tool()), same
    # convention the video tool's equivalent dialog uses for
    # ffmpeg/MKVToolNix.
    mp3val_path: str = ""
    keyfinder_cli_path: str = ""
    ffmpeg_path: str = ""
    ffprobe_path: str = ""

    # Where Load Files/Load Folder's file dialogs start from -- set via
    # remember_last_directory() after a successful pick. Empty string
    # means "let Qt use its own default" (same convention the epub
    # tool's QSettings-based last_directory uses, adapted to this
    # project's plain-configparser persistence).
    last_directory: str = ""

    # Column visibility/order, field-key based -- see
    # redactor_common.core.table_settings for why field-name (not
    # index) based persistence matters.
    hidden_columns: list[str] = field(default_factory=list)
    column_order: list[str] = field(default_factory=list)
    # True once the user has ever saved a hidden-columns choice --
    # including an explicit "show everything" (an empty list is still
    # a saved choice). Distinct from hidden_columns == [], which is
    # ambiguous between "never configured" and "deliberately show
    # everything" on its own; a first-ever run applies
    # gui.main_window.DEFAULT_HIDDEN_COLUMNS instead of showing every
    # column immediately, same convention cbzredactor's
    # has_hidden_columns_preference() uses.
    has_column_preference: bool = False

    # Genre/Language quick-pick ("+" button next to those two fields in
    # the bulk-edit panel): built-in defaults (individually hideable/
    # restorable via Settings > Add/Remove Genres.../Languages...) plus
    # any custom entries added the same way. Same shape as the epub/cbz
    # tools' equivalents -- see core/mp3_genres.py, core/mp3_languages.py.
    custom_genres: list[str] = field(default_factory=list)
    hidden_default_genres: list[str] = field(default_factory=list)
    custom_languages: list[tuple[str, str]] = field(default_factory=list)
    hidden_default_languages: list[str] = field(default_factory=list)

    def to_config(self) -> configparser.ConfigParser:
        config = configparser.ConfigParser()
        config[SECTION] = {
            "delete_backup_after_fix": str(self.delete_backup_after_fix),
            "mp3val_path": self.mp3val_path,
            "keyfinder_cli_path": self.keyfinder_cli_path,
            "ffmpeg_path": self.ffmpeg_path,
            "ffprobe_path": self.ffprobe_path,
            "last_directory": self.last_directory,
            "hidden_columns": _dump_list(self.hidden_columns),
            "column_order": _dump_list(self.column_order),
            "has_column_preference": str(self.has_column_preference),
            "custom_genres": _dump_list(self.custom_genres),
            "hidden_default_genres": _dump_list(self.hidden_default_genres),
            "custom_languages": _dump_list([list(pair) for pair in self.custom_languages]),
            "hidden_default_languages": _dump_list(self.hidden_default_languages),
        }
        return config

    @classmethod
    def from_config(cls, config: configparser.ConfigParser) -> "Settings":
        if SECTION not in config:
            return cls()
        section = config[SECTION]
        return cls(
            delete_backup_after_fix=section.getboolean("delete_backup_after_fix", fallback=False),
            mp3val_path=section.get("mp3val_path", fallback=""),
            keyfinder_cli_path=section.get("keyfinder_cli_path", fallback=""),
            ffmpeg_path=section.get("ffmpeg_path", fallback=""),
            ffprobe_path=section.get("ffprobe_path", fallback=""),
            last_directory=section.get("last_directory", fallback=""),
            hidden_columns=_load_str_list(section.get("hidden_columns", fallback="")),
            column_order=_load_str_list(section.get("column_order", fallback="")),
            has_column_preference=section.getboolean("has_column_preference", fallback=False),
            custom_genres=_load_str_list(section.get("custom_genres", fallback="")),
            hidden_default_genres=_load_str_list(section.get("hidden_default_genres", fallback="")),
            custom_languages=_load_pair_list(section.get("custom_languages", fallback="")),
            hidden_default_languages=_load_str_list(section.get("hidden_default_languages", fallback="")),
        )


def _settings_path(target_dir: Path | None = None) -> Path:
    d = target_dir if target_dir is not None else base_dir()
    return d / SETTINGS_FILENAME


def load_settings(target_dir: Path | None = None) -> Settings:
    """Returns defaults (all off) if no settings file exists yet, or it can't be read/parsed."""
    path = _settings_path(target_dir)
    config = configparser.ConfigParser()
    try:
        if path.exists():
            config.read(path, encoding="utf-8")
    except (OSError, configparser.Error):
        return Settings()
    return Settings.from_config(config)


def save_settings(settings: Settings, target_dir: Path | None = None) -> None:
    path = _settings_path(target_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            settings.to_config().write(f)
    except OSError:
        pass  # settings persistence is best-effort; in-memory value still applies this session


def resolve_start_directory(last_directory: str) -> str:
    """Returns last_directory if it's still a real directory, else "" --
    e.g. a removable drive that's since been unplugged, or a settings
    file carried over from another machine. Callers should treat ""
    as "let Qt use its own default" for a file dialog's starting
    location, same semantics as the epub tool's QSettings-based
    load_last_directory()."""
    return last_directory if last_directory and Path(last_directory).is_dir() else ""


def directory_for(path: str) -> str:
    """The directory to remember as the next dialog's starting point,
    given a path just picked -- a file (from Load Files) or a folder
    itself (from Load Folder)."""
    p = Path(path)
    return str(p if p.is_dir() else p.parent)
