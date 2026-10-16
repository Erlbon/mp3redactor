"""Folder notes: the engine end to end (core/notes_flow.py) on recorded MusicBrainz / Wikipedia / Discogs replies and
real tagged MP3 files. Nothing here reaches the network: an unexpected URL fails the test."""

import hashlib
from pathlib import Path

import pytest

from core import history
from core.notes_flow import (
    STATUS_SKIPPED, Confidence, Fetchers, NotesEngine, build_proposals, scan, summarize, undo_note_entry,
    write_proposal,
)
from core.notes_local import KIND_ALBUM, KIND_ARTIST
from redactor_common.core.notes import parse_front_matter
from tests.notes_helpers import (
    FakeFetch, cristina_fetchers, cristina_library, make_mp3, musicbrainz_routes, ultravox_fetchers, ultravox_library,
    wikipedia_routes,
)


def targets(root, kind=None, **kw):
    found = scan([root], kinds=[kind] if kind else (KIND_ARTIST, KIND_ALBUM), **kw)
    return {(t.kind, t.folder.name): t for t in found.targets}


# -- Ultravox: artist and album, MusicBrainz + Wikipedia ---------------------------------------------------------

def test_artist_note_is_verified_by_the_local_albums_and_reflects_the_folder(tmp_path):
    ultravox_library(tmp_path)
    fetchers = ultravox_fetchers()
    [proposal] = build_proposals([targets(tmp_path)[("artist", "Ultravox")]], fetchers)
    assert proposal.confidence == Confidence.CONFIDENT and proposal.writable
    assert "Vienna" in " ".join(proposal.reasons) and "Quartet" in " ".join(proposal.reasons)
    assert proposal.sources == ["musicbrainz", "wikipedia"]
    meta, body = parse_front_matter(proposal.note_text)
    assert meta["title"] == "Ultravox" and meta["status"] == "draft" and meta["years"] == "1976-"
    assert "new wave" in meta["genres"]
    assert "http://www.ultravox.org.uk/" in meta["links"]
    assert "https://en.wikipedia.org/wiki/Ultravox" in meta["links"]
    # the introduction came from Wikipedia through the wikidata sitelink (MusicBrainz had no wikipedia link)
    assert "Ultravox were a British new wave band" in body
    assert "**Type:** Group" in body and "**Country:** United Kingdom" in body and "**Active:** 1976 to present" in body
    assert "**Members:** Midge Ure, Billy Currie, Chris Cross, Warren Cann, John Foxx" in body
    # the FULL discography as plain lines (Pluteus adds the in-library / missing badges itself): no library list,
    # no [[links]], no 'in this library' marks, compilations under their own heading
    assert "[[" not in body and "In this library" not in body and "in this library" not in body
    assert "## Studio albums\n\n- Ha! Ha! Ha! (1977)\n- Ultravox! (1977)\n- Systems of Romance (1978)\n- Vienna (1980)" in body
    assert "- Lament (1984)" in body and "## Compilations\n\n- The Collection (1984)" in body
    assert body.rstrip().endswith("CC BY-SA 4.0).*")
    assert "Facts from [MusicBrainz](https://musicbrainz.org) (CC0)" in body


def test_artist_search_is_polite_and_cached_across_the_run(tmp_path):
    ultravox_library(tmp_path)
    fetchers = ultravox_fetchers()
    build_proposals(list(targets(tmp_path).values()), fetchers)
    mb = fetchers.mb
    assert mb.count(r"ws/2/artist\?query") == 1  # artist and both albums share one resolution
    assert mb.count(r"ws/2/release-group\?artist=") == 1  # ONE browse of the artist's release groups: the matching filters albums + EPs from it
    assert mb.count(r"ws/2/artist/[^?]+\?") == 1


