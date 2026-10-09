"""The command line (mp3cli/): every command on real small files, text and --json output, exit codes,
--dry-run. Settings and the Recycle Bin are the test-isolated ones."""

import json
import os
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.audiobook_lookup import BookMatch, LookupOutcome
from core.m4b_builder import BookDefaults
from core.mp3_file import STATUS_ERROR, STATUS_OK, MP3File
from core.settings import Settings
from core.tag_reader import load_tags
from mp3cli import cmd_analyze, cmd_files, cmd_m4b, cmd_redact, files as cli_files
from mp3cli.main import main
from redactor_common.cli import CliError
from tests.test_m4b_builder import _tone_mp3, requires_ffmpeg

FIXTURE = Path(__file__).parent / "fixtures" / "tiny.mp3"


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def run_json(capsys, *argv):
    code, out, _err = run(capsys, *argv, "--json")
    return code, json.loads(out)


def read_tags(path):
    mp3 = MP3File(path=Path(path))
    load_tags(mp3)
    return mp3


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """No real settings file."""
    app = Settings()
    monkeypatch.setattr(cli_files, "load_settings", lambda *a, **k: app)
    return SimpleNamespace(settings=app)


@pytest.fixture
def song(tmp_path):
    """A tagged copy of the tiny fixture."""
    path = tmp_path / "song.mp3"
    shutil.copyfile(FIXTURE, path)
    mp3 = read_tags(path)
    mp3.apply_tags({"title": "Bohemian Rhapsody", "artist": "Queen", "album": "A Night at the Opera", "track": "11", "year": "1975"})
    from core.tag_writer import save_tags

    assert save_tags(mp3)
    return str(path)


# --- info ---------------------------------------------------------------------------------------


def test_info_shows_the_default_fields(song, capsys):
    code, out, _ = run(capsys, "info", song)
    assert code == 0 and "title: Bohemian Rhapsody" in out and "artist: Queen" in out and "track: 11" in out


def test_info_json_fields_and_all(song, tmp_path, capsys):
    code, document = run_json(capsys, "info", str(tmp_path), "--fields", "artist,Album Artist,year")
    assert code == 0 and document["files"] == 1 and document["failed"] == 0
    assert document["results"][0]["fields"] == {"artist": "Queen", "year": "1975"}
    _code, document = run_json(capsys, "info", song, "--all")
    assert set(document["results"][0]["fields"]) == {"title", "artist", "album", "track", "year"}


def test_info_reports_an_unreadable_file_and_exits_1(tmp_path, capsys):
    bad = tmp_path / "bad.mp3"
    bad.write_bytes(b"this is not an mp3 file at all" * 20)
    code, document = run_json(capsys, "info", str(bad))
    assert code == 1 and document["failed"] == 1 and document["results"][0]["status"] != "ok"


def test_info_with_nothing_found_is_a_usage_error(tmp_path, capsys):
    with pytest.raises(CliError, match="no MP3 files"):
        main(["info", str(tmp_path / "nope.mp3")])


# --- set ------------------------------------------------------------------------------------------


def test_set_changes_fields_and_saves(song, capsys):
    code, document = run_json(capsys, "set", song, "-s", "artist=Queen & Co", "-s", "Album Artist=Queen", "--clear", "year")
    assert code == 0 and document["results"][0]["status"] == "changed"
    mp3 = read_tags(song)
    assert (mp3.artist, mp3.albumartist, mp3.year, mp3.title) == ("Queen & Co", "Queen", "", "Bohemian Rhapsody")


def test_set_dry_run_writes_nothing_and_same_value_is_unchanged(song, capsys):
    before = open(song, "rb").read()
    code, out, _ = run(capsys, "set", song, "-s", "artist=Else", "-n")
    assert code == 0 and "planned" in out and "'Queen' -> 'Else'" in out and open(song, "rb").read() == before
    _code, document = run_json(capsys, "set", song, "-s", "artist=Queen")
    assert document["results"][0]["status"] == "unchanged"


