"""Folder notes: the LEVELS (genre / collection / artist / album) and the artist test (core/notes_level.py), on real
small trees with tagged MP3 files. Network is faked; an unexpected URL fails the test."""

from core.notes_flow import STATUS_SKIPPED, Confidence, Fetchers, build_proposals, scan, write_proposal
from core.notes_level import (
    LEVEL_ARTIST, LEVEL_COLLECTION, LEVEL_UNCLEAR, artist_key, is_soundtrack_like, names_agree, split_collab, work_title,
)
from core.notes_local import ALL_KINDS, KIND_ALBUM, KIND_ARTIST, KIND_GENRE
from redactor_common.core.notes import parse_front_matter
from tests.notes_helpers import (
    RECORDED, FakeFetch, make_mp3, musicbrainz_routes, ultravox_fetchers, ultravox_library, wikipedia_routes,
)

NEW_WAVE_PAGE = {
    "type": "standard", "title": "New wave music", "description": "Genre of popular music",
    "extract": "New wave is a music genre that encompasses numerous pop-oriented styles from the late 1970s and the 1980s.",
    "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/New_wave_music"}},
}


def genre_wiki(found=True):
    routes = []
    if found:
        routes += [
            (r"(?i)search/title\?q=New\+wave\+music", {"pages": [{"title": "New wave music", "description": "Genre of popular music", "excerpt": "New wave"}]}),
            (r"page/summary/New_wave_music", NEW_WAVE_PAGE),
        ]
    return FakeFetch(routes + [(r"search/title", {"pages": []}), (r"page/summary/", None)])


def level_targets(root, **kw):
    return {(t.kind, t.folder.name): t for t in scan([root], kinds=ALL_KINDS, **kw).targets}


def proposal_for(root, key, fetchers):
    [p] = build_proposals([level_targets(root)[key]], fetchers)
    return p


# -- the pure parts ----------------------------------------------------------------------------------------------

def test_name_keys_and_collaborations():
    assert artist_key("The Icicle Works") == artist_key("Icicle Works")
    assert artist_key("Simon & Garfunkel") == artist_key("Simon and Garfunkel")
    assert artist_key("Ultravox feat. Someone") == artist_key("Ultravox")
    assert split_collab("A & B") == ["A", "B"] and split_collab("A feat. B") == ["A", "B"] and split_collab("Solo") == ["Solo"]
    assert names_agree("Ultravox & Friends", "Ultravox") and not names_agree("Ultravox", "Queen")


def test_soundtrack_detection_and_work_title():
    assert is_soundtrack_like("Blade Runner (Original Motion Picture Soundtrack)", [])
    assert is_soundtrack_like("Doom", ["Soundtrack"]) and is_soundtrack_like("Pulp Fiction", [], "Soundtracks")
    assert not is_soundtrack_like("Vienna", ["New Wave"])
    assert work_title("Blade Runner (Original Motion Picture Soundtrack)") == "Blade Runner"
    assert work_title("Doom OST") == "Doom" and work_title("Inception") == "Inception"


# -- Genre / Artist / Album ----------------------------------------------------------------------------------------

def test_three_level_tree_has_genre_artist_and_album_targets_and_no_note_for_the_library_root(tmp_path):
    ultravox_library(tmp_path / "Music" / "New Wave")
    found = level_targets(tmp_path / "Music")
    assert sorted(found) == [("album", "Quartet (1982)"), ("album", "Vienna (1980)"), ("artist", "Ultravox"), ("genre", "New Wave")]
    assert found[("genre", "New Wave")].note_file_name == "about.md"
    assert ("genre", "Music") not in found  # the folder the scan started from is the library root


def test_two_level_tree_has_no_genre_level(tmp_path):
    ultravox_library(tmp_path / "Music")
    found = level_targets(tmp_path / "Music")
    assert sorted(found) == [("album", "Quartet (1982)"), ("album", "Vienna (1980)"), ("artist", "Ultravox")]


