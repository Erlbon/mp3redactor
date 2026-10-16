"""Review of 2026-10-16, batch 3: the notes ENGINE's correctness. Every test drives the app's own engine (NotesEngine /
build_proposals / the CLI) on real tagged MP3 files and fake MusicBrainz / Wikipedia / Discogs replies; nothing reaches the
network (an unexpected URL fails the test).

  1 namesake artists            2 generic titles identify any artist       3 a best guess keeps every veto
  7 one timeout is not forever  8a an album folder is not a library root  8b a collection has no artist.md to replace
"""

import json
import re
import urllib.error
from urllib.parse import unquote_plus

import pytest

from core.discogs_lookup import DiscogsClient
from core.notes_flow import (
    Confidence, Fetchers, GuessCandidate, NotesEngine, build_proposals, scan, write_proposal,
)
from core.notes_local import ALL_KINDS, KIND_ALBUM, KIND_ARTIST
from redactor_common.core.lookup_client import Throttle
from tests.notes_helpers import FakeFetch, make_mp3, ultravox_fetchers, ultravox_library

QUIET_WIKI = [(r"search/title", {"pages": []}), (r"page/summary/", None), (r"wikidata\.org", {"entities": {}})]


def artist_json(aid, name, begin="1980"):
    return {"id": aid, "name": name, "sort-name": name, "type": "Group", "country": "GB", "score": 100,
            "life-span": {"begin": begin}, "aliases": [], "relations": [], "genres": [], "tags": []}


def rg_json(rid, title, date="1985", artist=None):
    out = {"id": rid, "title": title, "first-release-date": date, "primary-type": "Album", "secondary-types": []}
    if artist:
        out["artist-credit"] = [{"name": artist[1], "artist": {"id": artist[0], "name": artist[1]}}]
    return out


def mb_world(artists, search=None, owners=None):
    """FakeFetch routes for a small MusicBrainz. artists: {id: (name, begin, [album titles])}. search: the ids the artist-name
    search returns (default: none). owners: {album title: [artist ids]}: what the search by album title returns (25 per title)."""
    search = search or []
    owners = owners or {}

    def by_title(url):
        text = unquote_plus(url).casefold()
        found = []
        for title, ids in owners.items():
            if f'releasegroup:"{title.casefold()}"' in text or f"releasegroup:{title.casefold()}" in text:
                found += [rg_json(f"t-{i}-{title}", title, artist=(i, artists[i][0])) for i in ids]
        return {"release-groups": found}

    def one_artist(url):
        aid = re.search(r"ws/2/artist/([^?]+)\?", url).group(1)
        name, begin, _titles = artists[aid]
        return artist_json(aid, name, begin)

    def browse(url):
        aid = re.search(r"release-group\?artist=([^&]+)", url).group(1)
        return {"release-groups": [rg_json(f"{aid}-{n}", t) for n, t in enumerate(artists[aid][2])]}

    return [
        (r"ws/2/release-group\?query=[^ ]*limit=25", by_title),
        (r"ws/2/release-group\?query=", {"release-groups": []}),
        (r"ws/2/artist\?query", lambda url: {"artists": [artist_json(i, artists[i][0], artists[i][1]) for i in search]}),
        (r"ws/2/artist/[^?]+\?", one_artist),
        (r"ws/2/release-group\?artist=", browse),
    ]


def fetchers(artists, search=None, owners=None, discogs=None):
    return Fetchers(mb=FakeFetch(mb_world(artists, search, owners)), wiki=FakeFetch(QUIET_WIKI), discogs=discogs)


def one(root, kind):
    [target] = [t for t in scan([root], kinds=ALL_KINDS).targets if t.kind == kind]
    return target


def artist_proposal(tmp_path, folder, album_folder, fetch):
    make_mp3(tmp_path / folder / album_folder / "01 Opening.mp3", "Opening", folder, album_folder.rsplit(" (", 1)[0], 1, "1985")
    [proposal] = build_proposals(list(scan([tmp_path], kinds=(KIND_ARTIST,)).targets), fetch)
    return proposal


