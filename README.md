# The ɯP3 Redactor

Windows GUI utility (Python/PyQt6) for bulk-checking and bulk-editing
MP3 metadata. Sibling project to the EPUB and Video Redactors,
mp3tag-inspired UX.

## Status: v1 (integrity + BPM + key + basic tag editing + ffmpeg-based deep check/loudness/import + rename/parse-filename + lyrics fetch)

Roadmap, in build order:

1. **File integrity check** -- via `mp3val` (shelled out)
2. **BPM detection** -- via `aubio`'s Python bindings (in-process)
3. **Basic tag editing** -- Title/Artist/Album Artist/Album/Track/Disc
   Number/Year/Genre/Composer/Comment/Language, plus mp3tag's own
   "advanced" set (Album Sort/Artist Sort/Album Artist Sort/AcoustID
   Fingerprint/iTunesAdvisory -- hidden by default, real fields just
   less commonly needed; the full field list was checked directly
   against mp3tag's own to fill real gaps, Album Artist foremost among
   them), via a bulk-edit panel (`gui/tag_panel.py`) and `mutagen`
   (in-process, `core/tag_writer.py`) -- same mp3tag-style workflow as
   the epub tool's fuller tag panel: select rows, tick a field, type a
   value, Apply to the selection (undoable, `Ctrl+Z`), Save writes to
   disk. Genre and Language each get a quick-pick "+" button (curated
   defaults + custom entries, managed via Settings > Add/Remove
   Genres.../Add/Remove Languages...), and the table's columns are
   fully manageable -- drag a header to reorder, right-click for a
   show/hide checklist or Settings > Add/Remove Columns..., both
   persisted across restarts. Click a header to sort by that column
   (click again to reverse) -- Track/Disc Number/Year/BPM/Loudness/
   Sample Rate/Channels sort numerically, not as text
   (`redactor_common.gui.sortable_table`). File > Refresh List (F5/
   Ctrl+R) re-scans the folder(s) your loaded files live in and picks
   up anything new dropped there since (`redactor_common.core.
   folder_refresh`). A **Cover** column shows each file's embedded art
   (loaded lazily, only for rows on screen) -- see item 8 below.
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
   - **Find Duplicates** (Analyze menu) -- reviews ALL loaded files
     for identical audio (tags ignored, so retagging never hides a
     match), the same MusicBrainz recording, and the same artist +
     title + length. A review aid, not an error check: every group says
     why it matched (the same track on two albums is only a weak match),
     nothing is selected for you, and "Not Duplicates" is remembered.
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
     from a `%field%` pattern (every field in `core/fields.py`, e.g.
     `%artist% - %album%/%track% - %title%`), preview it per selected
     file, then rename in place or export renamed copies to a folder,
     originals untouched. Track gets an optional zero-pad-to-2-digits
     checkbox.
   - **Parse Filename...** (Import menu, `F3`) -- the reverse: extract
     field values back out of a filename using the same pattern syntax,
     preview per file, apply the accepted ones via the same
     `MP3File.apply_tags()` bulk-edit path (undoable, `Ctrl+Z`, dirty-
     highlighted, same as typing into the tag panel).
   - Both dialogs share one pattern history (`core/settings.py`'s
     `pattern_history`) and default to `%track% - %artist% - %title%`.
   - **Quick single-file rename**: double-click a Filename cell, or
     right-click a single selected file > Rename File..., to fix a
     typo directly without the pattern-based tool above -- the
     prompt/validate/rename/error-report flow itself is
     `redactor_common.gui.rename_single_file.rename_single_file()`
     (promoted there from this project's own first version), built on
     `core.rename_pattern.rename_file_on_disk()`.
7. **Lyrics fetch + write** -- via `lyricy` (fetch, `core/lyrics_fetcher.py`,
   LRCLIB provider -- free, no API key) + `mutagen` (write, the standard
   ID3v2 `USLT` frame, `core/tag_writer.py`). Not a `core/fields.py`
   entry -- full song lyrics are long, multi-line text that doesn't fit
   the bulk-edit panel's single-line rows, so it gets its own dedicated
   editor (`gui/lyrics_dialog.py`, double-click a file's Lyrics cell or
   right-click > Edit Lyrics...) plus a bulk "Fetch Lyrics for Selected
   Files" (Operations menu / right-click, thread-pooled across the
   selection like Detect BPM/Key). Read back from an existing `USLT`
   frame on load, same round-trip BPM/key/loudness get.
