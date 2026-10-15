"""
core/notes_render.py

Turns what the lookups found, and what the folder holds, into the text of an artist.md / album.md draft.
Pure (no network, no files): the same inputs always give the same note. The writing itself (never overwriting,
status: draft, UTF-8/LF) is redactor_common's core/notes.py; the credit lines and wiki links are its
core/note_text.py.

Shape (changed 2026-10-15 to the contract in Pluteus' docs/notes-format.md, "Discographies"):

  artist.md   front matter (title, years, genres, links, status: draft), a short introduction, a few fact lines,
              the FULL MusicBrainz discography as plain "- Title (Year)" lines under "## Studio albums",
              "## Live albums", "## Compilations", "## EPs", "## Singles", "## Remixes", "## Soundtracks",
              "## Releases" (Pluteus adds the in-library / missing badges by comparing them with the folders), the
              credit footer. No library list, no [[links]], no "(in this library)".
  about.md    front matter, a short introduction, the credit footer.
  album.md    front matter, a short introduction, Artist / Released / Label / Country lines, "## Credits" (only
              structured facts: producer, writers, who played what), "## Tracklist", "## In this folder" (the
              local files compared with the tracklist: present, missing, extra; INCLUDE_FOLDER_COMPARISON), the
              credit footer. No [[links]].
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

from core.musicbrainz_lookup import FileFacts, Release, ReleaseTrack, match_release
from core.notes_local import LocalAlbum, LocalTrack, fold_name
from core.notes_musicbrainz import Credits
from redactor_common.core.note_text import credit_footer
from core.version import APP_VERSION
from redactor_common.core.notes import NoteFrontMatter, render_note

# The shape of the notes this module writes, stamped into every note (with the app version and a hash, see
# redactor_common core/notes.py note_hash) so a later run can tell an old note of ours that nobody edited from one a
# person edited. The first released shapes are format 1 (no stamp); stamped notes start at 2. BUMP it when the shape of
# a note changes in a way worth refreshing old notes for.
CURRENT_NOTE_FORMAT = 2


def generator_text() -> str:
    return f"mp3redactor {APP_VERSION}"


@dataclass
class TrackInfo:
    """One track of the tracklist the folder is compared with."""

    disc: int
    position: int
    title: str
    seconds: Optional[float] = None


def mmss(seconds: Optional[float]) -> str:
    if not seconds:
        return ""
    total = int(round(seconds))
    return f"{total // 60}:{total % 60:02d}"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _lines(pairs: Iterable[tuple[str, str]]) -> str:
    """'**Label:** value' lines as Markdown hard breaks, for the non-empty values."""
    return "  \n".join(f"**{label}:** {value}" for label, value in pairs if value)


def _block(*parts: str) -> str:
    return "\n\n".join(p.strip("\n") for p in parts if p and p.strip())


# ---------------------------------------------------------------------------
# Comparing the folder with the tracklist
# ---------------------------------------------------------------------------

@dataclass
class Comparison:
    present: list[tuple[TrackInfo, LocalTrack]] = field(default_factory=list)
    missing: list[TrackInfo] = field(default_factory=list)
    extra: list[LocalTrack] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return not self.missing and not self.extra


def compare_tracks(local: Iterable[LocalTrack], tracks: list[TrackInfo]) -> Comparison:
    """Which tracks of the tracklist the files cover (by title, duration and number: the matcher of the
    MusicBrainz lookup), which are missing, and which files are on no track of it."""
    local = list(local)
    facts = [FileFacts(title=t.title or t.path.stem, track=t.track, disc=t.disc, seconds=t.seconds) for t in local]
    release = Release(id="", title="", artist="", tracks=[
        ReleaseTrack(disc=t.disc, position=t.position, title=t.title, seconds=t.seconds, recording_id="", artist="")
        for t in tracks])
    # Exactly equal titles pair first (a different pressing numbers its tracks differently, a short file has the
    # wrong duration); the matcher of the lookup dialog (title, duration, number) takes what is left.
    assignment: dict[int, ReleaseTrack] = {}
    taken: set[int] = set()
    for file_index, fact in enumerate(facts):
        wanted = fold_name(fact.title)
        options = [i for i, t in enumerate(release.tracks) if i not in taken and wanted and fold_name(t.title) == wanted]
        if options:
            best = next((i for i in options if release.tracks[i].position == fact.track), options[0])
            assignment[file_index] = release.tracks[best]
            taken.add(best)
    left = [i for i in range(len(facts)) if i not in assignment]
    if left:
        rest = Release(id="", title="", artist="", tracks=[t for i, t in enumerate(release.tracks) if i not in taken])
        for rest_index, track in match_release([facts[i] for i in left], rest).assignment.items():
            assignment[left[rest_index]] = track
    comparison = Comparison()
    used: set[int] = set()
    for file_index, track in assignment.items():
        index = next(i for i, candidate in enumerate(release.tracks) if candidate is track)
        used.add(index)
        comparison.present.append((tracks[index], local[file_index]))
    comparison.present.sort(key=lambda pair: (pair[0].disc, pair[0].position))
    comparison.missing = [t for i, t in enumerate(tracks) if i not in used]
    comparison.extra = [t for i, t in enumerate(local) if i not in assignment]
    return comparison


def _track_name(track: TrackInfo, multi_disc: bool) -> str:
    return (f"{track.disc}-{track.position}" if multi_disc else f"{track.position}") + f". {track.title}"


def render_folder_section(album: LocalAlbum, tracks: list[TrackInfo]) -> str:
    """The body of '## In this folder': present / missing / extra, in plain sentences and short lists."""
    if not tracks:
        return f"{_plural(album.track_count, 'file')} here; no tracklist was found to compare them with."
    comparison = compare_tracks(album.tracks, tracks)
    multi = len({t.disc for t in tracks}) > 1
    present = len(comparison.present)
    head = (f"All {_plural(len(tracks), 'track')} of the tracklist are here." if not comparison.missing
            else f"**Present:** {present} of {len(tracks)} tracks.")
    out = [head]
    if comparison.missing:
        out.append(f"**Missing ({len(comparison.missing)}):**\n\n" + "\n".join(
            f"- {_track_name(t, multi)}" for t in comparison.missing))
    if comparison.extra:
        out.append(f"**Extra, not on the tracklist ({len(comparison.extra)}):**\n\n" + "\n".join(
            f"- {t.name}" for t in comparison.extra))
    elif not comparison.missing:
        out[0] += " No other files."
    return "\n\n".join(out)


# ---------------------------------------------------------------------------
# Credits
# ---------------------------------------------------------------------------

def render_credits(credits: Credits) -> str:
    """'- **Producer:** ...' lines for the structured credits ('' when there are none)."""
    lines = []
    if credits.producers:
        lines.append(f"- **Producer:** {', '.join(credits.producers)}")
    if credits.writers:
        lines.append(f"- **Written by:** {', '.join(credits.writers)}")
    for role, names in credits.others.items():
        lines.append(f"- **{role.capitalize()}:** {', '.join(names)}")
    if credits.performers:
        people = [f"{name} ({', '.join(skills)})" if skills else name for name, skills in credits.performers.items()]
        lines.append(f"- **Musicians:** {', '.join(people)}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# The notes
# ---------------------------------------------------------------------------

# Whether an album note carries the "## In this folder" present / missing / extra comparison. It is a baked-in copy
# of what the folder holds when the note was written (Pluteus shows the live state itself, and a copy goes stale), so
# the owner decided on 2026-10-15 that it is OFF. The code stays behind this one switch.
INCLUDE_FOLDER_COMPARISON = False

# Discography headings, in the order they are written (Pluteus compares the plain `- Title (Year)` lines under them
# with the folder names beside the note: docs/notes-format.md, "Discographies"). A trailing "(all)" is ignored there,
# so none is written.
H_STUDIO, H_LIVE, H_COMPILATIONS, H_EPS, H_SINGLES, H_REMIXES, H_SOUNDTRACKS, H_OTHER = (
    "Studio albums", "Live albums", "Compilations", "EPs", "Singles", "Remixes", "Soundtracks", "Releases")
DISCOGRAPHY_HEADINGS = (H_STUDIO, H_LIVE, H_COMPILATIONS, H_EPS, H_SINGLES, H_REMIXES, H_SOUNDTRACKS, H_OTHER)
# Secondary types that are not releases of the music itself: left out of the discography (unless that would leave
# nothing at all, then they are listed under "Releases").
_SKIPPED_SECONDARY = frozenset({"demo", "mixtape/street", "bootleg", "interview", "audiobook", "audio drama", "spokenword"})
_SPACED_DASH = re.compile("\\s+[-\u2010-\u2015]\\s+")
_MARKDOWN_SPECIAL = re.compile(r"([*_\[\]<`])")


def discography_heading(primary: str, secondary: Iterable[str]) -> str:
    """The heading a release group is listed under, from MusicBrainz' primary type and secondary types (the order
    of the checks decides a group with several: soundtrack, compilation, remix, then live for an album or EP)."""
    primary = (primary or "").casefold()
    secondary = {x.casefold() for x in secondary}
    if "soundtrack" in secondary:
        return H_SOUNDTRACKS
    if "compilation" in secondary:
        return H_COMPILATIONS
    if "remix" in secondary:
        return H_REMIXES
    if "live" in secondary and primary in ("album", "ep"):
        return H_LIVE
    return {"album": H_STUDIO if not secondary else H_OTHER, "ep": H_EPS, "single": H_SINGLES}.get(primary, H_OTHER)


def release_line(title: str, year: str = "") -> str:
    """'- Title (Year)'. Only what Pluteus' matcher needs is changed in a title: a spaced dash ('Hits - Live') becomes
    ': ' (the matcher cuts everything after ' - ' off a list line, but not off a folder name, so the two would
    never meet), and Markdown specials are escaped so the title stays what it is."""
    clean = _MARKDOWN_SPECIAL.sub(lambda m: chr(92) + m.group(1), _SPACED_DASH.sub(": ", " ".join(title.split())))
    return f"- {clean}" + (f" ({year})" if year else "")


def group_discography(groups: Iterable) -> list[tuple[str, list[tuple[str, str]]]]:
    """[(heading, [(title, year)])] for MusicBrainz release groups (anything with title, year, primary,
    secondary): non-empty headings only, in DISCOGRAPHY_HEADINGS order, each sorted by year then title (no year
    last), a title and year listed once."""
    groups = list(groups)
    wanted = [g for g in groups if not ({x.casefold() for x in g.secondary} & _SKIPPED_SECONDARY)]
    if not wanted:
        wanted = groups  # nothing but demos / mixtapes: better those than no discography
    buckets: dict[str, dict[tuple[str, str], tuple[str, str]]] = {h: {} for h in DISCOGRAPHY_HEADINGS}
    for g in wanted:
        if not g.title.strip():
            continue
        heading = discography_heading(g.primary, g.secondary)
        buckets[heading].setdefault((" ".join(g.title.split()).casefold(), g.year), (g.title, g.year))
    return [(h, sorted(buckets[h].values(), key=lambda ty: (ty[1] or "9999", ty[0].casefold())))
            for h in DISCOGRAPHY_HEADINGS if buckets[h]]


def discogs_heading(format_text: str) -> str:
    """The heading a Discogs entry is listed under, from its format text ('LP, Album', '12", EP', 'CD, Single'...).
    Masters carry no format in an artist's release list: they are listed under 'Releases' (we do not fetch every
    master just to classify it)."""
    words = {w.strip(" .").casefold() for w in re.split(r"[,/]", format_text or "") if w.strip()}
    text = " ".join(words)
    if "compilation" in words:
        return H_COMPILATIONS
    if "remix" in text:
        return H_REMIXES
    if "ep" in words or "mini-album" in words:
        return H_EPS
    if "single" in text or "maxi-single" in words:
        return H_SINGLES
    if "album" in words:
        return H_STUDIO
    return H_OTHER


def group_discogs_discography(entries: Iterable[dict]) -> list[tuple[str, list[tuple[str, str]]]]:
    """[(heading, [(title, year)])] for the entries of Discogs' artist release list (dicts with title, year,
    format; the caller keeps only role Main): the same shape as group_discography(), a title and year once."""
    buckets: dict[str, dict[tuple[str, str], tuple[str, str]]] = {h: {} for h in DISCOGRAPHY_HEADINGS}
    for e in entries:
        title = " ".join(str(e.get("title") or "").split())
        if not title:
            continue
        year = str(e.get("year") or "")
        year = year if year.isdigit() and year != "0" else ""
        buckets[discogs_heading(str(e.get("format") or ""))].setdefault((title.casefold(), year), (title, year))
    for key in [k for k in buckets[H_OTHER] if any(k in buckets[h] for h in DISCOGRAPHY_HEADINGS if h != H_OTHER)]:
        del buckets[H_OTHER][key]  # a master (no format) of a title that a classified release already lists
    return [(h, sorted(buckets[h].values(), key=lambda ty: (ty[1] or "9999", ty[0].casefold())))
            for h in DISCOGRAPHY_HEADINGS if buckets[h]]


def discogs_facts_credit(kind: str, ident) -> str:
    """'Facts from [Discogs](https://www.discogs.com/release/N) (user-contributed)'."""
    kind = re.sub(r"[^a-z]", "", str(kind).lower()) or "release"
    return f"Facts from [Discogs](https://www.discogs.com/{kind}/{ident}) (user-contributed)"


LOCAL_ONLY_CREDIT = "Built from the file tags only, no online source"


def render_artist_note(
    *, name: str, years: str = "", genres: Iterable[str] = (), links: Iterable[str] = (), intro: str = "",
    facts: Iterable[tuple[str, str]] = (), discography: Iterable[tuple[str, list[tuple[str, str]]]] = (),
    footer: Iterable[str] = (),
) -> str:
    """The artist.md text: introduction, fact lines, then the artist's FULL discography from MusicBrainz as plain
    '- Title (Year)' lines under '## Studio albums', '## Live albums'... (`discography` is group_discography()'s
    result). No library list, no [[links]] and no 'in this library' marks: Pluteus compares the lines with the
    folders next to the note and shows the badges live (docs/notes-format.md). `footer` are the source sentences."""
    sections = ["## " + heading + "\n\n" + "\n".join(release_line(t, y) for t, y in releases)
                for heading, releases in discography]
    front = NoteFrontMatter(title=name, years=years, genres=list(genres), links=list(links),
                            generator=generator_text(), note_format=CURRENT_NOTE_FORMAT)
    body = _block(f"# {name}", intro, _lines(facts), *sections, credit_footer(list(footer)))
    return render_note(front, body)


def wikipedia_credits(pages: Iterable[tuple[str, str]]) -> str:
    """ONE credit sentence for several Wikipedia introductions: 'Introductions from Wikipedia ([Ska](url), [Ska punk](url)),
    CC BY-SA 4.0' (credit_footer adds the full stop). `pages` are (title, url); one page gives the usual single credit."""
    from redactor_common.core.note_text import wikipedia_credit

    pages = list(pages)
    if len(pages) == 1:
        return wikipedia_credit(pages[0][0])
    links = ", ".join(f"[{title.strip().replace('[', '(').replace(']', ')')}]({url})" for title, url in pages)
    return f"Introductions from Wikipedia ({links}), CC BY-SA 4.0"


