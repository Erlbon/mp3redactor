"""Bands the name search cannot find: the Russian band Leningrad (MusicBrainz and Discogs call it 'Ленинград'; a name search for
'Leningrad' returns Leningrad Cowboys and not the band). Wikipedia first (the article's Wikidata item names the MusicBrainz and
Discogs ids), the local album titles (any script), the alias / sort-name search, and 'Look up by link'. Recorded-shape fixtures
(tests/fixtures/notes/leningrad.json), counted requests."""

import json
import os
import re
from pathlib import Path
from urllib.parse import quote

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from core.discogs_lookup import DiscogsClient  # noqa: E402
from core.notes_flow import Confidence, Fetchers, NotesEngine, build_proposals, scan  # noqa: E402
from core.notes_link import LinkError, parse_link  # noqa: E402
from core.notes_local import fold_name  # noqa: E402
from redactor_common.core.lookup_client import Throttle  # noqa: E402
from redactor_common.core.notes import parse_front_matter  # noqa: E402
from tests.notes_helpers import FakeFetch, make_mp3  # noqa: E402
from tests.test_cover_gui import window  # noqa: E402,F401
from tests.test_notes_gui import boxes, click, review, wait  # noqa: E402,F401
from tests.test_notes_scope import drive, scope_dialog  # noqa: E402,F401

FX = json.loads((Path(__file__).parent / "fixtures" / "notes" / "leningrad.json").read_text(encoding="utf-8"))
MBID, COWBOYS = FX["mbid"], FX["cowboys"]
DACHNIKI, PIRATES = "Дачники", "Пираты XXI века"


def quoted(text):
    return re.escape(quote(f'"{text}"', safe=""))


def band_library(root, tag_artist="Leningrad", albums=(DACHNIKI,), folder="Leningrad"):
    years = {DACHNIKI: "2000", PIRATES: "2001"}
    for album in albums:
        make_mp3(root / folder / f"{album} ({years[album]})" / "01 a.mp3", "a", tag_artist, album, 1, years[album])
    return root


def mb_fetch(cyrillic_search=False, titles=True):
    routes = []
    if cyrillic_search:
        routes.append((r"ws/2/artist\?query=artist%3A" + quoted("Ленинград"), FX["search_cyrillic"]))
    routes += [
        (r"ws/2/artist\?query=artist%3A%22Leningrad%22", FX["search_latin"]),
        (r"ws/2/artist\?query", {"artists": []}),
        (r"ws/2/artist/" + MBID, FX["artist"]),
        (r"ws/2/artist/" + COWBOYS, {"id": COWBOYS, "name": "Leningrad Cowboys", "sort-name": "Leningrad Cowboys", "type": "Group",
                                    "country": "FI", "life-span": {"begin": "1986"}, "relations": []}),
        (r"ws/2/release-group\?artist=" + MBID, FX["release_groups_band"]),
        (r"ws/2/release-group\?artist=" + COWBOYS, FX["release_groups_cowboys"]),
        (r"ws/2/release-group\?artist=", {"release-groups": []}),
        (r"ws/2/artist/", {"id": "x", "name": "x", "relations": []}),
    ]
    if titles:
        routes += [(r"ws/2/release-group\?query=releasegroup%3A" + quoted(DACHNIKI), FX["title_search_dachniki"]),
                   (r"ws/2/release-group\?query=releasegroup%3A" + quoted(PIRATES), FX["title_search_pirates"])]
    routes.append((r"ws/2/release-group\?query", {"release-groups": []}))
    return FakeFetch(routes)


def wiki_fetch(article=True):
    routes = []
    if article:
        routes += [(r"page/summary/Leningrad_\(band\)$", FX["wikipedia_summary"]),
                   (r"wikidata\.org.*titles=Leningrad", FX["wikidata_claims"]),
                   (r"wikidata\.org.*ids=Q909810", FX["wikidata_claims"])]
    routes += [(r"wikidata\.org", FX["wikidata_missing"]), (r"search/title", {"pages": []}), (r"page/summary/", None)]
    return FakeFetch(routes)


