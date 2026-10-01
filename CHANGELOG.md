# Changelog

## 2026-10-01#02 -- Look up albums in the offline MusicBrainz database; Redact uses it first

- **New: Metadata > Look Up > MusicBrainz (Local Database)...** (also in the right-click Look Up menu). The same review dialog as Look Up via MusicBrainz -- one album per folder, Other Matches for other editions, a Search Query form -- answered instantly from the database you built in Tools > MusicBrainz Database, with no network and no one-request-a-second wait. Without a database it offers to open that dialog.
- **How it finds the album:** by the files' own MusicBrainz Album Id tag (the exact release), by barcode (type one into Album), by recording ids (a file's MusicBrainz Track Id tag, or AcoustID fingerprint hits -- AcoustID itself still needs the network and is simply skipped when offline), then by artist and album text (words in any order, "&" = "and", accents and punctuation ignored; "Beatles, The" and "The Beatles" are the same artist; a "Various Artists" folder is searched by album alone). Files are paired with tracks by number, title and length, exactly as online; multi-disc releases get their disc number, and vinyl sides (A1, B2...) are numbered down each disc as MusicBrainz does.
- **Fills** title, artist (each track's own credit on compilations), album artist, album, track, disc, year, MusicBrainz ids, and, new here, **Publisher (the label), Catalog Number and Release Country**. No genre and no cover: MusicBrainz tags are not part of the CC0 data, and the Cover Art Archive is online (use Set from Folder Image, or Redact's cover step).
- **Redact's "Fill missing tags (MusicBrainz / AcoustID)" step asks the local database FIRST** when one is set up, and asks online MusicBrainz and AcoustID only for what is still missing; with the network down it uses the local answer as it is (a note says AcoustID or MusicBrainz was skipped). It still fills EMPTY tags only and never overwrites (unless its option says so). Confidence: the file's own MusicBrainz Album Id naming the release is 97%; its own recording id tag on the track 95%; an AcoustID hit keeps AcoustID's score; a match by tags is title/length/track number scaled by how far album and artist agree, and reaches the default 90% only when artist and album are exact and the release has as many tracks as the folder has files (otherwise at most 85%, listed under Needs review). A single track with no album is found by title only when the database was built with the track search index, at most 80%. A configured database that can't be opened is a note, and the step carries on online. With no database set up nothing changes.
- Not yet checked against a real dump: the tests use small synthetic databases only.

## 2026-10-01#01 -- Build an offline MusicBrainz database (Tools > MusicBrainz Database)

- **New: Tools > MusicBrainz Database...** builds a compact offline database from MusicBrainz's core data dump, for the offline lookup that follows in the next release. You download `mbdump.tar.bz2` yourself from musicbrainz.org/doc/MusicBrainz_Database/Download (about 7 GB; the app never downloads it and does not need it unpacked), pick it in the dialog and click Build Database. The build streams the archive without unpacking it, shows progress, can be cancelled at any time, and replaces an existing database only once the new one is complete. It checks the dump's schema number first and says clearly if the dump is of a different layout than the app knows.
- **What is kept** (all choosable): official releases only (on), release types (Album, EP, Single, Compilation, Soundtrack and Live on; Broadcast, Other and the spoken-word and similar types off), skip undated releases (off), track titles (on; needed to match files to tracks) and an optional track search index (off; much bigger). Releases carry the artist credit with its join phrases ("Paul McCartney & Wings"), the earliest release date and country, label, catalogue number, barcode and format ("2×Vinyl"); a prebuilt full-text index makes searching by artist and album instant.
- **Licence:** only MusicBrainz's core data (CC0, public domain) is read. The derived data (tags and genres, ratings, annotations) is not CC0 and is never used, so there is no genre.
- The dialog shows the free space on the database's drive and warns if it is probably too little (a default build needs roughly 12 GB while it runs). The database and dump paths are saved in the settings and, because they belong to this computer, are exported only in the "this computer only" section of Export Settings and imported only if the file exists.
- Adopt redactor_common 2026-10-01-02 (dump readers for tar archives and PostgreSQL COPY files, the MusicBrainz table layout).
- Not yet checked against a real dump: the table layout comes from MusicBrainz's published schema and the tests use small synthetic archives only.

## 2026-09-30#21 -- Adopt redactor_common 2026-09-30-15

- Adopt redactor_common 2026-09-30-15: Redact saves retry briefly when Windows antivirus/indexer briefly locks a file (the shared rename/commit helpers now retry too).

## 2026-09-30#20 -- Saving survives a briefly locked file

- **Save and Redact no longer fail when Windows holds a file for a moment** (a virus scanner checking the fresh temporary copy, the search indexer). Copying the working file, opening it, writing the tags and swapping it in are retried a few times (6 attempts, 0.15 s apart) when the failure is a file-lock error. Other errors still fail at once, the original is untouched and the temporary copy removed, and if the lock never clears the message now says the file is locked by another program.
- The same retry protects the converter's final rename and the settings file write.
- Test-only: new tests for transient and permanent locks.

## 2026-09-30#19 -- Redact: fill missing tags from Discogs

- **New Redact step "Fill missing tags (Discogs)"**, right after the MusicBrainz / AcoustID step. It finds the file's album on Discogs (by its tags, which the MusicBrainz step may just have filled, or the folder name) and fills tags that are EMPTY only: title, artist, album artist, album, track, year, genre (Discogs genres plus styles; an option turns the styles off), label, catalogue number and country. Existing values are never replaced.
- **How sure it is:** 93% (applied at the default 90% threshold) only when the artist and album match exactly, the Discogs release has as many tracks as the folder has files, and one release clearly fits best. An exact artist and album with a different track count, or several equally good pressings, is 70%; a looser match is 60% at most; a file whose own title doesn't resemble its paired track is capped at 60% too. Below the threshold the match is listed under Needs review and nothing is written.
- **Needs a Discogs token** (Tools > API Keys). The step is on by default only when a token is stored; with none it is skipped with a note saying how to set one (not a failure). Offline is a note too. Discogs is asked about once a second, release details are fetched once per run, and if Discogs answers HTTP 429 ("too many requests") the step is skipped for the rest of the run.
- Test-only: the key-detection cancel test no longer depends on thread timing.

## 2026-09-30#18 -- Look Up via Discogs, and Tools > API Keys

- **New: Metadata > Look Up > Discogs...** (also in the right-click Look Up menu). Like the MusicBrainz lookup it finds the release each selected folder belongs to (by Artist + Album + Year from the tags, or an "Artist - Album (Year)" folder name), shows the release's cover next to your own, lists other pressings under Other Matches (label, catalogue number, year, country) and lets you correct the search and run it again. Apply is review-only: anything that would overwrite a different value goes through the per-field overwrite review first, and changes are written on Save. Files are paired with tracks by track number, then by title; vinyl positions (A1, B2...) are numbered down the release, "1-3", "2.4" and "CD1-5" carry the disc. Discogs' "Artist (2)" disambiguation numbers are dropped and "Various" becomes "Various Artists". "Add Styles to Genre" (on) writes genres then styles ("Rock; Prog Rock").
- **New fields: Publisher (the label), Catalog Number and Release Country**, read and written like any other tag (TPUB, TXXX:CATALOGNUMBER and TXXX:MusicBrainz Album Release Country, as Picard writes them) and hidden in the table until you show them (Tools > Columns).
- **New: Tools > API Keys...** holds your Discogs token (create one at discogs.com under Settings > Developers). It is stored in your computer's secure credential store (Windows Credential Manager), never in the settings file or in a log; a `DISCOGS_TOKEN` environment variable also works. Without a secure store you are asked once whether an unencrypted file may be used instead. Look Up via Discogs opens this dialog first if no token is set.
- Discogs is asked at most about once a second; on HTTP 429 ("too many requests") the lookup stops with a clear message instead of asking again. Shown data carries a "Data from Discogs" note.
- Fixed: pressing OK in Tools > Preferences reset every other preference (Redact recipe, patterns, columns, tool paths) to its default; it now changes only what that dialog edits.

## 2026-09-30#17 -- Export / Import Settings

- **File > Export Settings... and Import Settings... now work.** One file, `mp3redactor-settings.json`, carries your preferences to a fresh install or another computer. Import shows what would change, per section, and applies nothing until you confirm.
- **Ticked by default (portable):** Redact recipe; rename, move and path patterns (history and saved); column layout, visibility and order; field defaults (ASCII filenames, zero-padding, numbering, backup handling); custom genres and languages.
- **Unticked, opt-in, marked "this computer only":** external tool paths (mp3val, ffmpeg, ffprobe, keyfinder, fpcalc) and the last-used folder and library root. After an import you are offered the External Tools dialog to re-detect tools.
- **Secrets are never included** (this app stores none today; any secret-looking setting is dropped on export and import). Files from another Redactor app are rejected; unknown entries in a file are ignored.
- Column changes apply immediately; everything else is used the next time it is needed. Zoom, panel visibility and window size are not saved by this app, so they are not part of the file.

## 2026-09-30#16 -- Ctrl+S saves everything again

- **Ctrl+S saves ALL changed files again**, as it always did, and as in the other Redactor tools. This reverses the note in #13 ("Save now saves the selected files"): there is no selected-only Save any more. The single **File > Save All** (toolbar button "Save All") has Ctrl+Shift+A as its main key and Ctrl+S as a secondary key for one release (after that, Ctrl+Shift+A only).
- The toolbar has one save button instead of Save and Save All.

