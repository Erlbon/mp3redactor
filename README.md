# The ɯP3 Redactor

Windows GUI utility (Python/PyQt6) for bulk-checking and bulk-editing
MP3 metadata. Sibling project to the EPUB and Video Redactors,
mp3tag-inspired UX.

## Status: v1 (integrity + BPM + key + basic tag editing + ffmpeg-based deep check/loudness/import + rename/parse-filename)

Roadmap, in build order:

1. **File integrity check** -- via `mp3val` (shelled out)
2. **BPM detection** -- via `aubio`'s Python bindings (in-process)
3. **Basic tag editing** -- Title/Artist/Album/Track/Year/Genre/Language,
   via a bulk-edit panel (`gui/tag_panel.py`) and `mutagen` (in-process,
   `core/tag_writer.py`) -- same mp3tag-style workflow as the epub
   tool's fuller tag panel: select rows, tick a field, type a value,
   Apply to the selection (undoable, `Ctrl+Z`), Save writes to disk.
   Genre and Language each get a quick-pick "+" button (curated
   defaults + custom entries, managed via Settings > Add/Remove
   Genres.../Add/Remove Languages...), and the table's columns are
   fully manageable -- drag a header to reorder, right-click for a
   show/hide checklist or Settings > Add/Remove Columns..., both
   persisted across restarts. No cover art, extended tags (composer,
   comment, ...), or external lookups yet -- those stay later roadmap
   items, same as the two below.