def test_vienna_gets_the_album_release_group_not_the_single_and_a_wikipedia_intro_by_search(tmp_path):
    ultravox_library(tmp_path)
    fetchers = ultravox_fetchers()
    [proposal] = build_proposals([targets(tmp_path)[("album", "Vienna (1980)")]], fetchers)
    assert proposal.confidence == Confidence.CONFIDENT
    meta, body = parse_front_matter(proposal.note_text)
    assert meta["title"] == "Vienna" and meta["years"] == "1980" and "new wave" in meta["genres"]
    assert "https://musicbrainz.org/release-group/rg-ultravox-3" in meta["links"]  # the album, not cd130148 (the single)
    assert "cd130148" not in proposal.note_text
    # MusicBrainz has no Wikipedia link for it: found by search, the SONG page was rejected
    assert "Vienna is the fourth studio album" in body
    assert "Introduction from Wikipedia ([Vienna (Ultravox album)]" in body
    assert "https://en.wikipedia.org/wiki/Vienna_(Ultravox_album)" in meta["links"]
    assert "**Artist:** Ultravox" in body and "**Released:** 1980-07-11" in body
    assert "**Label:** Chrysalis (CDL 1296)" in body and "**Country:** GB" in body
    assert "- **Producer:** Ultravox, Conny Plank" in body
    assert "- **Musicians:** Billy Currie (synthesizer, viola), Midge Ure (lead vocals)" in body
    assert "1. Astradyne 7:07" in body and "9. All Stood Still 4:22" in body
    # no baked-in present/missing copy of the folder (dropped by the owner, 2026-10-15)
    assert "## In this folder" not in body and "**Present:**" not in body and "Extra" not in body


def test_album_search_fallback_asks_for_primary_type_album(tmp_path):
    ultravox_library(tmp_path, with_quartet=False)
    fetchers = ultravox_fetchers(search_artist=None)  # no artist found: the album search carries the proof
    [proposal] = build_proposals([targets(tmp_path)[("album", "Vienna (1980)")]], fetchers)
    assert fetchers.mb.count(r"release-group\?query=.*primarytype.*limit=6") == 1
    assert proposal.confidence == Confidence.CONFIDENT
    assert "rg-ultravox-3" in proposal.note_text and "cd130148" not in proposal.note_text
    first = proposal.candidates[0]
    assert "Album" in first.detail  # the single ranks below the album though MusicBrainz listed it first


def test_complete_folder_and_extra_files(tmp_path, monkeypatch):
    """The present/missing/extra section is OFF by default (owner, 2026-10-15); this checks it still works switched on."""
    monkeypatch.setattr("core.notes_render.INCLUDE_FOLDER_COMPARISON", True)
    ultravox_library(tmp_path, vienna_tracks=9, with_quartet=False)
    make_mp3(tmp_path / "Ultravox" / "Vienna (1980)" / "10 Secret Bonus.mp3", "Secret Bonus", "Ultravox", "Vienna", 10, "1980")
    [proposal] = build_proposals([targets(tmp_path)[("album", "Vienna (1980)")]], ultravox_fetchers())
    body = parse_front_matter(proposal.note_text)[1]
    assert "All 9 tracks of the tracklist are here." in body
    assert "**Extra, not on the tracklist (1):**" in body and "- 10 Secret Bonus.mp3" in body
    assert "Missing" not in body


def test_complete_folder_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr("core.notes_render.INCLUDE_FOLDER_COMPARISON", True)
    ultravox_library(tmp_path, vienna_tracks=9, with_quartet=False)
    [proposal] = build_proposals([targets(tmp_path)[("album", "Vienna (1980)")]], ultravox_fetchers())
    assert "All 9 tracks of the tracklist are here. No other files." in proposal.note_text


def test_a_folder_that_is_not_the_release_is_uncertain(tmp_path):
    folder = tmp_path / "Ultravox" / "Vienna (1980)"
    for i, title in enumerate(["Alpha", "Beta", "Gamma", "Delta"], start=1):
        make_mp3(folder / f"{i:02d} {title}.mp3", title, "Ultravox", "Vienna", i, "1980")
    [proposal] = build_proposals([targets(tmp_path)[("album", "Vienna (1980)")]], ultravox_fetchers())
    assert proposal.confidence == Confidence.UNCERTAIN and not proposal.writable
    assert "track titles match" in proposal.reasons[-1]


# -- Cristina: the namesake case -----------------------------------------------------------------------------------