# -- 1. namesake artists ------------------------------------------------------------------------------------------

@pytest.mark.parametrize("album", ["Mirage", "Greatest Hits", "Live", "Demo"])
def test_two_bands_with_the_same_name_and_the_same_album_are_uncertain_not_the_first_one(tmp_path, album):
    """Review 2026-10-16 (1): the candidate loop stopped at the first exact-name band holding the folder's only album, so
    decide_artist never saw the other one: 'Mirage/Mirage (1985)' came back CONFIDENT and a batch --write wrote it."""
    artists = {"mir-1": ("Mirage", "1980", [album]), "mir-2": ("Mirage", "1982", [album])}
    proposal = artist_proposal(tmp_path, "Mirage", f"{album} (1985)", fetchers(artists, search=["mir-1", "mir-2"]))
    assert proposal.confidence == Confidence.UNCERTAIN and not proposal.writable
    assert {c.id for c in proposal.candidates} == {"mir-1", "mir-2"}
    assert "two artists with this name hold the album" in " ".join(proposal.reasons)
    assert not write_proposal(proposal).ok  # nothing a batch --write could put on disk


def test_one_band_called_mirage_is_still_confident(tmp_path):
    artists = {"mir-1": ("Mirage", "1980", ["Mirage"])}
    proposal = artist_proposal(tmp_path, "Mirage", "Mirage (1985)", fetchers(artists, search=["mir-1"]))
    assert proposal.confidence == Confidence.CONFIDENT and proposal.writable


def test_a_namesake_that_does_not_hold_the_album_does_not_spoil_the_match(tmp_path):
    artists = {"mir-1": ("Mirage", "1980", ["Mirage"]), "mir-2": ("Mirage", "1982", ["Something Else"])}
    proposal = artist_proposal(tmp_path, "Mirage", "Mirage (1985)", fetchers(artists, search=["mir-1", "mir-2"]))
    assert proposal.confidence == Confidence.CONFIDENT
    assert [c.id for c in proposal.candidates][0] == "mir-1"


def test_two_albums_both_held_stop_the_search_early_as_before(tmp_path):
    """Two local albums held by an exact-name band is strong proof: the next candidates are not even looked at."""
    artists = {"mir-1": ("Mirage", "1980", ["Mirage", "Jewel"]), "mir-2": ("Mirage", "1982", ["Mirage", "Jewel"])}
    fx = fetchers(artists, search=["mir-1", "mir-2"])
    make_mp3(tmp_path / "Mirage" / "Mirage (1985)" / "01 a.mp3", "a", "Mirage", "Mirage", 1, "1985")
    make_mp3(tmp_path / "Mirage" / "Jewel (1986)" / "01 b.mp3", "b", "Mirage", "Jewel", 1, "1986")
    [proposal] = build_proposals([t for t in scan([tmp_path], kinds=(KIND_ARTIST,)).targets], fx)
    assert proposal.confidence == Confidence.CONFIDENT
    assert fx.mb.count(r"release-group\?artist=mir-2") == 0


def test_the_discogs_twin_of_the_namesake_case(tmp_path):
    """The same loop exists for Discogs: two Discogs artists called Mirage that both list the album."""
    discogs = FakeFetch([
        (r"database/search", {"results": [{"id": 1, "title": "Mirage", "uri": "/artist/1-Mirage"},
                                          {"id": 2, "title": "Mirage (2)", "uri": "/artist/2-Mirage"}]}),
        (r"artists/1/releases", {"releases": [{"title": "Mirage", "year": 1985, "role": "Main", "type": "master"}]}),
        (r"artists/2/releases", {"releases": [{"title": "Mirage", "year": 1986, "role": "Main", "type": "master"}]}),
        (r"artists/\d+", {"name": "Mirage", "profile": ""}),
    ])
    client = DiscogsClient("test-token", fetch=discogs, throttle=Throttle(0))
    proposal = artist_proposal(tmp_path, "Mirage", "Mirage (1985)", fetchers({}, search=[], discogs=client))
    assert proposal.confidence == Confidence.UNCERTAIN and not proposal.writable
    assert {c.id for c in proposal.candidates} >= {"1", "2"}
    assert "two artists with this name hold the album" in " ".join(proposal.reasons)