@pytest.mark.parametrize("argv, message", [
    (["-s", "Nonsense=1"], "unknown field"),
    (["-s", "track=abc"], "number or number/total"),
    (["-s", "year=75"], "YYYY"),
    (["-s", "language=english"], "three-letter"),
    (["-s", "novalue"], "FIELD=VALUE"),
    ([], "nothing to change"),
])
def test_set_refuses_bad_input_before_touching_anything(song, argv, message):
    before = open(song, "rb").read()
    with pytest.raises(CliError, match=message):
        main(["set", song, *argv])
    assert open(song, "rb").read() == before


def test_set_accepts_field_spellings_and_track_totals(song, capsys):
    run(capsys, "set", song, "-s", "albumartist=A", "-s", "DISC NUMBER=1/2", "-s", "track=3/12", "-s", "year=1975-11-21")
    mp3 = read_tags(song)
    assert (mp3.albumartist, mp3.discnumber, mp3.track, mp3.year) == ("A", "1/2", "3/12", "1975-11-21")


# --- rename / move -------------------------------------------------------------------------------------


def test_rename_by_pattern_with_padding(song, tmp_path, capsys):
    code, document = run_json(capsys, "rename", song, "-p", "%track% - %artist% - %title%", "--zero-pad", "3")
    assert code == 0 and document["results"][0]["status"] == "renamed"
    new_path = str(tmp_path / "011 - Queen - Bohemian Rhapsody.mp3")
    assert os.path.exists(new_path) and not os.path.exists(song)


def test_rename_default_pattern_dry_run_collisions_and_nameless(tmp_path, capsys):
    a, b, nameless = (tmp_path / n for n in ("a.mp3", "b.mp3", "n.mp3"))
    for p in (a, b, nameless):
        shutil.copyfile(FIXTURE, p)
    for p in (a, b):
        m = read_tags(p)
        m.apply_tags({"artist": "Queen", "title": "Song", "track": "1"})
        from core.tag_writer import save_tags

        save_tags(m)
    _code, dry = run_json(capsys, "rename", str(a), str(b), "-p", "%artist% - %title%", "-n")
    assert sorted(os.path.basename(r["new_path"]) for r in dry["results"]) == ["Queen - Song (2).mp3", "Queen - Song.mp3"]
    run(capsys, "rename", str(a), str(b), "-p", "%artist% - %title%")
    assert sorted(n for n in os.listdir(tmp_path) if n.endswith(".mp3")) == ["Queen - Song (2).mp3", "Queen - Song.mp3", "n.mp3"]
    _code, document = run_json(capsys, "rename", str(nameless), "-p", "%artist% - %title%")
    assert document["results"][0]["status"] == "skipped" and nameless.exists()


def test_move_into_folders_by_pattern_then_copy(song, tmp_path, capsys):
    lib = tmp_path / "library"
    lib.mkdir()
    pattern = "%artist%/%album%/%track% - %title%"
    _code, dry = run_json(capsys, "move", song, "-p", pattern, "--root", str(lib), "-n")
    assert dry["results"][0]["status"] == "planned" and os.path.exists(song)
    code, document = run_json(capsys, "move", song, "-p", pattern, "--root", str(lib))
    moved = lib / "Queen" / "A Night at the Opera" / "11 - Bohemian Rhapsody.mp3"
    assert code == 0 and document["results"][0]["status"] == "moved" and moved.exists() and not os.path.exists(song)
    _code, document = run_json(capsys, "move", str(moved), "-p", "Copies/%title%", "--root", str(lib), "--copy")
    assert document["results"][0]["status"] == "copied" and (lib / "Copies" / "Bohemian Rhapsody.mp3").exists() and moved.exists()


def test_move_takes_the_saved_library_root_and_checks_it(song, tmp_path, isolated):
    with pytest.raises(CliError, match="--root"):
        main(["move", song, "-p", "%title%"])
    with pytest.raises(CliError, match="does not exist"):
        main(["move", song, "-p", "%title%", "--root", str(tmp_path / "missing")])
    lib = tmp_path / "lib"
    lib.mkdir()
    isolated.settings.library_root = str(lib)
    assert main(["move", song, "-p", "%title%", "-n", "-q"]) == 0


