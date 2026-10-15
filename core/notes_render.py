"""
core/notes_render.py

Turns what the lookups found, and what the folder holds, into the text of an artist.md / album.md draft.
Pure (no network, no files): the same inputs always give the same note. The writing itself (never overwriting,
status: draft, UTF-8/LF) is redactor_common's core/notes.py; the credit lines and wiki links are its
core/note_text.py.

Shape (approved by the owner on 2026-10-15; see the sample notes under notes-spike):

  artist.md   front matter (title, years, genres, links, status: draft), a short introduction, a few fact lines,
              "## In this library" (the albums really in the folder, as wiki links with year and track count),
              "## Studio albums (all)", the credit footer.
  album.md    front matter, a short introduction, Artist / Released / Label / Country lines, "## Credits" (only
              structured facts: producer, writers, who played what), "## Tracklist", "## In this folder" (the
              local files compared with the tracklist: present, missing, extra), the credit footer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

from core.musicbrainz_lookup import FileFacts, Release, ReleaseTrack, match_release
from core.notes_local import LocalAlbum, LocalTrack, fold_name
from core.notes_match import album_key, titles_match
from core.notes_musicbrainz import Credits
from redactor_common.core.note_text import credit_footer, wiki_link
from redactor_common.core.notes import NoteFrontMatter, render_note


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

def render_artist_note(
    *, name: str, years: str = "", genres: Iterable[str] = (), links: Iterable[str] = (), intro: str = "",
    facts: Iterable[tuple[str, str]] = (), library: Iterable[LocalAlbum] = (),
    discography: Iterable[tuple[str, str]] = (), footer: Iterable[str] = (), loose_tracks: int = 0,
) -> str:
    """The artist.md text. `library` is what the folder holds (LocalAlbum with tracks as read); `discography`
    is [(title, year)] of the studio albums (all, oldest first). `footer` are the source sentences."""
    discography = list(discography)
    library = list(library)
    by_key = {album_key(title): year for title, year in discography if album_key(title)}
    in_library = []
    for album in library:
        year = next((by_key[album_key(t)] for t in album.titles if album_key(t) in by_key), "") or album.year
        shown = next((title for title, _y in discography if any(titles_match(t, title) for t in album.titles)), "") or album.title
        link = wiki_link(album.folder_name, shown) or shown
        count = f", {_plural(album.track_count, 'track')}" if album.track_count else ""
        in_library.append(f"- {link}" + (f" ({year})" if year else "") + count)
    if loose_tracks:
        in_library.append(f"- Loose tracks: {loose_tracks}")
    local_keys = {album_key(t) for album in library for t in album.titles}
    studio = []
    for title, year in discography:
        mark = " *(in this library)*" if album_key(title) in local_keys else ""
        studio.append(f"- {title}" + (f" ({year})" if year else "") + mark)
    front = NoteFrontMatter(title=name, years=years, genres=list(genres), links=list(links))
    body = _block(
        f"# {name}", intro, _lines(facts),
        "## In this library\n\n" + ("\n".join(in_library) if in_library else "*(no albums found in the folder)*"),
        ("## Studio albums (all)\n\n" + "\n".join(studio)) if studio else "",
        credit_footer(list(footer)),
    )
    return render_note(front, body)


def render_genre_note(
    *, name: str, intro: str = "", links: Iterable[str] = (), entries: Iterable[tuple[str, str]] = (),
    footer: Iterable[str] = (),
) -> str:
    """The about.md of a genre or collection folder: an introduction and '## In this library' listing what is
    really below it. `entries` is [(folder name, detail)] as wiki links ('2 albums', '12 tracks')."""
    listing = [f"- {wiki_link(folder) or folder}" + (f" ({detail})" if detail else "") for folder, detail in entries]
    front = NoteFrontMatter(title=name, links=list(links))
    body = _block(
        f"# {name}", intro,
        "## In this library\n\n" + ("\n".join(listing) if listing else "*(nothing found in the folder)*"),
        credit_footer(list(footer)),
    )
    return render_note(front, body)


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
    front = NoteFrontMatter(title=title, years=years, genres=list(genres), links=list(links))
    body = _block(
        f"# {title}", intro, _lines(facts),
        ("## Credits\n\n" + credit_text) if credit_text else "",
        ("## Tracklist\n\n" + "\n".join(listing).strip("\n")) if listing else "",
        "## In this folder\n\n" + render_folder_section(album, tracks),
        credit_footer(list(footer)),
    )
    return render_note(front, body)