# -- 2. generic titles identify nobody -------------------------------------------------------------------------------

QUEEN = {"queen": ("Queen", "1970", ["Greatest Hits", "Live", "Sheer Heart Attack"])}


def zyx_library(tmp_path, titles):
    for n, title in enumerate(titles):
        make_mp3(tmp_path / "Zyx Obscure" / f"{title} (199{n})" / "01 a.mp3", "a", "Zyx Obscure", title, 1, f"199{n}")


def test_greatest_hits_and_live_do_not_turn_an_unknown_band_into_queen(tmp_path):
    """Review 2026-10-16 (2): the search by album title returns 25 groups per title, so 'Greatest Hits' + 'Live' found the same big
    artist twice and it was promoted to CONFIDENT 'although the name differs': 'Zyx Obscure/' became Queen, writable."""
    zyx_library(tmp_path, ["Greatest Hits", "Live"])
    fx = fetchers(QUEEN, search=[], owners={"Greatest Hits": ["queen"], "Live": ["queen"]})
    [proposal] = build_proposals(scan([tmp_path], kinds=(KIND_ARTIST,)).targets, fx)
    assert proposal.confidence != Confidence.CONFIDENT and not proposal.writable
    assert fx.mb.count(r"release-group\?query=[^ ]*limit=25") == 0  # generic titles are not even searched


def test_two_distinctive_titles_that_only_one_artist_has_still_find_a_differently_written_name(tmp_path):
    """The step still does its job: a band written in another script, found by two titles nobody else has."""
    artists = {"len": ("Ленинград", "1985", ["Dachniki", "Pirates"])}
    zyx_library(tmp_path, ["Dachniki", "Pirates"])
    fx = fetchers(artists, search=[], owners={"Dachniki": ["len"], "Pirates": ["len"]})
    [proposal] = build_proposals(scan([tmp_path], kinds=(KIND_ARTIST,)).targets, fx)
    assert proposal.confidence == Confidence.CONFIDENT and proposal.candidates[0].id == "len"


def test_distinctive_titles_that_several_artists_share_stay_uncertain_when_the_name_is_unrelated(tmp_path):
    artists = {"a": ("Alpha Band", "1990", ["Dachniki", "Pirates"]), "b": ("Beta Band", "1990", ["Dachniki", "Pirates"])}
    zyx_library(tmp_path, ["Dachniki", "Pirates"])
    fx = fetchers(artists, search=[], owners={"Dachniki": ["a", "b"], "Pirates": ["a", "b"]})
    [proposal] = build_proposals(scan([tmp_path], kinds=(KIND_ARTIST,)).targets, fx)
    assert proposal.confidence == Confidence.UNCERTAIN and not proposal.writable
    assert {c.id for c in proposal.candidates} == {"a", "b"}


# -- 3. a best guess is not 'chosen by you' --------------------------------------------------------------------------

def test_a_best_guess_skips_a_candidate_with_no_local_album(tmp_path):
    """Review (3): the zero-hit 'Cristina D'Avena' was the 'best guess' because it was first; the cristina library with no
    Discogs token has only that candidate, and a guess from nothing is no guess."""
    from tests.notes_helpers import cristina_fetchers, cristina_library

    cristina_library(tmp_path)
    engine = NotesEngine(cristina_fetchers(with_discogs=False))
    [proposal] = build_proposals([next(t for t in scan([tmp_path], kinds=(KIND_ARTIST,)).targets)], cristina_fetchers(with_discogs=False),
                                 engine=engine)
    assert proposal.confidence == Confidence.UNCERTAIN and proposal.candidates and not any(c.hits for c in proposal.candidates)
    assert engine.best_guess(proposal) is None
    assert "no candidate has any of the local albums" in engine.why_no_guess(proposal)


