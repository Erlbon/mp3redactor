# Credits

The ɏP3 Redactor is built on the work of a number of other projects.

## Shared foundation

- **[redactor_common](https://github.com/erlbon/redactor_common)** — the
  UI/logic package shared with its sibling tools (epub, video). See its
  own version line above for which build is vendored here.

## Libraries

- **[PyQt6](https://www.riverbankcomputing.com/software/pyqt/)** — the
  application framework the whole GUI is built on.
- **[Mutagen](https://mutagen.readthedocs.io/)** — reading and writing
  MP3/ID3 tags.
- **[aubio](https://aubio.org/)** — BPM detection, via the
  **[aubio-ledfx](https://pypi.org/project/aubio-ledfx/)** fork (built
  by the [LedFx](https://github.com/LedFx) project to ship prebuilt
  Windows wheels the original PyPI package doesn't).

## External tools

Not bundled — installed separately, and only used if present on the
system or in `tools/`:

- **[mp3val](https://mp3val.sourceforge.net/)** — MP3 stream integrity
  checking and repair.
- **[keyfinder-cli](https://github.com/EvanPurkhiser/keyfinder-cli)**
  (v2+) — musical key detection.
