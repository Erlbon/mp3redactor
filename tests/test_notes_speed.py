"""Create Notes speed, round two: where the time goes (phases), Discogs asked as a fallback (or side by side with MusicBrainz),
a sample of the tags instead of all of them, tags read by a small pool, singles optional, and a busy MusicBrainz announced.
Counted calls, not stubs: the injected fetchers and loaders count what really reaches them."""

import json
import threading
import time
from pathlib import Path

import pytest

from core import notes_local
from core.notes_flow import Fetchers, NotesEngine, build_proposals, scan
from core.notes_local import KIND_ARTIST, read_artist
from core.notes_phases import PhaseTimer, mmss
from tests.notes_helpers import FakeFetch, make_mp3, ultravox_fetchers, ultravox_library, cristina_fetchers, cristina_library
from tests.test_notes_sources import artist_routes, discogs, no_mb, no_wiki


def artist_target(root, name="Ultravox"):
    return next(t for t in scan([root], kinds=(KIND_ARTIST,)).targets if t.folder.name == name)


def discogs_requests(fetchers) -> int:
    return len(fetchers.discogs._fetch.urls)


def with_discogs(base: Fetchers) -> Fetchers:
    base.discogs = discogs(*artist_routes())
    return base


# -- the phases ----------------------------------------------------------------------------------------------------

def test_the_phase_timer_formats_and_counts():
    timer = PhaseTimer()
    timer.add("scan", 12, count=656)
    timer.add("tags", 520, count=2000)
    timer.add("musicbrainz", 1270, count=900)
    timer.add("busy", 130)
    timer.add("discogs", 1530, count=600)
    timer.add("wikipedia", 80, count=700)
    assert mmss(65) == "1:05" and mmss(3725) == "1:02:05"
    assert timer.time_line() == ("Time: scan 0:12, tags 8:40, MusicBrainz 21:10 (of it 2:10 waiting for a busy service, 1x), "
                                 "Discogs 25:30, Wikipedia 1:20")
    assert "tags 8:40 (2000 files)" in timer.detail() and "Discogs 25:30 (600 requests)" in timer.detail()
    assert timer.to_dict()["musicbrainz"] == {"seconds": 1270.0, "count": 900} and timer.to_dict()["busy"]["count"] == 1
    assert PhaseTimer().time_line() == ""


def test_the_engine_counts_a_real_runs_phases(tmp_path):
    ultravox_library(tmp_path)
    engine = NotesEngine(with_discogs(ultravox_fetchers()), discogs_mode="always")
    build_proposals([artist_target(tmp_path)], engine=engine)
    phases = engine.phases
    assert phases.count("tags") == 5 and phases.count("musicbrainz") == 3 and phases.count("wikipedia") == 2
    assert phases.count("discogs") == 3  # search, releases, artist
    assert phases.seconds("musicbrainz") >= 0 and engine.phases.time_line().startswith("Time: tags ")


# -- Discogs: a fallback by default --------------------------------------------------------------------------------

def test_fallback_asks_discogs_only_when_musicbrainz_has_no_verified_match(tmp_path):
    ultravox_library(tmp_path)
    fetchers = with_discogs(ultravox_fetchers())
    [proposal] = build_proposals([artist_target(tmp_path)], engine=NotesEngine(fetchers))  # the default mode
    assert proposal.confidence.value == "confident" and proposal.sources == ["musicbrainz", "wikipedia"]
    assert discogs_requests(fetchers) == 0  # MusicBrainz and Wikipedia said it all: Discogs was never asked

    cristina_library(tmp_path / "other")
    cristina = cristina_fetchers(with_discogs=True)
    [found] = build_proposals([next(t for t in scan([tmp_path / "other"], kinds=(KIND_ARTIST,)).targets)],
                              engine=NotesEngine(cristina))
    assert found.confidence.value == "confident" and found.sources == ["discogs"]  # MusicBrainz had no match: Discogs asked
    assert discogs_requests(cristina) > 0


def test_fallback_asks_discogs_for_the_profile_when_wikipedia_has_no_introduction(tmp_path):
    ultravox_library(tmp_path)
    fetchers = ultravox_fetchers()
    fetchers.wiki = FakeFetch([(r"wikidata", {"entities": {}}), (r"search/title", {"pages": []}), (r"page/summary/", None)])  # no article
    with_discogs(fetchers)
    [proposal] = build_proposals([artist_target(tmp_path)], engine=NotesEngine(fetchers))
    assert "discogs" in proposal.sources and "musicbrainz" in proposal.sources
    assert "Wikipedia had no introduction" in " ".join(proposal.reasons)
    assert "British" in proposal.note_text and "Midge Ure" in proposal.note_text  # profile and members from Discogs
    assert discogs_requests(fetchers) == 3


def test_the_three_modes_count_their_discogs_requests(tmp_path):
    ultravox_library(tmp_path)
    counts = {}
    for mode in ("never", "fallback", "always"):
        fetchers = with_discogs(ultravox_fetchers())
        build_proposals([artist_target(tmp_path)], engine=NotesEngine(fetchers, discogs_mode=mode))
        counts[mode] = discogs_requests(fetchers)
    assert counts == {"never": 0, "fallback": 0, "always": 3}