def test_a_best_guess_takes_the_candidate_with_a_hit_over_the_first_one(tmp_path):
    from core.notes_flow import Proposal
    from redactor_common.core.notes_model import Candidate

    make_mp3(tmp_path / "Cristina" / "Sleep It Off (1984)" / "01 a.mp3", "a", "Cristina", "Sleep It Off", 1, "1984")
    target = next(t for t in scan([tmp_path], kinds=(KIND_ARTIST,)).targets)
    engine = NotesEngine(Fetchers(mb=FakeFetch([]), wiki=FakeFetch([])))
    zero = Candidate("musicbrainz", "d-avena", "Cristina D'Avena", "", [], 0.5, "")
    monet = Candidate("musicbrainz", "monet", "Cristina Monet", "", ["Sleep It Off"], 0.5, "")
    from core.notes_level import LEVEL_ARTIST
    proposal = Proposal(target, Confidence.UNCERTAIN, level=LEVEL_ARTIST, candidates=[zero, monet])
    guess = engine.best_guess(proposal)
    assert isinstance(guess, GuessCandidate) and guess.id == "monet"
    tied = Proposal(target, Confidence.UNCERTAIN, level=LEVEL_ARTIST, candidates=[monet, Candidate(
        "musicbrainz", "other", "Cristina Other", "", ["Sleep It Off"], 0.5, "")])
    assert engine.best_guess(tied) is None and "equally well" in engine.why_no_guess(tied)


def lament_library(tmp_path):
    """Ultravox/Lament (1984) whose files do not match the release's tracklist at all."""
    ultravox_library(tmp_path, with_quartet=False)
    folder = tmp_path / "Ultravox" / "Lament (1984)"
    for i, title in enumerate(["Alpha", "Beta", "Gamma", "Delta"], start=1):
        make_mp3(folder / f"{i:02d} {title}.mp3", title, "Ultravox", "Lament", i, "1984")
    return folder


def test_a_best_guess_keeps_the_track_overlap_veto_and_does_not_say_chosen_by_you(tmp_path):
    """Review (3): rebuild() forced the guess and _finish_album skipped the track-overlap veto when forced, so an album folder
    whose files match 0% of the release's tracklist became CONFIDENT and album.md was written with the wrong tracklist."""
    lament_library(tmp_path)
    fx = ultravox_fetchers()
    engine = NotesEngine(fx)
    target = next(t for t in scan([tmp_path], kinds=(KIND_ALBUM,)).targets if t.folder.name == "Lament (1984)")
    [proposal] = build_proposals([target], fx, engine=engine)
    assert proposal.confidence == Confidence.UNCERTAIN and "track titles match" in proposal.reasons[-1]
    guess = engine.best_guess(proposal)
    assert guess is not None  # the title matches and the score is good: it IS the engine's best guess ...
    rebuilt = engine.rebuild(proposal, candidate=guess)
    assert rebuilt.confidence == Confidence.UNCERTAIN and not rebuilt.writable  # ... but every veto still holds
    assert "track titles match" in " ".join(rebuilt.reasons) and "chosen by you" not in " ".join(rebuilt.reasons)
    picked = engine.rebuild(proposal, candidate=proposal.candidates[0])  # a PERSON's pick is believed past the veto
    assert picked.confidence == Confidence.CONFIDENT and "chosen by you" in " ".join(picked.reasons)


def test_a_best_guess_that_holds_says_best_guess_in_the_engine_itself(tmp_path):
    """Vienna matched 3 of its 9 tracks: uncertain by the rival rule would be needed; build one by lowering the rule:
    the folder is the release (confident), so build the uncertain state with a candidate list and rebuild around it."""
    ultravox_library(tmp_path, vienna_tracks=9, with_quartet=False)
    fx = ultravox_fetchers(search_artist=None)
    engine = NotesEngine(fx)
    target = next(t for t in scan([tmp_path], kinds=(KIND_ALBUM,)).targets)
    [proposal] = build_proposals([target], fx, engine=engine)
    proposal.confidence = Confidence.UNCERTAIN  # as a review of an uncertain row would hold it
    proposal.note_text = ""
    guess = engine.best_guess(proposal)
    rebuilt = engine.rebuild(proposal, candidate=guess)
    assert rebuilt.confidence == Confidence.CONFIDENT and "best guess" in " ".join(rebuilt.reasons)
    assert "chosen by you" not in " ".join(rebuilt.reasons)