# --- convert ---------------------------------------------------------------------------------------------


@requires_ffmpeg
def test_convert_makes_an_mp3_keeps_the_original_and_skips_existing(tmp_path, capsys):
    import wave

    wav = tmp_path / "tone.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(22050)
        w.writeframes(b"\x00\x10" * 22050)
    code, document = run_json(capsys, "convert", str(wav), "--bitrate", "128")
    assert code == 0 and document["results"][0]["status"] == "converted" and document["bitrate_kbps"] == 128
    assert (tmp_path / "tone.mp3").exists() and wav.exists()
    _code, document = run_json(capsys, "convert", str(wav), str(tmp_path / "tone.mp3"))
    assert [r["status"] for r in document["results"]] == ["skipped", "skipped"]


def test_convert_dry_run_and_trash_original_and_failure(tmp_path, capsys, monkeypatch):
    src = tmp_path / "a.flac"
    src.write_bytes(b"fLaC not really audio")
    _code, dry = run_json(capsys, "convert", str(src), "-n")
    assert dry["results"][0]["status"] == "planned" and not (tmp_path / "a.mp3").exists()
    code, document = run_json(capsys, "convert", str(src), "--trash-original")
    assert document["results"][0]["status"] == "failed" and code == 1 and src.exists()  # ffmpeg cannot read it: kept, no .mp3
    assert not (tmp_path / "a.mp3").exists()
    monkeypatch.setattr(cmd_files, "convert_to_mp3", lambda s, d, **k: (d.write_bytes(b"mp3"), (STATUS_OK, ""))[1])
    trashed = []
    monkeypatch.setattr(cmd_files, "move_to_trash", trashed.append)
    run(capsys, "convert", str(src), "--trash-original")
    assert trashed == [str(src)]


# --- redact ----------------------------------------------------------------------------------------------


def test_redact_lists_its_steps(capsys):
    code, document = run_json(capsys, "redact", "--list-steps")
    steps = {r["step"]: r["enabled"] for r in document["results"]}
    assert code == 0 and steps["integrity"] is True and steps["move_into_folders"] is False
    _code, document = run_json(capsys, "redact", "--list-steps", "--disable", "integrity", "--enable", "move_into_folders")
    steps = {r["step"]: r["enabled"] for r in document["results"]}
    assert steps["integrity"] is False and steps["move_into_folders"] is True


def test_redact_with_every_step_off_changes_nothing(song, capsys):
    before = open(song, "rb").read()
    steps = [r["step"] for r in run_json(capsys, "redact", "--list-steps")[1]["results"]]
    argv = ["redact", song, "--trash-dir", str(Path(song).parent / "trash")]
    for step in steps:
        argv += ["--disable", step]
    code, document = run_json(capsys, *argv)
    assert code == 0 and document["files"] == 1 and document["failed"] == 0
    assert document["results"][0]["status"] == "unchanged" and open(song, "rb").read() == before


def test_redact_rejects_unknown_steps_and_thresholds(song):
    with pytest.raises(CliError, match="unknown step"):
        main(["redact", song, "--disable", "nonsense"])
    with pytest.raises(CliError, match="threshold"):
        main(["redact", song, "--threshold", "250"])
    with pytest.raises(CliError, match="give the MP3"):
        main(["redact"])


def test_redact_reads_a_recipe_file(song, tmp_path, capsys):
    recipe = tmp_path / "recipe.json"
    recipe.write_text(json.dumps({"order": ["integrity"], "enabled": {"integrity": False}, "options": {}}), encoding="utf-8")
    _code, document = run_json(capsys, "redact", "--list-steps", "--recipe", str(recipe))
    assert {r["step"]: r["enabled"] for r in document["results"]}["integrity"] is False
    with pytest.raises(CliError, match="recipe file"):
        main(["redact", song, "--recipe", str(tmp_path / "missing.json")])