## 2026-09-30#15 -- Shortcut check after the menu move

- Audited every keyboard shortcut against the family standard: **none of this app's existing keys had to change** (Ctrl+O, Ctrl+Shift+O, Ctrl+S, F2, Ctrl+E, Ctrl+I, F5 / Ctrl+R, Ctrl+Z / Ctrl+Y, Ctrl+Shift+E, Ctrl++ / Ctrl+-), so no old-key aliases are needed. New standard keys: Save All Ctrl+Shift+A, Remove from List Delete, Search and Replace Ctrl+H, Apply Ctrl+Return, Reset Zoom Ctrl+0, Preferences Ctrl+, , Command Palette Ctrl+K.
- **F1 no longer opens About** (F1 is reserved for Help contents); About is reached from the Help menu.
- Tests now pin the shortcut table.

## 2026-09-30#14 -- Command palette

- **New: Ctrl+K opens a command palette** (also View > Command Palette). Type part of any menu command's name to find and run it without hunting through the menus; greyed commands are listed but cannot be run. The menu layout is also now checked automatically against the family's standard skeleton (labels, letters, shortcuts).

## 2026-09-30#13 -- New menu layout (family skeleton)

The menus now follow the same layout as the other Redactor tools: **File, Edit, View, Metadata, Analyze, Tools, Help**. Only the menus move in this release; every keyboard shortcut is unchanged (shortcut fixes follow in a later release, with the old keys kept for a while).

- **Where things went:** Load Files / Load Folder are now **Open Files / Open Folder**. Import > Parse Filename and Look Up via MusicBrainz are under **Metadata**; Fetch Lyrics is **Metadata > Look Up > Lyrics**; Edit Lyrics, Number Tracks and Cover are under Metadata too. The integrity check, Fix, Deep Check, BPM, Key and Loudness are under **Analyze** (the "for Selected Files" wording is gone; they still act on the selection). Undo, Redo, Apply, Redact, Edit Redact Recipe and Auto-Number are under **Edit**. Refresh List moved to **View**. Import & Convert is **File > Import and Convert**; Rename / Export Files is **Rename / Export / Move**. Everything from Settings is under **Tools**: Preferences, External Tools (was Locate External Tools), Columns, Genres, Languages. Help: Changelog, Credits, About.
- **New entries:** File > **Remove from List** (Delete key, asks first if a removed file has unsaved changes) and **Clear List**; Edit > **Search and Replace** (Ctrl+H) and **Change Case** on the selected files (in memory, Undo reverts, Save writes); **Save All** (Ctrl+Shift+A); View > Show Metadata Panel, Zoom In / Zoom Out / Reset Zoom (Ctrl+0); Tools > Preferences now has Ctrl+, . Export Settings / Import Settings are in the File menu but greyed out until they are wired up.
- **Save now saves the selected files** (Ctrl+S); **Save All** saves every changed file, which is what Save File(s) used to do.
- **Right-click menu is shorter:** Open in Default App, Open Containing Folder, Copy Path | Rename File | Look Up | Organize | Analyze | Cover submenus | Edit Lyrics | Redact | Remove from List.
- **Toolbar:** Open Files, Open Folder | Save, Save All | Apply | Redact | Undo, Redo | Panel, zoom.
- redactor_common pinned to 2026-09-30-13.

## 2026-09-30#12 -- Redact recipes keep their patterns

- **A saved Redact recipe keeps the pattern it was saved with.** The Rename, Move into folders and folder-path tag steps no longer drift when you later change the pattern in Rename / Export Files or Parse Filename. The first time you open Edit Redact Recipe (nothing saved yet), each pattern is pre-filled with the one currently in effect, so pressing OK pins it. A recipe saved earlier just keeps its patterns; an empty one keeps following the app's current pattern. No need to recreate anything.
- **The recipe editor shows the pattern trail:** a dropdown of recent patterns (newest first), a line "In effect: ... -- set in this recipe / follows: ...", a preview on a sample song, and a **Use fallback** button that clears the pattern so the step follows the app's current one again.
- redactor_common pinned to 2026-09-30-12.

## 2026-09-30#11 -- Read tags back out of the folder path

- **Import > Parse Filename now understands folders.** A pattern containing `/` (for example `%albumartist%/%album%/%track% - %title%`, the mirror of Move into folders) switches the dialog to path mode: the last part matches the file name, the parts before it the folders above, and a Library Root row and a confidence column appear. The library root is the same one Move into folders uses, and is remembered. Rows are ticked from 50% confidence. Filename-only patterns work as before; the starting pattern is still a filename pattern when the history has both kinds.
- **Redact gets a "Fill empty tags from the folder path" step** (on by default, runs before the online tag lookup). It fills only EMPTY tags, using a folder pattern option that starts as the most recent folder pattern in the history (else `%albumartist%/%album%/%track% - %title%`). It acts only when a library root is set and the file is under it; with no root the report notes how to set one. Matches at or above the recipe threshold (90%) are applied; weaker ones are listed under Needs review with the matched and missing parts. Files in the same folder in one run corroborate each other, which is what lifts a bare `%album%` folder over the threshold.
- redactor_common pinned to 2026-09-30-11.

## 2026-09-30#10 -- Move into folders

- **File > Rename / Export Files has a third mode, "Move into folders".** Pick a library root folder and a pattern with `/` for sub-folders (for example `%albumartist%/%album%/%track% - %title%`); the preview shows where each file will go, missing folders are created, and nothing is overwritten (a name already taken gets "(2)"). The root is remembered between runs.
- Moves are logged as one batch: **Undo Last Rename** puts the files back and offers to remove the folders the move created. After a move, the list offers to remove source folders that are now empty.
- **Redact gets a "Move into library folders" step** (off by default, runs last, after Rename) with its own folder/filename pattern option, starting as the pattern last used in Move into folders, and the same library root. Each file is saved first, then filed; a missing root is noted in the report and the file stays put.

## 2026-09-30#09 -- Redact on the improved engine

- Redact now runs on redactor_common 2026-09-30-10 (pinned). The report text is the same, with two visible differences: a file that is skipped (unsaved edits, unreadable) now appears under a **SKIPPED** heading instead of as an aborted file, and notes such as "mp3val not found" or "Tag lookup unavailable" are listed per file under **NOTES** instead of as one grouped block below the report.
- **Rename by saved pattern has its own pattern.** It is now an editable option of that step in Edit Redact Recipe, starting as the pattern last applied in Rename / Export Files. Before, it read the newest entry of the pattern history, so using Parse Filename could silently change how Redact renamed files; the history is now only the starting value the first time.
- **Fixed: a crash mid-Redact could leave a hidden scratch file (`.mp3redactor-redact-*.mp3`) that Load Folder then listed as a song.** Load Folder and Refresh ignore those files, and the next Redact removes any left in the folders it works on (the results say how many).
- PyInstaller spec names `send2trash` as a hidden import (the Recycle Bin used by Redact).

## 2026-09-30#08 -- Redact: one button to check, fix and fill in a folder

- **New: Operations > Redact (Ctrl+Shift+E, also on the toolbar)** runs a recipe on the selected files (or every loaded file, after asking, when nothing is selected) with no further questions. Each file is fixed and saved IN PLACE and the original goes to the Recycle Bin -- that copy is the undo; Redact is not on the in-app Undo stack (the stack is cleared). If the Recycle Bin can't take the original it is kept beside the new file as `<name>.redact-orig.mp3`.
- Default recipe: integrity check (mp3val, and fix what it can, on a copy), BPM, key, loudness (ReplayGain), fill MISSING tags from MusicBrainz/AcoustID, add a missing cover (folder image, or the Cover Art Archive). Off by default: deep check (slow) and rename by the saved pattern (on only once you have used Rename / Export). Scan results are stamped inside the file; a tool failure (TOOL ERROR / not installed) is never stamped and never triggers a fix.
- Guesses (tag lookup, cover) are applied only at confidence >= the threshold (default 90%); below it they are listed under **Needs review** in the results and left alone. Existing tag values are never replaced unless the recipe option says so; an existing BPM/Key tag is kept by default.
- **Operations > Edit Redact Recipe...** chooses, orders and configures the steps and sets the threshold; the recipe is stored in the settings file.
- Files with unsaved edits, or that failed to load, are skipped and named in the report. Every new file is verified (opens, same audio length, tags read back) before it replaces the original.
- Needs `send2trash` (added to requirements.txt); redactor_common pinned to 2026-09-30-06.

## 2026-09-30#07 -- A failed tool is not a bad file

- **Fixed: a timed-out or crashed mp3val/ffmpeg could stamp a false ERROR into a healthy file.** Integrity and Deep Check now show `TOOL ERROR` (orange, message in the tooltip) when the tool itself failed -- timeout, could not start, or exited with no findings -- and nothing is stamped or marked unsaved. An earlier good stamp stays as it was.
- Real findings are unchanged: mp3val ERROR/WARNING lines and ffmpeg decode errors are still ERROR/WARNING and are stamped. After a Fix that ends in TOOL ERROR the file is re-read from disk (the tool may have modified it) but not stamped.

## 2026-09-30#06 -- Scan results are remembered in the file

