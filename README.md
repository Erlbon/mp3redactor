# The ɯP3 Redactor

Windows GUI utility (Python/PyQt6) for bulk-checking and bulk-editing
MP3 metadata. Sibling project to the EPUB and Video Redactors,
mp3tag-inspired UX.

## Status: v1 (integrity + BPM + key + basic tag editing)

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
5. Cover art check/add/replace -- via `mutagen` (in-process) -- not yet built
6. Lyrics fetch + write -- via `lyricy` (fetch) + `mutagen` (write) -- not yet built

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
- **mp3val** and **keyfinder-cli** are shelled out to -- no Python
  bindings exist for either. Both need to be either bundled in `tools/`
  next to a frozen build, or present on PATH for dev-mode runs. See
  `core/tool_locator.py`.

## Building the .exe (Windows only)

PyInstaller can't cross-compile a Windows executable from another OS, so
this has to be built on Windows itself.

1. Get `mp3val.exe` and `keyfinder-cli.exe` (+ its 4 FFmpeg DLLs) into
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
   (The build still works without a `tools\` folder at all -- Check
   Integrity/Detect Key will just report TOOL MISSING until the
   relevant binary's added.)
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

`mp3val.exe`/`keyfinder-cli.exe` are shelled-out binaries, not bundled
data assets -- they're copied to sit next to the built exe rather than
packed inside it, matching where `core/tool_locator.py` looks for them.
If either isn't on PATH or bundled in `tools\`, point directly at it via
Settings > Locate External Tools in the app itself.

### Licensing note on the bundled tools

`keyfinder-cli.exe` and its 4 FFmpeg DLLs are GPLv3 (`keyfinder-cli`,
`libkeyfinder`, and this particular FFmpeg build are each GPLv3 --
see `tools\NOTICE.txt` for the breakdown once they're in place).
mp3redactor invokes `keyfinder-cli.exe` as a separate process (command-
line args + stdout, never linked into mp3redactor.exe itself), so this
doesn't affect mp3redactor's own licensing -- but if you distribute a
build that bundles these binaries (e.g. as a zip alongside
`dist\mp3redactor.exe`), GPLv3 requires the license text and source
pointers to travel with them. `tools\LICENSE.txt` (the keyfinder-cli-windows release bundle's GPLv3
text) and `tools\NOTICE.txt` exist for exactly that -- keep them in `tools\`
alongside the binaries (`build_exe.bat`'s `xcopy` already carries the
whole folder, text files included, into `dist\tools\`) rather than
distributing the binaries on their own.

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
is unit-tested with mocked subprocess/aubio calls. The GUI layer, like
the sibling projects, isn't visually testable in an automated way --
only syntax-checked and code-reviewed.
