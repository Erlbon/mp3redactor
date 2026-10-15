"""How long Create Notes takes, said plainly: the progress label's elapsed / remaining time, the 'Looked up N folders in
H:MM:SS' sentence (review banner, summary box, CLI), the scope dialog's time hint, a cancelled run that keeps what it did
and a second run that resumes from the cache, and the cache's per-request cost."""

import json
import os
import threading
import time
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402

import gui.notes_dialog as nd  # noqa: E402
from core.notes_cache import ResponseCache  # noqa: E402
from core.notes_flow import Fetchers  # noqa: E402
from core.notes_local import KIND_ARTIST  # noqa: E402
from core.notes_timing import (  # noqa: E402
    ALBUM_SECONDS, ARTIST_SECONDS, clock, estimate, format_duration, lookup_sentence, progress_suffix, scope_hint,
)
from tests.notes_helpers import set_kinds  # noqa: E402,F401
from tests.notes_helpers import FakeFetch, make_mp3, ultravox_fetchers, ultravox_library  # noqa: E402
from tests.test_cover_gui import window  # noqa: E402,F401
from tests.test_notes_gui import boxes, click, review, wait  # noqa: E402,F401
from tests.test_notes_scope import drive, scope_dialog  # noqa: E402,F401


# -- the words ----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("seconds,text", [(0, "0 s"), (35, "35 s"), (59.4, "59 s"), (60, "1 min"), (240, "4 min"),
                                          (3599, "1 h"), (3600, "1 h"), (6060, "1 h 41 min"), (7200, "2 h")])
def test_format_duration(seconds, text):
    assert format_duration(seconds) == text


def test_clock_and_the_lookup_sentence():
    assert clock(0) == "0:00:00" and clock(6612) == "1:50:12" and clock(75) == "0:01:15"
    assert lookup_sentence(656, 6612, 5120, 800) == "Looked up 656 folders in 1:50:12 (5120 requests, 800 from the cache)."
    assert lookup_sentence(1, 3, 1, 0) == "Looked up 1 folder in 0:00:03 (1 request, 0 from the cache)."


def test_the_progress_suffix_says_nothing_silly_early_on():
    assert progress_suffix(0, 656, 2, []) == "" and progress_suffix(2, 656, 20, [10, 10]) == ""
    assert progress_suffix(656, 656, 6000, [10] * 20) == ""  # nothing left
    assert progress_suffix(3, 656, 30, [10, 10, 10]) == ", 30 s elapsed, about 1 h 49 min left"
    assert progress_suffix(12, 656, 240, [20] * 12) == ", 4 min elapsed, about 3 h 35 min left"  # 644 left at 20 s each


def test_the_estimate_follows_the_recent_folders_and_counts_cache_hits_as_fast():
    slow = progress_suffix(20, 100, 200, [10.0] * 20)
    resumed = progress_suffix(20, 100, 200, [10.0] * 8 + [0.05] * 12)  # the latest dozen came from the cache
    assert "about 13 min left" in slow and resumed.endswith("almost done")  # 80 folders at 0.05 s each
    assert progress_suffix(90, 100, 900, [9.0] * 12).endswith("about 90 s left".replace("90 s", "2 min"))  # 10 left at 9 s


def test_the_scope_hint_is_for_big_scopes_with_album_notes_only():
    assert scope_hint(40, 50, 0, "both") == "" and scope_hint(300, 350, 5, "artist") == ""  # small / the fast choice
    text = scope_hint(250, 400, 3, "both")
    assert text.startswith("About 650 folders: roughly ") and "with album notes" in text
    assert "artist notes only is about 2x faster" in text
    assert format_duration(estimate(250, 400, 3, "both")) in text
    assert estimate(1, 1, 0, "both") == pytest.approx(ARTIST_SECONDS + ALBUM_SECONDS)
    assert scope_hint(120, 0, 0, "both") == ""  # only artists: 'both' is not slower, nothing to say about albums
    assert "Everything" not in scope_hint(250, 400, 3, "all") and "About 653 folders" in scope_hint(250, 400, 3, "all")


# -- the progress label and the summary, on the real engine and the real review dialog ---------------------------