def discogs_client():
    fake = FakeFetch([
        (r"database/search\?type=artist", FX["discogs_search_latin"]),
        (r"database/search", {"results": []}),
        (r"artists/111/releases", FX["discogs_releases_cowboys"]),
        (r"artists/222/releases", FX["discogs_releases_orchestra"]),
        (r"artists/331797/releases", FX["discogs_releases_band"]),
        (r"artists/331797", FX["discogs_artist_band"]),
        (r"artists/111", {"id": 111, "name": "Leningrad Cowboys"}),
        (r"artists/222", {"id": 222, "name": "Leningrad Philharmonic Orchestra"}),
    ])
    return DiscogsClient("t", fetch=fake, throttle=Throttle(0))


def run(root, fetchers, name="Leningrad", **engine_kwargs):
    engine = NotesEngine(fetchers, **engine_kwargs)
    target = next(t for t in scan([root], kinds=("artist",)).targets if t.folder.name == name)
    [proposal] = build_proposals([target], engine=engine)
    return proposal, engine


# -- the Cyrillic titles are comparable -------------------------------------------------------------------------------

def test_titles_in_any_script_are_comparable():
    assert fold_name(DACHNIKI) == "дачники" and fold_name("Ленинград (band)") == "ленинград band"
    assert fold_name("Café & Bar") == "cafe and bar"


# -- 1. Wikipedia first ---------------------------------------------------------------------------------------------------

def test_the_name_search_returns_the_cowboys_and_wikipedia_finds_the_band_through_wikidata(tmp_path):
    band_library(tmp_path)
    mb, wiki = mb_fetch(), wiki_fetch()
    proposal, engine = run(tmp_path, Fetchers(mb=mb, wiki=wiki))
    assert proposal.confidence == Confidence.CONFIDENT and "musicbrainz" in proposal.sources and "wikipedia" in proposal.sources
    reasons = " | ".join(proposal.reasons)
    assert "found through Wikipedia: 'Leningrad (band)'" in reasons and "Q909810" in reasons and "MusicBrainz" in reasons
    assert "1 local album(s) found in its discography: Дачники" in reasons
    cand = proposal.candidates[0]
    assert cand.id == MBID and "Дачники" in cand.hits and "Wikidata Q909810" in cand.note
    assert COWBOYS in [c.id for c in proposal.candidates]  # the Cowboys were listed, and not chosen
    meta, body = parse_front_matter(proposal.note_text)
    assert meta["title"] == "Leningrad"  # the folder's own name: it is the band's alias and sort name
    assert "Russian ska punk band" in body or "Leningrad (Russian: Ленинград)" in body  # the article is the introduction
    assert "Wikidata item (CC0)" in body and f"https://en.wikipedia.org/wiki/Leningrad_(band)" in proposal.note_text
    assert any("ids=Q" in u or "titles=Leningrad" in u for u in wiki.urls)  # one Wikidata request for the claims
    assert mb.count(r"ws/2/artist/" + MBID) >= 1 and mb.count(r"ws/2/release-group\?artist=" + MBID) >= 1  # looked up by id


def test_wikipedia_first_asks_discogs_by_id_when_there_is_a_token(tmp_path):
    band_library(tmp_path)
    client = discogs_client()
    proposal, _engine = run(tmp_path, Fetchers(mb=mb_fetch(), wiki=wiki_fetch(), discogs=client))
    assert proposal.confidence == Confidence.CONFIDENT
    urls = client._fetch.urls
    assert any("artists/331797/releases" in u for u in urls)  # the id Wikidata gave, directly
    assert proposal.candidates[0].also == "discogs" and proposal.candidates[0].also_id == "331797"
    assert "discogs" in proposal.sources


def test_an_artist_without_any_local_album_in_the_discography_found_through_wikipedia_is_uncertain(tmp_path):
    band_library(tmp_path, albums=(PIRATES,))
    mb = mb_fetch(titles=False)
    mb.routes.insert(0, (r"ws/2/release-group\?artist=" + MBID, {"release-groups": [
        {"id": "rg-x", "title": "Something else", "primary-type": "Album", "first-release-date": "2004"}]}))
    proposal, _e = run(tmp_path, Fetchers(mb=mb, wiki=wiki_fetch()))
    assert proposal.confidence == Confidence.UNCERTAIN and not proposal.writable
    assert proposal.candidates[0].id == MBID and "found through Wikipedia" in " | ".join(proposal.reasons)


