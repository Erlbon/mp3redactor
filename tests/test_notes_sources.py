"""Folder notes from the OTHER sources: a Discogs album (release or master) a person picks, a Discogs artist (profile,
members, discography when MusicBrainz has none), a thin Wikipedia-only draft, and the explicit minimal note from the
file tags. Pure engine tests on hand-made Discogs fixtures (tests/fixtures/notes/discogs_vienna.json) and recorded
MusicBrainz / Wikipedia replies; nothing reaches the network."""

import json
from pathlib import Path

import pytest

from core.discogs_lookup import DiscogsClient
from core.notes_flow import (
    DISCOGS_MISSING, LEVEL_LOCAL_TAGS, Confidence, Fetchers, NotesEngine, build_proposals, scan,
)
from core.notes_local import KIND_ALBUM, KIND_ARTIST
from redactor_common.core.lookup_client import Throttle
from redactor_common.core.notes import parse_front_matter
from tests.notes_helpers import RECORDED, FakeFetch, make_mp3, ultravox_fetchers, ultravox_library

DV = json.loads((Path(__file__).parent / "fixtures" / "notes" / "discogs_vienna.json").read_text(encoding="utf-8"))


def targets(root, kind=None):
    found = scan([root], kinds=[kind] if kind else (KIND_ARTIST, KIND_ALBUM))
    return {(t.kind, t.folder.name): t for t in found.targets}


def discogs(*routes) -> DiscogsClient:
    return DiscogsClient("test-token", fetch=FakeFetch(list(routes)), throttle=Throttle(0))


def no_mb() -> FakeFetch:
    return FakeFetch([(r"ws/2/artist\?query", {"artists": []}), (r"ws/2/release-group\?query=", {"release-groups": []})])


def no_wiki() -> FakeFetch:
    return FakeFetch([(r"wikidata", {"entities": {}}), (r"search/title", {"pages": []}), (r"page/summary/", None)])


def album_routes():
    return [(r"database/search\?type=artist", {"results": []}), (r"database/search", DV["search_vienna"]),
            (r"masters/555", DV["master_vienna"]),
            (r"releases/1111", DV["release_vienna"])]


def vienna_folder(tmp_path):
    for i, title in enumerate(["Astradyne", "New Europeans", "Private Lives"], start=1):
        make_mp3(tmp_path / "Ultravox" / "Vienna (1980)" / f"{i:02d} {title}.mp3", title, "Ultravox", "Vienna", i, "1980")
    return tmp_path


def album_target(root):
    return targets(root)[("album", "Vienna (1980)")]


# -- a Discogs album -----------------------------------------------------------------------------------------

def test_discogs_only_album_is_uncertain_but_its_candidate_can_be_chosen_and_builds_a_note(tmp_path):
    vienna_folder(tmp_path)
    fetchers = Fetchers(mb=no_mb(), wiki=no_wiki(), discogs=discogs(*album_routes()))
    engine = NotesEngine(fetchers)
    proposal = engine.build_proposal(album_target(tmp_path))
    assert proposal.confidence == Confidence.UNCERTAIN and not proposal.writable
    cands = [c for c in proposal.candidates if c.source == "discogs"]
    assert cands and cands[0].id == "1111" and "Ultravox - Vienna" in cands[0].name
    assert engine.can_choose(proposal, cands[0]) and engine.why_cannot_choose(proposal, cands[0]) == ""
    assert engine.best_guess(proposal) is not None  # --include-uncertain / Tick best guesses can use it

    built = engine.rebuild(proposal, cands[0])
    assert built.confidence == Confidence.CONFIDENT and built.writable and built.sources == ["discogs"]
    meta, body = parse_front_matter(built.note_text)
    assert meta["title"] == "Vienna" and meta["status"] == "draft" and "new wave" in meta["genres"]
    assert "**Released:** 1980-07-25" in body and "**Label:** Chrysalis (CDL 1296)" in body
    assert "**Country:** UK" in body and "**Artist:** Ultravox" in body
    # vinyl sides A1/A2/B1 are numbered 1..n, durations kept
    assert "1. Astradyne 6:09\n2. New Europeans 4:03\n3. Private Lives 4:58" in body
    # structured credits only: producer, written by, musicians, sleeve design, photography
    assert "- **Producer:** Conny Plank" in body and "- **Written by:** Midge Ure" in body
    assert "- **Sleeve design:** Peter Saville" in body and "- **Photography:** Peter Saville, Eddie Hallin" in body
    assert "Midge Ure (guitar, vocals)" in body
    assert "SHIPPING REMARK" not in built.note_text and "Hayes" not in built.note_text  # the free-text notes
    assert "Facts from [Discogs](https://www.discogs.com/release/1111) (user-contributed)" in body
    assert body.rstrip().startswith("---") or "*Draft." in body
    assert "100% of the local track titles are on the release" in " ".join(built.reasons)


