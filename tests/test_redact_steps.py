"""Redact steps (core/redact_steps.py) with mocked tools and network, driven
through the real engine (run_recipe_on_item) on copies of tests/fixtures/tiny.mp3."""

import os
import shutil
from pathlib import Path

import pytest

import core.redact_steps as rs
from core.mp3_file import (
    MP3File, STATUS_ERROR, STATUS_OK, STATUS_TOOL_ERROR, STATUS_TOOL_MISSING, STATUS_WARNING,
)
from core.musicbrainz_lookup import MusicBrainzError, Release, ReleaseTrack, match_release
from core.settings import Settings
from core.tag_reader import load_tags
from redactor_common.core.pipeline import FileStatus, Recipe, run_recipe_on_item

FIXTURE = Path(__file__).parent / "fixtures" / "tiny.mp3"


def make_file(tmp_path, name="a.mp3", **tags):
    path = tmp_path / name
    shutil.copyfile(FIXTURE, path)
    mp3 = MP3File(path=path)
    load_tags(mp3)
    for key, value in tags.items():
        setattr(mp3, key, value)
    if tags:  # tags given: write them to disk so they are the file's saved state
        from core.tag_writer import save_tags
        assert save_tags(mp3)
    return mp3


def run(mp3, only, recipe_opts=None, threshold=0.9, settings=None, items=None, rename_log=None):
    """Runs just the `only` steps (plus the save stage) on one file; returns (report entry, env)."""
    settings = settings or Settings()
    env = rs.RedactEnv(settings, rename_log=rename_log)
    env.begin(items or [mp3])
    catalogue = rs.build_catalogue(settings)
    recipe = Recipe.default_for(catalogue)
    recipe.enabled = {s.key: s.key in only for s in catalogue}
    recipe.options = {k: v for k, v in (recipe_opts or {}).items()}
    recipe.confidence_threshold = threshold
    resolved = recipe.resolve(catalogue)
    entry = run_recipe_on_item(
        mp3, resolved, threshold, lambda m: rs.Mp3Ctx(m, env), lambda m: m.filename,
        finalize=rs.save_stage, finalize_label=rs.FINALIZE_LABEL,
    )
    return entry, env


def notes_of(entry):
    return " | ".join(entry.notes)


def leftovers(folder):
    return [p.name for p in folder.iterdir() if p.name.startswith(".mp3redactor-redact-")]


# --- integrity ------------------------------------------------------------------


def test_integrity_ok_is_stamped_in_the_file_and_original_trashed(tmp_path, monkeypatch, recycle_bin):
    mp3 = make_file(tmp_path)
    before = mp3.path.read_bytes()
    monkeypatch.setattr(rs, "check_integrity", lambda p, override_path=None: (STATUS_OK, ""))
    entry, _env = run(mp3, {"integrity"})
    assert entry.status is FileStatus.CHANGED
    assert any("saved in place" in a for a in entry.applied)
    assert len(recycle_bin.trashed) == 1
    assert mp3.path.read_bytes() != before  # rewritten in place
    fresh = MP3File(path=mp3.path)
    load_tags(fresh)
    assert not fresh.load_error and fresh.integrity_stamp.startswith("OK;")
    assert not mp3.dirty and mp3.integrity_stamp == fresh.integrity_stamp
    assert leftovers(tmp_path) == []


def test_integrity_fix_runs_on_the_scratch_copy_then_rechecks(tmp_path, monkeypatch):
    mp3 = make_file(tmp_path)
    calls = []
    results = iter([(STATUS_WARNING, "WARNING: a"), (STATUS_OK, "")])

    def check(path, override_path=None):
        calls.append(("check", str(path)))
        return next(results)

    def fix(path, delete_backup=False, override_path=None):
        calls.append(("fix", str(path)))
        return STATUS_OK, ""

    monkeypatch.setattr(rs, "check_integrity", check)
    monkeypatch.setattr(rs, "fix_integrity", fix)
    entry, _ = run(mp3, {"integrity"})
    assert [c[0] for c in calls] == ["check", "fix", "check"]
    assert all(Path(c[1]) != mp3.path for c in calls)  # never the original
    assert any("fixed 1 problem" in a for a in entry.applied)
    assert mp3.integrity_status == STATUS_OK


def test_fix_option_off_does_not_fix(tmp_path, monkeypatch):
    mp3 = make_file(tmp_path)
    monkeypatch.setattr(rs, "check_integrity", lambda p, override_path=None: (STATUS_WARNING, "WARNING: a"))
    monkeypatch.setattr(rs, "fix_integrity", lambda *a, **k: pytest.fail("must not fix"))
    entry, _ = run(mp3, {"integrity"}, {"integrity": {"fix": False}})
    assert any("not fixed" in a for a in entry.applied)


def test_tool_error_is_never_fixed_or_stamped(tmp_path, monkeypatch, recycle_bin):
    mp3 = make_file(tmp_path)
    before = mp3.path.read_bytes()
    monkeypatch.setattr(rs, "check_integrity", lambda p, override_path=None: (STATUS_TOOL_ERROR, "timed out"))
    monkeypatch.setattr(rs, "fix_integrity", lambda *a, **k: pytest.fail("must not fix on a tool error"))
    entry, _ = run(mp3, {"integrity"})
    assert entry.status is FileStatus.FAILED and "timed out" in entry.failures[0]
    assert mp3.integrity_stamp == ""
    assert mp3.path.read_bytes() == before and recycle_bin.trashed == []