def test_genre_note_has_a_verified_wikipedia_intro_and_has_no_list_of_what_is_below(tmp_path):
    ultravox_library(tmp_path / "Music" / "New Wave")
    make_mp3(tmp_path / "Music" / "New Wave" / "Wire Train" / "In a Chamber (1987)" / "01 a.mp3", "a", "Wire Train", "In a Chamber")
    p = proposal_for(tmp_path / "Music", ("genre", "New Wave"), Fetchers(wiki=genre_wiki()))
    assert p.confidence == Confidence.CONFIDENT and p.level == "genre" and p.note_kind == "about"
    assert p.path.endswith("New Wave\\about.md") or p.path.endswith("New Wave/about.md")
    meta, body = parse_front_matter(p.note_text)
    assert meta["title"] == "New Wave" and meta["status"] == "draft"
    assert "https://en.wikipedia.org/wiki/New_wave_music" in meta["links"]
    assert "New wave is a music genre" in body
    assert "[[" not in body and "In this library" not in body and "Ultravox" not in body and "Wire Train" not in body
    assert "Introduction from Wikipedia ([New wave music]" in body
    assert write_proposal(p).ok and (tmp_path / "Music" / "New Wave" / "about.md").exists()
    assert not (tmp_path / "Music" / "New Wave" / "artist.md").exists()


def test_genre_without_a_verified_page_is_uncertain_and_never_written(tmp_path):
    make_mp3(tmp_path / "Music" / "Zorbland" / "Someone" / "Album (1990)" / "01 a.mp3", "a", "Someone", "Album")
    p = proposal_for(tmp_path / "Music", ("genre", "Zorbland"), Fetchers(wiki=genre_wiki(found=False)))
    assert p.confidence == Confidence.UNCERTAIN and not p.note_text
    assert "music genre called 'Zorbland'" in p.reasons[-1]
    assert write_proposal(p).status == STATUS_SKIPPED
    assert not (tmp_path / "Music" / "Zorbland" / "about.md").exists()


def test_genre_page_about_something_else_is_not_accepted(tmp_path):
    make_mp3(tmp_path / "Music" / "Jazz" / "Someone" / "Album (1990)" / "01 a.mp3", "a", "Someone", "Album")
    wiki = FakeFetch([
        (r"search/title\?q=Jazz\+music", {"pages": [{"title": "Jazz (disambiguation)", "description": "Wikimedia disambiguation page", "excerpt": ""},
                                                    {"title": "Utah Jazz", "description": "NBA basketball team in Salt Lake City", "excerpt": "jazz"}]}),
        (r"search/title", {"pages": []}),
        (r"page/summary/Utah_Jazz", {"type": "standard", "title": "Utah Jazz", "description": "NBA basketball team in Salt Lake City",
                                     "extract": "The Utah Jazz are an American professional basketball team."}),
        (r"page/summary/", None),
    ])
    p = proposal_for(tmp_path / "Music", ("genre", "Jazz"), Fetchers(wiki=wiki))
    assert p.confidence == Confidence.UNCERTAIN


def test_underscore_folder_is_a_grouping_at_the_about_level(tmp_path):
    make_mp3(tmp_path / "Lib" / "_Crossovers" / "Odd Album (2001)" / "01 a.mp3", "a", "Someone", "Odd Album")
    found = level_targets(tmp_path / "Lib")
    assert ("genre", "_Crossovers") in found and ("album", "Odd Album (2001)") in found
    assert found[("genre", "_Crossovers")].note_file_name == "about.md"


# -- collections: Soundtracks, Various Artists -------------------------------------------------------------------

def soundtracks(root):
    base = root / "Soundtracks"
    make_mp3(base / "Blade Runner (1982)" / "01 Main Titles.mp3", "Main Titles", "Vangelis", "Blade Runner", 1, "1982", genre="Soundtrack")
    make_mp3(base / "Pulp Fiction (1994)" / "01 Misirlou.mp3", "Misirlou", "Dick Dale", "Pulp Fiction", 1, "1994", albumartist="Various Artists")
    return base


