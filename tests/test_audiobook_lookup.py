"""core/audiobook_lookup.py with canned answers (no network)."""

import json

import pytest

from core import audiobook_lookup as al

AUDIBLE = {
    "products": [
        {
            "asin": "B0LONG", "title": "Wizard's First Rule", "subtitle": "Sword of Truth, Book 1",
            "authors": [{"name": "Terry Goodkind"}], "narrators": [{"name": "Sam Tsoutsouvas"}],
            "publisher_name": "Brilliance Audio", "release_date": "2008-10-01", "language": "english",
            "series": [{"title": "Sword of Truth", "sequence": "1", "asin": "S1"}],
            "product_images": {"500": "https://img/500.jpg"},
            "runtime_length_min": 2046, "publisher_summary": "<p><b>A</b> wizard &amp; a girl.</p><p>Second<br/>line</p>",
        },
        {
            "asin": "B0ABR", "title": "Wizard's First Rule (Abridged)", "authors": [{"name": "Terry Goodkind"}],
            "narrators": [], "release_date": "2001-01-01", "language": "english", "series": None,
            "product_images": {}, "runtime_length_min": 360,
        },
        {"asin": "B0JUNK", "title": ""},
    ]
}
OPEN_LIBRARY = {
    "docs": [
        {"title": "Hackers", "author_name": ["Steven Levy"], "first_publish_year": 1984, "publisher": ["Dell"], "cover_i": 42},
        {"title": "Hackers II", "author_name": ["A", "B", "C", "D"]},
    ]
}


def canned(audible=None, library=None, fail=()):
    seen = []

    def fetch(url):
        seen.append(url)
        host = "audible" if "audible" in url else "openlibrary"
        if host in fail:
            raise al.AudiobookLookupError(f"{host} is down")
        return json.dumps(audible if host == "audible" else library).encode()

    fetch.seen = seen
    return fetch


def test_audible_results_carry_narrator_series_and_a_plain_text_description():
    matches = al.search_audible("Wizard's First Rule", "Terry Goodkind", fetch=canned(AUDIBLE))
    first = matches[0]
    assert [m.title for m in matches] == ["Wizard's First Rule", "Wizard's First Rule (Abridged)"]  # blank title dropped
    assert first.narrator_text == "Sam Tsoutsouvas" and first.series == "Sword of Truth" and first.series_index == "1"
    assert first.year == "2008" and first.language == "eng" and first.publisher == "Brilliance Audio"
    assert first.cover_url == "https://img/500.jpg" and first.runtime_minutes == 2046 and first.asin == "B0LONG"
    assert first.description == "A wizard & a girl.\n\nSecond\nline"
    assert matches[1].series == "" and matches[1].cover_url == ""


def test_the_audible_request_names_the_title_author_and_response_groups():
    fetch = canned(AUDIBLE)
    al.search_audible("Wizard's First Rule", "Terry Goodkind", fetch=fetch, region="co.uk")
    url = fetch.seen[0]
    assert url.startswith("https://api.audible.co.uk/1.0/catalog/products?")
    assert "title=Wizard%27s+First+Rule" in url and "author=Terry+Goodkind" in url and "series" in url
    al.search_audible("X", fetch=fetch, region="evil.example")  # an unknown region falls back to .com
    assert fetch.seen[1].startswith("https://api.audible.com/")


def test_the_edition_whose_length_matches_the_files_ranks_first():
    outcome = al.search("Wizard's First Rule", "Terry Goodkind", runtime_minutes=2040, fetch=canned(AUDIBLE, OPEN_LIBRARY))
    assert outcome.matches[0].asin == "B0LONG"
    outcome = al.search("Wizard's First Rule", "Terry Goodkind", runtime_minutes=355, fetch=canned(AUDIBLE, OPEN_LIBRARY))
    assert outcome.matches[0].asin == "B0ABR"  # the abridged one is the closer length