def render_genre_note(
    *, name: str, intro: str = "", links: Iterable[str] = (), footer: Iterable[str] = (),
    sections: Iterable[tuple[str, str]] = (), genres: Iterable[str] = (),
) -> str:
    """The about.md of a genre or collection folder: a short introduction (Wikipedia) and the credit footer. No
    list of what is below and no [[links]]: the folders are the structure, Pluteus shows them.

    A folder that covers SEVERAL genres ('1.61 - Ska' covers ska and ska punk) passes `sections` ((genre title, its
    introduction) in order) and `genres` (listed in the front matter): the body is '# Name' and one '## Genre' section per
    genre, then the one credit footer. Without `sections` the note keeps its single-introduction shape."""
    front = NoteFrontMatter(title=name, genres=[g.lower() for g in genres], links=list(links), generator=generator_text(),
                            note_format=CURRENT_NOTE_FORMAT)
    parts = [f"# {name}"]
    sections = list(sections)
    if sections:
        parts += [f"## {title}" + ("\n\n" + text if text else "") for title, text in sections]
    else:
        parts.append(intro)
    return render_note(front, _block(*parts, credit_footer(list(footer))))


def render_album_note(
    *, title: str, album: LocalAlbum, years: str = "", genres: Iterable[str] = (), links: Iterable[str] = (),
    intro: str = "", facts: Iterable[tuple[str, str]] = (), credits: Optional[Credits] = None,
    tracks: list[TrackInfo], footer: Iterable[str] = (),
) -> str:
    """The album.md text; `tracks` is the tracklist the folder is compared with ([] when none was found)."""
    credit_text = render_credits(credits) if credits else ""
    multi = len({t.disc for t in tracks}) > 1
    listing = []
    last_disc = None
    for track in tracks:
        if multi and track.disc != last_disc:
            listing.append(f"\n### Disc {track.disc}\n")
            last_disc = track.disc
        listing.append(f"{track.position}. {track.title}" + (f" {mmss(track.seconds)}" if track.seconds else ""))
    front = NoteFrontMatter(title=title, years=years, genres=list(genres), links=list(links),
                            generator=generator_text(), note_format=CURRENT_NOTE_FORMAT)
    body = _block(
        f"# {title}", intro, _lines(facts),
        ("## Credits\n\n" + credit_text) if credit_text else "",
        ("## Tracklist\n\n" + "\n".join(listing).strip("\n")) if listing else "",
        ("## In this folder\n\n" + render_folder_section(album, tracks)) if INCLUDE_FOLDER_COMPARISON else "",
        credit_footer(list(footer)),
    )
    return render_note(front, body)