def test_soundtracks_folder_is_a_collection_with_albums_and_no_artist_level(tmp_path):
    lib = tmp_path / "Music"
    soundtracks(lib)
    found = level_targets(lib)
    assert sorted(found) == [("album", "Blade Runner (1982)"), ("album", "Pulp Fiction (1994)"), ("genre", "Soundtracks")]
    assert ("artist", "Soundtracks") not in found
    p = proposal_for(lib, ("genre", "Soundtracks"), Fetchers(wiki=genre_wiki(found=False)))
    assert p.note_kind == "about" and p.path.endswith("about.md") and p.confidence == Confidence.UNCERTAIN


def test_a_folder_of_different_artists_is_found_to_be_a_collection_from_the_tags(tmp_path):
    base = tmp_path / "Stuff"
    make_mp3(base / "Alpha (1990)" / "01 a.mp3", "a", "Band One", "Alpha", 1, "1990")
    make_mp3(base / "Beta (1991)" / "01 b.mp3", "b", "Band Two", "Beta", 1, "1991")
    make_mp3(base / "Gamma (1992)" / "01 c.mp3", "c", "Band Three", "Gamma", 1, "1992")
    assert ("artist", "Stuff") in level_targets(tmp_path)  # the scan only knows the structure
    p = proposal_for(tmp_path, ("artist", "Stuff"), Fetchers(wiki=genre_wiki(found=False)))
    assert p.level == LEVEL_COLLECTION and p.note_kind == "about"
    assert p.path.endswith("about.md") and not p.path.endswith("artist.md")
    assert "different artists" in p.level_reasons[0]
    assert p.suggested_levels[0] == "genre-collection" and "artist" in p.suggested_levels and "skip" in p.suggested_levels
    assert p.confidence == Confidence.UNCERTAIN  # no Wikipedia page about a genre called 'Stuff'


def test_various_artists_album_gets_album_md_and_its_container_never_artist_md(tmp_path):
    base = tmp_path / "Various Artists"
    make_mp3(base / "Now 1 (1983)" / "01 a.mp3", "a", "X", "Now 1", 1, "1983", albumartist="Various Artists")
    found = level_targets(tmp_path)
    assert ("artist", "Various Artists") not in found and ("genre", "Various Artists") in found
    assert ("album", "Now 1 (1983)") in found
    # a collection name decides before any tag: even tagged as one artist it is not an artist folder
    make_mp3(tmp_path / "VA" / "Solo (2000)" / "01 a.mp3", "a", "VA", "Solo", 1, "2000")
    assert ("artist", "VA") not in level_targets(tmp_path)


# -- the artist test --------------------------------------------------------------------------------------------

def test_composer_folder_is_an_artist_folder(tmp_path):
    base = tmp_path / "Hans Zimmer"
    make_mp3(base / "Inception (2010)" / "01 Dream.mp3", "Dream", "Hans Zimmer", "Inception", 1, "2010", composer="Hans Zimmer", genre="Soundtrack")
    make_mp3(base / "Interstellar (2014)" / "01 Cornfield.mp3", "Cornfield", "Hans Zimmer", "Interstellar", 1, "2014", composer="Hans Zimmer")
    mb = FakeFetch([(r"ws/2/artist\?query", {"artists": []}), (r"ws/2/", {})])
    p = proposal_for(tmp_path, ("artist", "Hans Zimmer"), Fetchers(mb=mb, wiki=genre_wiki(found=False)))
    assert p.level == LEVEL_ARTIST and p.note_kind == "artist"
    assert "2 of 2 albums are tagged 'Hans Zimmer'" in p.level_reasons[0]