8. **Cover art** -- embedded ID3v2 `APIC` pictures via `mutagen`
   (`core/cover_art.py`, written by `core/tag_writer.py`). A Cover
   column (Yes/No + thumbnail) and a cover preview in the side panel,
   both read and decoded in the background so large libraries stay
   responsive. Set a cover from an image file (JPEG/PNG embedded as-is,
   other formats converted), from the `cover.jpg`/`folder.jpg`/
   `front.jpg` next to each selected file (each album gets its own art
   in one go), remove it, or export it to an image file -- Operations >
   Cover, right-click, or the panel's buttons. Undoable; written on
   Save as a single front cover.
9. Duplicate detection via audio fingerprinting -- ffmpeg's bundled
   `chromaprint` support could back this; not yet built, suggested as
   a later addition
10. **MusicBrainz lookup** (stage 1 shipped 2026-09-28) -- Import >
    Look Up via MusicBrainz... finds the release each selected folder
    belongs to (one album per folder): it searches MusicBrainz by the
    folder's Artist + Album tags (or an "Artist - Album (Year)" folder
    name), then scores each candidate release's track list against the
    files by track number, title and length, assigning each file to its
    track. Review in the family's lookup dialog: the release's cover
    (Cover Art Archive) next to your own, other editions under Other
    Matches, Artist/Album correctable with Search This Item. Applies
    title, artist, album artist, album, track, disc (multi-disc
    releases), year, and the MusicBrainz Album Id / Track Id (written
    the way MusicBrainz Picard writes them: `TXXX:MusicBrainz Album Id`
    and a `UFID` frame owned by `http://musicbrainz.org`), through the
    per-field overwrite review; undoable, written on Save. MusicBrainz
    is queried at most once per second, as its terms ask. No key or
    tool needed. **Stage 2 (2026-09-29): identifying files by their
    sound** -- with `fpcalc` available (Chromaprint; official binaries
    per platform, found on PATH or located via Settings > Locate
    External Tools like the other tools, never bundled), each file is
    fingerprinted and looked up on AcoustID, giving its exact
    MusicBrainz recording: a file pairs with its track for certain, and
    a folder whose tags and names are useless (tested: two stripped
    files in "New Folder (3)") is found through the releases holding
    its recordings. AcoustID is queried at most 3 times a second; the
    app ships its own AcoustID application key (application keys
    identify the app, not a user). Without fpcalc the lookup works as
    stage 1.

## Command line

The one exe (`mp3redactor.exe`, or `python main.py` from source) is also the command line. When its first
argument is a command name, it runs that command and the window never opens; with no command, or with a file
or folder to open, the window starts as usual. `mp3redactor --help` lists the commands and
`mp3redactor COMMAND --help` lists the options of one.

```
mp3redactor info     PATH...  [--fields LIST | --all]
mp3redactor set      PATH...  -s FIELD=VALUE ... [--clear FIELD ...] [-n]
mp3redactor rename   PATH...  [-p PATTERN] [--zero-pad N] [--ascii] [-n]
mp3redactor move     PATH...  -p PATTERN [--root FOLDER] [--copy] [--zero-pad N] [--ascii] [-n]
mp3redactor convert  PATH...  [--bitrate KBPS] [--trash-original] [-n]
mp3redactor redact   [PATH...] [--recipe FILE] [--enable STEP] [--disable STEP] [--threshold N]
                               [--trash-dir FOLDER] [--list-steps]
mp3redactor analyze  PATH...  [--integrity] [--deep] [--bpm] [--key] [--loudness] [--save]
mp3redactor m4b      PATH...  [--into FOLDER | --library FOLDER | --file FILE] [--bitrate KBPS] [--sidecar]
                               [--replace] [--title T] [--author A] [--narrator N] [--series S] [--series-number N]
                               [--year Y] [--publisher P] [--language L] [--description D] [--cover IMAGE]
                               [--lookup] [--region STORE] [--min-score N] [-n]
```