def test_tool_missing_is_nothing_with_a_note(tmp_path, monkeypatch, recycle_bin):
    mp3 = make_file(tmp_path)
    monkeypatch.setattr(rs, "check_integrity", lambda p, override_path=None: (STATUS_TOOL_MISSING, "mp3val not found"))
    monkeypatch.setattr(rs, "fix_integrity", lambda *a, **k: pytest.fail("must not fix"))
    entry, _env = run(mp3, {"integrity"})
    assert entry.status is FileStatus.UNCHANGED and not entry.failures
    assert "mp3val not found" in notes_of(entry) and recycle_bin.trashed == []


def test_failed_fix_restores_the_scratch_copy_and_reports(tmp_path, monkeypatch, recycle_bin):
    mp3 = make_file(tmp_path)
    monkeypatch.setattr(rs, "check_integrity", lambda p, override_path=None: (STATUS_ERROR, "ERROR: bad"))

    def fix(path, delete_backup=False, override_path=None):
        Path(path).write_bytes(b"garbage")  # crashed midway
        return STATUS_TOOL_ERROR, "crashed"

    monkeypatch.setattr(rs, "fix_integrity", fix)
    entry, _ = run(mp3, {"integrity"})
    assert any("left as it was" in f for f in entry.failures)
    # the stamp from the pre-fix check (ERROR) was still saved, into an intact file
    fresh = MP3File(path=mp3.path)
    load_tags(fresh)
    assert not fresh.load_error and fresh.integrity_stamp.startswith("ERROR;") and fresh.duration_seconds


@pytest.mark.parametrize("keep_backups", [True, False])
def test_mp3val_backup_follows_the_setting(tmp_path, monkeypatch, keep_backups):
    mp3 = make_file(tmp_path)
    results = iter([(STATUS_WARNING, "WARNING: a"), (STATUS_OK, "")])
    monkeypatch.setattr(rs, "check_integrity", lambda p, override_path=None: next(results))

    def fix(path, delete_backup=False, override_path=None):
        assert delete_backup == (not keep_backups)
        if not delete_backup:
            shutil.copyfile(path, str(path) + ".bak")
        return STATUS_OK, ""

    monkeypatch.setattr(rs, "fix_integrity", fix)
    run(mp3, {"integrity"}, settings=Settings(delete_backup_after_fix=not keep_backups))
    assert (tmp_path / "a.mp3.bak").exists() == keep_backups
    assert leftovers(tmp_path) == [] and not [p for p in tmp_path.iterdir() if p.name.endswith(".tmp.bak")]


# --- bpm / key / loudness / deep check -----------------------------------------


def test_bpm_key_loudness_are_written(tmp_path, monkeypatch):
    mp3 = make_file(tmp_path)
    monkeypatch.setattr(rs, "detect_bpm", lambda p: (127.6, STATUS_OK, ""))
    monkeypatch.setattr(rs, "detect_key", lambda p, override_path=None: ("Am", STATUS_OK, ""))
    monkeypatch.setattr(rs, "measure_loudness", lambda p, override_path=None: (-14.0, -4.0, STATUS_OK, ""))
    entry, _ = run(mp3, {"bpm", "key", "loudness"})
    assert entry.status is FileStatus.CHANGED
    from mutagen.id3 import ID3
    tags = ID3(str(mp3.path))
    assert str(tags["TBPM"].text[0]) == "128" and str(tags["TKEY"].text[0]) == "Am"
    assert "-4.00 dB" in str(tags["TXXX:REPLAYGAIN_TRACK_GAIN"].text[0])


def test_existing_bpm_and_key_tags_are_kept_by_default(tmp_path, monkeypatch):
    mp3 = make_file(tmp_path)
    from mutagen.id3 import ID3, TBPM, TKEY
    tags = ID3(str(mp3.path))
    tags.add(TBPM(encoding=3, text="90"))
    tags.add(TKEY(encoding=3, text="C"))
    tags.save(str(mp3.path))
    monkeypatch.setattr(rs, "detect_bpm", lambda p: pytest.fail("must not measure"))
    monkeypatch.setattr(rs, "detect_key", lambda p, override_path=None: pytest.fail("must not detect"))
    entry, _ = run(mp3, {"bpm", "key"})
    assert entry.status is FileStatus.UNCHANGED


def test_missing_and_failed_tools_do_not_touch_the_file(tmp_path, monkeypatch, recycle_bin):
    mp3 = make_file(tmp_path)
    monkeypatch.setattr(rs, "detect_bpm", lambda p: (None, STATUS_TOOL_MISSING, "aubio is not installed"))
    monkeypatch.setattr(rs, "detect_key", lambda p, override_path=None: ("", STATUS_ERROR, "boom"))
    monkeypatch.setattr(rs, "measure_loudness", lambda p, override_path=None: (None, None, STATUS_TOOL_MISSING, "ffmpeg not found"))
    entry, _env = run(mp3, {"bpm", "key", "loudness"})
    assert entry.status is FileStatus.FAILED and len(entry.failures) == 1  # only the key tool failing
    assert recycle_bin.trashed == []
    assert "aubio is not installed" in notes_of(entry) and "ffmpeg not found" in notes_of(entry)


def test_deep_check_is_off_by_default_and_stamps_when_on(tmp_path, monkeypatch):
    assert not rs.DeepCheckStep().default_enabled
    mp3 = make_file(tmp_path)
    monkeypatch.setattr(rs, "deep_check_integrity", lambda p, override_path=None: (STATUS_ERROR, "decode error"))
    monkeypatch.setattr(rs, "probe_format", lambda p, override_path=None: ("LAME", 44100, 2, STATUS_OK, ""))
    entry, _ = run(mp3, {"deep_check"})
    assert entry.status is FileStatus.CHANGED
    assert mp3.deep_check_stamp.startswith("ERROR;") and mp3.sample_rate_hz == 44100


