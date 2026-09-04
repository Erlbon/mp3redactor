# Changelog

## 2026-09-04#06

- Added branding icon: `assets/icon.ico` / `icon.png`, turned-m glyph on
  a dark rounded square (matches "The \u026fP3 Redactor"). Set as the
  app/window icon (`main.py`, `gui/main_window.py`) and bundled into the
  build via PyInstaller's `--icon` flag.
- Fixed mp3val being able to pop up (or flash) its own console window
  even though this app is built `--windowed` -- same class of bug the
  epub tool hit with Calibre (v35). New `core/subprocess_utils.py`
  supplies Windows-only `CREATE_NO_WINDOW` kwargs, applied to every
  `subprocess.run()` call in `core/mp3val_runner.py`.
- Added a **Locate External Tools** dialog (`gui/external_tools_dialog.py`,
  Settings menu), mirroring the video tool's ffmpeg/MKVToolNix locator:
  per-tool Browse/Clear + live found/not-found indicator, auto-detect
  via PATH as the default. Covers mp3val now; keyfinder-cli's field is
  present ahead of key detection landing so the dialog doesn't need a
  second layout pass.
- `core/tool_locator.find_tool()` gained an `override` parameter (the
  user's manual path from that dialog) tried before the bundled-tools/
  PATH search; an override that doesn't exist is reported NOT FOUND
  rather than silently falling back, so the dialog's indicator stays
  honest. Threaded through `mp3val_runner` and `scan_service`'s
  integrity check/fix functions as `override_path`/`mp3val_path`.

## 2026-09-04#05

- Swapped `aubio` for `aubio-ledfx` in requirements.txt -- plain `aubio`
  is source-only on PyPI (no Windows wheels ever), so it always needed
  MSVC Build Tools to install on Windows. `aubio-ledfx` is a maintained
  fork shipping prebuilt Windows wheels (including for newer Python
  versions upstream has never built for). Same module name (`import
  aubio`), no code changes -- see `core/bpm_detector.py` and
  `README.md` for the provenance note (third-party fork, not the
  canonical aubio release).
- `build_exe.bat` ported from the working epub-tool script: step-by-step
  failure checks, `python -m PyInstaller`, no test run baked into the
  build. `mp3val.exe`/`keyfinder-cli.exe` copied into `dist\tools`
  after the PyInstaller build rather than bundled via `--add-data`,
  since `core/tool_locator.py` looks for them next to the exe, not in
  a onefile build's runtime-extracted temp folder.
- Added `bump_version.py` (same date/counter logic as the epub tool).
- `pytest` added to requirements.txt as an explicit dev dependency.

## 2026-09-04#03

- Added a **Settings** menu/dialog (`gui/settings_dialog.py`, backed by
  `core/settings.py`, persisted as `settings.ini` next to the app).
- New toggle: **delete .bak backup files after a successful fix** (mp3val's
  `-nb` flag) -- **off by default** (backups kept), opt-in via Settings.
  Threaded through `fix_integrity()` / `run_integrity_fix()`.
- The Fix confirmation dialog's wording now reflects whichever backup
  behavior is currently active.
- `core/settings.py` deliberately avoids Qt (configparser-based, `.ini`)
  so it stays unit-testable without PyQt6, same as the rest of `core/`.

## 2026-09-04#02

- Added file-integrity **fixing**, via `mp3val -f` (`core.mp3val_runner.fix_integrity`,
  `core.scan_service.run_integrity_fix`) -- a separate, deliberate action from
  Check, not folded into it, since it mutates files on disk.
- Fix operates on the table's current **selection only**, not all loaded
  files -- a targeted action, not a blanket one.
- Confirmation dialog before fixing, noting mp3val's automatic `.bak`
  backup and that not every issue is fixable (status can still come back
  WARNING/ERROR after a fix attempt).
- Available from the Checks menu and the table's right-click context menu.

## 2026-09-04#01

- Initial project scaffold.
- File table (Filename/Title/Artist/Album/Integrity/BPM) with row-to-file
  mapping via `Qt.UserRole`.
- Load Files / Load Folder (recursive, case-insensitive `.mp3` match, dedup).
- Tag reading via mutagen (title/artist/album/track/year/genre/duration/
  bitrate/cover-presence).
- File integrity check via `mp3val` (shelled out; reports OK/WARNING/ERROR,
  or TOOL MISSING when the binary isn't bundled or on PATH).
- BPM detection via `aubio`'s Python bindings (in-process; falls back to a
  median inter-beat-interval estimate if `get_bpm()` isn't reliable yet;
  reports TOOL MISSING when aubio isn't installed).
- Progress dialog for load/check operations.
- Crash logging (global excepthook + faulthandler), installed first thing
  in `main()`.
