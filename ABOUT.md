# The ɯP3 Redactor

**"ɯ" is U+026F, LATIN SMALL LETTER TURNED M** -- the MP3 sibling of "The ƎPUB Redactor"'s turned-E and
"The Ʌideo Redactor"'s turned-V branding.

A bulk tag editor and checker for MP3 files, in the spirit of mp3tag: select many files, edit their tags in one
go, check that they are sound, and let the app fill in what is missing -- without clicking into files one at a
time.

## What it does

- **Bulk-edit tags** from one side panel: title, artist, album artist, album, track, disc, year, genre,
  composer, comment, language, and mp3tag's "advanced" set (sort names, MusicBrainz ids, publisher, catalogue
  number, release country...). Tick a field, type a value, Apply to the selection; Save All writes the changed
  files. Everything is undoable until saved; changed rows are tinted.
- **Check the files**: mp3val's integrity check (and fix), a full ffmpeg decode (Deep Check), tempo (BPM),
  musical key (keyfinder-cli) and loudness (written as ReplayGain). Results are stamped into the file on Save, so
  a later run shows when a file was last checked.
- **Find Duplicates** across everything loaded: identical audio (tags ignored), the same MusicBrainz recording,
  or the same artist, title and length. A review aid -- every group says why it matched, nothing is chosen for
  you, and "Not Duplicates" is remembered.
- **Look things up**: MusicBrainz (online, or an offline database built from MusicBrainz's free data dump),
  identification by sound with AcoustID/fpcalc, Discogs (with your own token), lyrics from LRCLIB, and covers
  from the Cover Art Archive or Discogs. Every lookup is reviewed before anything is applied, and nothing
  overwrites an existing value without asking.
- **Covers**: a Cover column with thumbnails, a preview in the side panel; set from an image file or from the
  `cover.jpg` / `folder.jpg` next to each file, remove, or export.
- **Rename, move and parse**: rename files from a `%field%` pattern, copy them out renamed, move them into a
  library folder tree, or go the other way and read tags out of file and folder names. Renames can be undone
  with File > Undo Last Rename.
- **Redact** (Edit > Redact, Ctrl+Shift+E): one click runs a recipe of checks, lookups and fixes on the
  selection (or everything loaded) and saves each file in place, the original going to the Recycle Bin. Guesses
  below the confidence threshold are listed for review, not applied. Edit > Edit Redact Recipe chooses the
  steps.
- **Convert other formats**: File > Import and Convert turns FLAC, WAV, OGG, M4A, WMA, Opus or AIFF into MP3
  beside the original. Opening a folder that has no MP3 at all but such files offers the same.
- **M4B audiobooks**: File > Create M4B Audiobook joins the selected MP3s into a chaptered `.m4b` (one book per
  folder), with optional Audible / Open Library lookup and an Audiobookshelf-style library layout.
- **Create Notes** (Tools > Create Notes, or right-click > Create Notes for This Folder) drafts the short Markdown notes
  the Pluteus media server shows beside a folder: `artist.md`, `album.md`, and `about.md` for a genre or collection
  folder. Tools > Create Notes always asks which folder first, as a short list (the folder you opened, the artist /
  album of the selected files, one level up, the last folder, the library folder, or Another folder; the first is ticked,
  Enter continues, the selection is never used silently) and **what to create**: *Artist notes only* (recommended, fast: no
  album lookups at all; the default and remembered), *Artist and album notes* (slower) or *Everything, including genre
  folders*. After an artist-only run the review page offers **Create the album notes for these artists too**. The right-click
  entry takes each selected file's album folder AND the artist folder above it. The lookups (MusicBrainz, Wikipedia and, with the Discogs token saved under Tools > API
  Keys, Discogs) run under a progress dialog you can cancel; what was looked up so far is still reviewed. The review
  page has two views, chosen from what was found and switchable: one note at a time (one artist or album: Skip, Write
  note, Back, Write all remaining confident) or a list with tick boxes (several artists: Write N ticked notes).
  Confident matches are pre-ticked; uncertain and nothing-found ones cannot be ticked until you pick one of the
  candidates (or the level: artist, genre or collection, album) and the note is rebuilt from it. Nothing is written
  until you click Write; a note is never overwritten, and a note you write is a draft (`status: draft`).
- **History** (Tools > History) is opt-in, off by default: nothing is recorded until you turn on Tools >
  Preferences > History > "Keep a history of tag changes" (or answer yes when you open Tools > History).
  From then on every tag change the app writes to a file (Save, Save All, Redact, the command line's `set`,
  and restores) is recorded with the old and new value of each changed field, so an unwanted Save can be
  undone even after the app was closed: pick the change and click Restore Old Values. The history is the file
  `mp3redactor_journal.jsonl` next to the settings file (beside the exe, or the project folder when run from
  source); it keeps the newest 50,000 changes and nothing older than 90 days. Covers are not kept in it (only
  the fact that a cover changed), and replaced or converted files are covered by the Recycle Bin instead. You
  can switch it off again at any time; Preferences > History > Delete History sends the file to the Recycle
  Bin. The command line follows the same setting: with history off it records nothing. A renamed or moved file
  keeps its history: Rename File, Search and Replace on the filename, Rename / Export / Move, Undo Last Rename,
  Redact's rename and move steps and the command line's `rename`, `move` and `redact` all take its entries
  along, so Restore finds the file where it is now. File > Export Settings / Import Settings carry the
  on/off switch ("Change history on/off"); importing a file with it on starts recording on this computer.