class FakeProgress:
    """The progress dialog's object (the worker talks only to this): it records what the user would read."""

    def __init__(self, cancel_after=None):
        self.labels, self.values, self.cancel_after = [], [], cancel_after

    def set_label(self, text):
        self.labels.append(text)

    def set_value(self, value):
        self.values.append(value)

    def should_cancel(self):
        return self.cancel_after is not None and len(self.labels) >= self.cancel_after


def install_fake_progress(monkeypatch, cancel_after=None, step=40.0):
    holder = {}

    def fake_run(parent, total, label, work, cancellable=False, title=""):
        progress = holder["progress"] = FakeProgress(cancel_after)
        work(progress)
        return None, progress.should_cancel()

    clock_state = {"now": 1000.0}

    def perf_counter():
        clock_state["now"] += step  # every look at the clock is `step` seconds later: 'slow' folders
        return clock_state["now"]

    monkeypatch.setattr(nd, "run_work_with_progress", fake_run)
    monkeypatch.setattr(nd, "time", SimpleNamespace(perf_counter=perf_counter))
    return holder


def six_artists(root):
    for i in range(6):
        make_mp3(root / f"Band {i}" / f"Album {i} (2000)" / "01 a.mp3", "a", f"Band {i}", f"Album {i}", 1, "2000")
    return root


def nothing_found():
    mb = FakeFetch([(r"ws/2/artist\?query", {"artists": []}), (r"ws/2/release-group\?query=", {"release-groups": []})])
    wiki = FakeFetch([(r"wikidata", {"entities": {}}), (r"search/title", {"pages": []}), (r"page/summary/", None)])
    return Fetchers(mb=mb, wiki=wiki)


def run_create(window, monkeypatch, root, cache, cancel_after=None):
    holder = install_fake_progress(monkeypatch, cancel_after)
    seen = {}
    fetchers = nothing_found()

    def trigger():
        nd.create_notes(window, [str(root)], True, lambda: fetchers, kinds=(KIND_ARTIST,), cache=cache)

    def scenario():
        yield from wait(lambda: review() is not None, "the review page")
        dlg = review()
        seen["banner"] = dlg.banner_label.text()
        seen["items"] = len(dlg.session.items)
        dlg._summary_box()
        seen["summary"] = dlg.last_summary
        dlg.done(0)

    drive(scenario, trigger)
    return holder["progress"], seen, fetchers


def test_the_progress_label_gets_elapsed_and_remaining_time_after_three_folders_and_the_end_says_how_long(
        window, monkeypatch, tmp_path, boxes):
    root = six_artists(tmp_path / "lib")
    progress, seen, fetchers = run_create(window, monkeypatch, root, None)
    labels = progress.labels
    assert len(labels) == 6 and all(l.startswith("Looking up ") and f"({i + 1} of 6)" in l for i, l in enumerate(labels))
    assert not any("elapsed" in l for l in labels[:3])  # nothing silly early on
    assert all(", " in l and "elapsed, about " in l and l.endswith("left") for l in labels[3:]), labels[3:]
    # 40 s per look at the clock: the later labels have counted real time
    assert "min elapsed" in labels[-1]
    assert "Looked up 6 folders in 0:" in seen["banner"] and "requests, 0 from the cache)." in seen["banner"]
    assert "Looked up 6 folders in 0:" in seen["summary"]  # the summary box says it too
    assert "\nTime: " in seen["banner"] and "tags " in seen["banner"].split("\nTime: ")[1]  # the second line: where the time went


def test_a_cancelled_run_keeps_what_it_looked_up_and_the_next_run_resumes_from_the_cache(
        window, monkeypatch, tmp_path, boxes):
    root = six_artists(tmp_path / "lib")
    cache = ResponseCache(tmp_path / "cache.sqlite")
    progress, seen, first = run_create(window, monkeypatch, root, cache, cancel_after=3)
    assert seen["items"] == 3 and "Cancelled: 3 of 6 folders were looked up" in seen["banner"]  # kept, and said so
    asked = len(first.mb.urls) + len(first.wiki.urls)
    assert asked > 0
    _progress, seen2, second = run_create(window, monkeypatch, root, cache)
    assert seen2["items"] == 6
    again = len(second.mb.urls) + len(second.wiki.urls)
    assert again > 0 and again < 2 * asked  # only the other three folders were asked for
    # the first three folders came from the cache: the second run asked only for the other three
    import re

    match = re.search(r"\((\d+) requests, (\d+) from the cache\)", seen2["banner"])
    assert int(match.group(2)) == asked and int(match.group(1)) == again