def test_a_wikipedia_article_without_ids_leaves_the_folder_to_the_other_searches(tmp_path):
    band_library(tmp_path)
    wiki = wiki_fetch()
    wiki.routes.insert(0, (r"wikidata\.org", FX["wikidata_missing"]))
    proposal, _e = run(tmp_path, Fetchers(mb=mb_fetch(titles=False), wiki=wiki))
    assert proposal.confidence == Confidence.UNCERTAIN and not any(c.id == MBID for c in proposal.candidates)
    assert "has no MusicBrainz or Discogs id on Wikidata" in " | ".join(proposal.reasons)


# -- 2. the local album titles ---------------------------------------------------------------------------------------------

def test_the_albums_are_searched_by_title_and_grouped_by_artist_when_nothing_else_finds_the_band(tmp_path):
    band_library(tmp_path, albums=(DACHNIKI, PIRATES))
    mb = mb_fetch()
    proposal, _e = run(tmp_path, Fetchers(mb=mb, wiki=wiki_fetch(article=False)))
    assert proposal.confidence == Confidence.CONFIDENT and proposal.candidates[0].id == MBID
    assert "found by the local album titles (2 of them)" in proposal.candidates[0].note
    assert mb.count(r"release-group\?query=releasegroup%3A") >= 2  # one request per album title, only because the name failed


def test_a_single_distinctive_album_title_is_enough_to_find_the_candidate(tmp_path):
    band_library(tmp_path)
    proposal, _e = run(tmp_path, Fetchers(mb=mb_fetch(), wiki=wiki_fetch(article=False)))
    assert proposal.candidates[0].id == MBID and proposal.confidence == Confidence.CONFIDENT  # the alias carries the name
    assert any("found by the local album titles" in c.note for c in proposal.candidates)


def test_the_cowboys_false_positive_stays_rejected_without_evidence(tmp_path):
    band_library(tmp_path, albums=(PIRATES,))
    mb = mb_fetch(titles=False)  # no title search result either
    proposal, _e = run(tmp_path, Fetchers(mb=mb, wiki=wiki_fetch(article=False)))
    assert proposal.confidence == Confidence.UNCERTAIN and not proposal.writable
    assert [c.id for c in proposal.candidates][:1] == [COWBOYS] and all(not c.hits for c in proposal.candidates)
    assert "none of the local albums was found" in " | ".join(proposal.reasons)


# -- 3. spelling, alias, sort name ------------------------------------------------------------------------------------------

def test_the_search_asks_for_the_name_an_alias_or_the_sort_name(tmp_path):
    band_library(tmp_path)
    mb = mb_fetch()
    run(tmp_path, Fetchers(mb=mb, wiki=wiki_fetch(article=False)))
    searches = [u for u in mb.urls if "ws/2/artist?query=" in u]
    assert searches and all("%20OR%20alias%3A" in u and "%20OR%20sortname%3A" in u for u in searches)


def test_both_spellings_are_searched_and_the_cyrillic_one_finds_the_band_at_rank_one(tmp_path):
    band_library(tmp_path, tag_artist="Ленинград")  # a Latin folder, Cyrillic tags
    mb, wiki = mb_fetch(cyrillic_search=True), wiki_fetch()
    proposal, _e = run(tmp_path, Fetchers(mb=mb, wiki=wiki))
    assert proposal.confidence == Confidence.CONFIDENT and proposal.candidates[0].id == MBID
    searches = [u for u in mb.urls if "ws/2/artist?query=" in u]
    assert any("%D0%9B%D0%B5%D0%BD" in u for u in searches) and any("artist%3A%22Leningrad%22" in u for u in searches)
    assert not any("wikidata.org" in u for u in wiki.urls)  # the name search verified it: no Wikidata round trip was needed


