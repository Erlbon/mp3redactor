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
"""

import configparser
from dataclasses import dataclass
from pathlib import Path

from core.app_paths import base_dir

SETTINGS_FILENAME = "mp3redactor_settings.ini"
SECTION = "general"


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

    # Where Load Files/Load Folder's file dialogs start from -- set via
    # remember_last_directory() after a successful pick. Empty string
    # means "let Qt use its own default" (same convention the epub
    # tool's QSettings-based last_directory uses, adapted to this
    # project's plain-configparser persistence).
    last_directory: str = ""

    def to_config(self) -> configparser.ConfigParser:
        config = configparser.ConfigParser()
        config[SECTION] = {
            "delete_backup_after_fix": str(self.delete_backup_after_fix),
            "mp3val_path": self.mp3val_path,
            "keyfinder_cli_path": self.keyfinder_cli_path,
            "last_directory": self.last_directory,
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
            last_directory=section.get("last_directory", fallback=""),
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