The command line uses the same code as the window, so the results are the same. It reads the same settings file
(`mp3redactor_settings.ini` next to the exe: the saved Redact recipe, the library root, the tool paths, the
M4B quality) and the same secret store for the API keys. Not every window function is available from the
command line; the commands above are what is.

### Options every command has

| Option | Meaning |
| --- | --- |
| `PATH...` | One or more MP3 files, folders or wildcards (`D:\Music\Queen*.mp3`). A folder is searched recursively for `.mp3` files (`convert` looks for the other audio formats). A file you name is always used. A path that matches nothing is reported, and if nothing at all matches the command stops with exit code 2. A name containing `[` or `]` is taken literally, a wildcard's matches are filtered by extension like a folder's files, and a folder inside a folder that is a link or junction is not followed. |
| `-R`, `--no-recurse` | For a folder, look only at the files directly in it. |
| `--json` | Print one JSON document on stdout instead of text (see "JSON output"). Nothing else goes to stdout. |
| `-q`, `--quiet` | No progress lines and no warnings on stderr (errors are still shown). |
| `-o FILE`, `--output FILE` | Write the result (the text, or with `--json` the JSON document) to FILE instead of stdout. The file is complete when the program exits. This is the reliable way for a script to read a result. |
| `-n`, `--dry-run` | On the commands that change files (`set`, `rename`, `move`, `convert`, `m4b`): show what would happen and change nothing. |
| `-h`, `--help` | Help for the program or for one command. |
| `--version` | The version (top level only). |

Progress lines (`[3/20] name.mp3`) go to stderr when more than one file is processed.

### info

`mp3redactor info PATH... [--fields LIST | --all]`

Shows each file's length, bitrate, whether it has a cover, and its tags.

| Option | Meaning |
| --- | --- |
| `--fields LIST` | Comma-separated tag fields to show, e.g. `--fields artist,album,year`. Default: `title, artist, album, track, year`. |
| `--all` | Show every tag field that has a value. |

Only fields with a value are listed. Exit code 1 if a file could not be read.

### set

`mp3redactor set PATH... -s FIELD=VALUE [-s ...] [--clear FIELD ...] [-n]`

Sets or empties ID3 tag fields and saves each file in place (an empty value removes the tag). Fields and values
are checked before any file is touched; a bad one stops the command with exit code 2.

| Option | Meaning |
| --- | --- |
| `-s FIELD=VALUE`, `--set FIELD=VALUE` | Set a field (repeat for several). |
| `--clear FIELD` | Empty a field (repeat for several). |
| `-n`, `--dry-run` | Show the old and new value of each field, save nothing. |

Field names are case-insensitive and accept the plain, spaced or underscored spelling (`albumartist`, `Album
Artist`, `album_artist`). The fields are: title, artist, albumartist, album, track, discnumber, year, genre,
composer, comment, language, albumsort, artistsort, albumartistsort, acoustid_fingerprint, itunesadvisory,
musicbrainz_albumid, musicbrainz_trackid, publisher, catalognumber, releasecountry.

Checks: `track` and `discnumber` are a number or number/total (`3`, `3/12`, written with the digits 0-9); `year` is
`YYYY`, `YYYY-MM` or `YYYY-MM-DD`; `language` is a three-letter code (`eng`, `nor`, `deu`; it is stored in lower
case); `itunesadvisory` is `0` (none), `1` (explicit) or `2` (clean). A value with a control character in it is
refused, and so is a line break or tab in any field except `comment`, since an ID3 tag cannot hold them sensibly.
If a field is given both `-s` and `--clear`, `--clear` wins whatever the order.

