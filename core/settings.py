"""
Persisted app settings. Deliberately built on configparser rather than
QSettings so this stays testable without PyQt6 installed, same as every
other module under core/ -- only gui/ should need Qt at all.

Stored as an .ini file next to the app (core.app_paths.base_dir()),
matching the epub tool's Registry->ini migration (v31) -- ini survives
version upgrades and installer reruns in a way the Windows Registry
didn't for that project.
"""

import configparser
from dataclasses import dataclass
from pathlib import Path

from core.app_paths import base_dir

SETTINGS_FILENAME = "settings.ini"
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

    def to_config(self) -> configparser.ConfigParser:
        config = configparser.ConfigParser()
        config[SECTION] = {
            "delete_backup_after_fix": str(self.delete_backup_after_fix),
            "mp3val_path": self.mp3val_path,
            "keyfinder_cli_path": self.keyfinder_cli_path,
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
