# Changelog

## 2026-09-06#05

- **Column management + Genre/Language quick-pick + management
  dialogs** -- the "columns/genres/languages" trio every other
  Redactor-family app already has, built on redactor_common the same
  way (`core/table_settings`, `gui/column_menu`,
  `gui/column_settings_dialog`, `gui/quick_pick_dialog`,
  `gui/manage_list_dialog`):
  - The table's columns are now field-key based, not fixed-index --
    drag a header to reorder, right-click for a show/hide checklist or
    "Add/Remove Columns...", both persisted across restarts
    (`core/settings.py` gained `hidden_columns`/`column_order`/
    `has_column_preference`). Nothing hidden by default -- matches
    this app's existing behavior before column management existed.
  - New **Language** field (ID3 `TLAN`, ISO 639-2 codes --
    `core/mp3_languages.py`), alongside the existing six.
  - Genre and Language each get a "+" quick-pick button next to their
    bulk-edit field: a searchable popup (not a flat menu -- doesn't
    overflow once enough custom entries pile up) over a curated
    default list (`core/mp3_genres.py`'s `COMMON_MP3_GENRES` -- the
    standard ID3v1 genre list; `core/mp3_languages.py`'s
    `DEFAULT_LANGUAGES` -- ~18 common ISO 639-2 codes) plus custom
    entries, individually hideable/restorable via Settings >
    Add/Remove Genres.../Add/Remove Languages.... Picking a value
    replaces the field -- unlike epub's/cbz's semicolon/comma-joined
    multi-value Genre, MP3's TCON is conventionally single-valued in
    practice and nothing else here treats it as a delimited list.
  - Hiding a column also hides that field's row in the bulk-edit panel
    (`TagPanel.set_visible_fields()`, new) and vice versa -- same
    lock-step convention as epub/cbz.
- New tests: `test_mp3_genres.py`, `test_mp3_languages.py`, plus
  expanded `test_settings.py` coverage for the new persisted fields.
  Verified end-to-end via real Qt-driven scenarios (not just unit
  tests): quick-pick a genre and language, Apply, Save, confirm the
  actual `TCON`/`TLAN` frames on disk via mutagen; hide/reorder columns
  and confirm both the panel sync and cross-restart persistence via a
  fresh `load_settings()` call.

## 2026-09-06#04

- Load Files/Load Folder now remember the last directory used and
  start there next time, across app restarts -- previously always
  opened wherever Qt/Windows defaulted to. `core/settings.py` gained
  `last_directory` (persisted the same way `mp3val_path`/
  `keyfinder_cli_path` already are) plus two small pure helpers,
  `resolve_start_directory()` (falls back to "" -- Qt's own default --
  if the remembered directory no longer exists, e.g. an unplugged
  removable drive) and `directory_for()` (the directory to remember
  from whatever was just picked, a file or a folder itself). Same
  semantics as the epub tool's QSettings-based equivalent, adapted to
  this project's plain-configparser persistence.

## 2026-09-06#03

- **Detect BPM now runs across multiple cores in parallel**, same as
  Detect Key already did -- was fully sequential (one file at a time)
  despite aubio's decode+tempo-detection loop being a C extension that
  releases the GIL, confirmed by measurement (~2.8x speedup on an
  8-worker/12-file benchmark of a few-second clips; longer, real-world
  tracks should do better still, since aubio's fixed per-file setup
  overhead shrinks as a fraction of the total). `core/scan_service.
  run_bpm_check()` now dispatches the whole selection to a
  `ThreadPoolExecutor`, same shape as `run_key_detection()`.
  `MainWindow`'s two now share one `_run_concurrent_check_with_progress()`
  helper instead of duplicating the progress-dialog-driving logic twice.
- Fixed a pre-existing, already-failing test found while working on the
  above (unrelated to the parallelism change itself):
  `tests/test_bpm_detector.py`'s tool-missing test relied on aubio
  genuinely not being installed in the sandbox to exercise the
  `ImportError` branch. aubio is now actually installed here, so it was
  taking a different code path entirely (a real file-not-found error
  inside the broad `except`, landing on `STATUS_ERROR` instead of the
  `STATUS_TOOL_MISSING` it asserted) -- no longer testing what it
  claimed to. Now forces the `ImportError` via
  `sys.modules["aubio"] = None` regardless of whether the real package
  happens to be installed.
- New test: `test_scan_service_bpm_check.py`.

## 2026-09-06#02

- Fixed the gap between every Bulk Edit Tags field visibly growing as
  the window is resized taller -- the fields grid had no row stretch
  set anywhere, so Qt spread the extra vertical space evenly into
  every row's gap instead of leaving it as blank space below the last
  field. Fix lives in `redactor_common` (bumped to `2026-09-06-03`) so
  the same latent issue in epub's identically-structured grid gets it
  too.

## 2026-09-06#01

- Bumped `redactor_common` to `2026-09-06-02` -- fixes a stray leading
  comma in the About dialog ("`, ver 2026-...`") caused by this
  project's empty `RELEASE_LABEL`.

## 2026-09-04#11