# --- analyze ---------------------------------------------------------------------------------------------


def test_analyze_reports_each_check_and_exits_1_on_a_problem(song, capsys, monkeypatch):
    def fake_integrity(files, progress=None, mp3val_path=None):
        for mp3 in files:
            mp3.integrity_status, mp3.integrity_message = STATUS_ERROR, "bad frame"

    def fake_bpm(files, progress=None, **kw):
        for mp3 in files:
            mp3.bpm, mp3.bpm_status, mp3.bpm_message = 120.0, STATUS_OK, ""

    monkeypatch.setattr(cmd_analyze, "run_integrity_check", fake_integrity)
    monkeypatch.setattr(cmd_analyze, "run_bpm_check", fake_bpm)
    code, document = run_json(capsys, "analyze", song, "--integrity", "--bpm")
    assert code == 1 and document["problems"] == 1 and document["checks"] == ["integrity", "bpm"]
    row = document["results"][0]
    assert row["problem"] is True and row["checks"]["integrity"]["message"] == "bad frame" and row["checks"]["bpm"]["bpm"] == 120.0
    code, out, _ = run(capsys, "analyze", song, "--bpm")
    assert code == 0 and "bpm: OK (120.0)" in out and "ok " in out


def test_analyze_saves_only_when_asked(song, capsys, monkeypatch):
    def fake_bpm(files, progress=None, **kw):
        for mp3 in files:
            mp3.bpm, mp3.bpm_status, mp3.bpm_message, mp3.dirty = 128.0, STATUS_OK, "", True

    monkeypatch.setattr(cmd_analyze, "run_bpm_check", fake_bpm)
    run(capsys, "analyze", song, "--bpm")
    assert read_tags(song).bpm is None
    code, document = run_json(capsys, "analyze", song, "--bpm", "--save")
    assert code == 0 and document["saved"] is True and read_tags(song).bpm == 128.0


def test_analyze_defaults_to_the_integrity_check(song, capsys, monkeypatch):
    seen = []
    monkeypatch.setattr(cmd_analyze, "run_integrity_check", lambda files, **k: seen.append(len(files)))
    _code, document = run_json(capsys, "analyze", song)
    assert seen == [1] and document["checks"] == ["integrity"]


# --- m4b -----------------------------------------------------------------------------------------------


def _books(tmp_path, folders):
    """Two tagged tone MP3s per folder."""
    paths = []
    for folder, (album, artist) in folders.items():
        (tmp_path / folder).mkdir()
        for number in (1, 2):
            path = _tone_mp3(tmp_path / folder / f"{number:02d}.mp3", 1.0)
            mp3 = read_tags(path)
            mp3.apply_tags({"title": f"Part {number}", "album": album, "artist": artist, "track": str(number)})
            from core.tag_writer import save_tags

            assert save_tags(mp3)
            paths.append(str(path))
    return paths


@requires_ffmpeg
def test_m4b_makes_one_audiobook_per_folder_beside_the_files(tmp_path, capsys):
    _books(tmp_path, {"one": ("Book One", "Ann"), "two": ("Book Two", "Bob")})
    code, document = run_json(capsys, "m4b", str(tmp_path), "--bitrate", "48")
    assert code == 0 and document["books"] == 2 and document["bitrate_kbps"] == 48
    assert [r["status"] for r in document["results"]] == ["created", "created"]
    assert (tmp_path / "one" / "Book One.m4b").exists() and (tmp_path / "two" / "Book Two.m4b").exists()