def test_always_runs_discogs_beside_musicbrainz_and_gives_the_same_note(tmp_path, monkeypatch):
    ultravox_library(tmp_path)

    started = threading.get_ident()
    threads = set()
    base = with_discogs(ultravox_fetchers())
    for fake in (base.mb, base.discogs._fetch):
        real = type(fake).__call__

        def spy(self, request, real=real):
            threads.add(threading.get_ident())
            time.sleep(0.15)
            return real(self, request)

        fake.__class__ = type("Spy", (type(fake),), {"__call__": spy})
    engine = NotesEngine(base, discogs_mode="always")
    t = time.perf_counter()
    [parallel] = build_proposals([artist_target(tmp_path)], engine=engine)
    wall = time.perf_counter() - t
    busy = engine.phases.seconds("musicbrainz") + engine.phases.seconds("discogs")
    assert len(threads) == 2 and started in threads  # two threads: the caller and the one helper, never more
    assert wall < busy * 0.85, (wall, busy)  # side by side: the wall clock is less than the two services added up
    # the same note as when they run one after the other
    monkeypatch.setattr(NotesEngine, "_both", lambda self, a, b: (a(), b()))
    serial_fetchers = with_discogs(ultravox_fetchers())
    [serial] = build_proposals([artist_target(tmp_path)], engine=NotesEngine(serial_fetchers, discogs_mode="always"))
    assert parallel.note_text == serial.note_text and parallel.sources == serial.sources


def test_a_failure_in_the_first_service_still_lets_the_second_finish_and_is_raised():
    engine = NotesEngine(Fetchers())
    done = []

    def second():
        time.sleep(0.1)
        done.append(1)
        return "b"

    def first():
        raise ValueError("a failed")

    with pytest.raises(ValueError):
        engine._both(first, second)
    assert done == [1]  # no work left running behind the exception
    assert engine._both(lambda: "a", second) == ("a", "b")


# -- the tags: a sample, then all if unsure; a small pool ------------------------------------------------------------

def many_albums(root, albums=6, files=8, artist="Ultravox"):
    base = root / artist
    for a in range(albums):
        for f in range(files):
            make_mp3(base / f"Album {a} ({1980 + a})" / f"{f + 1:02d} t.mp3", f"t{f}", artist, f"Album {a}", f + 1, str(1980 + a))
    return base


class CountingLoader:
    def __init__(self):
        self.files, self.calls = [], 0

    def __call__(self, paths):
        self.calls += 1
        self.files += [Path(p).name for p in paths]
        return notes_local.default_loader(paths)


def test_the_artist_is_verified_from_a_sample_of_the_tags(tmp_path):
    many_albums(tmp_path)  # 6 albums x 8 files = 48 files
    loader = CountingLoader()
    local = read_artist(tmp_path / "Ultravox", loader, max_albums=4, files_per_album=3)
    assert len(loader.files) == 12 and loader.calls == 1  # 4 albums x 3 files, one call (a pooled reader works on all at once)
    assert local.sampled and len(local.albums) == 6  # the other two are listed by folder name
    assert [a.title for a in local.albums[4:]] == ["Album 4", "Album 5"] and local.albums[4].tracks == []
    assert local.tag_names == ["Ultravox"] and local.albums[0].year == "1980"
    full = read_artist(tmp_path / "Ultravox", CountingLoader())
    assert not full.sampled and sum(len(a.tracks) for a in full.albums) == 48


def test_a_confident_artist_costs_a_sample_of_tag_reads_not_all_of_them(tmp_path):
    many_albums(tmp_path, albums=2, files=12)  # 24 files; the recorded fixtures know Vienna and Quartet
    for old, new in (("Album 0 (1980)", "Vienna (1980)"), ("Album 1 (1981)", "Quartet (1982)")):
        (tmp_path / "Ultravox" / old).rename(tmp_path / "Ultravox" / new)
    loader = CountingLoader()
    engine = NotesEngine(ultravox_fetchers(), loader=loader, discogs_mode="never")
    [proposal] = build_proposals([artist_target(tmp_path)], engine=engine)
    assert proposal.confidence.value == "confident"
    assert len(loader.files) == 6 and engine.phases.count("tags") == 6  # 2 albums x 3 files, not 24


def test_an_unverified_match_reads_the_rest_of_the_tags_and_asks_again(tmp_path):
    many_albums(tmp_path)  # no album the fixtures know
    loader = CountingLoader()
    fetchers = Fetchers(mb=no_mb(), wiki=no_wiki())
    engine = NotesEngine(fetchers, loader=loader, discogs_mode="never")
    [proposal] = build_proposals([artist_target(tmp_path)], engine=engine)
    assert proposal.confidence.value != "confident"
    assert len(loader.files) == 12 + 48  # the sample first, then everything because the match was not verified
    assert not proposal.local.sampled