4. **Key detection** -- via `keyfinder-cli` (shelled out, `core/keyfinder_runner.py`).
   No prebuilt Windows binary exists upstream; see
   [Erlbon/keyfinder-cli-windows](https://github.com/Erlbon/keyfinder-cli-windows)
   for how it's built. "Detect Key" from the Operations menu or
   right-click, same as Check Integrity/Detect BPM -- the toolbar
   itself only carries the five everyday actions (Load Files, Load
   Folder, Save, Apply, Undo); Check Integrity/Detect BPM/Detect Key
   live in the Operations menu and right-click context menu only.
5. **ffmpeg/ffprobe-based analysis** (`core/ffmpeg_probe.py`) -- three
   more checks, same Operations-menu/right-click placement as the ones
   above:
   - **Deep Check Integrity** -- a real full decode (`ffmpeg -v error
     -i ... -f null -`), catching corrupt/truncated audio data
     mp3val's header-only scan can miss. Also grabs the actual
     encoder, sample rate, and channel count via `ffprobe` while it's
     at it (Encoder/Sample Rate/Channels columns, hidden by default --
     Settings > Add/Remove Columns... or right-click a header to show
     them).
   - **Measure Loudness** -- single-pass `loudnorm` measurement,
     written to the standard `TXXX:REPLAYGAIN_TRACK_GAIN` frame on
     Save (relative to ReplayGain 2.0's -18 LUFS reference), so any
     ReplayGain-aware player picks it up. Read back on load, same
     round-trip BPM/key get.
   - **Import & Convert to MP3** (Import menu) -- brings a non-MP3
     file (FLAC/WAV/OGG/M4A/...) into the library by converting it via
     `libmp3lame` (`core/mp3_converter.py`) at a chosen bitrate, then
     loads the resulting .mp3 alongside whatever's already loaded
     (additive, unlike Load Files/Folder's replace-wholesale).
6. **Rename/Export by Metadata Pattern, and its reverse, Parse Filename
   -> Metadata** -- via `redactor_common`'s generic
   `gui/rename_pattern_dialog.py` / `gui/parse_filename_dialog.py`
   (built on `core/rename_pattern.py` / `core/filename_parser.py`), the
   same modules epubredactor/cbzredactor already use; this project just
   hadn't wired them in yet.
   - **Rename / Export Files...** (File menu, `F2`) -- build a filename
     from a `%field%` pattern (Title/Artist/Album/Track/Year/Genre/
     Language), preview it per selected file, then rename in place or
     export renamed copies to a folder, originals untouched. Track gets
     an optional zero-pad-to-2-digits checkbox.
   - **Parse Filename...** (Import menu, `F3`) -- the reverse: extract
     field values back out of a filename using the same pattern syntax,
     preview per file, apply the accepted ones via the same
     `MP3File.apply_tags()` bulk-edit path (undoable, `Ctrl+Z`, dirty-
     highlighted, same as typing into the tag panel).
   - Both dialogs share one pattern history (`core/settings.py`'s
     `pattern_history`) and default to `%track% - %artist% - %title%`.
   - **Quick single-file rename**: double-click a Filename cell, or
     right-click a single selected file > Rename File..., to fix a
     typo directly without the pattern-based tool above -- built on
     `redactor_common.core.rename_pattern.rename_file_on_disk()`,
     already generic enough to need no project-specific wrapper.
7. Cover art check/add/replace -- via `mutagen` (in-process) -- not yet built
8. Lyrics fetch + write -- via `lyricy` (fetch) + `mutagen` (write) -- not yet built
9. Duplicate detection via audio fingerprinting -- ffmpeg's bundled
   `chromaprint` support could back this; not yet built, suggested as
   a later addition

## Tooling decisions

- **mutagen only** for all tag reading/writing -- no eyeD3, to keep a
  single tag-writing code path (mirrors the epub tool's single-source-
  of-truth approach to metadata).
- **aubio's Python bindings**, not its CLI -- called in-process rather
  than shelled out and parsed, since real bindings exist. Installed via
  the `aubio-ledfx` PyPI package rather than plain `aubio` -- the
  original package is source-only (no Windows wheels), so plain `aubio`
  requires MSVC Build Tools to build locally. `aubio-ledfx` is a fork
  that ships prebuilt Windows wheels; code still does `import aubio`
  unchanged.
- **mp3val**, **keyfinder-cli**, and **ffmpeg/ffprobe** are all shelled
  out to -- no Python bindings exist for mp3val/keyfinder-cli, and
  ffmpeg/ffprobe are used as real binaries (not a Python wrapper
  package) so the exact same tool this project already bundles for
  keyfinder-cli's dependency chain does double duty. All four need to
  be either bundled in `tools/` next to a frozen build, or present on
  PATH for dev-mode runs. See `core/tool_locator.py`.
- Every `subprocess.run()` call against ffmpeg/ffprobe passes
  `stdin=subprocess.DEVNULL` (`core/ffmpeg_probe.py`,
  `core/mp3_converter.py`) -- unlike mp3val/keyfinder-cli, ffmpeg can
  try to read stdin (interactive prompts, key-press handling) and hang
  forever if it inherits an unreadable/absent stdin handle, which a
  `--windowed` frozen app with no console can easily hand it. Found by
  hitting exactly this hang during testing, not a defensive guess.

## Building the .exe (Windows only)

PyInstaller can't cross-compile a Windows executable from another OS, so
this has to be built on Windows itself.

1. Get `mp3val.exe`, `keyfinder-cli.exe` (+ its 4 FFmpeg DLLs), and
   `ffmpeg.exe`/`ffprobe.exe` (+ their own, larger DLL set) into
   `tools\`:
   - `mp3val.exe`: download the official Windows binary and place it at
     `tools\mp3val.exe`.
   - `keyfinder-cli.exe`: **there is no prebuilt Windows binary upstream**
     -- it has to be compiled, which needs vcpkg + a ~120MB FFmpeg dev
     package. Rather than carrying that build setup (and its DLLs) in
     *this* repo, it lives in a separate one dedicated to it:
     [Erlbon/keyfinder-cli-windows](https://github.com/Erlbon/keyfinder-cli-windows).
     Download the `keyfinder-cli-<version>-windows.zip` bundle (exe + its
     4 FFmpeg DLLs together -- grabbing them as separate individual
     files from that release page's asset list is how you end up with
     the exe but not its DLLs, which fails at launch) from its
     [latest release](https://github.com/Erlbon/keyfinder-cli-windows/releases/latest)
     and extract all 5 files into `tools\`. That repo also
     has the build script, if `keyfinder-cli`/`libkeyfinder` ever need a
     newer version.
   - `ffmpeg.exe`/`ffprobe.exe`: download gyan.dev's "full" shared
     build -- `ffmpeg-release-full-shared.7z` from
     [gyan.dev/ffmpeg/builds](https://www.gyan.dev/ffmpeg/builds/) --
     and copy `bin\ffmpeg.exe`, `bin\ffprobe.exe`, and **all 7** DLLs
     from that same `bin\` folder (`avcodec-*.dll`, `avdevice-*.dll`,
     `avfilter-*.dll`, `avformat-*.dll`, `avutil-*.dll`,
     `swresample-*.dll`, `swscale-*.dll`) into `tools\`. This is a
     wider dependency set than keyfinder-cli.exe's narrower 4 --
     `ffmpeg.exe` itself links against the full filter/device stack
     (needed for the `loudnorm` filter Measure Loudness uses) even
     though this app only ever asks it to do simple audio-in,
     audio-out work. Unmodified, off-the-shelf download, no build step
     or wrapper repo needed the way keyfinder-cli has -- see the
     Licensing note below for why it's still GPLv3 even so.
   (The build still works without a `tools\` folder at all -- Check
   Integrity/Detect Key/Deep Check/Measure Loudness/Import & Convert
   will just report TOOL MISSING until the relevant binaries are
   added.)
2. Optionally bump the version first:
   ```
   python bump_version.py
   ```
3. Run:
   ```
   build_exe.bat
   ```
   This checks for Python, installs dependencies (including PyInstaller
   itself), builds a single-file windowed exe (with `assets/icon.ico` as
   its icon), and copies `tools\` alongside it in `dist\`. Every step is
   checked and stops with a clear message on failure rather than
   continuing to a false "Done."

Result: `dist\mp3redactor.exe` (+ `dist\tools\` if present) -- copy both
anywhere and run, no Python install needed on the target machine.

`mp3val.exe`/`keyfinder-cli.exe`/`ffmpeg.exe`/`ffprobe.exe` are
shelled-out binaries, not bundled data assets -- they're copied to sit
next to the built exe rather than packed inside it, matching where
`core/tool_locator.py` looks for them. If any of them isn't on PATH or
bundled in `tools\`, point directly at it via Settings > Locate
External Tools in the app itself.

### Licensing note on the bundled tools

`keyfinder-cli.exe`, `ffmpeg.exe`, `ffprobe.exe`, and every DLL in
`tools\` are GPLv3 (`keyfinder-cli`, `libkeyfinder`, and this
particular FFmpeg build are each GPLv3 -- see `tools\NOTICE.txt` for
the per-binary breakdown once they're in place). mp3redactor invokes
each of them as a separate process (command-line args + stdout/stderr,
never linked into mp3redactor.exe itself), so this doesn't affect
mp3redactor's own licensing -- but if you distribute a build that
bundles these binaries (e.g. as a zip alongside `dist\mp3redactor.exe`,
which is how this project's own GitHub Releases do it), GPLv3 requires
the license text and source pointers to travel with them. `tools\LICENSE.txt`
(the keyfinder-cli-windows release bundle's GPLv3 text -- the same
license also covers the ffmpeg/ffprobe binaries, sourced directly from
gyan.dev rather than through that repo) and `tools\NOTICE.txt` exist
for exactly that -- keep them in `tools\` alongside the binaries
(`build_exe.bat`'s `xcopy` already carries the whole folder, text
files included, into `dist\tools\`) rather than distributing the
binaries on their own.

## Setup (dev mode)

```
pip install -r requirements.txt
python main.py
```

## Tests

```
pytest
```

Core logic (mp3val output parsing, BPM fallback math, file discovery)
is unit-tested with mocked subprocess/aubio calls. The ffmpeg/ffprobe/
mp3_converter tests additionally exercise the real bundled binaries
when `tools\` has them (auto-skipped otherwise, e.g. a fresh clone
that hasn't placed them there yet) -- a real process exercises the
actual argument list and stdout/stderr parsing far more convincingly
than a mock. The GUI layer, like the sibling projects, isn't visually
testable in an automated way -- only syntax-checked and code-reviewed.