def test_deep_check_tool_error_is_not_stamped(tmp_path, monkeypatch):
    mp3 = make_file(tmp_path)
    monkeypatch.setattr(rs, "deep_check_integrity", lambda p, override_path=None: (STATUS_TOOL_ERROR, "timeout"))
    entry, _ = run(mp3, {"deep_check"})
    assert entry.status is FileStatus.FAILED and mp3.deep_check_stamp == ""


# --- tag lookup -------------------------------------------------------------------


def _release():
    return Release(
        id="rel1", title="Album", artist="Artist", date="1999-05-01",
        tracks=[ReleaseTrack(1, 1, "Song", 0.4, "rec1", "Artist")], track_count=1,
    )


def _patch_musicbrainz(monkeypatch):
    monkeypatch.setattr(rs, "find_tool", lambda *a, **k: None)
    monkeypatch.setattr(rs, "find_album", lambda facts, query: [match_release(facts, _release())])


def test_exact_tag_match_fills_only_missing_fields(tmp_path, monkeypatch):
    _patch_musicbrainz(monkeypatch)
    mp3 = make_file(tmp_path, title="Song", album="Album", artist="Artist", genre="Mine")
    entry, _ = run(mp3, {"tags"})
    assert entry.status is FileStatus.CHANGED
    assert mp3.track == "1" and mp3.year == "1999" and mp3.albumartist == "Artist"
    assert mp3.musicbrainz_albumid == "rel1" and mp3.musicbrainz_trackid == "rec1"
    assert mp3.genre == "Mine" and mp3.title == "Song"
    assert not mp3.dirty


def test_existing_values_are_not_overwritten_unless_asked(tmp_path, monkeypatch):
    _patch_musicbrainz(monkeypatch)
    mp3 = make_file(tmp_path, title="Song", album="Album", artist="Artist", year="1985")
    run(mp3, {"tags"})
    assert mp3.year == "1985"
    run(mp3, {"tags"}, {"tags": {"overwrite": True}})
    assert mp3.year == "1999"


def test_complete_tags_make_no_network_request(tmp_path, monkeypatch):
    monkeypatch.setattr(rs, "find_album", lambda *a: pytest.fail("no lookup needed"))
    mp3 = make_file(
        tmp_path, title="Song", album="Album", artist="Artist", albumartist="Artist", track="1", year="1999"
    )
    entry, _ = run(mp3, {"tags"})
    assert entry.status is FileStatus.UNCHANGED


def test_weaker_match_routes_by_threshold(tmp_path, monkeypatch):
    _patch_musicbrainz(monkeypatch)
    # no artist tag: the match is about 85% -> applied at 0.5, reviewed at 0.9 and 0.95
    low = make_file(tmp_path, "low.mp3", title="Song", album="Album")
    entry, _ = run(low, {"tags"}, threshold=0.5)
    assert entry.status is FileStatus.CHANGED and low.track == "1"
    assert any("auto-applied at" in a for a in entry.applied)
    for threshold in (0.9, 0.95):
        file = make_file(tmp_path, f"t{threshold}.mp3", title="Song", album="Album")
        entry, _ = run(file, {"tags"}, threshold=threshold)
        assert entry.status is FileStatus.NEEDS_REVIEW and not entry.applied
        assert 0.8 < entry.review[0].confidence < 0.9
        assert file.track == ""
    assert "track=" in str(entry.review[0].value)


def test_album_mismatch_is_low_confidence(tmp_path, monkeypatch):
    _patch_musicbrainz(monkeypatch)
    mp3 = make_file(tmp_path, title="Song", album="Something Else", artist="Artist")
    entry, _ = run(mp3, {"tags"})
    assert entry.status is FileStatus.NEEDS_REVIEW and entry.review[0].confidence < 0.7


def test_fingerprint_match_uses_the_acoustid_score(tmp_path, monkeypatch):
    from core.acoustid_lookup import RecordingHit

    mp3 = make_file(tmp_path, title="junk", album="junk")
    monkeypatch.setattr(rs, "find_tool", lambda *a, **k: Path("fpcalc"))
    monkeypatch.setattr(
        rs, "identify_files", lambda paths, fpcalc: ([[RecordingHit("rec1", 0.97, {"rel1"})]], [])
    )
    monkeypatch.setattr(rs, "find_album", lambda facts, query: [])
    monkeypatch.setattr(rs, "candidate_releases", lambda hits, limit=5: ["rel1"])
    monkeypatch.setattr(rs, "find_album_by_recordings", lambda facts, ids: [match_release(facts, _release())])
    entry, _ = run(mp3, {"tags"}, threshold=0.99)
    assert entry.status is FileStatus.NEEDS_REVIEW
    assert entry.review[0].confidence == pytest.approx(0.97) and "AcoustID" in entry.review[0].reason
    entry, _ = run(mp3, {"tags"}, threshold=0.95)
    assert entry.status is FileStatus.CHANGED and mp3.title == "junk"  # existing title kept
    assert mp3.musicbrainz_trackid == "rec1"