## Opening files

File > Open Files (Ctrl+O) or Open Folder (Ctrl+Shift+O), dropping files or folders on the window, or naming
them when starting the program (`mp3redactor.exe D:\Music`) all do the same: the list is replaced by what you
opened. A folder is searched including its subfolders while File > Include Subfolders is ticked (the default);
untick it to open just the folder itself. View > Refresh List (F5) picks up files added since. Edit > Filter
List (Ctrl+F) shows only the files whose name, title, artist, album or path contains a text; actions then reach
only the files shown, and an empty text shows them all again.

## Command line

The one exe (`mp3redactor.exe`, or `python main.py` from source) is also the command line. When its first
argument is a command name, it runs that command and the window never opens; with no command the window starts
as usual, and with a file or folder (`mp3redactor.exe D:\Music`) the window starts and opens it, like File > Open
Folder. `mp3redactor --help` lists the commands and
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
mp3redactor notes    PATH...  [--kind artist|album|genre|both|all] [--write] [--include-uncertain] [-n] [--no-discogs] [--no-cache] [--update] [--update-legacy] [--show-drafts] [-R]
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
are checked before any file is touched; a bad one stops the command with exit code 2. When the app's change
history is switched on (Tools > Preferences > History; opt-in, off by default), `set` and `redact` record their
changes there like the app does, so Tools > History can restore the old values; with it off nothing is recorded.

| Option | Meaning |
| --- | --- |
| `-s FIELD=VALUE`, `--set FIELD=VALUE` | Set a field (repeat for several). |
| `--clear FIELD` | Empty a field (repeat for several). |
| `-n`, `--dry-run` | Show the old and new value of each field, save nothing. |

Field names are case-insensitive and accept the plain, spaced, underscored or hyphenated spelling (`albumartist`,
`Album Artist`, `album_artist`, `album-artist`). The fields are: title, artist, albumartist, album, track, discnumber, year, genre,
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

Steps: `integrity`, `bpm`, `key`, `loudness`, `deep_check`, `path_tags`, `tags`, `discogs`, `cover`, `lyrics`, `rename`,
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
| `notes` | `folder`, `kind`, `note`, `status` (`would-write`, `written`, `review`, `no-match`, `has-note`, `failed`), `message`, `confidence`, `level`, `level_reasons`, `suggested_levels`, `title`, `reasons`, `sources`, `candidates`, `problems`, `draft` (with `--show-drafts`) | `total`, `confident`, `uncertain`, `none`, `written`, `would_write`, `has_note`, `failed`, `dry_run`, `cancelled` |
| `m4b` | `folder`, `title`, `output`, `status`, `message`, `chapters`, `minutes`, `match`, `notes` | `books`, `failed`, `dry_run`, `bitrate_kbps` |

### notes

`mp3redactor notes PATH... [--kind KIND] [--write] [--include-uncertain] [-n] [--no-discogs] [--no-cache] [--update] [--update-legacy] [--show-drafts] [-R]`

Drafts the short Markdown notes that the Pluteus media server shows beside a folder: `artist.md` in an artist
folder, `album.md` in an album folder, and (with `--kind genre` or `all`) `about.md` in a genre or collection folder
(`Music/Genre/Artist/Album (Year)/01 Song.mp3`). **A report by default**: every folder is looked up on MusicBrainz and
Wikipedia (and Discogs, when a token is saved under Tools > API Keys or in `DISCOGS_TOKEN`; without one the notes are
just thinner) and listed with how sure the match is; nothing is written. `--write` writes the drafts (`status: draft`
in the front matter) of the **confident** matches only. A match is confident when at least one of the folder's own album
titles is found in the candidate's discography (so "Cristina" is not Cristina D'Avena) and nothing equally good
competes with it. Uncertain and unmatched folders (to be decided on the review page of Tools > Create Notes) are listed with the reasons, the candidates and the levels they could
be (`artist`, `genre-collection`, `skip`), and are never written. A note is never overwritten, and a folder that already
has `artist.md`, `author.md`, `series.md`, `album.md` or `about.md` in any letter case is left alone. The album note
carries the tracklist and credits (no comparison with the files in the folder: Pluteus shows what is there); the artist note lists the artist's FULL discography from
MusicBrainz as plain `- Title (Year)` lines, grouped by release type (Studio albums, Live albums, Compilations, EPs,
Singles, Remixes, Soundtracks, Releases; demos, mixtapes and similar are left out), not only what the folder has:
Pluteus compares the lines with the folders next to the note and shows live "in library" / "missing" badges. The notes
carry no `[[wiki links]]`, no "in this library" list and no loose-track count. Example (Ultravox): a `Studio albums`
heading with the lines `- Ha! Ha! Ha! (1977)`, `- Vienna (1980)`, `- Quartet (1982)`, then a `Compilations` heading
with `- The Collection (1984)`.