def test_cristina_is_not_cristina_davena_and_discogs_finds_the_real_one(tmp_path):
    cristina_library(tmp_path)
    fetchers = cristina_fetchers(with_discogs=True)
    [proposal] = build_proposals([targets(tmp_path, KIND_ARTIST)[("artist", "Cristina")]], fetchers)
    assert proposal.confidence == Confidence.CONFIDENT
    assert proposal.sources == ["discogs"]  # MusicBrainz' candidate was rejected: none of her albums in ITS discography
    meta, body = parse_front_matter(proposal.note_text)
    assert "Monet Palaci" in body and "**Real name:** Cristina Monet Palaci" in body
    assert "D’Avena" not in proposal.note_text and "Avena" not in proposal.note_text
    assert "Profile from [Discogs](https://www.discogs.com/artist/55646)" in body
    assert "[[" not in body and "In this library" not in body
    names = {(c.source, c.name): c for c in proposal.candidates}
    assert names[("musicbrainz", "Cristina D’Avena")].hits == []
    assert names[("discogs", "Cristina")].hits == ["Cristina", "Sleep It Off"]
    assert any("Discogs" in r for r in proposal.reasons)


def test_cristina_without_a_discogs_token_is_uncertain_and_never_written(tmp_path):
    cristina_library(tmp_path)
    [proposal] = build_proposals([targets(tmp_path, KIND_ARTIST)[("artist", "Cristina")]], cristina_fetchers(with_discogs=False))
    assert proposal.confidence == Confidence.UNCERTAIN
    assert not proposal.note_text and not proposal.writable
    assert [c.name for c in proposal.candidates] == ["Cristina D’Avena"]
    result = write_proposal(proposal)
    assert result.status == STATUS_SKIPPED and "uncertain" in result.message
    assert not (tmp_path / "Cristina" / "artist.md").exists()


def test_nothing_found_is_none(tmp_path):
    ultravox_library(tmp_path, with_quartet=False)
    fetchers = ultravox_fetchers(search_artist=None)
    fetchers.mb.routes.insert(0, (r"ws/2/release-group\?query=", {"release-groups": []}))
    [proposal] = build_proposals([targets(tmp_path)[("artist", "Ultravox")]], fetchers)
    assert proposal.confidence == Confidence.NONE and not proposal.candidates


# -- writing ---------------------------------------------------------------------------------------------------------

def test_write_confident_creates_a_draft_and_dry_run_writes_nothing(tmp_path):
    artist = ultravox_library(tmp_path)
    [proposal] = build_proposals([targets(tmp_path)[("artist", "Ultravox")]], ultravox_fetchers())
    dry = write_proposal(proposal, dry_run=True)
    assert dry.ok and dry.dry_run and not (artist / "artist.md").exists()
    assert sorted(p.name for p in artist.iterdir()) == ["Quartet (1982)", "Vienna (1980)"]  # not even a temp file
    result = write_proposal(proposal)
    assert result.ok and Path(result.path) == artist / "artist.md"
    meta, _ = parse_front_matter((artist / "artist.md").read_text(encoding="utf-8"))
    assert meta["status"] == "draft"
    assert not any(p.name.startswith(".") for p in artist.iterdir())


def test_never_overwrites_and_existing_note_in_any_case_blocks(tmp_path):
    artist = ultravox_library(tmp_path)
    [proposal] = build_proposals([targets(tmp_path)[("artist", "Ultravox")]], ultravox_fetchers())
    (artist / "About.MD").write_text("the person's own note", encoding="utf-8")  # appeared after the scan
    result = write_proposal(proposal)
    assert result.status == "exists" and not result.ok
    assert (artist / "About.MD").read_text(encoding="utf-8") == "the person's own note"
    assert not (artist / "artist.md").exists()


def test_scan_skips_a_folder_with_a_lowercase_or_uppercase_note(tmp_path):
    artist = ultravox_library(tmp_path)
    (artist / "ARTIST.md").write_text("x", encoding="utf-8")
    assert ("artist", "Ultravox") not in targets(tmp_path)


def test_the_sidecar_text_is_the_introduction_when_nothing_online_has_one(tmp_path):
    artist = ultravox_library(tmp_path, with_quartet=False)
    (artist / "artist.nfo").write_text(
        "<artist><name>Ultravox</name><biography>A local biography from the nfo.</biography></artist>", encoding="utf-8")
    fetchers = ultravox_fetchers()
    fetchers.wiki = FakeFetch([(r"wikidata", {"entities": {}}), (r"search/title", {"pages": []}), (r"page/summary/", None)])
    [proposal] = build_proposals([targets(tmp_path)[("artist", "Ultravox")]], fetchers)
    assert "A local biography from the nfo." in proposal.note_text
    assert "Taken from artist.nfo" in proposal.note_text and "sidecar" in proposal.sources