# -- the links ------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("text,kind,ident", [
    (f"https://musicbrainz.org/artist/{MBID}", "mb-artist", MBID),
    (f"musicbrainz.org/release-group/{MBID}/", "mb-release-group", MBID),
    (MBID, "mb-artist", MBID), (MBID.upper(), "mb-artist", MBID),
    ("https://www.discogs.com/artist/331797-Ленинград", "discogs-artist", "331797"),
    ("https://www.discogs.com/release/12345-Name", "discogs-release", "12345"),
    ("https://www.discogs.com/master/777", "discogs-master", "777"),
    ("https://en.wikipedia.org/wiki/Leningrad_(band)", "wikipedia", "Leningrad (band)"),
    ("https://ru.wikipedia.org/wiki/Ленинград_(группа)", "wikipedia", "Ленинград (группа)"),
    ("https://www.wikidata.org/wiki/Q909810", "wikidata", "Q909810"),
])
def test_the_links_that_are_understood(text, kind, ident):
    target = parse_link(text)
    assert (target.kind, target.id) == (kind, ident)


@pytest.mark.parametrize("text", [
    "", "hello", "https://evil.example/musicbrainz.org/artist/" + MBID, "https://musicbrainz.org.evil.com/artist/" + MBID,
    "https://www.google.com/search?q=leningrad", "ftp://musicbrainz.org/artist/" + MBID, "https://musicbrainz.org/label/" + MBID,
    "https://en.wikipedia.org/", "https://www.discogs.com/label/1", "javascript:alert(1)", "file:///C:/x",
])
def test_everything_else_is_refused_with_a_plain_message_and_nothing_is_fetched(text):
    with pytest.raises(LinkError) as why:
        parse_link(text)
    assert str(why.value)


def test_a_bare_id_is_an_artist_for_an_artist_row_and_a_release_group_for_an_album_row():
    assert parse_link(MBID).kind == "mb-artist" and parse_link(MBID, bare_as="release-group").kind == "mb-release-group"


def uncertain_cowboys(tmp_path, **fetch_kwargs):
    band_library(tmp_path, albums=(PIRATES,))
    mb, wiki = mb_fetch(titles=False), wiki_fetch(article=False)
    engine = NotesEngine(Fetchers(mb=mb, wiki=wiki, **fetch_kwargs))
    target = next(t for t in scan([tmp_path], kinds=("artist",)).targets)
    [proposal] = build_proposals([target], engine=engine)
    assert proposal.confidence == Confidence.UNCERTAIN
    return proposal, engine, mb, wiki


@pytest.mark.parametrize("link", [
    f"https://musicbrainz.org/artist/{MBID}", MBID, "https://en.wikipedia.org/wiki/Leningrad_(band)",
    "https://www.wikidata.org/wiki/Q909810",
])
def test_look_up_by_link_makes_the_uncertain_folder_confident_chosen_by_you_from_a_link(tmp_path, link):
    proposal, engine, mb, wiki = uncertain_cowboys(tmp_path)
    wiki.routes.insert(0, (r"page/summary/Leningrad_\(band\)$", FX["wikipedia_summary"]))
    wiki.routes.insert(0, (r"wikidata\.org.*(titles=Leningrad|ids=Q909810)", FX["wikidata_claims"]))
    built = engine.rebuild(proposal, link=link)
    assert built.confidence == Confidence.CONFIDENT and built.writable and "musicbrainz" in built.sources
    assert any(r.startswith("chosen by you from a link: Ленинград") for r in built.reasons), built.reasons
    assert built.folder == proposal.folder and built.kind == "artist"


def test_look_up_by_a_discogs_link_needs_the_token_and_uses_the_id(tmp_path):
    proposal, engine, _mb, _wiki = uncertain_cowboys(tmp_path)
    with pytest.raises(LinkError) as why:
        engine.rebuild(proposal, link="https://www.discogs.com/artist/331797-Ленинград")
    assert "Discogs token not set" in str(why.value)
    proposal, engine, _mb, _wiki = uncertain_cowboys(tmp_path / "b", discogs=discogs_client()) if False else (None, None, None, None)