def test_discogs_master_candidate_uses_its_main_release(tmp_path):
    vienna_folder(tmp_path)
    client = discogs(*album_routes())
    engine = NotesEngine(Fetchers(mb=no_mb(), wiki=no_wiki(), discogs=client))
    proposal = engine.build_proposal(album_target(tmp_path))
    from core.notes_flow import Candidate

    master = Candidate("discogs", "master:555", "Ultravox - Vienna (master)", "1980", [], 1.0)
    built = engine.rebuild(proposal, master)
    assert built.confidence == Confidence.CONFIDENT and "Chrysalis (CDL 1296)" in built.note_text
    assert client._fetch.count(r"masters/555") == 1


def test_discogs_candidate_without_a_token_says_exactly_what_is_missing(tmp_path):
    vienna_folder(tmp_path)
    engine = NotesEngine(Fetchers(mb=no_mb(), wiki=no_wiki(), discogs=discogs(*album_routes())))
    proposal = engine.build_proposal(album_target(tmp_path))
    cand = next(c for c in proposal.candidates if c.source == "discogs")
    tokenless = NotesEngine(Fetchers(mb=no_mb(), wiki=no_wiki()))
    assert not tokenless.can_choose(proposal, cand)
    assert tokenless.why_cannot_choose(proposal, cand) == DISCOGS_MISSING
    assert "Tools > API Keys" in DISCOGS_MISSING
    # and a NONE proposal says which source was not searched, never a dead end
    nothing = tokenless.build_proposal(album_target(tmp_path))
    assert nothing.confidence == Confidence.NONE
    assert any("Discogs was not searched" in r and "Tools > API Keys" in r for r in nothing.reasons)


# -- a Discogs artist -------------------------------------------------------------------------------------------

def artist_routes():
    return [(r"database/search\?type=artist", {"results": [{"id": 777, "title": "Ultravox"}]}),
            (r"database/search", {"results": []}),
            (r"artists/777/releases", DV["releases_ultravox"]), (r"artists/777", DV["artist_ultravox"])]


def test_discogs_artist_without_musicbrainz_gets_profile_members_aliases_and_a_grouped_discography(tmp_path):
    ultravox_library(tmp_path)
    fetchers = Fetchers(mb=no_mb(), wiki=no_wiki(), discogs=discogs(*artist_routes()))
    [proposal] = build_proposals([targets(tmp_path)[("artist", "Ultravox")]], fetchers)
    assert proposal.confidence == Confidence.CONFIDENT and proposal.sources == ["discogs"]
    meta, body = parse_front_matter(proposal.note_text)
    assert meta["title"] == "Ultravox" and "https://www.discogs.com/artist/777-Ultravox" in meta["links"]
    assert "https://en.wikipedia.org/wiki/Ultravox" in meta["links"]
    assert "British Midge Ure fronted band. See [the site](https://example.org/uv)." in body  # markup cleaned
    assert "**Members:** Midge Ure, Billy Currie" in body and "**Also known as:** Tiger Lily" in body
    assert "## Studio albums\n\n- Ha!-Ha!-Ha! (1977)\n- Vienna (1980)" in body
    assert "## EPs\n\n- Quartet EP (1982)" in body and "## Singles\n\n- Sleepwalk (1980)" in body
    assert "## Compilations\n\n- The Collection (1984)" in body
    assert "Appears on something" not in body  # only releases where the artist is the main artist
    assert body.count("Vienna (1980)") == 1  # master and release of one title and year: one line
    assert "Profile from [Discogs](https://www.discogs.com/artist/777)" in body


def test_with_musicbrainz_the_verified_discography_wins_and_discogs_adds_members_only(tmp_path):
    ultravox_library(tmp_path)
    fetchers = ultravox_fetchers()
    fetchers.discogs = discogs(*artist_routes())
    [proposal] = build_proposals([targets(tmp_path)[("artist", "Ultravox")]], fetchers)
    assert proposal.confidence == Confidence.CONFIDENT and "musicbrainz" in proposal.sources
    body = parse_front_matter(proposal.note_text)[1]
    assert "- Ha! Ha! Ha! (1977)" in body and "Ha!-Ha!-Ha!" not in body  # MusicBrainz' spelling, not Discogs'
    assert "Quartet EP" not in body


def test_discogs_artist_candidate_chosen_by_a_person_builds_end_to_end(tmp_path):
    ultravox_library(tmp_path)
    fetchers = Fetchers(mb=no_mb(), wiki=no_wiki(), discogs=discogs(*artist_routes()))
    engine = NotesEngine(fetchers)
    proposal = engine.build_proposal(targets(tmp_path)[("artist", "Ultravox")])
    cand = next(c for c in proposal.candidates if c.source == "discogs")
    built = engine.rebuild(proposal, cand)
    assert built.writable and "## Studio albums" in built.note_text