def test_classical_composer_or_performer_is_unclear_and_never_written(tmp_path):
    base = tmp_path / "Beethoven"
    make_mp3(base / "Symphony 5 (Karajan)" / "01 Allegro.mp3", "Allegro", "Berliner Philharmoniker", "Symphony 5", 1, "1963", composer="Ludwig van Beethoven", albumartist="Berliner Philharmoniker")
    make_mp3(base / "Symphony 9 (Bernstein)" / "01 Allegro.mp3", "Allegro", "Wiener Philharmoniker", "Symphony 9", 1, "1979", composer="Beethoven", albumartist="Wiener Philharmoniker")
    base2 = tmp_path / "Ludwig van Beethoven"
    make_mp3(base2 / "Symphony 5 (Karajan)" / "01 Allegro.mp3", "Allegro", "Berliner Philharmoniker", "Symphony 5", 1, "1963", composer="Ludwig van Beethoven", albumartist="Berliner Philharmoniker")
    make_mp3(base2 / "Symphony 9 (Bernstein)" / "01 Allegro.mp3", "Allegro", "Wiener Philharmoniker", "Symphony 9", 1, "1979", composer="Ludwig van Beethoven", albumartist="Wiener Philharmoniker")
    p = proposal_for(tmp_path, ("artist", "Ludwig van Beethoven"), Fetchers(mb=FakeFetch([]), wiki=FakeFetch([])))
    assert p.level == LEVEL_UNCLEAR and p.confidence == Confidence.UNCERTAIN and not p.note_text
    assert p.reasons[0].startswith("level unclear") and "composer" in p.reasons[0]
    assert p.suggested_levels == ["artist", "genre-collection", "skip"]
    assert write_proposal(p).status == STATUS_SKIPPED
    assert not (base2 / "artist.md").exists() and not (base2 / "about.md").exists()
    assert (base.exists())


def test_folder_named_after_something_else_is_unclear(tmp_path):
    base = tmp_path / "Compilation Box"
    make_mp3(base / "One (1980)" / "01 a.mp3", "a", "Queen", "One", 1, "1980")
    make_mp3(base / "Two (1981)" / "01 a.mp3", "a", "Queen", "Two", 1, "1981")
    p = proposal_for(tmp_path, ("artist", "Compilation Box"), Fetchers(mb=FakeFetch([]), wiki=FakeFetch([])))
    assert p.level == LEVEL_UNCLEAR and "all by 'Queen'" in p.level_reasons[0]
    assert p.to_dict()["suggested_levels"] and p.to_dict()["level"] == "unclear"


# -- loose tracks, collaborations -----------------------------------------------------------------------------

def test_loose_singles_in_an_artist_folder_are_listed_not_given_an_album_note(tmp_path):
    artist = ultravox_library(tmp_path)
    make_mp3(artist / "Dancing With Tears in My Eyes.mp3", "Dancing With Tears in My Eyes", "Ultravox", "", 0)
    found = level_targets(tmp_path)
    assert sorted(k for k in found if k[0] == "album") == [("album", "Quartet (1982)"), ("album", "Vienna (1980)")]
    [p] = build_proposals([found[("artist", "Ultravox")]], ultravox_fetchers())
    assert p.confidence == Confidence.CONFIDENT
    assert "Loose tracks" not in p.note_text and "[[" not in p.note_text  # the folder view is Pluteus' business


def test_collaboration_folder_found_only_by_its_first_artist_is_uncertain(tmp_path):
    base = tmp_path / "Ultravox & Friends"
    for album, year in (("Vienna", "1980"), ("Quartet", "1982")):
        make_mp3(base / f"{album} ({year})" / "01 a.mp3", "a", "Ultravox & Friends", album, 1, year)
    p = proposal_for(tmp_path, ("artist", "Ultravox & Friends"), ultravox_fetchers())
    assert p.level == LEVEL_ARTIST  # the folder name matches the tags
    assert p.confidence == Confidence.UNCERTAIN and not p.note_text
    assert p.candidates and p.candidates[0].name == "Ultravox" and p.candidates[0].hits
    assert any("loose match" in r for r in p.reasons)


def test_collaboration_folder_matching_in_full_stays_confident(tmp_path):
    base = tmp_path / "Ultravox"
    ultravox_library(tmp_path)
    p = proposal_for(tmp_path, ("artist", "Ultravox"), ultravox_fetchers())
    assert p.confidence == Confidence.CONFIDENT and base.exists()


def test_loose_tracks_of_one_artist_in_one_folder_are_unclear(tmp_path):
    folder = tmp_path / "Ultravox"
    for i, album in enumerate(["A", "B", "C"], start=1):
        make_mp3(folder / f"0{i} song.mp3", f"song {i}", "Ultravox", album, i)
    [target] = [t for t in scan([tmp_path]).targets if t.kind == KIND_ALBUM]
    [p] = build_proposals([target], Fetchers(mb=FakeFetch([]), wiki=FakeFetch([])))
    assert target.kind == KIND_ALBUM and p.level == LEVEL_ARTIST and p.confidence == Confidence.UNCERTAIN
    assert "loose tracks" in p.reasons[0]