Each file's result is `changed`, `unchanged` (nothing differed), `planned` (dry run) or `failed`.

### rename

`mp3redactor rename PATH... [-p PATTERN] [--zero-pad N] [--ascii] [-n]`

Renames each MP3 from its tags, in its own folder, like Rename / Export / Move > Rename files in place. Never
overwrites: a name that is taken gets `(2)`, `(3)`, ... A change of letter case alone (`song` to `Song`) counts as a rename.

| Option | Meaning |
| --- | --- |
| `-p PATTERN`, `--pattern PATTERN` | The new name (without `.mp3`), with `%field%` tokens, e.g. `"%track% - %artist% - %title%"` (the default). Quote it so the shell leaves the `%` signs alone. |
| `--zero-pad N` | Pad the track number to N digits (`--zero-pad 2` gives `03`). Default: the choice saved in the app's Rename window (on, with its width, or off); `--zero-pad 0` turns it off. |
| `--ascii` | ASCII-safe names (é becomes e, æ becomes ae, other symbols are dropped). Also on when the app's Rename window has it saved. |
| `-n`, `--dry-run` | Show the new names, rename nothing. |

Tokens are the tag field names above (`%artist%`, `%album%`, `%title%`, `%track%`, `%year%`, ...). A token that is
not a field name (a typo such as `%tittle%`) is refused with exit code 2 instead of silently rendering as nothing,
and a rename pattern cannot contain `/` or `\` (`move` makes folders). A file the pattern gives no name for (all its
fields are empty) is `skipped`, not renamed to "untitled". A file that already has the name is `unchanged`. There is
no undo for the command line: preview with `--dry-run`.

In a batch file write `%%` for each `%` (`-p "%%track%% - %%title%%"`): cmd expands a single `%name%` itself, and a
pattern that then reads nothing makes the file `skipped`.

### move

`mp3redactor move PATH... -p PATTERN [--root FOLDER] [--copy] [--zero-pad N] [--ascii] [-n]`

Moves (or copies) each MP3 into a folder tree under a library folder, like Rename / Export / Move > Move into
folders. The pattern may contain `/` to make sub-folders: `"%albumartist%/%album%/%track% - %title%"`. Missing
folders are created; nothing is overwritten (a taken name gets `(2)`); a destination outside the library folder
or too long is refused.

| Option | Meaning |
| --- | --- |
| `-p PATTERN`, `--pattern PATTERN` | Required. The path under the library folder, with `%field%` tokens. |
| `--root FOLDER` | The library folder. Default: the one saved in the app (Rename / Export / Move window). The folder must exist. |
| `--copy` | Copy instead of move, leaving the originals. |
| `--zero-pad N`, `--ascii` | As for `rename`. |
| `-n`, `--dry-run` | Show where each file would go, change nothing. |

Across volumes a move is a verified copy followed by sending the original to the Recycle Bin. A file the
pattern has no name for is `skipped`. There is no undo for a move either: preview with `--dry-run`.

### convert

`mp3redactor convert PATH... [--bitrate KBPS] [--trash-original] [-n]`

Converts FLAC, WAV, OGG, M4A and the other audio formats the app can import to MP3 with ffmpeg, beside the
original (same name, `.mp3`). Never overwrites: if the `.mp3` already exists the file is `skipped`; an MP3 is
`skipped` too.

| Option | Meaning |
| --- | --- |
| `--bitrate KBPS` | MP3 bitrate: 128, 192, 256 or 320 (default 192). |
| `--trash-original` | After the `.mp3` is made, send the original to the Recycle Bin (never deleted for good; a move a virus scanner blocks for a moment is retried, and if the Recycle Bin still refuses, the original is kept and a warning says so). |
| `-n`, `--dry-run` | Show what would be converted, change nothing. |

Results: `converted`, `skipped`, `planned`, `failed`. The new path is in `new_path`. A file ffmpeg cannot read is
`failed` and leaves nothing behind.

### redact

`mp3redactor redact [PATH...] [--recipe FILE] [--enable STEP] [--disable STEP] [--threshold N] [--trash-dir FOLDER] [--list-steps]`

Runs the Redact recipe on the files, the same steps as Edit > Redact: integrity check, BPM, key, loudness, deep
check, tags from the folder path, lookups on MusicBrainz and Discogs, cover art, rename, move into folders. Each
file is saved in place and its original goes to the Recycle Bin (or `--trash-dir`). Guesses below the
confidence threshold are listed under "needs review" and not applied. There is no `--dry-run`: use `info`
first, and `--disable` for the steps you do not want.

| Option | Meaning |
| --- | --- |
| `--recipe FILE` | Use this recipe (a JSON file in the format the app stores) instead of the one saved in the app. |
| `--enable STEP` | Turn a step on for this run (repeatable). |
| `--disable STEP` | Turn a step off for this run (repeatable). |
| `--threshold N` | Confidence needed to apply a guess: a fraction `0`-`1` (`0.9`, also `1`), or a percentage with at least two digits (`90`, `90%`, `100`); a number above 1 and below 5 such as `1.5` is refused as ambiguous. |
| `--trash-dir FOLDER` | Move originals into this folder (created if needed) instead of the Recycle Bin, for a machine or a task that has none. |
| `--list-steps` | Show the steps and whether the recipe has each on, then stop (no `PATH` needed). |

Steps: `integrity`, `bpm`, `key`, `loudness`, `deep_check`, `path_tags`, `tags`, `discogs`, `cover`, `rename`,
`move_into_folders`. Without `--recipe` the recipe saved in the app is used (the defaults if none was saved).
The Discogs token comes from the `DISCOGS_TOKEN` environment variable if it is set, else from the app's saved
token; the library root and tool paths come from the app's settings. A `--recipe` file that is not valid JSON or not a
recipe is refused (exit code 2) rather than replaced by the default recipe. Exit code 1 if any file failed; files that
need review are not failures.

### analyze

`mp3redactor analyze PATH... [--integrity] [--deep] [--bpm] [--key] [--loudness] [--save]`

Runs the Analyze menu's checks. With none of the check options the integrity check runs. Results are shown;
nothing is written to the files unless `--save`.

| Option | Meaning |
| --- | --- |
| `--integrity` | The mp3val integrity check (the default check). |
| `--deep` | A full ffmpeg decode, plus the encoder, sample rate and channel count. |
| `--bpm` | Detect the tempo. |
| `--key` | Detect the musical key (keyfinder-cli). |
| `--loudness` | Measure the integrated loudness (LUFS) and the gain to the target. |
| `--save` | Write the results into the files' tags: BPM, key, the loudness (as ReplayGain, with `--loudness`) and the scan stamps. |

Exit code 1 when a check found a problem (WARNING or ERROR) or could not run (a tool is missing or failing), so a
script can act on it. A file's `problem` is `true` in the JSON in those cases.

### m4b

`mp3redactor m4b PATH... [--into FOLDER | --library FOLDER | --file FILE] [--bitrate KBPS] [--sidecar] [--replace] [book options] [--lookup] [-n]`

Makes one chaptered `.m4b` audiobook per folder of MP3 files, the same as File > Create M4B Audiobook: a chapter
per file in disc / track / file-name order, the files re-encoded to AAC (the originals are never touched). A
book that already exists is skipped unless `--replace`. Nothing is overwritten for good: an audiobook, `metadata.opf` or
cover image that a run replaces goes to the Recycle Bin (or `--trash-dir`) first. If it cannot be sent there, the old
audiobook is kept beside the new one as `<Title> (previous).m4b`, and a note says so. Two folders that would be
written to the same file make one book and a `skipped` for the other (`--dry-run` says the same). A failed or
cancelled run leaves nothing behind: no half-written file, no empty library folders, and an old build folder an
earlier crash left is cleaned up. The chapter marks follow the real length of every chapter's audio.

| Option | Meaning |
| --- | --- |
| `--into FOLDER` | Put every audiobook in this folder, named `<Title>.m4b`. Default: beside the MP3 files. |
| `--library FOLDER` | Library folder in Audiobookshelf's layout: `<FOLDER>/<Author>/[<Series>/]<Title>/<Title>.m4b`. |
| `--file FILE` | The exact `.m4b` to write (one book only; `.m4b` is added if missing). |
| `--bitrate KBPS` | AAC bitrate: 32, 48, 64, 96 or 128. Default: the one saved in the app (64). |
| `--sidecar` | Also write `metadata.opf` and a cover image beside each audiobook (Audiobookshelf, Calibre). Default: the app's saved choice. Refused with `--into` for several books (they would share one `metadata.opf`). |
| `--replace` | Replace an audiobook that already exists; the old one goes to the Recycle Bin or `--trash-dir`. |
| `--trash-dir FOLDER` | Where an audiobook, `metadata.opf` or cover that is replaced goes, instead of the Recycle Bin (created if needed). |
| `--title`, `--author`, `--narrator`, `--series`, `--series-number`, `--year`, `--publisher`, `--language`, `--description` | Set that detail for the book(s). Without them the details come from the first file's tags (album, album artist, year, publisher, language) and its cover. They apply to every book, so use them with one book. |
| `--cover IMAGE` | A JPEG or PNG cover instead of the files' own. A cover in the files that is not a JPEG or PNG (a GIF, a WebP) is left out, with a note. |
| `--lookup` | Look the book up on Audible (then Open Library) and fill in only what is still empty: narrator, series and its number (together), publisher, year, language, description, the author if the files have none, and a cover only if the files have none. It never replaces the files' own title, author, cover or other details, and what you gave explicitly wins. Whatever it has to say (a source that was down, nothing found, a match below `--min-score`) is in the row's `notes`. |
| `--region STORE` | The Audible store for `--lookup`: com, co.uk, de, fr, it, es, ca, com.au, in or co.jp. Default: the one saved in the app (com). |
| `--min-score N` | How good a `--lookup` match must be to be used, 0 to 4 (title counts most, then author, then how close the running time is). Default 2.8; below it the lookup is ignored with a note. A value outside 0-4 is refused (exit code 2). |
| `-n`, `--dry-run` | Show the audiobooks that would be made, make nothing. |

Results per book: `created`, `skipped`, `planned`, `failed`; the JSON also has the output path, the number of
chapters, the length in minutes, `notes` (things worth knowing that did not stop the audiobook) and, with `--lookup`,
the match that was used and `filled`, the names of what it filled in.

### JSON output

`--json` prints one document: `{"results": [...], <summary fields>, "warnings": [...], "errors": [...]}`. It is ASCII-only (a non-ASCII character in a path is a `\uXXXX` escape, which any JSON reader decodes). If a command fails or is interrupted after it started, the document is still printed, with what was done so far and an `error` entry, so a script reading `--output FILE` never finds an empty or half-written file.

| Command | Each entry in `results` | Summary fields |
| --- | --- | --- |
| `info` | `path`, `status`, `duration_seconds`, `bitrate_kbps`, `has_cover`, `fields` (name to value) | `files`, `failed` |
| `set` | `path`, `status`, `changes` (field to `{old, new}`), `message` | `files`, `failed`, `dry_run` |
| `rename`, `move` | `path`, `status`, `new_path`, `message` | `files`, `failed`, `dry_run`, and `pattern` or `root` |
| `convert` | `path`, `status`, `new_path`, `message` | `files`, `failed`, `dry_run`, `bitrate_kbps` |
| `redact` | `file`, `path`, `status`, `applied`, `needs_review` (step, value, confidence, reason), `failures`, `notes`, `skipped`, `not_saved` | `files`, `failed`, `needs_review`, `cancelled`, `confidence_threshold`, `run_notes` |
| `redact --list-steps` | `step`, `label`, `enabled` | `confidence_threshold` |
| `analyze` | `path`, `status`, `problem`, `checks` (per check: `status`, `message` and its values), `save_error` | `files`, `problems`, `checks`, `saved` |
| `m4b` | `folder`, `title`, `output`, `status`, `message`, `chapters`, `minutes`, `match`, `notes` | `books`, `failed`, `dry_run`, `bitrate_kbps` |

### Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Done (files that were skipped or unchanged are not failures). |
| 1 | The command ran but some files failed (for `analyze`: some files have problems). |
| 2 | Bad arguments, an unknown field or step, or no files found. The reason is on stderr. |
| 70 | An internal error (a bug); the traceback is on stderr. |
| 130 | Interrupted with Ctrl+C. |

### Using it from scripts and scheduled tasks (Windows)

`mp3redactor.exe` is a windowed program, and Windows shells treat those differently from console programs:
typed by hand in a terminal its output appears there and `>` / `|` redirection works, but an interactive shell
does not wait for it (the prompt can come back before the output), and a script cannot read a windowed
program's output unless it is redirected. So for automation: ask for the result in a file with `--output`, wait
for the process, and read the exit code.

```
:: batch file (cmd waits for the program in a batch file; %errorlevel% is the exit code)
mp3redactor.exe analyze "D:\Music" --deep --json --output "%TEMP%\check.json"
if errorlevel 1 echo some files have problems

