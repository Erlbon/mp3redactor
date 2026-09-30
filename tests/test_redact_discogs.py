"""The Redact step `discogs` (core/redact_steps.py DiscogsStep), driven through
the real engine on copies of tests/fixtures/tiny.mp3 with MOCKED Discogs
replies and the in-memory secret store -- no network, no real token."""

import urllib.error

import pytest

import core.redact_steps as rs
from core import discogs_lookup as dl
from core.settings import Settings
from core.tag_reader import load_tags
from core.mp3_file import MP3File
from redactor_common.core import secret_store
from redactor_common.core.pipeline import FileStatus, Recipe, run_recipe_on_item
from tests.test_discogs_lookup import KIND_OF_BLUE, TOKEN, FakeClock, FakeDiscogs, _hit, _http_error, _release
from tests.test_redact_steps import make_file, notes_of

TITLES = [t["title"] for t in KIND_OF_BLUE]


@pytest.fixture
def with_token():
    secret_store.set_secret("mp3redactor", "discogs_token", TOKEN)


def album_folder(tmp_path, count=5, name="Miles Davis - Kind Of Blue (1959)", album="Kind Of Blue", **extra):
    """`count` files of one album folder, tagged like the start of a Redact run."""
    folder = tmp_path / name
    folder.mkdir(exist_ok=True)
    return [
        make_file(folder, f"{n + 1:02d}.mp3", title=TITLES[n], artist="Miles Davis", album=album, track=str(n + 1), **extra)
        for n in range(count)
    ]


def single_fake():
    return FakeDiscogs(search_results=[_hit(1, "Miles Davis - Kind Of Blue")], releases={1: _release(1)})


def run_files(files, fake, threshold=0.9, options=None, throttle=None, settings=None, only=("discogs",)):
    """One run over `files` sharing one env (so per-run caches and the
    rate-limit flag work as in a real run); returns (entries, env)."""
    settings = settings or Settings()
    env = rs.RedactEnv(settings, discogs_fetch=fake, discogs_throttle=throttle or dl.Throttle(0.0))
    env.begin(files)
    catalogue = rs.build_catalogue(settings)
    recipe = Recipe.default_for(catalogue)
    recipe.enabled = {s.key: s.key in only for s in catalogue}
    recipe.options = dict(options or {})
    recipe.confidence_threshold = threshold
    resolved = recipe.resolve(catalogue)
    entries = [
        run_recipe_on_item(
            m, resolved, threshold, lambda x: rs.Mp3Ctx(x, env), lambda x: x.filename,
            finalize=rs.save_stage, finalize_label=rs.FINALIZE_LABEL,
        )
        for m in files
    ]
    return entries, env


def reloaded(mp3):
    fresh = MP3File(path=mp3.path)
    load_tags(fresh)
    return fresh


# --- catalogue --------------------------------------------------------------------


def test_the_step_sits_right_after_the_musicbrainz_step():
    keys = [s.key for s in rs.build_catalogue(Settings())]
    assert keys.index("discogs") == keys.index("tags") + 1 and keys.index("discogs") < keys.index("cover")
    order = Recipe.default_for(rs.build_catalogue(Settings())).order
    assert order.index("discogs") == order.index("tags") + 1


def test_default_on_only_when_a_token_exists(with_token):
    assert {s.key: s.default_enabled for s in rs.build_catalogue(Settings())}["discogs"] is True
    secret_store.delete_secret("mp3redactor", "discogs_token")
    assert {s.key: s.default_enabled for s in rs.build_catalogue(Settings())}["discogs"] is False
    assert Recipe.default_for(rs.build_catalogue(Settings())).enabled["discogs"] is False


# --- no token / offline / rate limit -------------------------------------------------


def test_no_token_is_a_note_with_the_way_to_set_one_not_a_failure(tmp_path):
    files = album_folder(tmp_path)
    fake = single_fake()
    entries, _ = run_files(files, fake)
    entry = entries[2]
    assert entry.status is FileStatus.UNCHANGED and not entry.failures
    assert "Tools > API Keys" in notes_of(entry) and "Developers" in notes_of(entry)
    assert fake.requests == [] and files[2].publisher == ""


def test_offline_is_a_note_not_a_failure_and_is_not_retried_for_every_file(tmp_path, with_token):
    files = album_folder(tmp_path, count=3)
    fake = FakeDiscogs(raises=urllib.error.URLError("offline"))
    entries, _ = run_files(files, fake)
    assert all(e.status is FileStatus.UNCHANGED and not e.failures for e in entries)
    assert "Discogs lookup unavailable" in notes_of(entries[0]) and "offline" in notes_of(entries[0])
    assert len(fake.requests) == 1  # one failed search for the folder, none for the other files


