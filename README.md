# The ɯP3 Redactor

Windows GUI utility (Python/PyQt6) for bulk-checking and bulk-editing
MP3 metadata. Sibling project to the EPUB and Video Redactors,
mp3tag-inspired UX.

## Status: v1 (integrity + BPM + basic tag editing)

Roadmap, in build order:

1. **File integrity check** -- via `mp3val` (shelled out)
2. **BPM detection** -- via `aubio`'s Python bindings (in-process)
3. **Basic tag editing** -- Title/Artist/Album/Track/Year/Genre, via a
   bulk-edit panel (`gui/tag_panel.py`) and `mutagen` (in-process,
   `core/tag_writer.py`) -- same mp3tag-style workflow as the epub
   tool's fuller tag panel: select rows, tick a field, type a value,
   Apply to the selection, Save writes to disk. No cover art, extended
   tags (composer, comment, ...), or external lookups yet -- those stay
   later roadmap items, same as the two below.
4. Key detection -- via `keyfinder-cli` (shelled out) -- not yet built
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
     Download `keyfinder-cli.exe` + the 4 `.dll` files from its
     [latest release](https://github.com/Erlbon/keyfinder-cli-windows/releases/latest)
     and drop all 5 into `tools\` (it's dynamically linked against
     FFmpeg, so the DLLs have to ship alongside the exe). That repo also
     has the build script, if `keyfinder-cli`/`libkeyfinder` ever need a
     newer version.
   (keyfinder-cli isn't wired into the app yet -- key detection is a
   later roadmap item -- but bundling it now avoids a second build once
   it lands. The build still works without a `tools\` folder at all;
   integrity check/fix will just report TOOL MISSING until it's added.)
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