- `redactor_common` is now a real pip dependency
  ([Erlbon/redactor_common](https://github.com/Erlbon/redactor_common),
  pinned to tag `2026-09-04-10` in `requirements.txt`) instead of a
  vendored copy under `redactor_common/`. This is the actual fix for
  what #10's setWindowModality crash exposed: three projects each
  hand-copying the same files meant a fix in one place didn't reach
  the other two without three separate manual resyncs -- one canonical
  source now, bumped via a deliberate one-line `requirements.txt` diff
  instead. Import paths are unchanged (`from redactor_common.gui...`
  still works, just resolves from site-packages now). No code changes
  needed here beyond removing the vendored folder.

## 2026-09-04#10

- **Detect Key now runs across multiple cores in parallel** instead of
  one file at a time -- each keyfinder-cli invocation is a genuinely
  separate OS process, and it's by far the slowest of the three checks
  (full decode + FFT per file), so this is where parallelism actually
  pays off. `core/scan_service.run_key_detection()` now dispatches the
  whole selection to a `ThreadPoolExecutor` (default: one worker per
  core, capped at the number of files) instead of being called once per
  file the way the other checks are; `MainWindow.run_key_detection()`
  was rewritten to match (drives its progress dialog from
  scan_service's own progress/should_cancel callbacks rather than
  `_run_check_with_progress`'s one-item-at-a-time loop, which would
  have silently defeated the whole point). Verified with 12 real files
  against the actual keyfinder-cli.exe binary: ~7.6x wall-clock speedup
  on this machine, all results correct.
- **Fixed a real, live crash** (found while testing the above with 3+
  files, not caused by it): `redactor_common/gui/progress.py`'s
  `run_with_progress()` called `dialog.setWindowModality(True)` -- a
  bare bool, not the `Qt.WindowModality` enum PyQt6 actually requires
  -- which raised `TypeError` in this PyQt6 version the moment the
  progress dialog was ever actually shown (3+ items). This affected
  every check in this app (Integrity/BPM/Key), not just the new
  parallel path; it just happened to surface here first since it's what
  I was testing with a bigger batch. Fixed upstream in
  [Erlbon/redactor_common](https://github.com/Erlbon/redactor_common)
  and re-synced into this project's vendored copy
  (`REDACTOR_COMMON_VERSION` bumped to `2026-09-04#09`); flagged for
  the epub/video tools too, since they vendor the same file.
- New test: `test_scan_service_key_detection.py`.

## 2026-09-04#09

- Added **key detection** -- via `keyfinder-cli` (shelled out,
  `core/keyfinder_runner.py`; see
  [Erlbon/keyfinder-cli-windows](https://github.com/Erlbon/keyfinder-cli-windows)
  for how the Windows binary is built, since none exists upstream).
  "Detect Key" from the Operations menu, its toolbar button, or
  right-click -- same three places Check Integrity/Detect BPM already
  live. New Key column in the table (`MP3File.key_status/key_value/
  key_message` were already reserved fields, now actually populated).
  A silent file (genuinely no key) is reported OK with an explanatory
  tooltip, not as an error.
- Fixed `core.scan_service.save_dirty_tags()`: its docstring already
  claimed non-dirty files are skipped, but the code saved every file it
  was given regardless -- harmless today (the only caller already
  pre-filters to dirty files) but now actually does what it says.
- New test: `test_keyfinder_runner.py`.

## 2026-09-04#08

- Added **basic bulk tag editing** -- Title/Artist/Album/Track/Year/Genre
  -- the mp3tag-style workflow every sibling Redactor project uses,
  built on the same `redactor_common` pieces the epub tool's tag panel
  uses (`collapsible_splitter`, `progress`, `error_summary`,
  `action_factory`):
  - `core/fields.py`: single source of truth for the editable fields,
    same pattern as the epub tool's (trimmed down -- no covers,
    genre/language pickers, or external lookups yet).
  - `core/tag_writer.py`: writes tags back via mutagen (the write
    counterpart to `core/tag_reader.py`'s read side, same ID3 frames).
    Blanking a field and saving removes that frame entirely rather than
    writing it empty -- how you clear a tag.
  - `MP3File.apply_tags()` / `.dirty` (`core/mp3_file.py`): in-memory
    edit + dirty tracking, mirroring the epub tool's
    `EpubBook.apply_metadata()`/`.dirty`.
  - `gui/tag_panel.py`: the bulk-edit panel itself -- select rows, tick
    a field (or just start typing -- that ticks it too), Apply to the
    selection. Shows "<multiple values>" (scroll to cycle through and
    pick one) when the selection disagrees on a field.
  - `gui/main_window.py`: panel lives in a collapsible splitter next to
    the file table (Panel toolbar button to minimize/restore); table
    gained Track/Year/Genre columns so applied edits are visible, not
    just Title/Artist/Album; dirty rows highlighted amber; **Save Tags**
    (Ctrl+S, File menu) writes every dirty file to disk, reporting any
    per-file failures; confirms before discarding unsaved edits (Load
    Files/Folder, window close).
  - New tests: `test_mp3_file.py`, `test_tag_writer.py` (the latter uses
    a real tiny MP3 fixture, `tests/fixtures/tiny.mp3`, round-tripping
    through actual mutagen rather than mocking it).

## 2026-09-04#07

- **Check File Integrity** and **Detect BPM** now operate on the table's
  current **selection only**, matching Fix's existing behavior, instead
  of silently running across every loaded file. Menu/toolbar/context-menu
  labels reworded ("...Selected Files...") to make that explicit.
- The "nothing to run on" message now distinguishes no files loaded
  ("Load some files first") from files loaded but none selected
  ("Select one or more files in the table").
- `keyfinder-cli.exe` build/distribution moved out to its own repo:
  [Erlbon/keyfinder-cli-windows](https://github.com/Erlbon/keyfinder-cli-windows)
  (there's no prebuilt Windows binary upstream, and the build needs
  vcpkg + a ~120MB FFmpeg dev package -- keeping that out of this repo).
  v1.2.0 is published there as a GitHub Release with `keyfinder-cli.exe`
  + its 4 DLLs attached. README's build step 1 updated accordingly.

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