def test_offline_is_nothing_with_a_note_not_a_failure(tmp_path, monkeypatch, recycle_bin):
    monkeypatch.setattr(rs, "find_tool", lambda *a, **k: None)

    def offline(facts, query):
        raise MusicBrainzError("Could not reach MusicBrainz")

    monkeypatch.setattr(rs, "find_album", offline)
    mp3 = make_file(tmp_path, title="Song", album="Album")
    entry, _env = run(mp3, {"tags"})
    assert entry.status is FileStatus.UNCHANGED and not entry.failures
    assert "Could not reach MusicBrainz" in notes_of(entry) and recycle_bin.trashed == []


def test_album_lookup_happens_once_per_folder(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(rs, "find_tool", lambda *a, **k: None)
    monkeypatch.setattr(rs, "find_album", lambda facts, query: calls.append(len(facts)) or [])
    a = make_file(tmp_path, "a.mp3", title="One", album="Album")
    b = make_file(tmp_path, "b.mp3", title="Two", album="Album")
    env = rs.RedactEnv(Settings())
    env.begin([a, b])
    catalogue = rs.build_catalogue()
    recipe = Recipe.default_for(catalogue)
    recipe.enabled = {s.key: s.key == "tags" for s in catalogue}
    resolved = recipe.resolve(catalogue)
    for m in (a, b):
        run_recipe_on_item(m, resolved, 0.9, lambda x: rs.Mp3Ctx(x, env))
    assert calls == [2]


# --- cover ------------------------------------------------------------------------


JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


def test_folder_image_is_embedded_at_default_threshold(tmp_path):
    (tmp_path / "cover.jpg").write_bytes(JPEG)
    mp3 = make_file(tmp_path)
    entry, _ = run(mp3, {"cover"})
    assert entry.status is FileStatus.CHANGED and mp3.has_cover
    from core.cover_art import read_cover
    assert read_cover(mp3.path)[0] == JPEG


def test_cover_below_threshold_goes_to_review(tmp_path):
    (tmp_path / "cover.jpg").write_bytes(JPEG)
    mp3 = make_file(tmp_path)
    before = mp3.path.read_bytes()
    entry, _ = run(mp3, {"cover"}, threshold=0.95)
    assert entry.status is FileStatus.NEEDS_REVIEW and mp3.path.read_bytes() == before


def test_existing_cover_is_left_alone(tmp_path):
    (tmp_path / "cover.jpg").write_bytes(JPEG)
    mp3 = make_file(tmp_path)
    mp3.set_cover(JPEG, "image/jpeg")
    from core.tag_writer import save_tags
    assert save_tags(mp3)
    entry, _ = run(mp3, {"cover"})
    assert entry.status is FileStatus.UNCHANGED


def test_online_cover_needs_a_release_id(tmp_path, monkeypatch):
    asked = []
    monkeypatch.setattr(rs, "fetch_front_cover", lambda rid, fetch=None, size=250: asked.append((rid, size)) or JPEG)
    plain = make_file(tmp_path, "p.mp3")
    assert run(plain, {"cover"})[0].status is FileStatus.UNCHANGED and asked == []
    tagged = make_file(tmp_path, "t.mp3", musicbrainz_albumid="rel1")
    entry, _ = run(tagged, {"cover"})
    assert entry.status is FileStatus.CHANGED and asked == [("rel1", 500)]


# --- rename -----------------------------------------------------------------------


class Log:
    def __init__(self):
        self.recorded = []

    def record(self, label, renames):
        self.recorded.append((label, renames))


def test_rename_default_depends_on_a_saved_pattern():
    assert not rs.RenameStep(Settings()).default_enabled
    assert rs.RenameStep(Settings(pattern_history=["%title%"])).default_enabled


def test_rename_pattern_is_a_dedicated_setting_history_is_only_a_first_run_default():
    # first run: the most recent history entry is what an empty (following) pattern resolves to
    step = rs.RenameStep(Settings(pattern_history=["%title%"]))
    assert step.options[0].key == "pattern" and step.options[0].kind == "str" and step.options[0].default == ""
    assert step.options[0].fallback() == "%title%"
    # once saved, Parse Filename pushing another pattern into the history no longer changes it
    settings = Settings(pattern_history=["%artist%", "%title%"], rename_pattern="%track% %title%")
    step = rs.RenameStep(settings)
    assert step.options[0].fallback() == "%track% %title%" and step.default_enabled
    assert not rs.RenameStep(Settings()).default_enabled


def test_rename_uses_the_recipe_option_over_the_saved_pattern(tmp_path):
    mp3 = make_file(tmp_path, title="Song", artist="Band")
    settings = Settings(rename_pattern="%title%")
    entry, _ = run(mp3, {"rename"}, {"rename": {"pattern": "%artist% - %title%"}}, settings=settings)
    assert mp3.path.name == "Band - Song.mp3", entry.failures


def test_rename_uses_the_saved_pattern_and_logs(tmp_path):
    settings = Settings(pattern_history=["%track% - %title%"], rename_zero_pad=True, rename_zero_pad_width=2)
    mp3 = make_file(tmp_path, title="Song", track="3")
    log = Log()
    entry, _ = run(mp3, {"rename"}, settings=settings, rename_log=log)
    assert mp3.path.name == "03 - Song.mp3" and mp3.path.exists()
    assert not (tmp_path / "a.mp3").exists()
    assert log.recorded == [("Redact", [(str(tmp_path / "a.mp3"), str(mp3.path))])]
    assert any("renamed to" in a for a in entry.applied)


def test_rename_never_clobbers_and_skips_when_already_named(tmp_path):
    settings = Settings(pattern_history=["%title%"])
    (tmp_path / "Song.mp3").write_bytes(b"someone else's file")
    mp3 = make_file(tmp_path, title="Song")
    run(mp3, {"rename"}, settings=settings)
    assert mp3.path.name == "Song (2).mp3"
    assert (tmp_path / "Song.mp3").read_bytes() == b"someone else's file"
    entry, _ = run(mp3, {"rename"}, settings=Settings(pattern_history=["%title% (2)"]))
    assert entry.status is FileStatus.UNCHANGED  # already matches: own path is no collision


def test_rename_without_a_pattern_or_title_is_a_noted_nothing(tmp_path):
    mp3 = make_file(tmp_path, title="Song")
    entry, _env = run(mp3, {"rename"})
    assert entry.status is FileStatus.UNCHANGED and "no rename pattern" in notes_of(entry)
    untitled = make_file(tmp_path, "u.mp3")
    entry, _env = run(untitled, {"rename"}, settings=Settings(pattern_history=["%title%"]))
    assert untitled.path.name == "u.mp3" and "no title" in notes_of(entry)


# --- move into folders ------------------------------------------------------------


def _library(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    return root


def test_move_step_is_off_by_default_and_last():
    step = rs.MoveIntoFoldersStep(Settings())
    assert not step.default_enabled and step.position == "last"
    assert step.options[0].kind == "str" and step.options[0].fallback() == "%albumartist%/%album%/%track% - %title%"
    assert rs.MoveIntoFoldersStep(Settings(move_pattern="%artist%/%title%")).options[0].fallback() == "%artist%/%title%"
    cat = rs.build_catalogue(Settings())
    assert [s.key for s in cat][-2:] == ["rename", "move_into_folders"]
    assert "move_into_folders" not in [s.key for s, _ in Recipe.default_for(cat).resolve(cat)]


def test_move_files_the_saved_file_under_the_library_root_and_undo_puts_it_back(tmp_path):
    from redactor_common.core.rename_log import RenameLog

    root = _library(tmp_path)
    log = RenameLog(str(tmp_path / "log.json"))
    mp3 = make_file(tmp_path, title="Song", artist="Band", album="Album", track="3", albumartist="Band")
    original = mp3.path
    settings = Settings(library_root=str(root), rename_zero_pad=True)
    opts = {"move_into_folders": {"pattern": "%albumartist%/%album%/%track% - %title%"}}
    entry, _ = run(mp3, {"bpm", "move_into_folders"}, opts, settings=settings, rename_log=log)
    target = root / "Band" / "Album" / "03 - Song.mp3"
    assert not entry.failures, entry
    assert target.exists() and not original.exists() and mp3.path == target
    assert any("moved to" in a for a in entry.applied)
    fresh = MP3File(path=target)
    load_tags(fresh)
    assert fresh.title == "Song" and not fresh.load_error
    assert leftovers(tmp_path) == [] and leftovers(target.parent) == []

    batch = log.last_batch()
    assert batch.root == str(root) and [os.path.basename(d) for d in batch.created_dirs] == ["Band", "Album"]
    result = log.undo_last()
    assert original.exists() and not target.exists() and result.restored == [(str(target), str(original))]
    assert [os.path.basename(d) for d in result.created_dirs] == ["Album", "Band"]  # offered for tidying, deepest first


def test_move_saves_pending_changes_first_and_reports_the_save_once(tmp_path, monkeypatch):
    root = _library(tmp_path)
    monkeypatch.setattr(rs, "detect_bpm", lambda p: (100.0, STATUS_OK, ""))
    mp3 = make_file(tmp_path, title="Song", artist="Band", album="Album", track="1")
    opts = {"move_into_folders": {"pattern": "%artist%/%title%"}}
    entry, _ = run(mp3, {"bpm", "move_into_folders"}, opts, settings=Settings(library_root=str(root)))
    assert mp3.path == root / "Band" / "Song.mp3"
    fresh = MP3File(path=mp3.path)
    load_tags(fresh)
    assert fresh.bpm == 100.0
    assert len([a for a in entry.applied if a.startswith(rs.FINALIZE_LABEL)]) == 1


def test_move_without_a_library_root_is_a_noted_nothing(tmp_path):
    mp3 = make_file(tmp_path, title="Song")
    entry, _ = run(mp3, {"move_into_folders"}, settings=Settings())
    assert entry.status is FileStatus.UNCHANGED and "no library root" in notes_of(entry)
    entry, _ = run(mp3, {"move_into_folders"}, settings=Settings(library_root=str(tmp_path / "gone")))
    assert "no library root" in notes_of(entry) and mp3.path.parent == tmp_path


def test_move_leaves_a_file_that_is_already_in_place_and_never_overwrites(tmp_path):
    root = _library(tmp_path)
    settings = Settings(library_root=str(root))
    opts = {"move_into_folders": {"pattern": "%artist%/%title%"}}
    (root / "Band").mkdir()
    (root / "Band" / "Song.mp3").write_bytes(b"someone else's file")
    mp3 = make_file(tmp_path, title="Song", artist="Band")
    run(mp3, {"move_into_folders"}, opts, settings=settings)
    assert mp3.path == root / "Band" / "Song (2).mp3"
    assert (root / "Band" / "Song.mp3").read_bytes() == b"someone else's file"
    again = {"move_into_folders": {"pattern": "%artist%/%title% (2)"}}
    entry, _ = run(mp3, {"move_into_folders"}, again, settings=settings)
    assert entry.status is FileStatus.UNCHANGED  # already where the pattern puts it


# --- save stage, guards ---------------------------------------------------------------


def test_unsaved_edits_and_load_errors_are_skipped_with_a_report_line(tmp_path, recycle_bin):
    dirty = make_file(tmp_path, "d.mp3")
    dirty.title, dirty.dirty = "unsaved", True
    broken = make_file(tmp_path, "b.mp3")
    broken.load_error = "boom"
    before = {m.path: m.path.read_bytes() for m in (dirty, broken)}
    for mp3, phrase in ((dirty, "unsaved edits"), (broken, "could not be read")):
        entry, _ = run(mp3, {"integrity"})
        assert entry.status is FileStatus.SKIPPED and phrase in entry.skips[0] and not entry.failures
    assert {m.path: m.path.read_bytes() for m in (dirty, broken)} == before
    assert dirty.title == "unsaved" and dirty.dirty and recycle_bin.trashed == []
    assert leftovers(tmp_path) == []


def test_failed_verification_leaves_the_original_untouched(tmp_path, monkeypatch, recycle_bin):
    mp3 = make_file(tmp_path)
    before = mp3.path.read_bytes()
    monkeypatch.setattr(rs, "check_integrity", lambda p, override_path=None: (STATUS_OK, ""))
    monkeypatch.setattr(rs, "verify_written_file", lambda *a: False)
    entry, _ = run(mp3, {"integrity"})
    assert any("NOT SAVED" in f and "verification" in f for f in entry.failures)
    assert mp3.path.read_bytes() == before and recycle_bin.trashed == [] and leftovers(tmp_path) == []


def test_write_error_is_reported_as_not_saved(tmp_path, monkeypatch, recycle_bin):
    mp3 = make_file(tmp_path)
    monkeypatch.setattr(rs, "check_integrity", lambda p, override_path=None: (STATUS_OK, ""))
    monkeypatch.setattr(rs, "apply_tags_to_file", lambda m, p: "disk full")
    entry, _ = run(mp3, {"integrity"})
    assert any("NOT SAVED" in f and "disk full" in f for f in entry.failures)
    assert recycle_bin.trashed == [] and leftovers(tmp_path) == []


def test_a_failed_recycle_bin_keeps_the_original_beside_the_new_file(tmp_path, monkeypatch, recycle_bin):
    mp3 = make_file(tmp_path)
    monkeypatch.setattr(rs, "check_integrity", lambda p, override_path=None: (STATUS_OK, ""))
    recycle_bin.fail_with = OSError("no bin on this share")
    entry, _env = run(mp3, {"integrity"})
    assert any("original is kept at" in a for a in entry.applied)
    assert (tmp_path / "a.redact-orig.mp3").exists() and "no bin" in notes_of(entry)


def test_nothing_changed_means_no_rewrite(tmp_path, recycle_bin):
    mp3 = make_file(tmp_path)
    before = mp3.path.read_bytes()
    entry, _ = run(mp3, set())
    assert entry.status is FileStatus.UNCHANGED and mp3.path.read_bytes() == before
    assert recycle_bin.trashed == []


def test_a_crashed_runs_scratch_files_are_removed_at_the_next_run(tmp_path, recycle_bin):
    mp3 = make_file(tmp_path)
    stale = [tmp_path / ".mp3redactor-redact-abc123.mp3", tmp_path / ".mp3redactor-redact-abc123.mp3.bak"]
    for path in stale:
        path.write_bytes(b"left behind")
    other = tmp_path / "keep-me.mp3"
    other.write_bytes(b"not ours")
    entry, env = run(mp3, {"integrity"})
    assert env.cleaned == 2 and not any(p.exists() for p in stale) and other.exists()
    assert leftovers(tmp_path) == []


def test_load_folder_ignores_scratch_files(tmp_path):
    from core.scan_service import find_mp3_files

    make_file(tmp_path, "real.mp3")
    (tmp_path / ".mp3redactor-redact-xyz.mp3").write_bytes(b"scratch")
    assert [p.name for p in find_mp3_files([tmp_path])] == ["real.mp3"]
    assert find_mp3_files([tmp_path / ".mp3redactor-redact-xyz.mp3"]) == []


def test_the_save_stage_reports_a_skip_even_when_no_step_runs(tmp_path):
    dirty = make_file(tmp_path, "d.mp3")
    dirty.dirty = True
    entry, _ = run(dirty, set())
    assert entry.status is FileStatus.SKIPPED and "unsaved edits" in entry.skips[0]
    assert entry.skips[0].startswith(rs.FINALIZE_LABEL)


# --- recipe ---------------------------------------------------------------------


def test_defaults_match_the_brief():
    cat = {s.key: s for s in rs.build_catalogue(Settings())}
    assert list(cat) == ["integrity", "bpm", "key", "loudness", "deep_check", "path_tags", "tags", "cover", "rename", "move_into_folders"]
    assert [k for k, s in cat.items() if not s.default_enabled] == ["deep_check", "rename", "move_into_folders"]


def test_rename_is_a_last_step_whatever_the_stored_order_says():
    cat = rs.build_catalogue(Settings())
    recipe = Recipe(order=["rename", "cover", "integrity"], enabled={"rename": True})
    resolved = [s.key for s, _ in recipe.resolve(cat)]
    assert resolved[-1] == "rename"
    # a recipe saved before a step existed gets it at its catalogue position, not at the end
    assert resolved.index("bpm") > resolved.index("integrity") and "save" not in resolved


def test_recipe_round_trips_through_the_settings_file(tmp_path):
    from core.settings import load_settings, save_settings

    cat = rs.build_catalogue(Settings())
    recipe = Recipe.default_for(cat)
    recipe.enabled["deep_check"] = True
    recipe.enabled["path_tags"] = False
    recipe.options["integrity"] = {"fix": False}
    recipe.options["path_tags"] = {"pattern": "%genre%/%artist%/%title%"}
    recipe.confidence_threshold = 0.75
    recipe.order.reverse()
    save_settings(Settings(redact_recipe=rs.recipe_to_setting(recipe)), tmp_path)
    loaded = rs.recipe_from_setting(load_settings(tmp_path).redact_recipe, cat)
    assert loaded.to_dict() == recipe.to_dict()
    assert loaded.options["path_tags"] == {"pattern": "%genre%/%artist%/%title%"} and loaded.enabled["path_tags"] is False
    assert rs.recipe_from_setting("", cat).enabled["integrity"] is True
    assert rs.recipe_from_setting("", cat).enabled["path_tags"] is True
    assert rs.recipe_from_setting("not json", cat).confidence_threshold == 0.9


# --- fill empty tags from the folder path ------------------------------------------


def make_in_library(tmp_path, *parts, **tags):
    """A file at library/<parts...> (the last part is the file name)."""
    folder = tmp_path / "library" / Path(*parts[:-1])
    folder.mkdir(parents=True, exist_ok=True)
    return make_file(folder, parts[-1], **tags)


def path_settings(tmp_path, **kw):
    return Settings(library_root=str(tmp_path / "library"), **kw)


def test_path_tags_is_on_by_default_and_runs_before_the_tag_lookup():
    cat = [s.key for s, _ in Recipe.default_for(rs.build_catalogue(Settings())).resolve(rs.build_catalogue(Settings()))]
    assert cat.index("path_tags") < cat.index("tags")
    step = next(s for s in rs.build_catalogue(Settings()) if s.key == "path_tags")
    assert step.default_enabled and step.options[0].fallback() == "%albumartist%/%album%/%track% - %title%"


def test_path_tags_default_pattern_is_the_latest_saved_path_pattern():
    settings = Settings(pattern_history=["%track% - %title%", "%genre%/%title%", "%album%/%title%"])
    step = next(s for s in rs.build_catalogue(settings) if s.key == "path_tags")
    assert step.options[0].fallback() == "%genre%/%title%"


def test_path_tags_fills_empty_fields_from_a_well_matching_path(tmp_path, recycle_bin):
    mp3 = make_in_library(tmp_path, "Queen", "Jazz", "03 - Fat Bottomed Girls.mp3")
    sibling = make_in_library(tmp_path, "Queen", "Jazz", "04 - Dreamer's Ball.mp3")
    entry, _ = run(mp3, {"path_tags"}, settings=path_settings(tmp_path), items=[mp3, sibling])
    assert entry.status is FileStatus.CHANGED
    assert (mp3.albumartist, mp3.album, mp3.track, mp3.title) == ("Queen", "Jazz", "3", "Fat Bottomed Girls")
    assert not entry.review


def test_path_tags_alone_is_a_bare_folder_guess_and_needs_review(tmp_path, recycle_bin):
    # a bare %field% folder matches any name (0.75) and nothing else in the run backs it up
    mp3 = make_in_library(tmp_path, "Queen", "Jazz", "03 - Fat Bottomed Girls.mp3")
    entry, _ = run(mp3, {"path_tags"}, settings=path_settings(tmp_path))
    assert entry.status is FileStatus.NEEDS_REVIEW and not mp3.album
    assert entry.review[0].step_key == "path_tags" and entry.review[0].confidence < 0.9


def test_path_tags_never_replaces_existing_values(tmp_path, recycle_bin):
    mp3 = make_in_library(tmp_path, "Queen", "Jazz", "03 - Fat Bottomed Girls.mp3", title="Kept", album="Kept Album")
    sibling = make_in_library(tmp_path, "Queen", "Jazz", "04 - Dreamer's Ball.mp3")
    run(mp3, {"path_tags"}, settings=path_settings(tmp_path), items=[mp3, sibling])
    assert (mp3.title, mp3.album) == ("Kept", "Kept Album")
    assert (mp3.albumartist, mp3.track) == ("Queen", "3")


def test_path_tags_sends_a_weak_match_to_needs_review(tmp_path, recycle_bin):
    # the file name doesn't fit "<track> - <title>": the path pattern is only met by the folders
    mp3 = make_in_library(tmp_path, "Queen", "Jazz", "Fat Bottomed Girls.mp3")
    entry, _ = run(mp3, {"path_tags"}, settings=path_settings(tmp_path))
    assert entry.status is FileStatus.UNCHANGED  # the file name doesn't match: nothing to offer
    assert not mp3.albumartist and not mp3.title
    # a pattern with one more folder than the path has: matched below the threshold
    short = make_in_library(tmp_path, "Jazz", "03 - Fat Bottomed Girls.mp3")
    entry, _ = run(short, {"path_tags"}, settings=path_settings(tmp_path))
    assert not short.albumartist
    assert entry.status is FileStatus.NEEDS_REVIEW and "no match for" in entry.review[0].reason
    # ...and the same result applies once the threshold is lowered
    entry, _ = run(short, {"path_tags"}, threshold=0.5, settings=path_settings(tmp_path))
    assert short.album == "Jazz" and short.title == "Fat Bottomed Girls" and not short.albumartist


def test_path_tags_without_a_library_root_is_a_noted_nothing(tmp_path, recycle_bin):
    mp3 = make_in_library(tmp_path, "Queen", "Jazz", "03 - Fat Bottomed Girls.mp3")
    entry, _ = run(mp3, {"path_tags"}, settings=Settings())
    assert entry.status is not FileStatus.FAILED and not mp3.album
    assert "no library root" in notes_of(entry)


def test_path_tags_leaves_a_file_outside_the_library_root_alone(tmp_path, recycle_bin):
    (tmp_path / "library").mkdir()
    outside = make_file(tmp_path, "03 - Elsewhere.mp3")
    entry, _ = run(outside, {"path_tags"}, settings=path_settings(tmp_path))
    assert not outside.title and entry.status is not FileStatus.FAILED


def test_path_tags_uses_the_runs_other_files_to_corroborate_a_folder(tmp_path, recycle_bin):
    bare = {"pattern": "%albumartist%/%album%/%title%"}
    solo = make_in_library(tmp_path, "Solo", "Only", "Song.mp3")
    a = make_in_library(tmp_path, "Band", "Album", "One.mp3")
    b = make_in_library(tmp_path, "Band", "Album", "Two.mp3")
    settings = path_settings(tmp_path)
    _, env = run(solo, {"path_tags"}, bare, settings=settings, items=[solo])
    alone = rs._run_folder_counts(env, bare["pattern"], settings.library_root)
    _, env = run(a, {"path_tags"}, bare, settings=settings, items=[a, b])
    together = rs._run_folder_counts(env, bare["pattern"], settings.library_root)
    assert max(alone.values()) == 1 and together[("album", "album")] == 2 and together[("albumartist", "band")] == 2


# --- pattern trail: a saved recipe keeps its pattern, empty follows the fallback ---


def test_pattern_options_carry_the_trail_fields():
    settings = Settings(pattern_history=["%album%/%title%", "%artist% - %title%"], rename_pattern="%title%")
    for step in rs.build_catalogue(settings):
        if step.key not in ("rename", "move_into_folders", "path_tags"):
            continue
        spec = step.options[0]
        assert spec.default == "" and spec.fallback_label
        assert spec.suggestions() == ["%album%/%title%", "%artist% - %title%"]
        assert spec.preview(spec.fallback() or "%title%")
    rename = next(s for s in rs.build_catalogue(settings) if s.key == "rename")
    assert rename.options[0].preview("%artist% - %title%") == "Queen - Bohemian Rhapsody.mp3"
    move = next(s for s in rs.build_catalogue(settings) if s.key == "move_into_folders")
    assert move.options[0].preview("%artist%/%album%/%title%") == "Queen/A Night at the Opera/Bohemian Rhapsody.mp3"


def test_stored_pattern_wins_and_empty_follows_the_fallback(tmp_path):
    settings = Settings(rename_pattern="%title%")
    first = make_file(tmp_path, "one.mp3", title="Song", artist="Band")
    run(first, {"rename"}, {"rename": {"pattern": "%artist% - %title%"}}, settings=settings)
    assert first.path.name == "Band - Song.mp3"
    second = make_file(tmp_path, "two.mp3", title="Tune", artist="Band")
    run(second, {"rename"}, {"rename": {"pattern": ""}}, settings=settings)
    assert second.path.name == "Tune.mp3"
    settings.rename_pattern = "%artist% %title%"
    third = make_file(tmp_path, "three.mp3", title="Air", artist="Band")
    run(third, {"rename"}, {"rename": {"pattern": ""}}, settings=settings)
    assert third.path.name == "Band Air.mp3"


def test_first_save_pins_the_current_patterns_later_changes_do_not_steer(tmp_path):
    settings = Settings(rename_pattern="%title%", move_pattern="%artist%/%title%", pattern_history=["%album%/%title%"])
    cat = rs.build_catalogue(settings)
    recipe = rs.pin_patterns(Recipe.default_for(cat), cat)
    assert recipe.options["rename"]["pattern"] == "%title%"
    assert recipe.options["move_into_folders"]["pattern"] == "%artist%/%title%"
    assert recipe.options["path_tags"]["pattern"] == "%album%/%title%"
    stored = rs.recipe_to_setting(recipe)
    # Rename / Export later changes; the stored recipe still does what it did at save time
    settings.rename_pattern = "%artist% - %title%"
    reloaded = rs.recipe_from_setting(stored, rs.build_catalogue(settings))
    mp3 = make_file(tmp_path, title="Song", artist="Band")
    env = rs.RedactEnv(settings)
    env.begin([mp3])
    cat = rs.build_catalogue(settings)
    reloaded.enabled = {s.key: s.key == "rename" for s in cat}
    run_recipe_on_item(
        mp3, reloaded.resolve(cat), 0.9, lambda m: rs.Mp3Ctx(m, env), lambda m: m.filename,
        finalize=rs.save_stage, finalize_label=rs.FINALIZE_LABEL,
    )
    assert mp3.path.name == "Song.mp3"


def test_pinning_keeps_an_existing_stored_pattern_and_old_json_loads_unchanged():
    cat = rs.build_catalogue(Settings(rename_pattern="%title%"))
    old = '{"order":["rename"],"enabled":{"rename":true},"options":{"rename":{"pattern":"%track% %title%"},"move_into_folders":{"pattern":""}},"confidence_threshold":0.8}'
    recipe = rs.recipe_from_setting(old, cat)
    assert recipe.options["rename"]["pattern"] == "%track% %title%"
    assert recipe.options["move_into_folders"]["pattern"] == ""
    assert rs.recipe_to_setting(recipe) == rs.recipe_to_setting(Recipe.from_json(old))
