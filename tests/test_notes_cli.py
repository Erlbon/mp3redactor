"""The `notes` command line end to end (mp3cli/cmd_notes.py), the services replaced by recorded fixtures."""

import json

import pytest

from core.settings import Settings
from mp3cli import cmd_notes, files as cli_files
from mp3cli.main import main
from redactor_common.cli import CliError
from tests.notes_helpers import FakeFetch, cristina_fetchers, cristina_library, make_mp3, ultravox_fetchers, ultravox_library


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(cli_files, "load_settings", lambda *a, **k: Settings())


@pytest.fixture
def services(monkeypatch):
    """Replaces the one function that makes the real services; records the --no-discogs choice."""
    state = {"use_discogs": None, "fetchers": ultravox_fetchers()}

    def make(use_discogs):
        state["use_discogs"] = use_discogs
        return state["fetchers"]

    monkeypatch.setattr(cmd_notes, "make_fetchers", make)
    return state


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def run_json(capsys, *argv):
    code, out, _err = run(capsys, *argv, "--json")
    return code, json.loads(out)


def test_default_is_a_report_and_writes_nothing(tmp_path, capsys, services):
    artist = ultravox_library(tmp_path)
    code, out, _err = run(capsys, "notes", str(tmp_path))
    assert code == 0
    assert "would-write" in out and "3 folder(s) looked up: 3 confident" in out
    assert not list(tmp_path.rglob("*.md"))
    assert services["use_discogs"] is True
    assert artist.exists()


def test_write_writes_confident_drafts_only_and_never_overwrites(tmp_path, capsys, services):
    artist = ultravox_library(tmp_path)
    make_mp3(tmp_path / "Mystery" / "Unknown Thing" / "01 a.mp3", "a", "Nobody", "Unknown Thing")
    services["fetchers"].mb.routes.insert(0, (r"ws/2/artist\?query=artist%3A%22Nobody", {"artists": []}))
    services["fetchers"].mb.routes.insert(0, (r"ws/2/artist\?query=artist%3A%22Mystery", {"artists": []}))
    services["fetchers"].mb.routes.insert(0, (r"ws/2/release-group\?query=.*Unknown", {"release-groups": []}))
    code, document = run_json(capsys, "notes", str(tmp_path), "--write")
    assert code == 0
    by = {(r["kind"], r["folder"].split("\\")[-1].split("/")[-1]): r for r in document["results"]}
    assert by[("artist", "Ultravox")]["status"] == "written"
    assert (artist / "artist.md").exists() and (artist / "Vienna (1980)" / "album.md").exists()
    assert not (tmp_path / "Mystery" / "artist.md").exists()
    assert by[("artist", "Mystery")]["status"] == "no-match" or by[("artist", "Mystery")]["status"] == "review"
    assert document["written"] == 3 and document["dry_run"] is False
    text = (artist / "artist.md").read_text(encoding="utf-8")
    assert text.startswith("---\n") and "status: draft" in text
    # a second run finds the notes already there: nothing is touched
    before = text
    code, again = run_json(capsys, "notes", str(tmp_path), "--write")
    assert code == 0 and again["written"] == 0 and again["has_note"] >= 3
    assert (artist / "artist.md").read_text(encoding="utf-8") == before


def test_dry_run_overrides_write(tmp_path, capsys, services):
    ultravox_library(tmp_path)
    code, document = run_json(capsys, "notes", str(tmp_path), "--write", "--dry-run")
    assert code == 0 and document["dry_run"] is True and document["would_write"] == 3 and document["written"] == 0
    assert not list(tmp_path.rglob("*.md"))


def test_uncertain_is_listed_with_reasons_and_candidates_and_never_written(tmp_path, capsys, services):
    cristina_library(tmp_path)
    services["fetchers"] = cristina_fetchers(with_discogs=False)
    code, out, _ = run(capsys, "notes", str(tmp_path), "--write", "--kind", "artist")
    assert code == 0
    assert "review" in out and "none of the local albums" in out and "candidate: musicbrainz" in out
    assert "not written" in out
    assert not list(tmp_path.rglob("*.md"))
    assert services["use_discogs"] is True


def test_no_discogs_flag_reaches_the_services(tmp_path, capsys, services):
    ultravox_library(tmp_path)
    run(capsys, "notes", str(tmp_path), "--no-discogs", "--kind", "artist")
    assert services["use_discogs"] is False