- **Integrity and Deep Check results now carry a timestamp that is saved in the MP3 itself** (ID3 frames `REDACTOR_INTEGRITY` and `REDACTOR_DEEP_CHECK`, value like `OK;2026-09-30T14:05:11Z`), so the record survives copying the file. Running a check (or Fix) marks the file as unsaved; Save writes the stamp.
- The Integrity and Deep Check columns show `OK · 2026-09-30 14:05` (local time) instead of "UNCHECKED" for a file scanned before; the tooltip has the full time. Files never scanned still say UNCHECKED. A stamp read from disk is the last known result and does not mark the file unsaved.
- A missing tool or a crashed check does not stamp anything. Unreadable stamp values are ignored and left untouched.

## 2026-09-30#05 -- Open in Default App

- Right-click menu gains Open in Default App (redactor_common 2026-09-30-04).

## 2026-09-30#04 -- Lyrics must match the song, sturdier settings

- **Fixed: Fetch Lyrics could write another song's lyrics.** The first LRCLIB hit was used blindly. Now a result must match the file's artist and title (ignoring case, accents and "(Remastered)" suffixes); the first matching hit is used, and if none matches the file is reported as "no lyrics found" with the closest result named.
- **Fixed: a hand-edited settings file with a bad number or yes/no value crashed startup.** Bad values now fall back to their defaults, and settings are written to a temporary file first so a crash can't truncate them.
- **Fixed: MusicBrainz lookup could write Track "0"** (or Disc "0") when a release had no usable position; those are left alone now.
- The Edit Lyrics header shows artist/title as plain text (a "<" in a tag was being read as markup).
- "Not found" messages for external tools no longer say ".exe" (the app also runs on Linux and macOS).

## 2026-09-30#03 -- Progress for scans and copies, safer imports, mp3val reload

- **Progress while scanning and copying.** Load Files/Folder and Refresh List show a progress dialog while they look for MP3 files, Export by Pattern shows progress (and can be cancelled) while it copies, and loading converted files shows progress too -- on a big or networked library the window no longer looks frozen.
- **Fixed: Import & Convert could overwrite its own output.** Two sources that map to the same name (a.flac and a.wav) now get distinct names (a.mp3, a (2).mp3) instead of the second silently replacing the first.
- **Fixed: a failed or timed-out conversion left a partial .mp3 behind.** Conversions are encoded to a temporary file and moved into place only on success.
- **Fixed: Fix Integrity left stale data in memory.** After mp3val rewrites a file, its tags, duration and bitrate are re-read (pending edits are kept), so a later Save no longer writes the old state back.

## 2026-09-30#02 -- Saving no longer damages tags

- **Fixed: Save rewrote tags you never touched.** Editing one field collapsed multi-valued frames (an artist tag holding two names became one), several comment/lyrics frames became one, and a comment's language was reset to English. Save now rewrites only the fields you actually changed; a changed comment or lyrics text keeps its language.
- **Fixed: Save upgraded ID3v2.3 files to v2.4.** The file's tag version (and an existing ID3v1 tag) is kept.
- **Safer writes.** Tags are written to a temporary copy next to the file, which then replaces it, so a crash or full disk can't leave a half-rewritten MP3.
- **Fixed: files that failed to load could be overwritten with blank tags.** They're now skipped by edits, detections and Save, and the writer refuses them.
- **Fixed: Undo.** After a rename, Undo no longer points the file back at its old name, and after a Save, Undo marks the file unsaved again so the restored values are written.
- **Fixed: one odd file could abort a whole scan.** Tool output (mp3val, keyfinder-cli, fpcalc) is decoded as UTF-8 with replacement, and a check that fails on one file is reported on that file only.
- mp3val: a non-zero exit with no WARNING/ERROR lines is now reported as an error, not "OK".
- redactor_common 2026-09-30-02 (from 2026-09-30-01).

## 2026-09-30#01 -- Zero-padding remembered, MusicBrainz in the right-click menu

- Rename / Export by Pattern remembers the zero-pad checkbox and width,
  and Auto-Numbering remembers its "Zero-pad to" width.
- Right-clicking a file now has **Look Up via MusicBrainz...**, same as
  the Import menu.
- redactor_common 2026-09-30-01 (from 2026-09-29-04): the shared dialogs
  that make the padding memory possible.

## 2026-09-29#06 -- Shared library update

- redactor_common 2026-09-29-04 (from 2026-09-29-03): a fix to the shared preview loader, which this app doesn't use -- no change in behavior here.

## 2026-09-29#05 -- Edits stay on the file you selected

- **Fixed: an edit could land on the wrong file.** After Apply (or Parse Filename, Auto-Numbering, a MusicBrainz lookup, Undo...) the table is redrawn, and the selection stayed on the same row *number* -- which, in a sorted table (the default), now held a different file. The panel then showed that other file, and the next Apply edited it. The selection now stays on the same files.
- Checked for the stale-panel bug fixed in cbzredactor 2026-09-29#05: not present here (the panel only writes fields you tick, and is reloaded after every edit); a regression test now covers it.

## 2026-09-29#04 -- Undo Last Rename

- **File > Undo Last Rename...**: renames are now logged (Rename/Export by Pattern, Rename File) and the newest one can be taken back -- even after restarting the app. It shows what will be renamed back first, and never overwrites: a file that has moved since, or whose old name is taken again, is skipped and reported. The in-app Undo still covers metadata edits only.
- redactor_common 2026-09-29-03 (from 2026-09-29-02).

## 2026-09-29#03 -- Small fixes

- Message boxes with several wide buttons keep their text next to the icon (on Linux the text could end up in a narrow strip far to the right).
- Look Up via MusicBrainz: rows are album folders, and the dialog now says so ("Folder" column, "Found something for 2 of 5 folder(s)" instead of "file(s)").
- redactor_common 2026-09-29-02 (from 2026-09-29-01).

## 2026-09-29#02 -- Identify songs by their sound

- **Look Up via MusicBrainz now also listens.** With `fpcalc` set up
  (Chromaprint's free tool -- Settings > Locate External Tools, or on
  your PATH), every file is fingerprinted and looked up on AcoustID,
  which knows the exact MusicBrainz recording: each file then pairs
  with its track for certain, and a folder with useless tags and names
  ("New Folder (3)", "Track 01") is found through the releases that
  hold its recordings. Without fpcalc it works from tags and names, as
  before.