# -- Wikipedia only -----------------------------------------------------------------------------------------------

def wiki_only_fetchers():
    r = RECORDED
    wiki = FakeFetch([
        (r"wikidata", {"entities": {}}),
        (r"search/title\?q=Ultravox", {"pages": [{"title": "Ultravox", "description": "British new wave band",
                                                  "excerpt": "Ultravox were a British new wave band"}]}),
        (r"search/title\?q=Vienna", r["wiki_search_vienna"]),
        (r"search/title", {"pages": []}),
        (r"page/summary/Ultravox$", r["wiki_summary_ultravox"]),
        (r"page/summary/Vienna_\(Ultravox_album\)", r["wiki_summary_vienna_album"]),
        (r"page/summary/", None),
    ])
    return Fetchers(mb=no_mb(), wiki=wiki)


def test_wikipedia_only_artist_is_uncertain_with_a_wikipedia_candidate_and_a_thin_draft_when_chosen(tmp_path):
    ultravox_library(tmp_path)
    engine = NotesEngine(wiki_only_fetchers())
    proposal = engine.build_proposal(targets(tmp_path)[("artist", "Ultravox")])
    assert proposal.confidence == Confidence.UNCERTAIN and not proposal.writable and not proposal.note_text
    [cand] = proposal.candidates
    assert cand.source == "wikipedia" and cand.id == "Ultravox"
    assert engine.can_choose(proposal, cand)
    built = engine.rebuild(proposal, cand)
    assert built.writable and built.sources == ["wikipedia"]
    meta, body = parse_front_matter(built.note_text)
    assert meta["status"] == "draft" and meta["title"] == "Ultravox"
    assert "Ultravox were a British new wave band" in body and "**Wikipedia describes it as:** British new wave band" in body
    assert "## " not in body.replace("# Ultravox", "")  # no discography invented
    assert "Introduction from Wikipedia" in body


def test_wikipedia_only_album_and_never_in_a_batch_without_include_uncertain(tmp_path):
    vienna_folder(tmp_path)
    engine = NotesEngine(wiki_only_fetchers())
    proposal = engine.build_proposal(album_target(tmp_path))
    assert proposal.confidence == Confidence.UNCERTAIN and not proposal.writable
    cand = next(c for c in proposal.candidates if c.source == "wikipedia")
    assert cand.id == "Vienna (Ultravox album)"
    assert engine.best_guess(proposal) is cand  # only --include-uncertain / Tick best guesses ever use it
    built = engine.rebuild(proposal, cand)
    body = parse_front_matter(built.note_text)[1]
    assert built.writable and "Vienna is the fourth studio album" in body and "1. Astradyne" in body


# -- the explicit local-only note ----------------------------------------------------------------------------------

def test_local_only_album_note_has_the_tracklist_from_the_files_and_says_so(tmp_path):
    vienna_folder(tmp_path)
    engine = NotesEngine(Fetchers(mb=no_mb(), wiki=no_wiki()))
    proposal = engine.build_proposal(album_target(tmp_path))
    assert proposal.confidence == Confidence.NONE
    assert engine.local_note(proposal).confidence == Confidence.CONFIDENT  # only because it is asked for explicitly
    built = engine.rebuild(proposal, level=LEVEL_LOCAL_TAGS)
    meta, body = parse_front_matter(built.note_text)
    assert built.writable and built.sources == ["tags"] and meta["status"] == "draft"
    assert meta["title"] == "Vienna" and meta["years"] == "1980"
    assert "**Artist:** Ultravox" in body and "1. Astradyne\n2. New Europeans\n3. Private Lives" in body
    assert "*Draft. Built from the file tags only, no online source.*" in body
    assert "Facts from" not in body


def test_local_only_artist_note_lists_the_album_folders_as_plain_releases(tmp_path):
    ultravox_library(tmp_path)
    engine = NotesEngine(Fetchers(mb=no_mb(), wiki=no_wiki()))
    proposal = engine.build_proposal(targets(tmp_path)[("artist", "Ultravox")])
    built = engine.rebuild(proposal, level=LEVEL_LOCAL_TAGS)
    body = parse_front_matter(built.note_text)[1]
    assert built.writable
    assert "## Releases\n\n- Vienna (1980)\n- Quartet (1982)" in body
    assert "Built from the file tags only, no online source" in body


def test_a_batch_run_never_builds_the_local_note(tmp_path):
    ultravox_library(tmp_path)
    fetchers = Fetchers(mb=no_mb(), wiki=no_wiki())
    proposals = build_proposals(list(targets(tmp_path).values()), fetchers)
    assert all(not p.writable and "tags" not in p.sources for p in proposals)