The levels come from the folder structure (what holds audio is an album, its parent an artist, a folder of artists
a genre; the folder you start from is the library and gets no note) and from the albums' tags: a folder whose albums are
by different artists (Soundtracks, Various Artists) is a collection (`about.md`), never an artist.

| Option | Meaning |
| --- | --- |
| `--kind KIND` | `artist`, `album`, `genre` (the `about.md` of a genre or collection folder), `both` (artist + album) or `all`. The default is `artist`: fast, no album lookups at all (the albums' tags and folder names are still read locally to verify the artist). |
| `--write` | Write the confident drafts. Without it the command only reports. |
| `--include-uncertain` | With `--write`: also write the uncertain matches from the engine's best candidate, as drafts. Never over an existing note, never for "nothing found", never when the level is unclear (listed with the reason). Marked `from_uncertain` in the report and in `--json`. |
| `-n`, `--dry-run` | Never write, even with `--write`: show what would be written. |
| `--no-discogs` | Do not use Discogs even when a token is saved. |
| `--update` | Also list the notes made by an older version of this app (stamped, an older note format, nobody edited them) as updates, and with `--write` replace ONLY those: the old note goes to the Recycle Bin. A note edited by a person, a note this app did not write and a note that is already current are never touched (the summary says how many were left alone). |
| `--update-legacy` | With `--update --write`: also replace the notes of the first versions (no stamp, so it cannot be known whether they were edited; still the Recycle Bin, still never an edited or foreign note). |
| `--no-cache` | Do not use the disk cache of earlier answers (see below): every lookup is asked again. |
| `--show-drafts` | Print each draft note (with `--json`: include it in the row). |
| `-R`, `--no-recurse` | Look only at the folders named, not at the folders below them. |

**Stamps and updating old notes.** Every note this app writes carries three plain front matter lines Pluteus ignores:
`generator: mp3redactor <version>`, `note-format: 2` and `body-sha: <hash of the note as written>`. A later version can then tell a note
of an older shape that nobody edited (`--update` replaces it) from one you edited (even just changing `status: draft` counts), from a note of
the first versions (no stamp: only with `--update-legacy`, because nobody can tell whether it was edited) and from a note somebody else
wrote (never touched). In the app, Tools > Create Notes has the checkbox **Also review notes made by an older version** (off every time):
the old notes show up as updates with an *Update* badge, a *Before / after* diff and, for the first versions' notes, a warning; they are
never pre-ticked, and updating asks first and sends the old note to the Recycle Bin (you can restore it from there).

**The lookup cache.** The answers of MusicBrainz, Wikipedia and Discogs (public data, plus the artist and album names asked
about) are kept for 30 days (one day for "not found" and empty results, errors never) in `mp3redactor_notes_cache.sqlite` next to the
settings file, at most 200 MB, so a repeated, cancelled or resumed run is answered from disk. Preferences > Create Notes
switches it off and has **Clear the notes lookup cache**; the same URL is never asked twice within one run either way. The
last line of a run says how many requests were made and how many came from the cache.

Exit code 0 when done, 1 when a note could not be written or a lookup failed, 130 when interrupted (what was written
stays). With the opt-in history on, each written note is recorded and can be undone (moved to the Recycle Bin, only if
it is unchanged).

### Creating notes for a whole library

- **In the app:** Tools > Create Notes..., then pick the library root in the list (or **Another folder...**), with
  **Include subfolders** ticked and *Artist notes only* chosen for the fast first pass. On the review page switch to the list view, check the
  confident pre-ticks, optionally **Tick best guesses** (uncertain matches rebuilt around their top candidate: they
  can be wrong, they are still drafts), then **Write N ticked notes**.
- **On the command line:** `mp3redactor notes <library root> --kind artist --write` writes the confident artist notes;
  add `--include-uncertain` to also write the uncertain ones from the engine's best candidate (drafts, never over an
  existing note, never for "nothing found" or when the level is unclear; those are listed with the reason). The report
  marks each note written from an uncertain match so you can review it afterwards.

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

## Design principles

- **Nothing is written that you did not confirm.** Edits and lookup results stay in memory until Save All;
  Redact applies only guesses above its confidence threshold and lists the rest for review.
- **Nothing is deleted for good.** Replaced originals go to the Recycle Bin (or are kept beside the file when a
  drive has none), and nothing is overwritten.
- **Fail loud, not silent.** A file that cannot be read, saved or checked says so in its row and in the report;
  a missing tool shows TOOL MISSING rather than a fake result.
- **Use the real tools.** Tags go through mutagen; checks shell out to mp3val, ffmpeg/ffprobe, keyfinder-cli
  and fpcalc.

## License

Licensed under the [GNU General Public License v3.0 or later](LICENSE). The GUI is built on PyQt6, which
Riverbank Computing licenses under GPL v3 (or a paid commercial license).