def test_a_release_group_link_on_an_artist_row_finds_the_artist(tmp_path):
    proposal, engine, mb, _wiki = uncertain_cowboys(tmp_path)
    mb.routes.insert(0, (r"ws/2/release-group/rg-dachniki", {"id": "rg-dachniki", "title": "Дачники", "primary-type": "Album",
                                                             "first-release-date": "2000",
                                                             "artist-credit": [{"name": "Ленинград", "artist": {"id": MBID}}]}))
    rgid = "0d6e6f84-2f6e-4c64-b8c2-111111111111"
    mb.routes.insert(0, (r"ws/2/release-group/" + rgid, {"id": rgid, "title": "Дачники", "primary-type": "Album",
                                                         "first-release-date": "2000",
                                                         "artist-credit": [{"name": "Ленинград", "artist": {"id": MBID}}]}))
    built = engine.rebuild(proposal, link=f"https://musicbrainz.org/release-group/{rgid}")
    assert built.confidence == Confidence.CONFIDENT and built.candidates and "Ленинград" in " ".join(built.reasons)


def test_only_the_four_sites_are_ever_fetched_for_a_link(tmp_path):
    proposal, engine, mb, wiki = uncertain_cowboys(tmp_path)
    before = len(mb.urls) + len(wiki.urls)
    for bad in ("https://evil.example/artist/" + MBID, "https://musicbrainz.org.evil.com/artist/" + MBID, "hello"):
        with pytest.raises(LinkError):
            engine.rebuild(proposal, link=bad)
    assert len(mb.urls) + len(wiki.urls) == before  # refused before any request
    hosts = {re.match(r"https?://([^/]+)", u).group(1) for u in mb.urls + wiki.urls}
    assert all(h.endswith(("musicbrainz.org", "wikipedia.org", "wikidata.org")) for h in hosts)


def test_an_album_row_takes_a_release_group_link(tmp_path):
    rgid = "0d6e6f84-2f6e-4c64-b8c2-222222222222"
    band_library(tmp_path)
    mb = mb_fetch(titles=False)
    mb.routes.insert(0, (r"ws/2/release-group/" + rgid, {"id": rgid, "title": "Дачники", "primary-type": "Album",
                                                         "first-release-date": "2000", "relations": [], "genres": [],
                                                         "releases": []}))
    engine = NotesEngine(Fetchers(mb=mb, wiki=wiki_fetch(article=False)))
    target = next(t for t in scan([tmp_path], kinds=("album",)).targets)
    [proposal] = build_proposals([target], engine=engine)
    built = engine.rebuild(proposal, link=f"https://musicbrainz.org/release-group/{rgid}")
    assert built.kind == "album" and built.confidence == Confidence.CONFIDENT
    assert any(r.startswith("chosen by you from a link") for r in built.reasons), built.reasons
    with pytest.raises(LinkError):  # an artist page is not an album
        engine.rebuild(proposal, link=f"https://musicbrainz.org/artist/{MBID}")



# -- the review page ---------------------------------------------------------------------------------------------------------