- AcoustID is asked at most 3 times a second; each folder is
  fingerprinted once (Search This Item doesn't redo it).
- Settings > Locate External Tools: a row for fpcalc, and on Linux/Mac
  its Browse button now shows all files (it only offered .exe files).

## 2026-09-29#01 -- ASCII-safe filenames

- **Rename/Export by Pattern: "ASCII-safe filenames"** -- new names use only plain ASCII letters, digits and punctuation: accents removed (é -> e, å -> a), æ -> ae, ø -> o, ß -> ss, typographic quotes and dashes made plain, and anything with no ASCII form (other scripts, emoji, symbols) dropped. For old file systems, network shares, e-readers, car stereos and sync tools that mangle anything else. The preview updates as you tick it, and the choice is remembered.
- redactor_common 2026-09-29-01 (from 2026-09-28-07).

## 2026-09-28#04 -- Shared release notes

No change to the app. The GitHub Release notes (every CHANGELOG section since the previous release) are now built by redactor_common's shared script instead of a copy in this repo (redactor_common 2026-09-28-07, from 2026-09-28-06).

## 2026-09-28#03 -- Linux tool lookup fix

- **Linux: external tools are found again.** The first Linux release looked for `ffmpeg.exe`, `mp3val.exe` and `keyfinder-cli.exe` by their Windows names, so on Linux it found none of them on PATH -- integrity checks, loudness, conversion and key detection didn't work. They're now found as `ffmpeg`, `mp3val` and `keyfinder-cli` (redactor_common 2026-09-28-06). Windows is unchanged.

## 2026-09-28#02 -- Linux version

- **A Linux download** alongside the Windows one:
  `mp3redactor-linux-x86_64.tar.gz`, a single self-contained program for
  64-bit desktop Linux (glibc 2.35+: Ubuntu 22.04+, Debian 12+, Fedora
  36+, Mint 21+). Built with Python 3.12 like the Windows version; the
  whole test suite runs on Linux as part of every release build.
  External tools are found on your PATH, as on Windows.
- redactor_common 2026-09-28-05 (from 2026-09-28-02): on Linux the settings live in `~/.config/mp3redactor/`, the standard place, instead of next to the program (Windows unchanged).

## 2026-09-28#01 -- Look up albums on MusicBrainz

- **Import > Look Up via MusicBrainz...**: finds the MusicBrainz release
  each selected folder belongs to (one album per folder) and matches
  every file to its track -- by the folder's Artist + Album tags or its
  "Artist - Album (Year)" name, then by track number, title and length.
  Other editions of the album (other countries, remasters) are offered
  under Other Matches; Artist/Album can be corrected and searched again.
  The release's cover (Cover Art Archive) is shown next to your own for
  comparison.
- Applies title, artist, album artist, album, track, disc, year and the
  **MusicBrainz Album Id / Track Id** (new fields, hidden columns by
  default, written the way MusicBrainz Picard writes them), through the
  per-field overwrite review; one Undo step; written on Save.
- Lookups run in the background (the window stays responsive), at most
  one MusicBrainz request per second as its terms ask.
- redactor_common 2026-09-28-02 (background lookups, shared lookup and
  overwrite-review dialogs).

## 2026-09-23#03 -- resizable cover area

The cover in the side panel is now resizable: drag the divider between
the tag fields and the cover to make the image as big (or small) as you
like -- it used to be capped at a fixed 260 px. The fields scroll rather
than squash when the cover takes more room. Built on redactor_common's
shared `ImagePanelSplitter`/`ImagePreviewBox` (2026-09-23-02), the same
resizable image area cbz, epub and video now use.

## 2026-09-23#02 -- cover art (roadmap item 8)

Embedded cover art (ID3v2 `APIC`), read and written with `mutagen`:

- **Cover column** -- Yes/No plus a thumbnail. Thumbnails load lazily
  and in the background: only rows on screen ever read or decode a
  cover (the same mechanism epub/cbz use), and none at all while the
  column is hidden.
- **Cover preview** in the side panel for the selected file, read and
  downscaled off the GUI thread.
- **Set Cover from Image File...** -- JPEG/PNG embedded exactly as-is;
  WebP/BMP/GIF/TIFF converted (PNG if transparent, else JPEG).
- **Set Cover from Folder Image** -- each selected file gets the
  `cover.jpg` / `folder.jpg` / `front.jpg` (etc.) sitting next to it, so
  a selection spanning several albums gets each album's own art at once.
- **Remove Cover** and **Export Cover to Image File...**
- Operations > Cover, the table's right-click menu, and buttons under the
  panel's cover. Undoable like any edit; written on Save as a single
  front cover (replacing any existing pictures). Saving other tags never
  touches existing pictures.
- Covers are not held in memory for every loaded file -- only a pending,
  unsaved change is -- so a large library costs no extra memory.

## 2026-09-23#01 -- Shared-code consolidation

Moves onto redactor_common 2026-09-23-01 (was pinned at 2026-09-13-03,
missing ten days of shared fixes -- including the progress dialog that
jumped in size with long filenames).

- **ffprobe/ffmpeg output decoded as UTF-8** via the shared `run_tool()`
  -- the Windows locale default (cp1252) garbled non-ASCII tag text in
  ffprobe's JSON. `mp3val`/`keyfinder-cli` now also get
  `stdin=DEVNULL`.
- **Progress dialogs:** Import & Convert to MP3 and the BPM/key checks
  now use the shared fixed-width `ProgressReporter` instead of two
  hand-rolled dialogs.
- **Crash log** trims whole entries (it used to cut the oldest one off
  mid-traceback) and shows an "Unexpected Error" dialog; startup, app
  paths, pattern history, the Genre/Language list rules and the version
  bump are the shared implementations now. Language names come from the
  family's shared ISO 639 table (same codes and names as before).

## 2026-09-17#01 -- lyrics fetch + write (roadmap item 7)

Implements the long-planned "Lyrics fetch + write" roadmap item:

- **Fetch** via the `lyricy` package's LRCLIB provider
  (`core/lyrics_fetcher.py`) -- a free, keyless REST API (lrclib.net),
  no Settings/API key needed. Searches "`<artist> <title>`" from the
  file's current tags, falling back to the filename when untagged.
- **Write** to the standard ID3v2 `USLT` ("Unsynchronised lyrics/text
  transcription") frame via `mutagen` (`core/tag_writer.py`'s
  `_write_lyrics_frame()`) -- same multi-desc/lang consolidation and
  blank-to-clear convention Comment already uses for its own `COMM`
  frame. Read back on load (`core/tag_reader.py`), same round trip
  BPM/key/loudness get, so a saved fetch doesn't look like it silently
  failed the next time the file is loaded.
- **Not** a `core/fields.py` bulk-edit-panel entry -- full song lyrics
  are long, multi-line text that doesn't fit that panel's single-line
  rows (even Comment's, despite its own unused "multiline" flag).
  Instead: a dedicated **Lyrics editor dialog** (`gui/lyrics_dialog.py`)
  with a real multi-line text box and its own "Fetch from LRCLIB"
  button -- double-click a file's new **Lyrics** table column, or
  Operations menu / right-click > **Edit Lyrics...** (single file).
  A new **Lyrics** table column shows a short "Yes (N lines)"/"Not
  found"/blank indicator, not the full text.
- A bulk **Fetch Lyrics for Selected Files** (Operations menu /
  right-click) fetches for many files at once, thread-pooled across the
  selection like Detect BPM/Detect Key -- each fetch is a network round
  trip, the same "blocked on I/O" shape that already justifies a thread
  pool for those two checks.

## 2026-09-13#04 -- missing mp3tag fields: Album Artist, Disc Number, Composer, Comment, + advanced set

This app's field list was checked against mp3tag's own (the reference
point most people already know this kind of app from) and found
genuinely short -- Album Artist most visibly ("we need at least the
Album Artist, but check for other that are missing"), but several
others too. Added, all as real `core/fields.py` entries -- table
column, bulk-edit panel row, and Rename/Export + Parse Filename
`%placeholder%`, same as every existing field, no special-casing:

- **Album Artist** (`TPE2`), **Disc Number** (`TPOS`, sorts/zero-pads
  numerically like Track), **Composer** (`TCOM`), **Comment** (`COMM`)
  -- shown by default, matching mp3tag's own commonly-visible column
  set (checked against a real mp3tag column list).
- **Album Sort** (`TSOA`), **Artist Sort** (`TSOP`), **Album Artist
  Sort** (`TSO2`), **AcoustID Fingerprint** (`TXXX:Acoustid
  Fingerprint`, matching MusicBrainz Picard's own convention for
  interop), **iTunesAdvisory** (`TXXX:ITUNESADVISORY`) -- mp3tag's own
  "advanced" set: real, editable fields, just less commonly needed, so
  hidden by default (Settings > Add/Remove Columns... or right-click a
  header to show them), same convention as the Encoder/Sample Rate/
  Channels trio already gets.
- **Comment** needed its own read/write handling, not the plain-frame
  loop the others use -- ID3's `COMM` frame keys itself by
  description+language (`"COMM::eng"`), and a file can carry more than
  one from other software. This app treats Comment as a single field
  like everywhere else in the bulk-edit panel, so saving clears every
  existing `COMM` frame (any desc/lang) and writes back at most one
  (`lang="eng"`, empty description).
- **Not added: Covers/Picture.** Embedded artwork is binary image data
  that doesn't fit this plain-text-field shape at all -- it stays its
  own, separate, not-yet-built roadmap item (unchanged from before).

8 new tests in `tests/test_tag_writer.py` (real frame-id checks via
mutagen directly, not just round-tripped through this app's own reader
-- including the multi-`COMM`-frame consolidation case), plus its main
round-trip test extended to cover every new field. Verified end-to-end
via a real GUI bulk-edit Apply -> Save -> reload for Album Artist
specifically, plus confirming the advanced fields' columns exist but
start hidden. Full suite: 156 passed.

## 2026-09-13#03 -- Save now shows which file it's on

Saving Tags... already had a progress dialog; it now also shows the
current filename ("Saving: foo.mp3") via
`redactor_common.gui.run_with_progress`'s new `label_for` param, same
as epub/video's save dialogs. Bumped `redactor_common` to
`2026-09-13-03`.

## 2026-09-13#02 -- Ctrl+E/Ctrl+I export/import shortcut pairing

Rename / Export Files... moves from Ctrl+Shift+R (this morning's
choice) to **Ctrl+E**, and Parse Filename... from Ctrl+E to **Ctrl+I**
-- a deliberate export/import mnemonic pair for the two directions of
the filename<->metadata relationship, requested explicitly. Applied
family-wide via `redactor_common.gui.standard_shortcuts`.

## 2026-09-13#01 -- hotkey audit: Redo, real F2, and family-wide alignment

Full audit of keyboard shortcuts across the whole Redactor family
against Qt's own Windows-standard bindings (verified via
`QKeySequence.keyBindings()`, not assumed). Real changes here:

- **Load Files and Load Folder finally have shortcuts at all**
  (Ctrl+O / Ctrl+Shift+O) -- the single biggest gap the audit found:
  this app's most-used action had no keyboard shortcut whatsoever,
  unlike both its siblings that share this feature.
- **New Redo** (Ctrl+Y, Operations menu and toolbar, right after Undo)
  -- `redactor_common.core.undo.UndoManager` gained real redo support.
- **F2 now directly renames the one selected file** (Explorer
  convention) -- same action the right-click "Rename File..." already
  did, now also reachable by keyboard. The pattern-based batch tool
  ("Rename / Export Files...") moves to **Ctrl+Shift+R** to make room
  -- matches videoredactor's own existing convention for the same
  shape of feature.
- **Parse Filename... moves from F3 to Ctrl+E** -- F3 is
  `QKeySequence::FindNext` (search) everywhere else; a metadata tool
  had no business sitting on it.
- **About gains F1** (`QKeySequence::HelpContents`).
- **Exit's shortcut hint removed** (it never had one to begin with,
  now deliberately so) -- Alt+F4 already closes this (or any) app at
  the OS level, verified with a real launch-and-close test.

New shared `redactor_common.gui.standard_shortcuts` module is now the
source of truth for all of the above, imported instead of literal key
strings, so this doesn't drift again.

## 2026-09-12#02 -- Refresh List (F5 / Ctrl+R)

File > Refresh List (also F5/Ctrl+R) re-scans the folder(s) your
currently-loaded files live in, picks up any new .mp3 dropped there
since you loaded, and re-reads everything still present fresh from
disk. epubredactor already had this; cbzredactor independently
rewrote the same behavior from scratch; mp3redactor and videoredactor
had neither.

- Doesn't discover a brand-new subfolder you haven't loaded anything
  from yet -- only folders already represented in the current list get
  scanned, non-recursively (`core/scan_service.py`'s `find_mp3_files()`
  gained a `recursive` parameter, defaulting to `True` so Load Files/
  Folder are unaffected; Refresh passes `recursive=False`). Use Load
  Folder for an actual new subfolder.
- Discards unsaved in-memory edits (with confirmation first, same as
  Load Files/Folder) and clears the undo stack, since its entries would
  reference `MP3File` objects this replaces.
- The "what's new on disk" logic itself is
  `redactor_common.core.folder_refresh.find_new_files_in_loaded_folders()`
  -- generalized off epubredactor's own version; this project's own
  wiring is just how paths come out of `self.files` and what to do once
  the new set is known. Bumps the `redactor_common` pin to
  `2026-09-12-02`.

1 new test for `find_mp3_files(recursive=False)`. Verified end-to-end
against real files on disk (not mocked): a file dropped into an
already-loaded folder is found, a file in an unloaded subfolder is
correctly NOT found, "nothing new" produces the right message with the
list left stable, unsaved changes trigger a discard-confirmation
prompt, and both F5 and Ctrl+R fire the same action. Full suite: 139
passed, 9 skipped (environment-gated real-binary tests, unchanged).

## 2026-09-12#01 -- click a column header to sort

Click any column header to sort the table by it (click again to
reverse); epubredactor already had this, mp3redactor was missing it
entirely. Safe here specifically because row->file mapping is
`Qt.UserRole`-based, not list-index-based (see this module's own
docstring) -- native Qt sorting physically relocates rows, which is
exactly what makes it unsafe for a project like cbzredactor whose rows
are `self.books[row]`-indexed (that project's own click-to-sort is
deliberately a different, non-native implementation for that reason).

- Numeric-looking columns (Track, Year, BPM, Loudness, Sample Rate,
  Channels) sort as numbers, not text -- "2" before "9" before "10",
  not "10" before "2" before "9". Loudness ("-14.2 LUFS") and Sample
  Rate ("44100 Hz") pass their real underlying float explicitly rather
  than relying on parsing it back out of the suffixed display text.
- `_rebuild_table()` (called after every load/check/edit/save) now
  suspends sorting while it repopulates and restores it after --
  required, not just tidy: Qt re-sorts as items land when sorting is
  live, which can relocate an earlier row's cells before a later row
  is even written, silently scrambling which row ends up holding which
  file's data.
- Built on `redactor_common.gui.sortable_table`
  (`NumericTableWidgetItem` + `suspend_sorting()`), promoted from
  epubredactor's own version (nine hand-written copies of the disable/
  restore pattern there, now one shared context manager). Bumps the
  `redactor_common` pin to `2026-09-12-01`.