# -- the scope dialog's hint ---------------------------------------------------------------------------------------

def test_the_scope_dialog_warns_about_a_big_scope_with_album_notes_and_stays_quiet_otherwise(window, tmp_path, boxes):
    root = tmp_path / "big"
    for i in range(60):  # 60 artists with two albums each: 60 + 120 = 180 folders to look up
        for j in range(2):
            make_mp3(root / f"Band {i:02d}" / f"Album {j} (200{j})" / "01 a.mp3", "a", f"Band {i}", f"Album {j}", 1, f"200{j}")
    window.settings.last_directory = str(root)
    window.settings.include_subfolders = True
    window._load_paths([root / "Band 00"])
    seen = {}

    def scenario():
        yield from wait(lambda: scope_dialog() is not None, "the scope dialog")
        dlg = scope_dialog()
        next(r for r, p in dlg.radios if p == str(root)).click()
        yield from wait(lambda: dlg.counts is not None, "the folder count", 30)
        seen["counts"] = dlg.counts
        seen["artist_only"] = (dlg.hint_label.isVisibleTo(dlg), dlg.hint_label.text())
        set_kinds(dlg, "both")
        seen["both"] = (dlg.hint_label.isVisibleTo(dlg), dlg.hint_label.text())
        set_kinds(dlg, "artist")
        seen["back"] = dlg.hint_label.isVisibleTo(dlg)
        QTest.mouseClick(dlg.cancel_button, nd.Qt.MouseButton.LeftButton)

    drive(scenario, window.action_registry["create_notes"].trigger)
    assert seen["counts"] == (60, 120, 1)  # 60 artists, 120 albums, and the folder itself as a genre folder
    assert seen["artist_only"][0] is False  # the fast choice: nothing to warn about
    visible, text = seen["both"]
    assert visible and text.startswith("About 180 folders: roughly ") and "artist notes only is about 2x faster" in text
    assert seen["back"] is False


# -- the command line ----------------------------------------------------------------------------------------------

def test_cli_reports_elapsed_requests_and_cache_hits_in_json_and_on_stderr(tmp_path, capsys, monkeypatch):
    from core.settings import Settings
    from mp3cli import cmd_notes, files as cli_files
    from mp3cli.main import main

    monkeypatch.setattr(cli_files, "load_settings", lambda *a, **k: Settings())
    fetchers = ultravox_fetchers()
    monkeypatch.setattr(cmd_notes, "make_fetchers", lambda use: fetchers)
    ultravox_library(tmp_path)
    code = main(["notes", str(tmp_path), "--json", "--no-cache"])
    captured = capsys.readouterr()
    document = json.loads(captured.out)
    assert code == 0 and document["requests"] == 5 and document["from_cache"] == 0
    assert isinstance(document["elapsed_seconds"], float) and document["elapsed_seconds"] >= 0
    assert "Looked up 1 folder in 0:00:0" in captured.err and "(5 requests, 0 from the cache)" in captured.err


# -- the cache's cost per request ----------------------------------------------------------------------------------

def test_the_cache_costs_next_to_nothing_per_request_and_holds_up_under_threads(tmp_path):
    cache = ResponseCache(tmp_path / "speed.sqlite")
    body = b'{"x": "' + b"a" * 4000 + b'"}'
    start = time.perf_counter()
    for i in range(600):
        cache.put(f"k{i}", body)
    put_ms = (time.perf_counter() - start) / 600 * 1000
    start = time.perf_counter()
    for i in range(600):
        assert cache.get(f"k{i}") == (200, body)
    hit_ms = (time.perf_counter() - start) / 600 * 1000
    # a MusicBrainz request costs 1100 ms: even a loose bound is a tiny share of it (measured: about 0.3 and 0.01 ms)
    assert put_ms < 25 and hit_ms < 25, (put_ms, hit_ms)
    errors = []

    def worker(n):
        try:
            for i in range(200):
                assert cache.get(f"k{(i * 7 + n) % 600}") is not None
                cache.put(f"t{n}-{i}", body)
        except Exception as exc:  # noqa: BLE001
            errors.append(repr(exc))

    start = time.perf_counter()
    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    per_call = (time.perf_counter() - start) / (8 * 400) * 1000
    assert errors == [] and per_call < 25, per_call
    cache.close()