def test_open_library_is_asked_too_only_when_audible_finds_little():
    many = {"products": [{"asin": f"A{i}", "title": f"Book {i}"} for i in range(5)]}
    fetch = canned(many, OPEN_LIBRARY)
    al.search("Book", fetch=fetch)
    assert all("audible" in url for url in fetch.seen)
    fetch = canned({"products": []}, OPEN_LIBRARY)
    outcome = al.search("Hackers", "Steven Levy", fetch=fetch)
    assert [m.source for m in outcome.matches] == ["Open Library", "Open Library"]
    assert outcome.matches[0].cover_url == "https://covers.openlibrary.org/b/id/42-L.jpg"
    assert outcome.matches[1].authors == ["A", "B", "C"]  # capped at three


def test_one_source_down_is_a_note_both_down_is_an_error():
    outcome = al.search("Hackers", fetch=canned({"products": []}, OPEN_LIBRARY, fail=("audible",)))
    assert outcome.matches and outcome.notes == ["Audible: audible is down"]
    with pytest.raises(al.AudiobookLookupError, match="Audible.*Open Library"):
        al.search("Hackers", fetch=canned(fail=("audible", "openlibrary")))
    with pytest.raises(al.AudiobookLookupError, match="title"):
        al.search("  ", fetch=canned())


def test_language_names_become_codes():
    assert al.language_code("English") == "eng" and al.language_code("german") == "deu"
    assert al.language_code("nor") == "nor" and al.language_code("klingon") == "" and al.language_code("") == ""


def test_covers_must_be_real_images():
    jpeg = b"\xff\xd8\xff\xe0data"
    assert al.download_cover("https://img/a.jpg", fetch=lambda url: jpeg) == (jpeg, "image/jpeg")
    assert al.download_cover("https://img/a.png", fetch=lambda url: b"\x89PNG\r\n")[1] == "image/png"
    with pytest.raises(al.AudiobookLookupError):
        al.download_cover("https://img/a", fetch=lambda url: b"<html>not an image</html>")
    with pytest.raises(al.AudiobookLookupError):
        al.download_cover("", fetch=lambda url: b"")


# --- second review: a service's answer never has to have the documented shape -----------------------------------


ODD_AUDIBLE = {
    "products": [
        {
            "title": "Odd Book", "authors": None, "narrators": [None, {"name": None}, {"name": "N. Reader"}, "x"],
            "series": {"title": "Not A List"}, "product_images": ["a list, not a dict"], "runtime_length_min": "2046",
            "publisher_summary": None, "language": 42, "release_date": ["2020"],
        },
        "not even a product",
        {"title": "Second", "series": [None], "product_images": {"500": None, "1000": "https://img/1000.jpg"},
         "runtime_length_min": {"x": 1}},
    ]
}


def test_odd_audible_shapes_give_matches_not_a_crash():
    found = al.search_audible("Odd Book", fetch=canned(audible=ODD_AUDIBLE))
    assert [m.title for m in found] == ["Odd Book", "Second"]
    odd, second = found
    assert odd.authors == [] and odd.narrators == ["N. Reader"] and odd.series == "" and odd.cover_url == ""
    assert odd.runtime_minutes == 2046 and odd.description == "" and odd.language == "" and odd.year == ""
    assert second.cover_url == "https://img/1000.jpg" and second.runtime_minutes is None


def test_odd_open_library_shapes_give_matches_not_a_crash():
    answer = {"docs": [{"title": "T", "author_name": "Just A String", "publisher": None, "cover_i": "42", "first_publish_year": None}, 7]}
    found = al.search_open_library("T", fetch=canned(library=answer))
    assert len(found) == 1 and found[0].authors == [] and found[0].publisher == "" and found[0].cover_url == ""
    assert al.search_open_library("T", fetch=canned(library={"docs": "nope"})) == []


def test_an_answer_cut_short_is_a_lookup_error_not_an_http_exception(monkeypatch):
    import http.client

    class Cut:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            raise http.client.IncompleteRead(b"par")

    monkeypatch.setattr(al.urllib.request, "urlopen", lambda *a, **k: Cut())
    with pytest.raises(al.AudiobookLookupError, match="could not reach"):
        al.default_fetch("https://api.audible.com/x")