# -- soundtrack album notes ------------------------------------------------------------------------------------

def test_soundtrack_album_note_uses_the_soundtrack_release_group_the_film_article_and_names_its_sources(tmp_path):
    base = tmp_path / "Soundtracks"
    folder = base / "Blade Runner (Original Motion Picture Soundtrack) (1994)"
    titles = ["Main Titles", "Blush Response", "Wait for Me"]
    for i, t in enumerate(titles, start=1):
        make_mp3(folder / f"0{i} {t}.mp3", t, "Vangelis", "Blade Runner (Original Motion Picture Soundtrack)", i, "1994", genre="Soundtrack")
    make_mp3(base / "Other (1990)" / "01 x.mp3", "x", "Someone", "Other", 1, "1990")
    rg = {"id": "rg-br", "title": "Blade Runner (Original Motion Picture Soundtrack)", "score": 100, "primary-type": "Album",
          "secondary-types": ["Soundtrack"], "first-release-date": "1994-10-11", "artist-credit": [{"name": "Vangelis", "joinphrase": ""}]}
    release = {"id": "rel-br", "title": rg["title"], "status": "Official", "date": "1994-10-11", "country": "GB",
               "artist-credit": [{"name": "Vangelis", "joinphrase": ""}], "label-info": [{"label": {"name": "Atlantic"}}],
               "relations": [{"type": "composer", "artist": {"name": "Vangelis"}}],
               "media": [{"position": 1, "tracks": [{"position": i, "title": t, "length": 200000, "recording": {"id": f"r{i}", "title": t}}
                                                    for i, t in enumerate(titles, start=1)]}]}
    mb = FakeFetch([
        (r"ws/2/artist\?query", {"artists": []}),
        (r"ws/2/release-group\?query=[^ ]*limit=25", {"release-groups": []}),  # (the search by album titles finds nothing)
        (r"ws/2/release-group\?query=.*secondarytype", {"release-groups": [rg]}),
        (r"ws/2/release-group/", {"id": "rg-br", "title": rg["title"], "first-release-date": "1994-10-11", "genres": [{"name": "synth-pop", "count": 2}], "relations": []}),
        (r"ws/2/release\?release-group=", {"releases": [{"id": "rel-br", "status": "Official", "date": "1994-10-11", "media": [{"track-count": 3}]}]}),
        (r"ws/2/release/", release),
    ])
    wiki = FakeFetch([
        (r"search/title\?q=Blade\+Runner\+soundtrack", {"pages": [{"title": "Blade Runner (soundtrack)", "description": "1994 soundtrack album by Vangelis", "excerpt": ""}]}),
        (r"page/summary/Blade_Runner_\(soundtrack\)", {"type": "standard", "title": "Blade Runner (soundtrack)", "description": "1994 soundtrack album by Vangelis",
                                                      "extract": "Blade Runner is the soundtrack album to the 1982 film of the same name, composed by Vangelis.",
                                                      "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Blade_Runner_(soundtrack)"}}}),
        (r"wikidata", {"entities": {}}), (r"search/title", {"pages": []}), (r"page/summary/", None),
    ])
    target = level_targets(tmp_path)[("album", folder.name)]
    [p] = build_proposals([target], Fetchers(mb=mb, wiki=wiki))
    assert p.confidence == Confidence.CONFIDENT, p.reasons
    assert mb.count(r"secondarytype%3Asoundtrack") == 1  # the search asked for the Soundtrack secondary type
    meta, body = parse_front_matter(p.note_text)
    assert "soundtrack" in meta["genres"] and meta["years"] == "1994"
    assert "soundtrack album to the 1982 film" in body and "- **Written by:** Vangelis" in body
    assert "Introduction from Wikipedia ([Blade Runner (soundtrack)]" in body and "MusicBrainz" in body.splitlines()[-1]
    assert "## In this folder" not in body  # the present/missing copy is off (owner, 2026-10-15)