def test_an_odd_level_is_decided_on_all_the_tags(tmp_path):
    base = tmp_path / "Various Stuff"
    for a in range(6):
        for f in range(4):
            make_mp3(base / f"Album {a}" / f"{f}.mp3", "t", f"Other {a}", f"Album {a}", f, "2000")
    loader = CountingLoader()
    engine = NotesEngine(Fetchers(mb=no_mb(), wiki=no_wiki()), loader=loader, discogs_mode="never")
    [proposal] = build_proposals([artist_target(tmp_path, "Various Stuff")], engine=engine)
    assert proposal.level != "artist"  # different artists in the albums: a collection / unclear level
    assert len(loader.files) == 12 + 24  # the sample, then all of them for the odd level


def test_the_tag_reader_uses_a_small_pool_and_keeps_the_order(tmp_path, monkeypatch):
    many_albums(tmp_path, albums=1, files=12)
    files = sorted((tmp_path / "Ultravox" / "Album 0 (1980)").glob("*.mp3"))
    seen = set()
    from core import scan_service

    real = scan_service.load_files

    def spy(paths, progress=None):
        seen.add(threading.get_ident())
        time.sleep(0.05)
        return real(paths, progress)

    monkeypatch.setattr(scan_service, "load_files", spy)
    started = time.perf_counter()
    result = notes_local.default_loader(files)
    wall = time.perf_counter() - started
    assert [Path(m.path).name for m in result] == [f.name for f in files]
    assert 1 < len(seen) <= notes_local.TAG_WORKERS and wall < 12 * 0.05 * 0.6  # four at a time: well under serial


# -- singles ---------------------------------------------------------------------------------------------------------

def test_without_singles_only_albums_and_eps_are_asked_for(tmp_path):
    ultravox_library(tmp_path)
    with_all, without = ultravox_fetchers(), ultravox_fetchers()
    [full] = build_proposals([artist_target(tmp_path)], engine=NotesEngine(with_all))
    [lean] = build_proposals([artist_target(tmp_path)], engine=NotesEngine(without, singles=False))
    assert not any("type=album" in u for u in with_all.mb.urls if "release-group?artist" in u)
    assert any("type=album%7Cep" in u for u in without.mb.urls)  # the typed browse: albums and EPs only
    assert "## Studio albums" in lean.note_text and full.confidence.value == lean.confidence.value == "confident"


# -- the command line -------------------------------------------------------------------------------------------------

@pytest.fixture
def cli(monkeypatch, capsys):
    from core.settings import Settings
    from mp3cli import cmd_notes, files as cli_files
    from mp3cli.main import main

    monkeypatch.setattr(cli_files, "load_settings", lambda *a, **k: Settings())
    state = {}

    def run(*argv):
        state["fetchers"] = with_discogs(ultravox_fetchers())
        monkeypatch.setattr(cmd_notes, "make_fetchers", lambda use: state["fetchers"] if use else ultravox_fetchers())
        code = main(["notes", *argv, "--json", "--no-cache"])
        captured = capsys.readouterr()
        return code, json.loads(captured.out), captured.err, state["fetchers"]

    return run


def test_cli_discogs_modes_and_the_phase_summary(tmp_path, cli):
    ultravox_library(tmp_path)
    code, document, err, fetchers = cli(str(tmp_path))  # the default: fallback
    assert code == 0 and discogs_requests(fetchers) == 0
    assert document["phases"]["tags"]["count"] == 5 and document["phases"]["musicbrainz"]["count"] == 3
    assert "Time: " in err and "Looked up 1 folder in" in err
    _c, _d, _e, always = cli(str(tmp_path), "--discogs", "always")
    assert discogs_requests(always) == 3
    _c, never_document, _e, never = cli(str(tmp_path), "--discogs", "never")
    assert discogs_requests(never) == 0 and "discogs" not in never_document["phases"]
    _c, _d, _e, no_discogs = cli(str(tmp_path), "--no-discogs")
    assert discogs_requests(no_discogs) == 0


def test_cli_no_singles(tmp_path, cli):
    ultravox_library(tmp_path)
    _c, _d, _e, fetchers = cli(str(tmp_path), "--no-singles")
    assert any("type=album%7Cep" in u for u in fetchers.mb.urls)


# -- the window ---------------------------------------------------------------------------------------------------------

def test_the_preferences_name_the_new_switches_and_they_are_off_by_default(tmp_path):
    from core.settings import Settings, load_settings, save_settings
    from gui.preferences import NOTES_DISCOGS_LABEL, NOTES_SKIP_SINGLES_LABEL, preference_sections

    assert NOTES_DISCOGS_LABEL == "Also ask Discogs for every artist (slower, adds members and profile)"
    labels = [spec.label for section in preference_sections() for spec in section.specs]
    assert NOTES_DISCOGS_LABEL in labels and NOTES_SKIP_SINGLES_LABEL in labels
    s = Settings()
    assert s.notes_discogs_always is False and s.notes_skip_singles is False  # off by default
    s.notes_discogs_always, s.notes_skip_singles = True, True
    save_settings(s, tmp_path)
    back = load_settings(tmp_path)
    assert back.notes_discogs_always is True and back.notes_skip_singles is True
