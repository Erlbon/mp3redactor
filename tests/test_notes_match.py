"""Folder notes: the judgement (core/notes_match.py) -- pure functions, no network."""

from core.notes_match import (
    Confidence, Evaluation, album_key, decide_artist, discography_hits, era_conflict, name_similarity,
    score_release_group, titles_match, track_overlap, type_rank,
)


def test_album_key_drops_year_edition_and_the():
    assert album_key("Vienna (1980)") == "vienna"
    assert album_key("The Small Price of a Bicycle") == album_key("Small Price Of A Bicycle")
    assert album_key("Quartet (Deluxe Edition)") == "quartet"
    assert album_key("Vienna [Remastered]") == "vienna"
    assert album_key("Rage in Eden - Remastered 2008") == "rage in eden"
    assert album_key("Café Bleu!") == album_key("cafe bleu")


def test_titles_match_is_not_a_substring_match():
    assert titles_match("Vienna", "vienna (deluxe edition)")
    assert not titles_match("Vienna", "Vienna Calling")
    assert not titles_match("", "Vienna")


def test_name_similarity():
    assert name_similarity(["Ultravox"], ["Ultravox"]) == 1.0
    assert name_similarity(["The Icicle Works"], ["Icicle Works, The"]) == 1.0
    assert name_similarity(["Beyonce"], ["Beyoncé"]) == 1.0
    # an extension of the name is only half a match: "Cristina" is not "Cristina D'Avena"
    assert name_similarity(["Cristina"], ["Cristina D’Avena"]) == 0.5
    assert name_similarity(["Ultravox"], ["Queen"]) < 0.5


def test_discography_hits_counts_each_local_album_once():
    local = [["Vienna", "Vienna (tag)"], ["Quartet"], ["Unknown Album"]]
    assert discography_hits(local, ["Vienna", "Vienna (Deluxe Edition)", "Quartet", "Lament"]) == ["Vienna", "Quartet"]


def test_cristina_namesake_is_not_confident_without_discography_hits():
    davena = Evaluation("musicbrainz", "id-1", "Cristina D’Avena", hits=[], name_sim=0.5, search_score=100)
    winner, confidence, reasons = decide_artist([davena])
    assert confidence == Confidence.UNCERTAIN
    assert "none of the local albums" in reasons[0]


def test_artist_confident_when_a_local_album_is_in_the_discography_and_the_name_fits():
    real = Evaluation("discogs", "55646", "Cristina", hits=["Cristina", "Sleep It Off"], name_sim=1.0)
    davena = Evaluation("musicbrainz", "x", "Cristina D’Avena", hits=[], name_sim=0.5, search_score=100)
    winner, confidence, _ = decide_artist([davena, real])
    assert winner is real and confidence == Confidence.CONFIDENT


def test_artist_uncertain_when_two_candidates_fit_equally():
    a = Evaluation("musicbrainz", "a", "Wire Train", hits=["Chamber Music"], name_sim=1.0)
    b = Evaluation("musicbrainz", "b", "Wire Train", hits=["Chamber Music"], name_sim=1.0, detail="US")
    _, confidence, reasons = decide_artist([a, b])
    assert confidence == Confidence.UNCERTAIN and "equally well" in reasons[-1]


def test_artist_uncertain_on_a_loose_name_even_with_a_hit_and_none_without_candidates():
    loose = Evaluation("musicbrainz", "a", "Cristina D’Avena", hits=["Greatest Hits"], name_sim=0.5)
    assert decide_artist([loose])[1] == Confidence.UNCERTAIN
    assert decide_artist([])[1] == Confidence.NONE


def test_era_conflict_flags_an_album_older_than_the_artist():
    assert era_conflict("1990", "", ["1980"])
    assert not era_conflict("1976", "", ["1980", "1982"])
    assert not era_conflict("", "", ["1980"])
    assert not era_conflict("1976", "1986", ["1993"])  # reunions and reissues are never held against an artist


def test_album_beats_single_of_the_same_title():
    single = score_release_group(["Vienna"], "1980", ["Ultravox"], "Vienna", "1981", "Single", [], "Ultravox")
    album = score_release_group(["Vienna"], "1980", ["Ultravox"], "Vienna", "1980", "Album", [], "Ultravox")
    assert album.score > single.score
    assert album.title_match and single.title_match
    assert type_rank("Album") > type_rank("EP") > type_rank("Single")
    assert type_rank("Album", ["Compilation"]) < type_rank("Album")


def test_track_overlap():
    release = ["Astradyne", "New Europeans", "Private Lives", "Vienna"]
    assert track_overlap(["Astradyne", "Vienna"], release) == 1.0
    assert track_overlap(["astradyne (remastered)", "Nothing"], release) == 0.5
    assert track_overlap([], release) == 1.0