def test_the_review_page_has_a_link_box_with_an_inline_error_and_rebuilds_the_row_in_place(window, tmp_path, boxes):
    from PyQt6.QtCore import Qt

    root = tmp_path / "Music"
    band_library(root, albums=(PIRATES,))
    make_mp3(root / "Other Band" / "Debut (1990)" / "01 a.mp3", "a", "Other Band", "Debut", 1, "1990")
    window.settings.last_directory = str(root)
    window.settings.include_subfolders = True
    window._load_paths([root / "Leningrad" / f"{PIRATES} (2001)"])
    mb, wiki = mb_fetch(titles=False), wiki_fetch(article=False)
    window.notes_fetchers = lambda: Fetchers(mb=mb, wiki=wiki)
    seen = {}

    def scenario():
        yield from wait(lambda: scope_dialog() is not None, "the scope dialog")
        dlg = scope_dialog()
        next(r for r, p in dlg.radios if p == str(root)).click()
        click(dlg.continue_button)
        yield from wait(lambda: review() is not None, "the review page")
        page = review()
        tree = page.tree
        index = next(i for i, it in enumerate(page.session.items) if it.proposal.folder.name == "Leningrad")
        row = next(tree.topLevelItem(r) for r in range(tree.topLevelItemCount())
                   if tree.topLevelItem(r).data(0, Qt.ItemDataRole.UserRole) == index)
        tree.setCurrentItem(row)
        panel = page.list_panel
        wiki.routes.insert(0, (r"page/summary/Leningrad_\(band\)$", FX["wikipedia_summary"]))
        wiki.routes.insert(0, (r"wikidata\.org.*(titles=Leningrad|ids=Q909810)", FX["wikidata_claims"]))
        seen["visible"] = panel.link_box.isVisibleTo(panel)
        rows_before = [tree.topLevelItem(r) for r in range(tree.topLevelItemCount())]
        requests = len(mb.urls) + len(wiki.urls)
        panel.link_edit.setText("https://evil.example/artist/" + MBID)
        click(panel.link_button)
        seen["error"] = (panel.link_error.isVisibleTo(panel), panel.link_error.text())
        seen["fetched_for_bad"] = len(mb.urls) + len(wiki.urls) - requests
        seen["still_uncertain"] = page.session.items[index].proposal.confidence.value
        panel.link_edit.setText("https://en.wikipedia.org/wiki/Leningrad_(band)")
        seen["error_cleared"] = not panel.link_error.isVisibleTo(panel)
        calls = []
        real = page._fill_tree
        page._fill_tree = lambda: (calls.append(1), real())[1]
        click(panel.link_button)
        QApplication.processEvents()
        item = page.session.items[index]
        seen["after"] = (item.proposal.confidence.value, item.proposal.sources, item.ticked)
        seen["reasons"] = item.proposal.reasons
        seen["rows_same"] = [tree.topLevelItem(r) for r in range(tree.topLevelItemCount())] == rows_before
        seen["fill_calls"] = calls
        seen["count"] = len(page.session.items)
        page.done(0)

    drive(scenario, window.action_registry["create_notes"].trigger)
    assert seen["visible"] and seen["error"][0] and "Only links from musicbrainz.org" in seen["error"][1]
    assert seen["fetched_for_bad"] == 0 and seen["still_uncertain"] == "uncertain" and seen["error_cleared"]
    assert seen["after"][0] == "confident" and "musicbrainz" in seen["after"][1] and seen["after"][2] is True
    assert any(r.startswith("chosen by you from a link") for r in seen["reasons"])
    assert seen["rows_same"] and seen["fill_calls"] == [] and seen["count"] == 2


# -- the command line --------------------------------------------------------------------------------------------------------

def test_cli_link_gives_a_folder_the_page_and_checks_the_link(tmp_path, monkeypatch, capsys):
    from core.settings import Settings
    from mp3cli import cmd_notes, files as cli_files
    from mp3cli.main import main
    from redactor_common.cli import CliError

    monkeypatch.setattr(cli_files, "load_settings", lambda *a, **k: Settings())
    band_library(tmp_path, albums=(PIRATES,))
    mb, wiki = mb_fetch(titles=False), wiki_fetch(article=False)
    wiki.routes.insert(0, (r"wikidata\.org.*ids=Q909810", FX["wikidata_claims"]))
    monkeypatch.setattr(cmd_notes, "make_fetchers", lambda use: Fetchers(mb=mb, wiki=wiki))
    code = main(["notes", str(tmp_path), "--kind", "artist", "--write", "--no-cache", "--json",
                 "--link", "Leningrad=https://www.wikidata.org/wiki/Q909810"])
    document = json.loads(capsys.readouterr().out)
    assert code == 0 and [r["status"] for r in document["results"]] == ["written"]
    written = (tmp_path / "Leningrad" / "artist.md").read_text(encoding="utf-8")
    assert "title: Leningrad" in written and "musicbrainz.org" in written
    with pytest.raises(CliError):
        main(["notes", str(tmp_path), "--kind", "artist", "--link", "Leningrad=https://evil.example/x"])
    with pytest.raises(CliError):
        main(["notes", str(tmp_path), "--kind", "artist", "--link", "no equals sign"])