@pytest.fixture
def cli(monkeypatch):
    """The command line with its services replaced; returns run(capsys, *argv) -> (code, out, err)."""
    from core.settings import Settings
    from mp3cli import cmd_notes, files as cli_files
    from mp3cli.main import main

    monkeypatch.setattr(cli_files, "load_settings", lambda *a, **k: Settings())
    state = {"fetchers": ultravox_fetchers()}
    monkeypatch.setattr(cmd_notes, "make_fetchers", lambda use_discogs: state["fetchers"])

    def run(capsys, *argv):
        code = main(list(argv))
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    run.state = state
    return run


def test_the_cli_include_uncertain_writes_no_wrong_tracklist_and_says_why(tmp_path, capsys, cli):
    lament_library(tmp_path)
    code, out, _err = cli(capsys, "notes", str(tmp_path / "Ultravox" / "Lament (1984)"), "--kind", "album", "--write",
                          "--include-uncertain", "--no-cache")
    assert not (tmp_path / "Ultravox" / "Lament (1984)" / "album.md").exists()
    assert "review" in out and "the best guess did not hold" in out and "track titles match" in out
    assert "from an UNCERTAIN match" not in out


def test_the_cli_batch_write_does_not_write_a_namesake(tmp_path, capsys, cli):
    make_mp3(tmp_path / "Mirage" / "Mirage (1985)" / "01 a.mp3", "a", "Mirage", "Mirage", 1, "1985")
    cli.state["fetchers"] = fetchers({"mir-1": ("Mirage", "1980", ["Mirage"]), "mir-2": ("Mirage", "1982", ["Mirage"])},
                                     search=["mir-1", "mir-2"])
    code, out, _err = cli(capsys, "notes", str(tmp_path), "--kind", "artist", "--write", "--no-cache")
    assert not (tmp_path / "Mirage" / "artist.md").exists()
    assert "two artists with this name hold the album" in out


# -- 7. one timeout is not forever -------------------------------------------------------------------------------------

def test_a_transient_failure_is_not_remembered_and_the_run_goes_on(tmp_path):
    """Review (7): a failed resolve_artist was memoized for the engine's life, so with --kind both one timeout made every later
    folder of that artist fail from the memo, the failure counter reached 2 and the rest of the run was skipped."""
    ultravox_library(tmp_path)
    real = ultravox_fetchers()
    calls = []

    def flaky(url):
        calls.append(url)
        if len(calls) == 1:
            raise urllib.error.URLError("timed out")
        return real.mb(url)

    engine = NotesEngine(Fetchers(mb=flaky, wiki=real.wiki))
    proposals = build_proposals(list(scan([tmp_path], kinds=(KIND_ARTIST, KIND_ALBUM)).targets), engine=engine)
    by = {(p.kind, p.folder.name): p for p in proposals}
    assert by[("artist", "Ultravox")].confidence == Confidence.NONE and by[("artist", "Ultravox")].problems
    assert by[("album", "Vienna (1980)")].confidence == Confidence.CONFIDENT  # the same artist asked again, not the memo
    assert by[("album", "Quartet (1982)")].confidence != Confidence.NONE or not by[("album", "Quartet (1982)")].problems
    assert not any("repeated" in " ".join(p.problems) for p in proposals)


# -- 8a. an album folder given to the default kind ------------------------------------------------------------------------

def test_an_album_folder_with_the_default_kind_says_so_not_library_root(tmp_path, capsys, cli):
    ultravox_library(tmp_path, with_quartet=False)
    album = tmp_path / "Ultravox" / "Vienna (1980)"
    found = scan([album], kinds=(KIND_ARTIST,))
    message = found.wrong_level_message("add --kind album")
    assert not found.targets and "album folder" in message and "--kind album" in message
    from redactor_common.cli import CliError

    with pytest.raises(CliError) as why:
        cli(capsys, "notes", str(album), "--no-cache")
    assert "album folder" in str(why.value) and "--kind album" in str(why.value) and "library root" not in str(why.value)
    code, out, err = cli(capsys, "notes", str(album), "--kind", "album", "--no-cache")
    assert code == 0 and "would-write" in out


