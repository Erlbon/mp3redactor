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
- **[libkeyfinder](https://github.com/mixxxdj/libkeyfinder)** (GPL-3.0,
  Ibrahim Sha'ath and the Mixxx project) — musical key detection. The
  app carries a Python/NumPy port of its method and constants
  (`core/key_detector.py`).
- **[mp3val](https://mp3val.sourceforge.net/)** 0.1.8 (GPL-2.0-or-later,
  Alexey Kuznetsov (ring0) and Eugen Tikhonov (jetsys)) — MP3 stream
  integrity checking and repair. The app carries a Python port of it
  (`core/mp3_validator.py`).
- **[NumPy](https://numpy.org/)** — the maths behind key detection.

## External tools

Not bundled — installed separately, and only used if present on the
system or in `tools/`:

- **[mp3val](https://mp3val.sourceforge.net/)** — now only a fallback
  for the built-in port, if that fails on a file.
- **[Chromaprint](https://acoustid.org/chromaprint)** (`fpcalc`, LGPL
  2.1) — the audio fingerprints for identifying songs by sound. Optional;
  found on PATH or via Settings > Locate External Tools, never bundled.
- **[keyfinder-cli](https://github.com/EvanPurkhiser/keyfinder-cli)**
  (v2+) — musical key detection; now only a fallback for files the
  built-in port cannot decode.

## Online services

- **[MusicBrainz](https://musicbrainz.org/)** — the open music
  encyclopedia behind Import > Look Up via MusicBrainz (release search
  and track lists, via its web service; core data CC0).
- **[Cover Art Archive](https://coverartarchive.org/)** — release cover
  previews in that lookup (shown only, never embedded by it).
- **[AcoustID](https://acoustid.org/)** — identifies songs by their
  sound in that lookup: audio fingerprints matched to MusicBrainz
  recordings (its web service).