@requires_ffmpeg
def test_m4b_library_layout_sidecar_overrides_and_existing_skipped(tmp_path, capsys):
    _books(tmp_path, {"one": ("Book One", "Ann")})
    lib = tmp_path / "lib"
    code, document = run_json(
        capsys, "m4b", str(tmp_path / "one"), "--library", str(lib), "--series", "Saga", "--series-number", "2",
        "--narrator", "Reader", "--sidecar",
    )
    book = lib / "Ann" / "Saga" / "Book One"
    assert code == 0 and (book / "Book One.m4b").exists() and (book / "metadata.opf").exists()
    assert '<meta name="calibre:series" content="Saga" />' in (book / "metadata.opf").read_text(encoding="utf-8")
    _code, again = run_json(capsys, "m4b", str(tmp_path / "one"), "--library", str(lib), "--series", "Saga")
    assert again["results"][0]["status"] == "skipped"
    _code, replaced = run_json(capsys, "m4b", str(tmp_path / "one"), "--library", str(lib), "--series", "Saga", "--replace")
    assert replaced["results"][0]["status"] == "created"


@requires_ffmpeg
def test_m4b_dry_run_into_and_file(tmp_path, capsys):
    _books(tmp_path, {"one": ("Book One", "Ann")})
    _code, dry = run_json(capsys, "m4b", str(tmp_path / "one"), "-n")
    assert dry["results"][0]["status"] == "planned" and not (tmp_path / "one" / "Book One.m4b").exists()
    out_dir = tmp_path / "out"
    run(capsys, "m4b", str(tmp_path / "one"), "--into", str(out_dir), "--title", "Chosen Title")
    assert (out_dir / "Chosen Title.m4b").exists()
    target = tmp_path / "exact" / "mine"
    run(capsys, "m4b", str(tmp_path / "one"), "--file", str(target))
    assert (tmp_path / "exact" / "mine.m4b").exists()


def test_m4b_option_errors(tmp_path):
    for folder in ("a", "b"):
        (tmp_path / folder).mkdir()
        shutil.copyfile(FIXTURE, tmp_path / folder / "1.mp3")
    with pytest.raises(CliError, match="one per folder"):
        main(["m4b", str(tmp_path), "--file", str(tmp_path / "x.m4b")])
    with pytest.raises(CliError, match="JPEG or PNG"):
        notimage = tmp_path / "c.jpg"
        notimage.write_bytes(b"not an image")
        main(["m4b", str(tmp_path / "a"), "--cover", str(notimage)])
    with pytest.raises(SystemExit):  # --into and --library together
        main(["m4b", str(tmp_path / "a"), "--into", "x", "--library", "y"])


def _canned(score_title="Book One", **overrides):
    match = BookMatch(
        source="Audible", title=score_title, authors=["Ann"], narrators=["Sam Reader"], publisher="Pub House",
        year="2008", language="eng", series="Sword of Truth", series_index="1", description="A story.",
        cover_url="https://img/c.jpg", runtime_minutes=0, asin="B0TEST",
    )
    for key, value in overrides.items():
        setattr(match, key, value)
    return LookupOutcome([match])


def _tagged_book(tmp_path, name="one", **tags):
    """A one-file book folder tagged like Book One by Ann, plus any extra tags."""
    folder = tmp_path / name
    folder.mkdir()
    shutil.copyfile(FIXTURE, folder / "1.mp3")
    m = read_tags(folder / "1.mp3")
    m.apply_tags({"album": "Book One", "artist": "Ann", "title": "Part 1", "track": "1", **tags})
    from core.tag_writer import save_tags

    save_tags(m)
    return folder


def _capture_spec(monkeypatch):
    captured = {}
    monkeypatch.setattr(cmd_m4b, "build_m4b", lambda spec, **k: captured.setdefault("spec", spec) and SimpleNamespace(
        status=STATUS_OK, notes=[], message="", duration_seconds=60.0))
    return captured