Verified end-to-end against real MP3 files (not mocked): a real click-
to-sort by Track producing numeric (not lexicographic) order, the same
for BPM descending, `Qt.UserRole` row->file mapping staying correct
after a real native sort, and a rebuild under an active sort neither
desyncing rows nor leaving sorting disabled afterward. Full suite: 138
passed, 9 skipped (environment-gated real-binary tests, unchanged).

## 2026-09-10#06 -- Quick "Number Tracks" on right-click

- **New "Number Tracks..." in the table's right-click menu** -- the
  quick, one-prompt version of Auto-Numbering: just asks for a
  starting Track # and numbers the selected files +1 per row, no field
  picker or preview (Operations > Auto-Numbering... is still there for
  that). Built on `redactor_common.gui.quick_series_number`, promoted
  from epub's own quick right-click Number Series. Bumped
  `redactor_common` to `2026-09-10-04`.

## 2026-09-10#05 -- Auto-Numbering

- **New "Operations > Auto-Numbering..."** -- assigns a sequential
  number to a chosen field across the selected files, in table order
  (start value, increment, zero-padding, and a separator for non-
  numeric fields, e.g. "01 - Pilot"). Only "Track" gets numeric
  (direct-write) treatment; every other field is prefixed onto its
  existing value instead. Built on
  `redactor_common.gui.auto_numbering_dialog`, promoted from video (the
  only sibling project that had a generic version of this -- epub's
  "Number Series" is narrower, single-field). Bumped `redactor_common`
  to `2026-09-10-03`.

## 2026-09-10#04 -- smaller download

No functional changes. The built .exe is now noticeably smaller
(~62.8MB -> ~50.6MB, about 20%), for two reasons:

- UPX compression -- already configured in the PyInstaller spec
  (`upx=True`) but never actually installed in the build environment,
  so it had silently done nothing on every release so far -- is now
  genuinely wired into `build_exe.bat`. Same fix applied across the
  whole Redactor family.
- BPM detection's `aubio` dependency pulls in `numpy`, whose own
  `__init__.py` unconditionally imports `numpy.linalg` (confirmed:
  blocking it makes even a plain `import numpy` fail), which is what
  actually requires numpy's ~20MB vendored OpenBLAS DLL -- not
  anything this app calls. `numpy.fft`/`polynomial`/`random`/`ma`/
  `testing` are NOT required the same way and aren't used here either,
  so they're now excluded from the build (a modest ~2MB on their own,
  confirmed safe by the same test).

## 2026-09-10#03

The quick single-file rename added in the previous release is now
built on a shared `redactor_common` function instead of a copy local
to this project -- per explicit request ("that was my intention") once
it became clear the feature would otherwise need writing a third and
fourth time for cbzredactor/videoredactor too.

- Bumps the `redactor_common` pin to `2026-09-10-01`, which adds
  `gui/rename_single_file.py` (`rename_single_file()`) -- generalized
  off this project's own `2026-09-10#02` method, the same "prompt via
  QInputDialog, delegate to `core.rename_pattern.rename_file_on_disk()`,
  report a failure via QMessageBox" flow, just parameterized via a
  `set_path` callback instead of an `MP3File` directly.
- `gui/main_window.py`'s `rename_single_file()` method is now a thin
  wrapper (imported as `prompt_rename_single_file` to avoid shadowing
  its own method name) -- same double-click/context-menu triggers,
  same "physical file operation, not pushed onto the undo stack"
  behavior, no user-visible change.

Full suite: 138 passed, 9 skipped. Re-verified end-to-end against a
real on-disk file through the new code path -- same checks as
`2026-09-10#02`'s release notes, all still passing after the swap.

## 2026-09-10#02

Quick single-file rename: double-click a Filename cell (or right-click
> Rename File... for a single selected file) to fix a typo in one
filename directly, without going through the pattern-based Rename/
Export tool added in the previous release. epubredactor already had
this (its own local, pre-`redactor_common` copy); mp3redactor, like
cbzredactor and videoredactor, never picked it up. Built entirely on
`redactor_common.core.rename_pattern.rename_file_on_disk()`, which was
already generic (works on a plain path string, no project-specific
book/file wrapper needed) -- no new core module required here.

- Prompts for a new filename (extension kept automatically, current
  name pre-filled), renames on disk immediately -- a physical file
  operation, not staged until Save, and (like Rename/Export and Fix
  Integrity) never pushed onto the in-memory undo stack.
- Refuses silently-invalid names (illegal characters, reserved Windows
  device names, trailing dot/space) and an already-existing filename
  in the same folder, both with a clear message rather than a raw
  exception or a silent overwrite.