# -- 8b. a collection has no artist.md to replace -----------------------------------------------------------------------------

def test_a_folder_found_to_be_a_collection_does_not_keep_the_artist_note_to_replace(tmp_path):
    """Review (8b): _collection_proposal set kind_override 'about' but old_state stayed the folder's old artist.md, so writing
    failed with 'There is no about.md to replace any more'."""
    from redactor_common.core.note_text import credit_footer  # noqa: F401
    from redactor_common.core.notes import NoteFrontMatter, render_note
    from core.notes_render import CURRENT_NOTE_FORMAT
    from tests.test_notes_levels import genre_wiki

    base = tmp_path / "New wave music"
    make_mp3(base / "Alpha (1990)" / "01 a.mp3", "a", "Band One", "Alpha", 1, "1990")
    make_mp3(base / "Beta (1991)" / "01 b.mp3", "b", "Band Two", "Beta", 1, "1991")
    make_mp3(base / "Gamma (1992)" / "01 c.mp3", "c", "Band Three", "Gamma", 1, "1992")
    (base / "artist.md").write_text(render_note(NoteFrontMatter(title="x", generator="mp3redactor 2026-10-01#01", note_format=1),
                                                "# x\n\nOld text."), encoding="utf-8", newline="\n")
    found = scan([tmp_path], kinds=(KIND_ARTIST,), include_outdated=True)
    [target] = [t for t in found.targets if t.folder.name == "New wave music"]
    assert target.update is not None  # the scan offers the old artist.md as an update
    [proposal] = build_proposals([target], Fetchers(wiki=genre_wiki(found=True)))
    assert proposal.note_kind == "about" and proposal.confidence == Confidence.CONFIDENT
    assert proposal.old_state is None and not proposal.is_update
    result = write_proposal(proposal)  # the folder's own artist.md still blocks a new about.md: said plainly, no crash
    assert result.status == "exists" and "no about.md to replace" not in (result.message or "")
    assert not (base / "about.md").exists()


# -- 8e. one link parser ------------------------------------------------------------------------------------------------------

MBID = "2bf704b3-4ffe-4c3e-9f62-0d44a3a8a6c3"


@pytest.mark.parametrize("text,kind,ident,lang", [
    (f"https://musicbrainz.org/artist/{MBID}", "mb-artist", MBID, ""),
    (f"musicbrainz.org/release-group/{MBID}/", "mb-release-group", MBID, ""),
    (MBID.upper(), "mb-artist", MBID, ""),
    ("https://en.wikipedia.org/wiki/Leningrad_(band)", "wikipedia", "Leningrad (band)", "en"),
    ("https://no.wikipedia.org/wiki/Ultravox#Historie", "wikipedia", "Ultravox", "no"),
    ("https://www.discogs.com/artist/55646-Cristina", "discogs-artist", "55646", ""),
    ("https://www.discogs.com/release/12345-Foo", "discogs-release", "12345", ""),
    ("https://www.wikidata.org/wiki/Q42", "wikidata", "Q42", ""),
])
def test_every_good_link_still_parses(text, kind, ident, lang):
    from core.notes_link import parse_link

    target = parse_link(text)
    assert (target.kind, target.id, target.lang) == (kind, ident, lang)