def test_kind_filter_json_shape_and_show_drafts(tmp_path, capsys, services):
    ultravox_library(tmp_path)
    code, document = run_json(capsys, "notes", str(tmp_path), "--kind", "album", "--show-drafts")
    assert code == 0 and {r["kind"] for r in document["results"]} == {"album"}
    row = document["results"][0]
    assert row["confidence"] == "confident" and row["draft"].startswith("---\n") and row["note_kind"] == "album"
    assert row["level"] == "album" and "sources" in row and "candidates" in row
    code, document = run_json(capsys, "notes", str(tmp_path), "--kind", "album")
    assert "draft" not in document["results"][0]


def test_genre_kind_and_all(tmp_path, capsys, services):
    ultravox_library(tmp_path / "Music" / "New Wave")
    services["fetchers"].wiki.routes.insert(0, (r"(?i)search/title\?q=New\+wave\+music", {"pages": [{"title": "New wave music", "description": "Genre of popular music"}]}))
    services["fetchers"].wiki.routes.insert(0, (r"page/summary/New_wave_music", {
        "type": "standard", "title": "New wave music", "description": "Genre of popular music",
        "extract": "New wave is a music genre from the late 1970s.", "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/New_wave_music"}}}))
    code, document = run_json(capsys, "notes", str(tmp_path / "Music"), "--kind", "genre", "--write")
    assert code == 0 and [r["kind"] for r in document["results"]] == ["genre"]
    assert (tmp_path / "Music" / "New Wave" / "about.md").exists()
    code, document = run_json(capsys, "notes", str(tmp_path / "Music"), "--kind", "all")
    assert {r["kind"] for r in document["results"]} == {"genre", "artist", "album"}


def test_no_recurse_looks_only_at_the_named_folder(tmp_path, capsys, services):
    ultravox_library(tmp_path)
    code, document = run_json(capsys, "notes", str(tmp_path / "Ultravox"), "-R")
    assert code == 0 and [r["kind"] for r in document["results"]] == ["artist"]


def test_missing_path_and_nothing_to_do_are_messages(tmp_path, capsys, services):
    with pytest.raises(CliError, match="no artist or album folders found"):
        main(["notes", str(tmp_path / "nope")])
    assert "nothing found for" in capsys.readouterr().err
    (tmp_path / "empty").mkdir()
    with pytest.raises(CliError):
        main(["notes", str(tmp_path / "empty")])


def test_network_failure_is_a_message_and_exit_1(tmp_path, capsys, services):
    import urllib.error

    ultravox_library(tmp_path)

    def down(url):
        raise urllib.error.URLError("no network")

    services["fetchers"].mb = down
    code, out, err = run(capsys, "notes", str(tmp_path), "--kind", "artist")
    assert code == 1 and "Could not reach MusicBrainz" in out and "Traceback" not in out + err
    assert not list(tmp_path.rglob("*.md"))


def test_ctrl_c_ends_with_130_and_keeps_what_was_written(tmp_path, capsys, services):
    ultravox_library(tmp_path)
    real = services["fetchers"].mb
    calls = {"n": 0}

    def interrupting(url):
        calls["n"] += 1
        if calls["n"] > 6:  # the first target is done by then; a later one is interrupted
            raise KeyboardInterrupt
        return real(url)

    services["fetchers"].mb = interrupting
    code, out, err = run(capsys, "notes", str(tmp_path), "--write", "--json")
    document = json.loads(out)
    assert code == 130 and document["cancelled"] is True
    assert any(r["status"] == "written" for r in document["results"])
    assert (tmp_path / "Ultravox" / "artist.md").exists() or (tmp_path / "Ultravox" / "Vienna (1980)" / "album.md").exists()


def test_history_stays_off_and_records_only_when_the_user_turned_it_on(tmp_path, capsys, services, journal):
    from core import history

    ultravox_library(tmp_path)
    run(capsys, "notes", str(tmp_path), "--write", "--kind", "artist")
    assert journal.entries() == []
    (tmp_path / "Ultravox" / "artist.md").unlink()
    history.set_history_enabled(True)
    run(capsys, "notes", str(tmp_path), "--write", "--kind", "artist")
    assert [e.kind for e in journal.entries()] == ["note"]