- Right-click menu only offers it for exactly one selected file
  (renaming several files to the same name doesn't make sense) and
  never for a file that failed to load.

Verified end-to-end against a real on-disk file (not mocked): a real
double-click dispatch through a real `QInputDialog`, a real `os.rename`,
correct no-op when double-clicking any other column, a cancelled dialog
leaving the file untouched, and a collision against an existing
filename producing a warning instead of a crash or a clobber. No new
permanent test file -- this project's GUI layer, like its siblings',
has no automated test coverage (see README's Tests section); the
underlying `rename_file_on_disk()`/`validate_filename_stem()` logic is
already covered by `redactor_common`'s own test suite.

## 2026-09-10#01

Rename/Export by Metadata Pattern, and its reverse, Parse Filename ->
Metadata -- both already generic, ready-to-consume modules in
`redactor_common` (`gui/rename_pattern_dialog.py` /
`gui/parse_filename_dialog.py`, built on `core/rename_pattern.py` /
`core/filename_parser.py`) and already wired into epubredactor/
cbzredactor, but never retrofitted into this project. Same gap
`redactor_common`'s own README flags as "mandatory, not optional" for
every Redactor-family app.

- **Rename / Export Files...** (File menu, `F2`): builds a filename
  from a `%field%` pattern (Title/Artist/Album/Track/Year/Genre/
  Language -- `core/fields.py`'s own field list, so it can never drift
  out of sync with the bulk-edit panel or table columns), previews it
  for every selected file, then either renames the files in place or
  exports renamed copies to a chosen folder, originals untouched.
  Track gets an optional zero-pad-to-2-digits checkbox. A physical
  file operation, like Save/Fix Integrity -- deliberately not pushed
  onto the in-memory undo stack.
- **Parse Filename...** (Import menu, `F3`): the reverse -- extracts
  field values back out of a filename using the same pattern syntax,
  previews what would be applied to each selected file (checkbox per
  file to skip individual ones), then applies the accepted fields via
  the same `MP3File.apply_tags()` bulk-edit path already used everywhere
  else, so a parse-filename change is undoable (`Ctrl+Z`) and highlighted
  dirty exactly like typing into the tag panel.
- Both dialogs default to `%track% - %artist% - %title%` and share one
  pattern history (`core/settings.py`'s new `pattern_history`, most-
  recently-used first, capped at 15) -- describe your naming convention
  once in either dialog and it's offered back in the other.
- Both require an explicit file selection (same "No Files
  Loaded"/"No Files Selected" convention every other Operations/File
  action in this app already uses) rather than silently falling back to
  "every loaded file" the way some sibling projects' equivalent dialogs
  do -- Rename mutates files on disk, so asking for a deliberate choice
  here is the safer default, and Parse Filename stays consistent with it.

**Real bug found and fixed while adding this:** `core/settings.py`'s
`ConfigParser()` used the default interpolation mode, which treats a
bare `%` as the start of an interpolation reference -- writing a
pattern like `%artist% - %title%` to the settings file raised
`ValueError: invalid interpolation syntax` the moment a pattern was
actually saved. Both `ConfigParser()` calls now pass
`interpolation=None` (nothing else stored here ever used interpolation
either way).

6 new tests in `tests/test_settings.py` (pattern-history round-trip +
`dedupe_and_trim_pattern_history()` logic). Also verified end-to-end
against a real audio file outside the test suite: a real on-disk
rename with zero-padding, and a real parse-then-apply that landed in
`MP3File`'s actual tag attributes and set `dirty`.

## 2026-09-07#05

A cross-repo review of `redactor_common` adoption found this project
had no path-too-long protection on the one real gap that mattered:
`core/tag_writer.py`'s save failures. Fixed:

- `save_tags()`'s three failure points (opening the file, adding a tag
  header, writing tags back) now route their error message through
  `redactor_common.core.save_errors.describe_save_error()` instead of a
  bare `str(exc)`. A file whose path is over Windows' 260-character
  limit now gets a clear explanation that moving it is required
  (retrying the same save can't help), instead of a raw, confusing
  `WinError` message. Bumps the `redactor_common` pin to `2026-09-07-01`,
  which also fixes a real selection-color bug at the source (see that
  repo's own changelog).

1 new test in `tests/test_tag_writer.py`.

## 2026-09-07#04

- **Deep Check Integrity** (Operations menu/right-click) -- a real
  full ffmpeg decode (`core/ffmpeg_probe.py`), catching corrupt/
  truncated audio data mp3val's header-only scan can miss. Also grabs
  the actual encoder, sample rate, and channel count via ffprobe in
  the same pass (new Encoder/Sample Rate/Channels columns, hidden by
  default -- Settings > Add/Remove Columns... or right-click a header
  to show them). Read-only, like Check Integrity -- never marks a file
  dirty.
- **Measure Loudness** (Operations menu/right-click) -- single-pass
  ffmpeg `loudnorm` measurement, new Loudness column (LUFS). A
  successful measurement writes the ReplayGain-style track gain to
  `TXXX:REPLAYGAIN_TRACK_GAIN` on Save (relative to ReplayGain 2.0's
  -18 LUFS reference) -- any ReplayGain-aware player picks it up. Read
  back on load, same round-trip BPM/key already got in #03. Genuinely
  silent audio measures a real "-inf LUFS" result (not a failure) with
  nothing finite to write, so it's correctly left un-dirtied rather
  than writing a nonsense gain.
- **Import & Convert to MP3** (Import menu, previously empty) --
  brings a non-MP3 file (FLAC/WAV/OGG/M4A/AIFF/Opus/WMA/...) into the
  library by converting it via ffmpeg's `libmp3lame` encoder
  (`core/mp3_converter.py`) at a chosen bitrate (128/192/256/320
  kbps), same directory, same base filename, then loads the result
  alongside whatever's already loaded -- additive, unlike Load Files/
  Folder's replace-wholesale semantics. Refuses to overwrite an
  existing same-named .mp3 rather than silently clobbering it.
- **Bundles ffmpeg.exe/ffprobe.exe** (`tools\`, GPLv3, see
  `tools\NOTICE.txt`/README's licensing note) -- the same gyan.dev
  "full" build already providing keyfinder-cli.exe's FFmpeg DLLs, now
  also used directly by this app. Needs a wider DLL set than
  keyfinder-cli.exe's narrower 4 (`avdevice`/`avfilter`/`swscale` in
  addition) -- ffmpeg.exe itself links the full filter/device stack
  even for pure audio-in/audio-out work.
- **Fixed a real hang**: every ffmpeg/ffprobe `subprocess.run()` call
  now passes `stdin=subprocess.DEVNULL`. Found by actually hitting it
  during testing -- unlike mp3val/keyfinder-cli, ffmpeg can try to
  read stdin (interactive prompts, key-press handling mid-run) and
  block forever if it inherits an unreadable/absent stdin handle,
  which a `--windowed` frozen app with no console can easily hand it.
- New tests: `test_ffmpeg_probe.py`, `test_mp3_converter.py`,
  `test_scan_service_deep_check.py`, `test_scan_service_loudness.py`,
  `test_scan_service_import_conversion.py`, `test_tag_reader_loudness.py`,
  plus loudness cases added to `test_tag_writer.py`. The ffmpeg/
  ffprobe/converter tests run against the real bundled binaries (not
  mocked), auto-skipping if `tools\` doesn't have them. Also verified
  end-to-end with real `QAction.trigger()` calls against the real
  binaries: Deep Check populating probe columns, Measure Loudness
  showing the dirty highlight and surviving a simulated restart after
  Save, and a full Import & Convert flow (mocked file-picker/bitrate
  dialogs only) actually producing and loading a converted .mp3. Full
  suite: 140/140.

## 2026-09-07#03

- **Fixed: a saved BPM/key vanished from the table on reload, looking
  like the save silently failed.** `core/tag_reader.py`'s `load_tags()`
  never read an existing `TBPM`/`TKEY` frame back into `mp3.bpm`/
  `key_value` -- #01/#02 fixed *writing* those tags, but nothing read
  them back on the next load (a fresh Load Files/Folder, or just
  restarting the app), so a value that genuinely made it to disk still
  disappeared from the BPM/Key columns the moment the file was
  reloaded. It now reads both back (without marking the file dirty --
  reading back what's already on disk isn't an unsaved change).
- **Fixed: a BPM/key-only change showed no visual "unsaved" cue at
  all.** The amber dirty highlight was only ever applied to the
  Title/Artist/Album/etc. columns, never to BPM/Key -- so running
  Detect BPM/Detect Key without touching any text field left the row
  looking completely unchanged even though it genuinely needed saving,
  easy to read as "it thinks this is already applied." The BPM and Key
  cells now get the same amber tint as every other dirty field.
- **Save Tags -> Save File(s)** -- renamed per feedback that "Tags"
  undersold what the button (and Ctrl+S) actually does: open, modify,
  and re-save the real file on disk, not some separate tag store.
- New tests (`test_tag_reader_bpm_key.py`): reading an existing TBPM/
  TKEY frame back into `mp3.bpm`/`key_value` without marking dirty,
  ignoring a malformed TBPM rather than crashing the load, and a full
  detect(-or-set) -> save -> reload-in-a-brand-new-MP3File round trip
  for both fields. Also verified end-to-end with a real
  `QAction.trigger()` scenario across two separate `MainWindow`
  instances (simulating an actual app restart, not just reused
  in-memory state): Detect BPM, confirmed the BPM cell itself shows
  the dirty highlight, Save, confirmed it clears, then a fresh
  `MainWindow` + fresh `load_files()` call on the same path confirmed
  the BPM value and status survive the "restart" intact. Full suite:
  97/97, real fixture untouched throughout.
- Also independently double-checked #01/#02's actual fix reached the
  shipped exe: extracted and disassembled `core.scan_service`'s
  compiled bytecode directly out of the built `mp3redactor.exe`'s PYZ
  archive and confirmed the dirty-marking logic is genuinely there,
  then ran real (unmocked) aubio detection against a synthesized
  120 BPM click track through the full detect -> dirty -> save ->
  on-disk-TBPM pipeline -- it worked correctly. The write-side fix was
  never actually broken; this release's fixes are the read-back and
  visual-feedback gaps that made a working save look like it wasn't
  happening.

## 2026-09-07#02

- **Fixed: a detected key was never actually written to the file** --
  the same gap as #01's BPM fix, same shape of fix: Detect Key now
  marks the file dirty (including a genuine "no key" silent-audio
  result -- keyfinder-cli reports that as success, not a failure, see
  `core/keyfinder_runner.py`), and Save now writes it to the standard
  ID3v2 `TKEY` ("Initial key") frame. A pre-existing `TKEY` from other
  software is left alone unless this app's own Detect Key actually
  completed on that file.
- **The "no output at all" report turned out to be `keyfinder-cli.exe`
  simply not being bundled** -- a built exe with no `tools\` folder
  present reports TOOL MISSING for both Check Integrity and Detect Key
  (see README.md's "Building the .exe" section), which can easily read
  as "nothing happened" rather than an informative status. No code
  change for this half -- it's a distribution/packaging step, not a
  bug -- but the next released build bundles a working
  `keyfinder-cli.exe` so Detect Key works out of the box.
- New tests: `run_key_detection()` marking (and not marking) dirty --
  including the silent-but-OK case, which is dirty-worthy unlike BPM's
  equivalent "nothing detected" case -- and `save_tags()` writing/
  clearing `TKEY` and leaving a pre-existing one alone on a failed
  detection, verified via real ID3 write/independent-mutagen-read round
  trips, plus an end-to-end real-`QAction`-triggered Detect Key -> Save
  scenario against the real `keyfinder-cli.exe` binary (not mocked).

## 2026-09-07#01

- **Fixed: a detected BPM was never actually written to the file.**
  Detect BPM has always populated the table's BPM column and
  `MP3File.bpm` in memory, but `core/tag_writer.py`'s frame map never
  had a BPM entry, so Save silently had nothing to write for it --
  BPM never round-tripped to disk no matter how many times you hit
  Save. Detect BPM now marks the file dirty (same signal a manual
  bulk-edit uses), and Save writes it to the standard ID3v2 `TBPM`
  frame, rounded to the nearest whole beat per the frame's own spec
  (e.g. aubio's `127.6` -> `"128"`). A file's existing `TBPM` tag (from
  other software) is left alone until this app's own Detect BPM
  actually runs on it -- this app has never read `TBPM` on load, so
  there'd be no way to tell "never detected" apart from "detected as
  empty" otherwise.
- New tests: `run_bpm_check()` marking (and not marking) dirty,
  `save_tags()` writing/rounding `TBPM` and leaving a pre-existing one
  untouched -- verified via a real ID3 write/independent-mutagen-read
  round trip, plus an end-to-end real-`QAction`-triggered Detect BPM ->
  Save scenario confirming the row's dirty highlight and the actual
  on-disk frame.

## 2026-09-06#07

- **New "Path" column**, alongside Filename -- shows each file's full
  path, matching the sibling projects. Hideable/reorderable like any
  other column.
- Colors (unsaved-edit row tint, table selection highlight) now come
  from `redactor_common.gui.colors` -- standardized on the epub tool's
  scheme (this project's dirty-row tint already matched it; the table
  selection highlight is new here, this project had none before).
- Removed the "Uncheck All Fields" button from the bulk-edit panel --
  not needed.
- Bumped `redactor_common` to `2026-09-06-06`.

## 2026-09-06#06

- **Toolbar trimmed to the five everyday actions** -- Load Files, Load
  Folder, Save, Apply, Undo. Check Integrity, Detect BPM, and Detect
  Key stay available (unchanged) from the Operations menu and the
  table's right-click context menu, but no longer duplicate themselves
  onto the toolbar.
- **Undo** -- new, via `redactor_common.core.undo.UndoManager`. Covers
  in-memory edits only (currently just the bulk-edit Apply); never
  physical file operations (Save, mp3val Fix), same scope convention
  as the sibling projects. `Ctrl+Z` / Operations menu / toolbar button,
  label reflects what it'll undo (e.g. "Undo Bulk Edit"), bounded to
  the last 5 edits, and clears itself when Load Files/Folder replaces
  the file list wholesale (old snapshots would no longer reach
  anything on screen).
- **Table zoom** -- new "-  100%  +" toolbar control
  (`redactor_common.gui.zoom_toolbar.TableZoomController`), matching
  the epub/cbz tools. Adjusts the table's font size (and re-fits row
  heights); a this-window display preference, not persisted across
  restarts.

## 2026-09-06#05

- **Column management + Genre/Language quick-pick + management
  dialogs** -- the "columns/genres/languages" trio every other
  Redactor-family app already has, built on redactor_common the same
  way (`core/table_settings`, `gui/column_menu`,
  `gui/column_settings_dialog`, `gui/quick_pick_dialog`,
  `gui/manage_list_dialog`):
  - The table's columns are now field-key based, not fixed-index --
    drag a header to reorder, right-click for a show/hide checklist or
    "Add/Remove Columns...", both persisted across restarts
    (`core/settings.py` gained `hidden_columns`/`column_order`/
    `has_column_preference`). Nothing hidden by default -- matches
    this app's existing behavior before column management existed.
  - New **Language** field (ID3 `TLAN`, ISO 639-2 codes --
    `core/mp3_languages.py`), alongside the existing six.
  - Genre and Language each get a "+" quick-pick button next to their
    bulk-edit field: a searchable popup (not a flat menu -- doesn't
    overflow once enough custom entries pile up) over a curated
    default list (`core/mp3_genres.py`'s `COMMON_MP3_GENRES` -- the
    standard ID3v1 genre list; `core/mp3_languages.py`'s
    `DEFAULT_LANGUAGES` -- ~18 common ISO 639-2 codes) plus custom
    entries, individually hideable/restorable via Settings >
    Add/Remove Genres.../Add/Remove Languages.... Picking a value
    replaces the field -- unlike epub's/cbz's semicolon/comma-joined
    multi-value Genre, MP3's TCON is conventionally single-valued in
    practice and nothing else here treats it as a delimited list.
  - Hiding a column also hides that field's row in the bulk-edit panel
    (`TagPanel.set_visible_fields()`, new) and vice versa -- same
    lock-step convention as epub/cbz.
- New tests: `test_mp3_genres.py`, `test_mp3_languages.py`, plus
  expanded `test_settings.py` coverage for the new persisted fields.
  Verified end-to-end via real Qt-driven scenarios (not just unit
  tests): quick-pick a genre and language, Apply, Save, confirm the
  actual `TCON`/`TLAN` frames on disk via mutagen; hide/reorder columns
  and confirm both the panel sync and cross-restart persistence via a
  fresh `load_settings()` call.

## 2026-09-06#04

- Load Files/Load Folder now remember the last directory used and
  start there next time, across app restarts -- previously always
  opened wherever Qt/Windows defaulted to. `core/settings.py` gained
  `last_directory` (persisted the same way `mp3val_path`/
  `keyfinder_cli_path` already are) plus two small pure helpers,
  `resolve_start_directory()` (falls back to "" -- Qt's own default --
  if the remembered directory no longer exists, e.g. an unplugged
  removable drive) and `directory_for()` (the directory to remember
  from whatever was just picked, a file or a folder itself). Same
  semantics as the epub tool's QSettings-based equivalent, adapted to
  this project's plain-configparser persistence.

## 2026-09-06#03

- **Detect BPM now runs across multiple cores in parallel**, same as
  Detect Key already did -- was fully sequential (one file at a time)
  despite aubio's decode+tempo-detection loop being a C extension that
  releases the GIL, confirmed by measurement (~2.8x speedup on an
  8-worker/12-file benchmark of a few-second clips; longer, real-world
  tracks should do better still, since aubio's fixed per-file setup
  overhead shrinks as a fraction of the total). `core/scan_service.
  run_bpm_check()` now dispatches the whole selection to a
  `ThreadPoolExecutor`, same shape as `run_key_detection()`.
  `MainWindow`'s two now share one `_run_concurrent_check_with_progress()`
  helper instead of duplicating the progress-dialog-driving logic twice.
- Fixed a pre-existing, already-failing test found while working on the
  above (unrelated to the parallelism change itself):
  `tests/test_bpm_detector.py`'s tool-missing test relied on aubio
  genuinely not being installed in the sandbox to exercise the
  `ImportError` branch. aubio is now actually installed here, so it was
  taking a different code path entirely (a real file-not-found error
  inside the broad `except`, landing on `STATUS_ERROR` instead of the
  `STATUS_TOOL_MISSING` it asserted) -- no longer testing what it
  claimed to. Now forces the `ImportError` via
  `sys.modules["aubio"] = None` regardless of whether the real package
  happens to be installed.
- New test: `test_scan_service_bpm_check.py`.

## 2026-09-06#02

- Fixed the gap between every Bulk Edit Tags field visibly growing as
  the window is resized taller -- the fields grid had no row stretch
  set anywhere, so Qt spread the extra vertical space evenly into
  every row's gap instead of leaving it as blank space below the last
  field. Fix lives in `redactor_common` (bumped to `2026-09-06-03`) so
  the same latent issue in epub's identically-structured grid gets it
  too.

## 2026-09-06#01

- Bumped `redactor_common` to `2026-09-06-02` -- fixes a stray leading
  comma in the About dialog ("`, ver 2026-...`") caused by this
  project's empty `RELEASE_LABEL`.

## 2026-09-04#11

- `redactor_common` is now a real pip dependency
  ([Erlbon/redactor_common](https://github.com/Erlbon/redactor_common),
  pinned to tag `2026-09-04-10` in `requirements.txt`) instead of a
  vendored copy under `redactor_common/`. This is the actual fix for
  what #10's setWindowModality crash exposed: three projects each
  hand-copying the same files meant a fix in one place didn't reach
  the other two without three separate manual resyncs -- one canonical
  source now, bumped via a deliberate one-line `requirements.txt` diff
  instead. Import paths are unchanged (`from redactor_common.gui...`
  still works, just resolves from site-packages now). No code changes
  needed here beyond removing the vendored folder.

## 2026-09-04#10

- **Detect Key now runs across multiple cores in parallel** instead of
  one file at a time -- each keyfinder-cli invocation is a genuinely
  separate OS process, and it's by far the slowest of the three checks
  (full decode + FFT per file), so this is where parallelism actually
  pays off. `core/scan_service.run_key_detection()` now dispatches the
  whole selection to a `ThreadPoolExecutor` (default: one worker per
  core, capped at the number of files) instead of being called once per
  file the way the other checks are; `MainWindow.run_key_detection()`
  was rewritten to match (drives its progress dialog from
  scan_service's own progress/should_cancel callbacks rather than
  `_run_check_with_progress`'s one-item-at-a-time loop, which would
  have silently defeated the whole point). Verified with 12 real files
  against the actual keyfinder-cli.exe binary: ~7.6x wall-clock speedup
  on this machine, all results correct.
- **Fixed a real, live crash** (found while testing the above with 3+
  files, not caused by it): `redactor_common/gui/progress.py`'s
  `run_with_progress()` called `dialog.setWindowModality(True)` -- a
  bare bool, not the `Qt.WindowModality` enum PyQt6 actually requires
  -- which raised `TypeError` in this PyQt6 version the moment the
  progress dialog was ever actually shown (3+ items). This affected
  every check in this app (Integrity/BPM/Key), not just the new
  parallel path; it just happened to surface here first since it's what
  I was testing with a bigger batch. Fixed upstream in
  [Erlbon/redactor_common](https://github.com/Erlbon/redactor_common)
  and re-synced into this project's vendored copy
  (`REDACTOR_COMMON_VERSION` bumped to `2026-09-04#09`); flagged for
  the epub/video tools too, since they vendor the same file.
- New test: `test_scan_service_key_detection.py`.

## 2026-09-04#09

- Added **key detection** -- via `keyfinder-cli` (shelled out,
  `core/keyfinder_runner.py`; see
  [Erlbon/keyfinder-cli-windows](https://github.com/Erlbon/keyfinder-cli-windows)
  for how the Windows binary is built, since none exists upstream).
  "Detect Key" from the Operations menu, its toolbar button, or
  right-click -- same three places Check Integrity/Detect BPM already
  live. New Key column in the table (`MP3File.key_status/key_value/
  key_message` were already reserved fields, now actually populated).
  A silent file (genuinely no key) is reported OK with an explanatory
  tooltip, not as an error.
- Fixed `core.scan_service.save_dirty_tags()`: its docstring already
  claimed non-dirty files are skipped, but the code saved every file it
  was given regardless -- harmless today (the only caller already
  pre-filters to dirty files) but now actually does what it says.
- New test: `test_keyfinder_runner.py`.

## 2026-09-04#08

- Added **basic bulk tag editing** -- Title/Artist/Album/Track/Year/Genre
  -- the mp3tag-style workflow every sibling Redactor project uses,
  built on the same `redactor_common` pieces the epub tool's tag panel
  uses (`collapsible_splitter`, `progress`, `error_summary`,
  `action_factory`):
  - `core/fields.py`: single source of truth for the editable fields,
    same pattern as the epub tool's (trimmed down -- no covers,
    genre/language pickers, or external lookups yet).
  - `core/tag_writer.py`: writes tags back via mutagen (the write
    counterpart to `core/tag_reader.py`'s read side, same ID3 frames).
    Blanking a field and saving removes that frame entirely rather than
    writing it empty -- how you clear a tag.
  - `MP3File.apply_tags()` / `.dirty` (`core/mp3_file.py`): in-memory
    edit + dirty tracking, mirroring the epub tool's
    `EpubBook.apply_metadata()`/`.dirty`.
  - `gui/tag_panel.py`: the bulk-edit panel itself -- select rows, tick
    a field (or just start typing -- that ticks it too), Apply to the
    selection. Shows "<multiple values>" (scroll to cycle through and
    pick one) when the selection disagrees on a field.
  - `gui/main_window.py`: panel lives in a collapsible splitter next to
    the file table (Panel toolbar button to minimize/restore); table
    gained Track/Year/Genre columns so applied edits are visible, not
    just Title/Artist/Album; dirty rows highlighted amber; **Save Tags**
    (Ctrl+S, File menu) writes every dirty file to disk, reporting any
    per-file failures; confirms before discarding unsaved edits (Load
    Files/Folder, window close).
  - New tests: `test_mp3_file.py`, `test_tag_writer.py` (the latter uses
    a real tiny MP3 fixture, `tests/fixtures/tiny.mp3`, round-tripping
    through actual mutagen rather than mocking it).

## 2026-09-04#07

- **Check File Integrity** and **Detect BPM** now operate on the table's
  current **selection only**, matching Fix's existing behavior, instead
  of silently running across every loaded file. Menu/toolbar/context-menu
  labels reworded ("...Selected Files...") to make that explicit.
- The "nothing to run on" message now distinguishes no files loaded
  ("Load some files first") from files loaded but none selected
  ("Select one or more files in the table").
- `keyfinder-cli.exe` build/distribution moved out to its own repo:
  [Erlbon/keyfinder-cli-windows](https://github.com/Erlbon/keyfinder-cli-windows)
  (there's no prebuilt Windows binary upstream, and the build needs
  vcpkg + a ~120MB FFmpeg dev package -- keeping that out of this repo).
  v1.2.0 is published there as a GitHub Release with `keyfinder-cli.exe`
  + its 4 DLLs attached. README's build step 1 updated accordingly.

## 2026-09-04#06

- Added branding icon: `assets/icon.ico` / `icon.png`, turned-m glyph on
  a dark rounded square (matches "The \u026fP3 Redactor"). Set as the
  app/window icon (`main.py`, `gui/main_window.py`) and bundled into the
  build via PyInstaller's `--icon` flag.
- Fixed mp3val being able to pop up (or flash) its own console window
  even though this app is built `--windowed` -- same class of bug the
  epub tool hit with Calibre (v35). New `core/subprocess_utils.py`
  supplies Windows-only `CREATE_NO_WINDOW` kwargs, applied to every
  `subprocess.run()` call in `core/mp3val_runner.py`.
- Added a **Locate External Tools** dialog (`gui/external_tools_dialog.py`,
  Settings menu), mirroring the video tool's ffmpeg/MKVToolNix locator:
  per-tool Browse/Clear + live found/not-found indicator, auto-detect
  via PATH as the default. Covers mp3val now; keyfinder-cli's field is
  present ahead of key detection landing so the dialog doesn't need a
  second layout pass.
- `core/tool_locator.find_tool()` gained an `override` parameter (the
  user's manual path from that dialog) tried before the bundled-tools/
  PATH search; an override that doesn't exist is reported NOT FOUND
  rather than silently falling back, so the dialog's indicator stays
  honest. Threaded through `mp3val_runner` and `scan_service`'s
  integrity check/fix functions as `override_path`/`mp3val_path`.

## 2026-09-04#05

- Swapped `aubio` for `aubio-ledfx` in requirements.txt -- plain `aubio`
  is source-only on PyPI (no Windows wheels ever), so it always needed
  MSVC Build Tools to install on Windows. `aubio-ledfx` is a maintained
  fork shipping prebuilt Windows wheels (including for newer Python
  versions upstream has never built for). Same module name (`import
  aubio`), no code changes -- see `core/bpm_detector.py` and
  `README.md` for the provenance note (third-party fork, not the
  canonical aubio release).
- `build_exe.bat` ported from the working epub-tool script: step-by-step
  failure checks, `python -m PyInstaller`, no test run baked into the
  build. `mp3val.exe`/`keyfinder-cli.exe` copied into `dist\tools`
  after the PyInstaller build rather than bundled via `--add-data`,
  since `core/tool_locator.py` looks for them next to the exe, not in
  a onefile build's runtime-extracted temp folder.
- Added `bump_version.py` (same date/counter logic as the epub tool).
- `pytest` added to requirements.txt as an explicit dev dependency.

## 2026-09-04#03

- Added a **Settings** menu/dialog (`gui/settings_dialog.py`, backed by
  `core/settings.py`, persisted as `settings.ini` next to the app).
- New toggle: **delete .bak backup files after a successful fix** (mp3val's
  `-nb` flag) -- **off by default** (backups kept), opt-in via Settings.
  Threaded through `fix_integrity()` / `run_integrity_fix()`.
- The Fix confirmation dialog's wording now reflects whichever backup
  behavior is currently active.
- `core/settings.py` deliberately avoids Qt (configparser-based, `.ini`)
  so it stays unit-testable without PyQt6, same as the rest of `core/`.

## 2026-09-04#02

- Added file-integrity **fixing**, via `mp3val -f` (`core.mp3val_runner.fix_integrity`,
  `core.scan_service.run_integrity_fix`) -- a separate, deliberate action from
  Check, not folded into it, since it mutates files on disk.
- Fix operates on the table's current **selection only**, not all loaded
  files -- a targeted action, not a blanket one.
- Confirmation dialog before fixing, noting mp3val's automatic `.bak`
  backup and that not every issue is fixable (status can still come back
  WARNING/ERROR after a fix attempt).
- Available from the Checks menu and the table's right-click context menu.

## 2026-09-04#01

- Initial project scaffold.
- File table (Filename/Title/Artist/Album/Integrity/BPM) with row-to-file
  mapping via `Qt.UserRole`.
- Load Files / Load Folder (recursive, case-insensitive `.mp3` match, dedup).
- Tag reading via mutagen (title/artist/album/track/year/genre/duration/
  bitrate/cover-presence).
- File integrity check via `mp3val` (shelled out; reports OK/WARNING/ERROR,
  or TOOL MISSING when the binary isn't bundled or on PATH).
- BPM detection via `aubio`'s Python bindings (in-process; falls back to a
  median inter-beat-interval estimate if `get_bpm()` isn't reliable yet;
  reports TOOL MISSING when aubio isn't installed).
- Progress dialog for load/check operations.
- Crash logging (global excepthook + faulthandler), installed first thing
  in `main()`.
