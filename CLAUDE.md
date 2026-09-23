# mp3redactor

PyQt6 Windows desktop tool for bulk-checking and bulk-editing MP3 metadata, mp3tag-style. Part of the Redactor family; shared code lives in [redactor_common](https://github.com/Erlbon/redactor_common), pinned by tag in `requirements.txt`.

## Commands
- Run: `pip install -r requirements.txt` then `python main.py`
- Test: `pytest` (tests in `tests/`)
- Build exe: `build_exe.bat` (`mp3redactor.spec`). Releases deliberately do not bundle the external tools.
- Bump version: `python bump_version.py`

## Layout
`core/` (`tag_writer.py`, `ffmpeg_probe.py`, `mp3_converter.py`, `keyfinder_runner.py`), `gui/` (`tag_panel.py`), `tools/`, `main.py`, `CHANGELOG.md`.

## Domain notes
- Integrity check: `mp3val` (shelled out). Deep check and loudness: ffmpeg/ffprobe. Key detection: `keyfinder-cli` (no upstream Windows binary; see [keyfinder-cli-windows](https://github.com/Erlbon/keyfinder-cli-windows)). BPM: `aubio` bindings in-process (the `aubio-ledfx` fork; building locally needs MSVC Build Tools). Tags: `mutagen` in-process.
- Every `subprocess.run()` against ffmpeg/ffprobe must pass `stdin=subprocess.DEVNULL`, or ffmpeg can hang waiting on stdin.
- Table sorting is numeric for Track, Disc, Year, BPM, Loudness, Sample Rate, Channels (`redactor_common.gui.sortable_table`).
- Cover art (roadmap item 8) shipped 2026-09-23: `core/cover_art.py`. Covers are never kept in memory for every file -- `has_cover` is read on load and the image is read from disk on demand, off the GUI thread (lazy table thumbnails, async panel preview); only a pending change lives in `MP3File` until Save. Lyrics fetch shipped as roadmap item 7.
## Family conventions (apply to every Redactor repo)
1. **Sync first.** Other Claude sessions, sometimes on other machines, edit these repos concurrently. Before editing, and again right before every `git push`: `git fetch origin -q; git status --porcelain -b`. Confirm you match origin and the tree is clean. Fast-forward if behind; resolve if diverged.
2. **Version bump at check-in.** Any commit that changes real code must, in the same check-in, run `python bump_version.py` and add a matching `CHANGELOG.md` entry, before pushing, without being asked. This was missed twice on epubredactor (2026-09-14, 2026-09-17). `release.ps1` does not bump versions for you.
3. **Progress feedback for any per-book loop.** Any loop over more than a handful of books, including a dialog's preview or scan step, must go through `redactor_common.gui.progress.run_with_progress()`. `cancellable=False` for read-only scans, `True` for applies that mutate. Only exception: a live-typing preview (search/replace, rename pattern) whose per-item work is cheap pure string logic, confirmed by a timing check at large N.
4. **Promote to redactor_common first.** A fix or feature useful to more than one app is built and verified in `redactor_common`, version-bumped, tagged, and pushed. Then each app bumps its `requirements.txt` pin plus its own `APP_VERSION` and `CHANGELOG.md`. Flag a stale pin rather than ignoring it.
5. **Landing page honesty.** erlbon.github.io's Formats table must match the app's real menu actions and file-picker filters, not what the underlying library could theoretically do.
6. **Cross-platform goal.** A Linux/Mac port is planned. Avoid new unguarded Windows-only code (registry, hardcoded `C:\` paths, Windows APIs without a `sys.platform` guard). Follow the existing patterns: PATH-based tool lookup (`redactor_common/core/tool_locator.py`), `QSettings` with `IniFormat` and an explicit path, `sys.platform == "win32"` guards.
7. **PowerShell 5.1 pitfall in release scripts.** Under `$ErrorActionPreference = "Stop"`, a native command's stderr becomes a terminating error. Redirect only stdout (`cmd | Out-Null`), never `2>&1`, and reset `$LASTEXITCODE` after reading it. Also, the release script tags and pushes before `gh release create`, so "tag exists, release doesn't" is a resumable state; check with `gh release view <tag>`.

Full text of these rules: `.claude/skills/redactor-conventions/SKILL.md` in redactor_common.

## Machine-local, not in git
The release scripts (`release.ps1`, `release-<project>.ps1`, `release-all.ps1`) and a hand-placed `upx.exe` lived in a machine-local `_shared-tools` folder that is deliberately not in git. They are not in this repo and must be recreated or copied over by hand.