def test_m4b_lookup_fills_in_only_what_is_missing_and_explicit_options_win(tmp_path, capsys, monkeypatch):
    folder = _tagged_book(tmp_path, publisher="Own Pub")
    monkeypatch.setattr(cmd_m4b, "search", lambda *a, **k: _canned(authors=["Someone Else"]))
    monkeypatch.setattr(cmd_m4b, "download_cover", lambda url, fetch=None: (b"\xff\xd8\xff\xe0jpeg", "image/jpeg"))
    code, document = run_json(capsys, "m4b", str(folder), "--lookup", "--min-score", "1", "--dry-run", "--series", "My Series")
    row = document["results"][0]
    assert row["match"]["source"] == "Audible" and row["match"]["asin"] == "B0TEST"
    assert set(row["match"]["filled"]) >= {"narrator", "year", "description", "cover"}
    captured = _capture_spec(monkeypatch)
    run(capsys, "m4b", str(folder), "--lookup", "--min-score", "1", "--series", "My Series", "--replace")
    spec = captured["spec"]
    assert spec.narrator == "Sam Reader" and spec.description == "A story." and spec.year == "2008"
    assert spec.publisher == "Own Pub"  # the files' own value is never replaced
    assert spec.title == "Book One" and spec.author == "Ann"  # nor the title or the author
    assert spec.series == "My Series" and spec.series_index == ""  # the user's series wins; its number is not another series'
    assert spec.cover == b"\xff\xd8\xff\xe0jpeg"  # the files had none


def test_m4b_lookup_takes_the_series_and_its_number_together_and_never_replaces_the_files_cover(tmp_path, capsys, monkeypatch):
    folder = _tagged_book(tmp_path)
    monkeypatch.setattr(cmd_m4b, "search", lambda *a, **k: _canned())
    downloads = []
    monkeypatch.setattr(cmd_m4b, "download_cover", lambda url, fetch=None: downloads.append(url) or (b"\xff\xd8\xff\xe0new", "image/jpeg"))
    own = (b"\xff\xd8\xff\xe0own", "image/jpeg")
    monkeypatch.setattr(cmd_m4b, "defaults_for", lambda book: BookDefaults(
        title="Book One", author="Ann", year="", cover=own[0], cover_mime=own[1]))
    captured = _capture_spec(monkeypatch)
    run(capsys, "m4b", str(folder), "--lookup", "--replace")
    spec = captured["spec"]
    assert spec.series == "Sword of Truth" and spec.series_index == "1"
    assert spec.cover == own[0] and downloads == []  # the files' own cover stays, and nothing is downloaded for nothing


def test_m4b_lookup_notes_reach_the_json_document(tmp_path, capsys, monkeypatch):
    folder = _tagged_book(tmp_path)
    outcome = _canned()
    outcome.notes = ["Audible: 503 Service Unavailable from api.audible.com"]
    monkeypatch.setattr(cmd_m4b, "search", lambda *a, **k: outcome)
    monkeypatch.setattr(cmd_m4b, "download_cover", lambda url, fetch=None: (b"\xff\xd8\xff\xe0jpeg", "image/jpeg"))
    _code, document = run_json(capsys, "m4b", str(folder), "--lookup", "-n")
    assert any("503" in n for n in document["results"][0]["notes"])


def test_m4b_lookup_below_the_minimum_score_is_not_used(tmp_path, capsys, monkeypatch):
    (tmp_path / "one").mkdir()
    shutil.copyfile(FIXTURE, tmp_path / "one" / "1.mp3")
    monkeypatch.setattr(cmd_m4b, "search", lambda *a, **k: _canned(score_title="Something Else Entirely"))
    code, document = run_json(capsys, "m4b", str(tmp_path / "one"), "--lookup", "-n", "--title", "Book One")
    row = document["results"][0]
    assert code == 0 and any("below --min-score" in n for n in row["notes"]) and row["match"] is None


# --- one exe ------------------------------------------------------------------------------------------


def test_a_command_name_starts_the_command_line_and_a_path_starts_the_window():
    from mp3cli import COMMANDS, cli_requested

    assert set(COMMANDS) == {"info", "set", "rename", "move", "convert", "redact", "analyze", "m4b"}
    assert cli_requested(["mp3redactor.exe", "info", "x.mp3"]) and cli_requested(["mp3redactor.exe", "--version"])
    assert not cli_requested(["mp3redactor.exe"]) and not cli_requested(["mp3redactor.exe", "D:/Music/a.mp3"])