def test_http_429_stops_discogs_for_the_rest_of_the_run(tmp_path, with_token):
    first = album_folder(tmp_path, count=2)
    second = album_folder(tmp_path, count=2, name="Miles Davis - Sketches of Spain", album="Sketches of Spain")
    fake = FakeDiscogs(raises=_http_error(429, "Too Many Requests"))
    entries, env = run_files(first + second, fake)
    assert all(e.status is FileStatus.UNCHANGED and not e.failures for e in entries)
    assert "429" in notes_of(entries[0])
    assert "rate limit" in notes_of(entries[1]) and "rate limit" in notes_of(entries[3])
    assert len(fake.requests) == 1  # nothing was asked after the 429, for this folder or the next
    assert env._discogs["client"].rate_limited


def test_a_file_with_everything_filled_makes_no_request(tmp_path, with_token):
    folder = tmp_path / "x"
    folder.mkdir()
    mp3 = make_file(
        folder, "a.mp3", title="So What", artist="Miles Davis", albumartist="Miles Davis", album="Kind Of Blue",
        track="1", year="1959", genre="Jazz", publisher="Columbia", catalognumber="CL 1355", releasecountry="US",
    )
    fake = single_fake()
    entries, _ = run_files([mp3], fake)
    assert entries[0].status is FileStatus.UNCHANGED and fake.requests == []


# --- confidence routing -----------------------------------------------------------------


def test_exact_match_with_equal_track_count_fills_empty_fields_and_saves(tmp_path, with_token):
    files = album_folder(tmp_path)
    entries, _ = run_files(files, single_fake())
    assert all(e.status is FileStatus.CHANGED for e in entries)
    assert any("auto-applied at 93%" in a for a in entries[2].applied)
    fresh = reloaded(files[2])
    assert (fresh.publisher, fresh.catalognumber, fresh.releasecountry) == ("Columbia", "CL 1355", "US")
    assert (fresh.genre, fresh.year, fresh.albumartist, fresh.track) == ("Jazz; Modal", "1959", "Miles Davis", "3")


def test_only_empty_fields_are_filled(tmp_path, with_token):
    files = album_folder(tmp_path, year="1985", genre="Mine")
    files[2].publisher = "My Label"
    from core.tag_writer import save_tags

    assert save_tags(files[2])
    run_files(files, single_fake())
    fresh = reloaded(files[2])
    assert (fresh.year, fresh.genre, fresh.publisher) == ("1985", "Mine", "My Label")  # kept
    assert (fresh.catalognumber, fresh.releasecountry) == ("CL 1355", "US")  # filled
    assert fresh.title == "Blue In Green"


def test_exact_artist_and_album_with_a_different_track_count_is_reviewed_at_seventy_percent(tmp_path, with_token):
    files = album_folder(tmp_path, count=3)
    entries, _ = run_files(files, single_fake())
    entry = entries[1]
    assert entry.status is FileStatus.NEEDS_REVIEW and not entry.applied
    assert entry.review[0].confidence == pytest.approx(0.7) and "5 tracks, the folder 3 files" in entry.review[0].reason
    assert files[1].publisher == "" and reloaded(files[1]).publisher == ""
    entries, _ = run_files(album_folder(tmp_path, count=3, name="again"), single_fake(), threshold=0.7)
    assert entries[1].status is FileStatus.CHANGED


def test_several_equally_good_pressings_are_not_confident(tmp_path, with_token):
    fake = FakeDiscogs(
        search_results=[_hit(1, "Miles Davis - Kind Of Blue"), _hit(2, "Miles Davis - Kind Of Blue", label=("Legacy",))],
        releases={1: _release(1), 2: _release(2, label="Legacy")},
    )
    files = album_folder(tmp_path)
    entries, _ = run_files(files, fake)
    assert entries[0].status is FileStatus.NEEDS_REVIEW
    assert entries[0].review[0].confidence == pytest.approx(0.7)


def test_the_year_picks_out_one_pressing_so_it_is_confident(tmp_path, with_token):
    fake = FakeDiscogs(
        search_results=[_hit(2, "Miles Davis - Kind Of Blue", year="1997", label=("Legacy",)), _hit(1, "Miles Davis - Kind Of Blue")],
        releases={1: _release(1), 2: _release(2, year=1997, label="Legacy")},
    )
    files = album_folder(tmp_path, year="1959")
    entries, _ = run_files(files, fake)
    assert entries[0].status is FileStatus.CHANGED and reloaded(files[0]).publisher == "Columbia"


def test_a_fuzzy_match_is_at_most_sixty_percent_and_needs_review(tmp_path, with_token):
    files = album_folder(tmp_path, album="Kind Of Blu")
    entries, _ = run_files(files, single_fake(), threshold=0.6)
    entry = entries[0]
    assert entry.status is FileStatus.CHANGED  # 0.6 meets a 0.6 threshold ...
    files = album_folder(tmp_path, album="Kind Of Blu", name="other")
    entries, _ = run_files(files, single_fake())
    assert entries[0].status is FileStatus.NEEDS_REVIEW and entries[0].review[0].confidence <= 0.6  # ... but not the default 0.9