@pytest.mark.parametrize("text", [
    f"https://user:pw@musicbrainz.org/artist/{MBID}", f"https://musicbrainz.org:8080/artist/{MBID}",
    f"https://test.musicbrainz.org/artist/{MBID}", "https://www.wikipedia.org/wiki/Queen", "https://m.wikipedia.org/wiki/Queen",
    "https://évil.wikipedia.org/wiki/X", "https://xn--vil-8na.wikipedia.org/wiki/X", "https://wikipedia.org.evil.com/wiki/X",
    "https://musicbrainz.org/artist/zzzzzzzz-4ffe-4c3e-9f62-0d44a3a8a6c3", f"https://musicbrainz.org/release/{MBID}",
    "https://example.com/x",
])
def test_the_link_parser_refuses_what_the_shared_parser_refuses(text):
    """Review (8e): notes_link.parse_link was laxer than redactor_common's (user-info, odd ports, non-ASCII hosts, the language
    taken from the first label of ANY host); it now goes through the shared one."""
    from core.notes_link import LinkError, parse_link

    with pytest.raises(LinkError):
        parse_link(text)


def test_the_link_parser_keeps_the_apps_plain_messages():
    from core.notes_link import LinkError, parse_link

    with pytest.raises(LinkError, match="Only links from musicbrainz.org, discogs.com, wikipedia.org or wikidata.org are used"):
        parse_link("https://example.com/x")
    with pytest.raises(LinkError, match="not an artist or a release group page"):
        parse_link(f"https://musicbrainz.org/release/{MBID}")
    with pytest.raises(LinkError, match="Paste a Wikipedia, MusicBrainz or Discogs link first"):
        parse_link("  ")


# -- 4. the Norwegian number words are an option ---------------------------------------------------------------------------------

def test_the_norwegian_number_option_is_a_preference_off_by_default_and_a_cli_flag(tmp_path, capsys, cli):
    from core.notes_numbers import norwegian_numbers, set_norwegian_numbers
    from core.settings import Settings, load_settings, save_settings
    from gui.preferences import NOTES_NORWEGIAN_LABEL, preference_sections

    assert Settings().notes_norwegian_numbers is False
    assert NOTES_NORWEGIAN_LABEL in [spec.label for section in preference_sections() for spec in section.specs]
    s = Settings()
    s.notes_norwegian_numbers = True
    save_settings(s, tmp_path)
    assert load_settings(tmp_path).notes_norwegian_numbers is True
    ultravox_library(tmp_path / "lib", with_quartet=False)
    try:
        cli(capsys, "notes", str(tmp_path / "lib"), "--kind", "both", "--no-cache", "--norwegian-numbers")
        assert norwegian_numbers() is True
        cli(capsys, "notes", str(tmp_path / "lib"), "--kind", "both", "--no-cache")
        assert norwegian_numbers() is False  # every run sets it from its own option
    finally:
        set_norwegian_numbers(False)


# -- 6. the lookup cache (redactor_common 2026-10-16#08) ---------------------------------------------------------------------

def test_a_wikidata_error_answer_is_not_kept_by_the_cache_as_the_artists_wikipedia_link(tmp_path):
    """Review (6): Wikidata says maxlag / ratelimited as HTTP 200 + {"error": ...}; the cache stored it for 30 days and the artist
    had no Wikipedia introduction for a month. The next run must ask again (and get the article)."""
    from core.notes_cache import ResponseCache
    from tests.notes_helpers import RECORDED, wikipedia_routes

    ultravox_library(tmp_path / "lib", with_quartet=False)
    path = tmp_path / "cache.sqlite"
    busy = {"error": {"code": "maxlag", "info": "Waiting for a database server"}}
    first = ultravox_fetchers()
    first.wiki = FakeFetch([(r"wikidata\.org", busy), *wikipedia_routes()[1:]])
    cache = ResponseCache(path)
    [one] = build_proposals([t for t in scan([tmp_path / "lib"], kinds=(KIND_ARTIST,)).targets], first, cache=cache)
    assert "Ultravox were a British new wave band" not in one.note_text
    cache.close()
    second = ultravox_fetchers()
    cache2 = ResponseCache(path)
    [two] = build_proposals([t for t in scan([tmp_path / "lib"], kinds=(KIND_ARTIST,)).targets], second, cache=cache2)
    assert "Ultravox were a British new wave band" in two.note_text
    assert second.wiki.count(r"wikidata\.org") >= 1  # asked again: the error answer was not remembered
    cache2.close()