def test_the_app_entry_point_runs_the_command_line_without_a_window(song, monkeypatch, capsys):
    import main as app

    monkeypatch.setattr(app.sys, "argv", ["mp3redactor", "info", song, "--json"])
    monkeypatch.setattr(app, "run_app", lambda **kw: pytest.fail("the window was started"))
    assert app.main() == 0
    assert json.loads(capsys.readouterr().out)["results"][0]["fields"]["artist"] == "Queen"


def test_the_app_entry_point_still_starts_the_window_for_no_command(monkeypatch):
    import main as app

    started = []
    monkeypatch.setattr(app.sys, "argv", ["mp3redactor"])
    monkeypatch.setattr(app, "run_app", lambda **kw: started.append(kw["app_name"]) or 0)
    assert app.main() == 0 and started


def test_output_writes_the_result_to_a_file_for_scripts(song, tmp_path, capsys):
    target = tmp_path / "result.json"
    assert main(["info", song, "--json", "--output", str(target)]) == 0 and capsys.readouterr().out == ""
    assert json.loads(target.read_text(encoding="utf-8"))["results"][0]["fields"]["title"] == "Bohemian Rhapsody"


# --- the documentation covers every option --------------------------------------------------------------


def _readme_cli_section() -> str:
    text = (Path(__file__).parent.parent / "README.md").read_text(encoding="utf-8")
    start = text.index("## Command line")
    end = text.find("\n## ", start + 5)
    return text[start:end if end != -1 else None]


def test_the_readme_documents_every_command_option_and_field():
    import argparse

    from mp3cli.fields import FIELD_KEYS
    from mp3cli.main import build_parser

    section = _readme_cli_section()
    parser = build_parser()
    missing = [o for action in parser._actions for o in action.option_strings if o not in section]
    subparsers = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    for name, sub in subparsers.choices.items():
        if f"### {name}" not in section:
            missing.append(f"### {name}")
        missing += [f"{name} {o}" for action in sub._actions for o in action.option_strings if o not in section]
    from core.redact_steps import build_catalogue

    missing += [f"step {s.key}" for s in build_catalogue(Settings()) if not s.hidden and s.key not in section]
    missing += [f"field {key}" for key in FIELD_KEYS if key not in section]
    assert missing == [], f"the README's Command line section does not mention: {missing}"


def test_the_readme_lists_the_exit_codes_and_the_scripting_ways():
    section = _readme_cli_section()
    for code in ("| 0 |", "| 1 |", "| 2 |", "| 70 |", "| 130 |"):
        assert code in section
    for way in ("start /wait", "Start-Process", "Out-Null", "--output"):
        assert way in section


# --- second review -------------------------------------------------------------------------------------------


@requires_ffmpeg
def test_m4b_two_books_for_the_same_file_are_not_both_made(tmp_path, capsys):
    _books(tmp_path, {"one": ("Same Title", "Ann"), "two": ("Same Title", "Bob")})
    out_dir = tmp_path / "out"
    _code, plan = run_json(capsys, "m4b", str(tmp_path / "one"), str(tmp_path / "two"), "--into", str(out_dir), "-n")
    assert [r["status"] for r in plan["results"]] == ["planned", "skipped"]
    code, real = run_json(capsys, "m4b", str(tmp_path / "one"), str(tmp_path / "two"), "--into", str(out_dir))
    assert [r["status"] for r in real["results"]] == ["created", "skipped"]
    assert "same file" in real["results"][1]["message"] and sorted(p.name for p in out_dir.iterdir()) == ["Same Title.m4b"]


def test_m4b_sidecar_with_into_for_several_books_is_refused(tmp_path):
    for folder in ("a", "b"):
        (tmp_path / folder).mkdir()
        shutil.copyfile(FIXTURE, tmp_path / folder / "1.mp3")
    with pytest.raises(CliError, match="same folder"):
        main(["m4b", str(tmp_path), "--into", str(tmp_path / "out"), "--sidecar"])


