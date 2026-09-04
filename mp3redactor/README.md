# The ɯP3 Redactor

Windows GUI utility (Python/PyQt6) for bulk-checking and eventually
bulk-editing MP3 metadata. Sibling project to the EPUB and Video
Redactors, mp3tag-inspired UX.

## Status: v1 (integrity + BPM)

Roadmap, in build order:

1. **File integrity check** -- via `mp3val` (shelled out)
2. **BPM detection** -- via `aubio`'s Python bindings (in-process)
3. Key detection -- via `keyfinder-cli` (shelled out) -- not yet built
4. Cover art check/add/replace -- via `mutagen` (in-process) -- not yet built
5. Lyrics fetch + write -- via `lyricy` (fetch) + `mutagen` (write) -- not yet built

Tag editing (mp3tag-style bulk-edit panel) is deferred until there's
something beyond read-only fields to edit toward.

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

1. Download `mp3val.exe` and `keyfinder-cli.exe` and place them at:
   ```
   tools\mp3val.exe
   tools\keyfinder-cli.exe
   ```
   (keyfinder-cli isn't used yet -- key detection is a later roadmap item
   -- but bundling it now avoids a second build once it lands. The build
   still works without a `tools\` folder at all; integrity check/fix
   will just report TOOL MISSING until it's added.)
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