def test_a_service_failure_is_a_message_not_a_traceback(tmp_path):
    ultravox_library(tmp_path)
    import urllib.error

    def down(url):
        raise urllib.error.URLError("no network")

    [proposal] = build_proposals([targets(tmp_path)[("artist", "Ultravox")]], Fetchers(mb=down, wiki=down))
    assert proposal.confidence == Confidence.NONE
    assert any("Could not reach MusicBrainz" in p for p in proposal.problems)


def test_repeated_network_failure_stops_the_run_early(tmp_path):
    ultravox_library(tmp_path)
    import urllib.error

    calls = []

    def down(url):
        calls.append(url)
        raise urllib.error.URLError("no network")

    proposals = build_proposals(list(targets(tmp_path).values()), Fetchers(mb=down, wiki=down))
    # two folders in a row failed (each one asked again: a failure is no longer remembered as an answer): the third was not even tried
    assert len(proposals) == 3 and len(calls) == 3
    assert "skipped" in proposals[2].reasons[0]


def test_progress_cancel_and_summary(tmp_path):
    ultravox_library(tmp_path)
    seen = []
    cancelled = {"now": False}

    def progress(done, total, label):
        seen.append((done, total, label))

    def cancel():
        return cancelled["now"]

    all_targets = list(targets(tmp_path).values())
    proposals = build_proposals(all_targets, ultravox_fetchers(), progress=progress, cancel=cancel)
    assert [s[:2] for s in seen] == [(0, 3), (1, 3), (2, 3), (3, 3)] and seen[0][2].startswith(("album", "artist"))
    results = {p.path: write_proposal(p, dry_run=True) for p in proposals}
    summary = summarize(proposals, results)
    assert summary.total == 3 and summary.confident == 3 and summary.would_write == 3 and summary.written == 0

    cancelled["now"] = True
    assert build_proposals(all_targets, ultravox_fetchers(), cancel=cancel) == []
    assert summarize([], cancelled=True).cancelled


# -- the opt-in journal -------------------------------------------------------------------------------------------

def _written_artist(tmp_path):
    artist = ultravox_library(tmp_path)
    [proposal] = build_proposals([targets(tmp_path)[("artist", "Ultravox")]], ultravox_fetchers())
    return artist, proposal


def test_journal_is_off_by_default_nothing_is_recorded(tmp_path, journal):
    artist, proposal = _written_artist(tmp_path)
    assert write_proposal(proposal).ok
    assert history.history_enabled() is False
    assert journal.entries() == []


def test_journal_records_a_created_note_and_undo_moves_it_to_the_bin(tmp_path, journal, recycle_bin):
    history.set_history_enabled(True)
    artist, proposal = _written_artist(tmp_path)
    result = write_proposal(proposal)
    [entry] = journal.entries()
    assert entry.kind == "note" and entry.path == result.path and entry.status == "ok"
    assert entry.note == "sha256:" + hashlib.sha256((artist / "artist.md").read_bytes()).hexdigest()
    assert not entry.restorable  # a note is not a tag change: the tag restore never touches it
    assert undo_note_entry(entry, journal) == ""
    assert not (artist / "artist.md").exists() and len(recycle_bin.trashed) == 1
    assert journal.entries()[0].status == "restored"


def test_undo_refuses_a_note_that_was_edited(tmp_path, journal, recycle_bin):
    history.set_history_enabled(True)
    artist, proposal = _written_artist(tmp_path)
    write_proposal(proposal)
    [entry] = journal.entries()
    note = artist / "artist.md"
    note.write_text(note.read_text(encoding="utf-8") + "\nMy own sentence.\n", encoding="utf-8")
    assert "changed" in undo_note_entry(entry, journal)
    assert note.exists() and recycle_bin.trashed == []
    note.unlink()
    assert "no longer there" in undo_note_entry(entry, journal)


# -- a busy MusicBrainz ---------------------------------------------------------------------------------------------

def _busy_then_ok(payload, times):
    import urllib.error

    state = {"n": 0}

    def answer(url):
        state["n"] += 1
        if state["n"] <= times:
            raise urllib.error.HTTPError(url, 503, "Service Unavailable", {}, None)
        return payload

    return answer


