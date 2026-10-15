# The ɯP3 Redactor

Windows GUI utility (Python/PyQt6) for bulk-checking and bulk-editing
MP3 metadata. Sibling project to the EPUB and Video Redactors,
mp3tag-inspired UX.

## What it does

The user-facing description (features, opening files, the full command line) is [ABOUT.md](ABOUT.md), which
the app also shows under Help > About. In short: bulk tag editing, integrity / deep check / BPM / key / loudness
checks, Find Duplicates, MusicBrainz / AcoustID / Discogs / lyrics lookups, covers, rename / move / parse
filename, Redact, conversion of other audio formats to MP3, and M4B audiobooks.

Where each part lives:

| Feature | Code |
| --- | --- |
| Integrity check and fix | `core/mp3val_runner.py` (mp3val, shelled out) |
| BPM | `core/bpm_detector.py` (aubio in-process, ffmpeg fallback) |
| Key | `core/keyfinder_runner.py` (keyfinder-cli; Windows build at [Erlbon/keyfinder-cli-windows](https://github.com/Erlbon/keyfinder-cli-windows)) |
| Deep check, loudness, format details | `core/ffmpeg_probe.py` (ffmpeg / ffprobe) |
| Tags, covers, lyrics on disk | `core/tag_reader.py`, `core/tag_writer.py`, `core/cover_art.py` (mutagen) |
| Find Duplicates | `core/mp3_duplicates.py` on redactor_common's duplicates dialog |
| MusicBrainz / AcoustID | `core/musicbrainz_lookup.py`, `core/acoustid_lookup.py`, offline database `core/musicbrainz_local.py` + `core/musicbrainz_import.py` |
| Discogs | `core/discogs_lookup.py` (token in the secure credential store) |
| Lyrics | `core/lyrics_fetcher.py` (LRCLIB via `lyricy`) |
| Convert to MP3 | `core/mp3_converter.py` (ffmpeg libmp3lame) |
| M4B audiobooks | `core/m4b_builder.py`, lookup `core/audiobook_lookup.py` |
| Redact steps | `core/redact_steps.py` on redactor_common's pipeline |
| Command line | `mp3cli/` (the same exe, see ABOUT.md) |
| Slow work off the GUI thread | redactor_common's `run_work_with_progress` / `run_with_progress` / `run_busy` |

## Menus and shortcuts

The menu bar is the family's standard skeleton (redactor_common's `gui/standard_menus.py`): File, Edit, View,
Metadata, Analyze, Tools, Help. Ctrl+K (View > Command Palette) finds any menu item by name. There are no
Ctrl+Alt shortcuts (AltGr types them on European keyboards).

| Menu | Item | Shortcut |
| --- | --- | --- |
| File | Open Files… | Ctrl+O |
| File | Open Folder… | Ctrl+Shift+O |
| File | Include Subfolders (ticked: Open Folder, drops and Refresh List search subfolders; also a checkbox on the toolbar next to Open Folder, both stay in step) | |
| File | Import and Convert… (FLAC/WAV/OGG/M4A/... to MP3 beside the original) | |
| File | Create M4B Audiobook… | |
| File | Play (plays the one selected file in a separate window; also a Play button on the toolbar and a large one under the cover in the side panel) | Ctrl+P |
| File | Save All | Ctrl+Shift+A |
| File | Rename File… (the one selected file) | F2 |
| File | Undo Last Rename | |
| File | Rename / Export / Move… (by `%field%` pattern) | Ctrl+E |
| File | Export Settings…, Import Settings… | |
| File | Remove from List, Clear List (nothing on disk changes) | Del (Remove) |
| File | Exit | Alt+F4 |
| Edit | Undo, Redo | Ctrl+Z, Ctrl+Y |
| Edit | Apply to N Selected (the side panel's ticked fields) | Ctrl+Return |
| Edit | Redact | Ctrl+Shift+E |
| Edit | Edit Redact Recipe… | |
| Edit | Search and Replace… | Ctrl+H |
| Edit | Change Case… | |
| Edit | Auto-Number… (Preset "Number Tracks": Track 1..N in table order, no padding) | |
| Edit | Filter List (show only files whose Filename, Title, Artist, Album or Path contains a text; empty text shows all) | Ctrl+F |
| View | Show Metadata Panel | |
| View | Zoom In, Zoom Out, Reset Zoom | Ctrl++, Ctrl+-, Ctrl+0 |
| View | Refresh List | F5, Ctrl+R |
| View | Command Palette… | Ctrl+K |
| Metadata | Parse Filename… (tags from file and folder names) | Ctrl+I |
| Metadata | Look Up > MusicBrainz…, MusicBrainz (Local Database)…, Discogs…, Lyrics | |
| Metadata | Edit Lyrics… | |
| Metadata | Cover > Set from Image File…, Set from Folder Image, Get from MusicBrainz…, Get from Discogs…, Remove Cover, Export Cover to Image File… | |
| Analyze | Check Integrity, Fix Integrity Issues…, Deep Check Integrity… | |
| Analyze | Detect BPM, Detect Key, Measure Loudness | |
| Analyze | Find Duplicates… (all loaded files) | |
| Tools | Create Notes… (draft the `artist.md` / `album.md` / `about.md` folder notes the Pluteus media server shows, from MusicBrainz, Wikipedia and Discogs; Tools > Create Notes always asks which folder first, as a short list (the folder you opened, the artist / album of the selected files, one level up, the last folder, the library folder, or Another folder…; the first is ticked so Enter continues; Include subfolders is the File > Include Subfolders setting), the right-click entry uses the album and artist folders of the selected files; for a whole library: pick the library root with Include Subfolders on, list view, Tick best guesses if wanted, Write ticked, or on the command line `mp3redactor notes <root> --kind artist --write [--include-uncertain]`; a review page with two views, one note at a time or a ticking list; nothing is written until you click Write; the engine is `core/notes_flow.py`, the page `gui/notes_dialog.py`, its rules `core/notes_review.py`) | |
| Tools | Preferences… | Ctrl+, |
| Tools | API Keys… (Discogs token), External Tools… (mp3val, keyfinder-cli, ffmpeg, ffprobe, fpcalc), MusicBrainz Database Setup… | |
| Tools | Columns…, Genres…, Languages… | |
| Tools | History… (opt-in, off by default: every tag change the app made, old and new values, Restore Old Values; switched on in Preferences > History or by answering yes when History is opened; entries follow a renamed or moved file, via the RenameLog's on_move hook and the command line's rename/move/redact; Preferences > History > Delete History… is a PrefAction that sends the file to the Recycle Bin; Export/Import Settings carry `[history] enabled`) | |
| Help | Changelog…, Credits…, About The ɯP3 Redactor | |

The table's right-click menu has the everyday subset: Preview (one file), Rename File (one file), Look Up, Organize (Rename /
Export / Move, Auto-Number), Analyze, Cover, Edit Lyrics, Create Notes for This Folder, Redact, Remove from List, plus Open in Default App,
Open Containing Folder and Copy Path. Double-click a Filename cell to rename that file, a Lyrics cell to edit its
lyrics. Click a header to sort (Track, Disc, Year, BPM, Loudness, Sample Rate and Channels sort as numbers), drag
a header to reorder, right-click a header to show or hide columns.

Edit > Filter List (Ctrl+F) hides the rows whose Filename, Title, Artist, Album and Path all lack the text
(case-insensitive); the status bar says "Showing N of M files (filter: text)". A hidden row is never a target:
selected rows the filter hides are deselected, and Select All reaches only the rows shown. The filter stays
through edits, sorting and View > Refresh List; Clear List, or opening new files, clears it.

Every action that can take more than a moment (opening a folder, loading, saving, the checks, conversion,
lookups, M4B builds) shows a progress dialog, and Cancel works where the work can stop. Routine results go to the
status bar; message boxes are for errors and questions.

## Redact

Edit > Redact runs the saved recipe on the selected files (or, after asking, on everything loaded). Each file is
worked on as a hidden copy, saved in place, and its original goes to the Recycle Bin (on a drive without one --
a network share, a removable drive -- the original is kept beside it as `<name>.redact-orig.mp3`). A file with
unsaved edits is skipped. Redact is not on the Undo stack: the Recycle Bin copy is the way back. The steps, in
order, with the default recipe:

| Step | On by default | What it does |
| --- | --- | --- |
| Integrity check (mp3val) | yes | Checks and stamps the result. "Fix problems" is off by default; with it on, a fix also leaves mp3val's backup as `<name>.mp3.bak` unless Tools > Preferences > Fixing deletes those. |
| Detect BPM | yes | Tempo into the BPM tag (an existing one is kept unless "replace"). |
| Detect key | yes | Musical key (keyfinder-cli). |
| Measure loudness (ReplayGain) | yes | Track gain to -18 LUFS. |
| Deep check (ffmpeg full decode) | no | Decodes the whole file to find damage mp3val misses. |
| Fill empty tags from the folder path | yes | From a `%field%` pattern over the folder names. |
| Fill missing tags (MusicBrainz / AcoustID) | yes | Album lookup, by sound when fpcalc is set up. |
| Fill missing tags (Discogs) | no | Needs a Discogs token. |
| Add missing cover art | yes | Folder image, then Cover Art Archive, then Discogs. |
| Fetch lyrics | yes | LRCLIB, only for a result matching artist and title. |
| Rename by saved pattern | no | The Rename / Export / Move pattern. |
| Move into library folders | no | Under the saved library folder. |

Leftover `.redact-orig.mp3` and `.bak` files are plain copies of the originals: delete them in Explorer once you
are happy with the result. Open Folder and Refresh List never list them (nor the other Redactor apps' temp and
scratch files: redactor_common's `core/temp_names`).

## Command line

The same exe is also a command-line tool (`mp3redactor info|set|rename|move|convert|redact|analyze|m4b ...`).
Every command and option is documented in [ABOUT.md](ABOUT.md#command-line).

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

**Published releases deliberately do NOT bundle `tools\`** -- an
earlier release that did came out at ~226MB (vs. ~50MB without), and
that's simply too much for what most people using this app actually
need; Check Integrity/Detect Key/Deep Check/Measure Loudness/Import and
Convert just report TOOL MISSING until someone points Tools >
External Tools at binaries on their own machine (or builds a
personal copy with `tools\` populated, per the steps below, if they
want a fully self-contained exe for themselves). This is a standing
decision, not an oversight -- don't re-bundle for an official release.

If you want a personal build with `tools\` populated anyway:

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
   Integrity/Detect Key/Deep Check/Measure Loudness/Import and Convert
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
bundled in `tools\`, point directly at it via Tools > External Tools
in the app itself.

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

## License

Licensed under the [GNU General Public License v3.0 or later](LICENSE).
The GUI is built on PyQt6, and BPM detection uses `aubio` in-process --
both are GPL v3 (PyQt6 with a paid commercial alternative from
Riverbank; aubio is GPL-only) -- this project ships under
GPL-compatible terms to match.