def test_a_title_that_clashes_with_the_paired_track_caps_the_confidence(tmp_path, with_token):
    files = album_folder(tmp_path)
    files[2].title = "Completely Different Song"
    from core.tag_writer import save_tags

    assert save_tags(files[2])
    entries, _ = run_files(files, single_fake())
    assert entries[0].status is FileStatus.CHANGED  # the other files still agree
    assert entries[2].status is FileStatus.NEEDS_REVIEW
    assert entries[2].review[0].confidence <= 0.6 and "does not resemble" in entries[2].review[0].reason
    assert reloaded(files[2]).title == "Completely Different Song" and reloaded(files[2]).publisher == ""


def test_a_file_that_cannot_be_placed_on_the_release_is_left_alone(tmp_path, with_token):
    files = album_folder(tmp_path)
    folder = files[0].path.parent
    stray = make_file(folder, "99.mp3", title="Unknown Bonus", artist="Miles Davis", album="Kind Of Blue", track="99")
    entries, _ = run_files(files + [stray], single_fake())
    assert entries[-1].status is FileStatus.UNCHANGED and reloaded(stray).publisher == ""


def test_genre_styles_follow_the_option(tmp_path, with_token):
    files = album_folder(tmp_path)
    run_files(files, single_fake(), options={"discogs": {"styles": False}})
    assert reloaded(files[0]).genre == "Jazz"


# --- per-run caching and pacing ---------------------------------------------------------


def test_release_details_are_fetched_once_per_run(tmp_path, with_token):
    files = album_folder(tmp_path)
    fake = single_fake()
    run_files(files, fake)
    assert len(fake.searches()) == 1
    assert len([r for r in fake.requests if "/releases/" in r.full_url]) == 1


def test_a_release_seen_from_two_folders_is_fetched_once(tmp_path, with_token):
    a = album_folder(tmp_path)
    b = album_folder(tmp_path, name="Miles Davis - Kind Of Blue (Remaster)", album="Kind of Blue")  # another folder, same release
    fake = single_fake()
    run_files(a + b, fake)
    assert len(fake.searches()) == 2
    assert len([r for r in fake.requests if "/releases/" in r.full_url]) == 1


def test_requests_are_paced_across_the_run(tmp_path, with_token):
    fake = FakeDiscogs(
        search_results=[_hit(1, "Miles Davis - Kind Of Blue"), _hit(2, "Miles Davis - Kind Of Blue", year="1997")],
        releases={1: _release(1), 2: _release(2, year=1997)},
    )
    clock = FakeClock()
    files = album_folder(tmp_path)
    run_files(files, fake, throttle=dl.Throttle(1.1, clock=clock, sleep=clock.sleep))
    assert len(fake.requests) == 3  # one search, two release details
    assert clock.slept == [pytest.approx(1.1), pytest.approx(1.1)]


# --- the token never shows ---------------------------------------------------------------


def test_the_token_is_nowhere_in_the_report_or_the_requests(tmp_path, with_token):
    files = album_folder(tmp_path, count=2)
    leaky = FakeDiscogs(raises=OSError(f"connection reset (Authorization: Discogs token={TOKEN})"))
    entries, env = run_files(files, leaky)
    text = repr([e.__dict__ for e in entries]) + repr(env._discogs.get("client"))
    assert TOKEN not in text and TOKEN not in notes_of(entries[0])
    assert all(TOKEN not in r.full_url for r in leaky.requests)
    ok = single_fake()
    entries, _ = run_files(album_folder(tmp_path, name="ok"), ok)
    assert TOKEN not in repr([e.__dict__ for e in entries]) and all(TOKEN not in r.full_url for r in ok.requests)


# --- the recipe -----------------------------------------------------------------------


def test_recipe_round_trips_with_the_new_step(tmp_path, with_token):
    from core.settings import load_settings, save_settings

    cat = rs.build_catalogue(Settings())
    recipe = Recipe.default_for(cat)
    assert recipe.enabled["discogs"] is True
    recipe.enabled["discogs"] = False
    recipe.options["discogs"] = {"styles": False}
    save_settings(Settings(redact_recipe=rs.recipe_to_setting(recipe)), tmp_path)
    loaded = rs.recipe_from_setting(load_settings(tmp_path).redact_recipe, cat)
    assert loaded.to_dict() == recipe.to_dict()
    assert loaded.enabled["discogs"] is False and loaded.options["discogs"] == {"styles": False}
    assert TOKEN not in rs.recipe_to_setting(recipe)


def test_a_recipe_saved_before_the_step_existed_gets_it_after_the_tag_step(with_token):
    cat = rs.build_catalogue(Settings())
    old = Recipe(order=["integrity", "bpm", "key", "loudness", "deep_check", "path_tags", "tags", "cover", "rename", "move_into_folders"],
                 enabled={"integrity": True, "tags": True, "cover": True})
    order = [s.key for s, _ in old.resolve(cat)]
    assert order.index("discogs") == order.index("tags") + 1