def test_a_busy_musicbrainz_is_asked_again_after_a_pause(tmp_path, monkeypatch):
    from core import notes_musicbrainz
    from tests.notes_helpers import RECORDED

    pauses = []
    monkeypatch.setattr(notes_musicbrainz, "_sleep", pauses.append)
    ultravox_library(tmp_path)
    fetchers = ultravox_fetchers()
    fetchers.mb.routes.insert(0, (r"ws/2/release/", _busy_then_ok(RECORDED["mb_release_vienna"], 1)))
    [proposal] = build_proposals([targets(tmp_path)[("album", "Vienna (1980)")]], fetchers)
    assert proposal.confidence == Confidence.CONFIDENT and "1. Astradyne 7:07" in proposal.note_text
    assert pauses == [1.5]  # the first retry is soon: a busy answer is mostly a passing one


def test_a_service_that_stays_busy_is_backed_off_from_and_the_wait_is_announced_and_counted(tmp_path, monkeypatch):
    from core import notes_musicbrainz
    from tests.notes_helpers import RECORDED

    pauses = []
    monkeypatch.setattr(notes_musicbrainz, "_sleep", pauses.append)
    ultravox_library(tmp_path)
    fetchers = ultravox_fetchers()
    fetchers.mb.routes.insert(0, (r"ws/2/release/", _busy_then_ok(RECORDED["mb_release_vienna"], 2)))
    engine = NotesEngine(fetchers)
    said = []
    engine.on_status = said.append  # what the progress label shows: never wait silently
    [proposal] = build_proposals([targets(tmp_path)[("album", "Vienna (1980)")]], engine=engine)
    assert proposal.confidence == Confidence.CONFIDENT
    assert pauses == [1.5, 4.0] and said == ["MusicBrainz is busy, waiting 1.5 s", "MusicBrainz is busy, waiting 4 s"]
    assert engine.phases.count("busy") == 2 and engine.phases.seconds("busy") == 5.5
    assert "waiting for a busy service, 2x" in engine.phases.time_line()


def test_a_failed_tracklist_fetch_never_writes_a_thin_note(tmp_path, monkeypatch):
    from core import notes_musicbrainz
    from tests.notes_helpers import RECORDED

    monkeypatch.setattr(notes_musicbrainz, "_sleep", lambda _s: None)
    ultravox_library(tmp_path)
    fetchers = ultravox_fetchers()
    fetchers.mb.routes.insert(0, (r"ws/2/release/", _busy_then_ok(RECORDED["mb_release_vienna"], 99)))
    [proposal] = build_proposals([targets(tmp_path)[("album", "Vienna (1980)")]], fetchers)
    assert proposal.confidence == Confidence.UNCERTAIN and not proposal.note_text
    assert any("busy" in p for p in proposal.problems)
    assert write_proposal(proposal).status == STATUS_SKIPPED


def test_comparison_pairs_equal_titles_even_when_numbers_and_durations_differ():
    from core.notes_local import LocalTrack
    from core.notes_render import TrackInfo, compare_tracks

    tracks = [TrackInfo(1, 1, "Sleepwalk", 193), TrackInfo(1, 5, "Astradyne", 428)]
    local = [LocalTrack(path=Path("01 Astradyne.mp3"), title="Astradyne", track=1, seconds=12.0),
             LocalTrack(path=Path("99 Bonus.mp3"), title="Bonus", track=99, seconds=60.0)]
    result = compare_tracks(local, tracks)
    assert [(t.title, f.name) for t, f in result.present] == [("Astradyne", "01 Astradyne.mp3")]
    assert [t.title for t in result.missing] == ["Sleepwalk"] and [f.name for f in result.extra] == ["99 Bonus.mp3"]


def test_release_pick_prefers_the_first_release_date_and_a_home_country():
    from core import notes_flow
    from core.notes_musicbrainz import ReleaseSummary

    releases = [ReleaseSummary("ca", date="1980-07-11", country="CA", status="Official", track_count=9),
                ReleaseSummary("gb", date="1980-07-11", country="GB", status="Official", track_count=9),
                ReleaseSummary("old", date="1979-01-01", country="DE", status="Official", track_count=9),
                ReleaseSummary("boot", date="1980-07-11", country="GB", status="Bootleg", track_count=9)]
    assert notes_flow._pick_release(releases, 9, "1980-07-11").id == "gb"