:: interactive cmd: start /wait waits and keeps the exit code
start /wait mp3redactor.exe redact "D:\Incoming" --quiet --trash-dir "D:\Trash"

# PowerShell: wait with Start-Process, read .ExitCode
$p = Start-Process mp3redactor.exe -ArgumentList 'analyze','D:\Music','--json','-o','C:\Temp\check.json' -Wait -PassThru
$p.ExitCode
(Get-Content C:\Temp\check.json -Raw | ConvertFrom-Json).results | Where-Object problem

# PowerShell: piping to Out-Null also waits
mp3redactor.exe convert "D:\Incoming" --trash-original | Out-Null; $LASTEXITCODE
```

Task Scheduler waits for the program and records its exit code as it is. On Linux and macOS there is no such
distinction: the output goes to the terminal and pipes as usual.

### Examples

```
mp3redactor info "D:\Music\Queen" --all                                   what is in a folder
mp3redactor set "D:\Music\Queen" -s AlbumArtist=Queen -n                   preview a bulk edit, then run it without -n
mp3redactor rename "D:\Music\Queen" -p "%track% - %title%" --zero-pad 2 -n
mp3redactor move "D:\Incoming" -p "%albumartist%/%album%/%track% - %title%" --root "D:\Library"
mp3redactor convert "D:\Rips" --bitrate 256 --trash-original              FLAC to MP3, recycle the FLACs
mp3redactor analyze "D:\Music" --deep --bpm --save --json -o report.json  check, tag the tempo, report
mp3redactor redact "D:\Incoming" --disable discogs --trash-dir "D:\Trash"
mp3redactor m4b "D:\Audiobooks" --library "D:\Library" --lookup --sidecar one audiobook per folder, tagged from Audible
mp3redactor m4b "D:\Books\Dune" --title "Dune" --author "Frank Herbert" --cover cover.jpg
```

What the commands will not do: overwrite a file, delete anything for good, or ask a question. Everything that
could be a prompt in the window is a flag here or a skipped file in the report.

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
need; Check Integrity/Detect Key/Deep Check/Measure Loudness/Import &
Convert just report TOOL MISSING until someone points Settings >
Locate External Tools at binaries on their own machine (or builds a
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

## License

Licensed under the [GNU General Public License v3.0 or later](LICENSE).
The GUI is built on PyQt6, and BPM detection uses `aubio` in-process --
both are GPL v3 (PyQt6 with a paid commercial alternative from
Riverbank; aubio is GPL-only) -- this project ships under
GPL-compatible terms to match.