@requires_ffmpeg
def test_m4b_replace_sends_the_old_audiobook_and_sidecars_to_the_trash_dir(tmp_path, capsys):
    _books(tmp_path, {"one": ("Book One", "Ann")})
    run(capsys, "m4b", str(tmp_path / "one"), "--sidecar")
    book = tmp_path / "one" / "Book One.m4b"
    old = book.read_bytes()
    bin_dir = tmp_path / "bin"
    code, document = run_json(
        capsys, "m4b", str(tmp_path / "one"), "--sidecar", "--replace", "--trash-dir", str(bin_dir), "--narrator", "New Reader",
    )
    assert code == 0 and document["results"][0]["status"] == "created"
    assert sorted(p.name for p in bin_dir.iterdir()) == [".Book One.replaced.m4b", "metadata.opf"]
    assert (bin_dir / ".Book One.replaced.m4b").read_bytes() == old  # the old audiobook, kept
    assert "New Reader" in (tmp_path / "one" / "metadata.opf").read_text(encoding="utf-8")
    assert not [p for p in (tmp_path / "one").iterdir() if p.name.startswith(".")]  # no leftovers beside the book


@requires_ffmpeg
def test_m4b_replace_with_an_unusable_trash_dir_keeps_the_old_audiobook(tmp_path, capsys):
    _books(tmp_path, {"one": ("Book One", "Ann")})
    run(capsys, "m4b", str(tmp_path / "one"))
    blocker = tmp_path / "bin"
    blocker.write_bytes(b"a file, not a folder")
    code, document = run_json(capsys, "m4b", str(tmp_path / "one"), "--replace", "--trash-dir", str(blocker))
    row = document["results"][0]
    assert code == 0 and row["status"] == "created" and any("kept as Book One (previous).m4b" in n for n in row["notes"])
    assert (tmp_path / "one" / "Book One (previous).m4b").exists() and (tmp_path / "one" / "Book One.m4b").exists()


@pytest.mark.parametrize("value", ["5", "-1", "nan", "abc"])
def test_m4b_min_score_must_be_a_number_from_0_to_4(tmp_path, value):
    with pytest.raises(SystemExit):
        main(["m4b", str(tmp_path), "--lookup", "--min-score", value])


@pytest.mark.parametrize("argv,message", [
    (["-s", "title=bad\x01char"], "control character"),
    (["-s", "title=two\nlines"], "control character"),
    (["-s", "track=٣"], "number or number/total"),
    (["-s", "language=English"], "three-letter"),
    (["-s", "itunesadvisory=3"], "itunesadvisory must be 0"),
])
def test_set_refuses_values_no_id3_tag_should_hold(song, argv, message):
    before = open(song, "rb").read()
    with pytest.raises(CliError, match=message):
        main(["set", song, *argv])
    assert open(song, "rb").read() == before


def test_set_keeps_line_breaks_in_a_comment_and_lowercases_a_language(song, capsys):
    code, document = run_json(capsys, "set", song, "-s", "comment=line one\nline two", "-s", "language=ENG")
    assert code == 0 and document["results"][0]["status"] == "changed"
    tags = read_tags(song)
    assert tags.comment == "line one\nline two" and tags.language == "eng"


def test_rename_defaults_come_from_the_saved_settings(song, capsys, monkeypatch):
    monkeypatch.setattr(cmd_files, "settings", lambda: Settings(rename_zero_pad=True, rename_zero_pad_width=3))
    run_json(capsys, "set", song, "-s", "track=4")
    _code, document = run_json(capsys, "rename", song, "-p", "%track% %title%", "-n")
    assert os.path.basename(document["results"][0]["new_path"]).startswith("004 ")
    _code, document = run_json(capsys, "rename", song, "-p", "%track% %title%", "--zero-pad", "0", "-n")
    assert os.path.basename(document["results"][0]["new_path"]).startswith("4 ")
