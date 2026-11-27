"""Review of 2026-11-27, batch 1 (cards 1-17): things that could lose or damage the user's files. One test per card,
each failing without its fix. Every file lives under pytest's tmp_path."""

import os
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtWidgets import QMessageBox  # noqa: E402

import core.redact_steps as rs  # noqa: E402
from core import conversion_safety as cs  # noqa: E402
from core import mp3_validator as mv  # noqa: E402
from core.mp3_file import STATUS_OK, MP3File  # noqa: E402
from core.mp3val_runner import fix_integrity  # noqa: E402
from core.tag_reader import load_tags  # noqa: E402
from core.tag_writer import save_tags  # noqa: E402
from core.whitespace import fixes_for  # noqa: E402
from redactor_common.core import conversion_policy as pm  # noqa: E402
from redactor_common.core.pipeline import FileStatus  # noqa: E402
from tests.test_cover_gui import window  # noqa: E402,F401
from tests.test_redact_steps import JPEG, make_file, run  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "tiny.mp3"


def _copy(tmp_path, name="song.mp3") -> Path:
    dest = tmp_path / name
    shutil.copyfile(FIXTURE, dest)
    return dest


def _frames(path):
    from mutagen.id3 import ID3

    return ID3(path)


# --- card 1: a fix that would leave an empty file ---------------------------------------------------------------


def _empty_rebuild_sample() -> bytes:
    size = 5000  # the tag says more than the file holds
    syncsafe = bytes([(size >> 21) & 0x7F, (size >> 14) & 0x7F, (size >> 7) & 0x7F, size & 0x7F])
    return b"ID3\x04\x00\x00" + syncsafe + b"\x00" * 50


def test_card1_fix_never_replaces_a_song_with_an_empty_file(tmp_path):
    path = tmp_path / "b.mp3"
    original = _empty_rebuild_sample()
    path.write_bytes(original)
    assert mv.validate_bytes(original, "b.mp3", fix=True).fixed == b""  # the quirk is still there: the guard is in fix_file
    with pytest.raises(mv.FixRefusedError):
        mv.fix_file(path, delete_backup=True)
    assert path.read_bytes() == original and sorted(p.name for p in tmp_path.iterdir()) == ["b.mp3"]
    status, message = fix_integrity(path, delete_backup=True)
    assert status == "TOOL ERROR" and "refused" in message and path.read_bytes() == original


# --- card 2: iTunes COMM frames ---------------------------------------------------------------------------------


def _itunes_file(tmp_path) -> Path:
    from mutagen.id3 import COMM

    path = _copy(tmp_path)
    tags = _frames(path)
    tags.add(COMM(encoding=3, lang="eng", desc="iTunNORM", text=[" 000001AB 000002CD"]))
    tags.add(COMM(encoding=3, lang="eng", desc="iTunSMPB", text=[" 00000000 00000210 000003F5"]))
    tags.add(COMM(encoding=3, lang="eng", desc="", text=["my comment"]))
    tags.save(path)
    return path


def test_card2_only_the_empty_description_comm_is_the_comment(tmp_path):
    path = _itunes_file(tmp_path)
    mp3 = MP3File(path=path)
    load_tags(mp3)
    assert mp3.comment == "my comment"
    assert fixes_for(mp3) == []  # the iTunNORM text (leading space) is never shown, so Tidy has nothing to fix
    mp3.apply_tags({"comment": "new text"})
    assert save_tags(mp3)
    comms = {f.desc: f.text[0] for f in _frames(path).values() if f.FrameID == "COMM"}
    assert comms == {"iTunNORM": " 000001AB 000002CD", "iTunSMPB": " 00000000 00000210 000003F5", "": "new text"}


def test_card2_a_file_with_only_itunes_comm_frames_shows_no_comment_and_keeps_them(tmp_path):
    from mutagen.id3 import COMM

    path = _copy(tmp_path)
    tags = _frames(path)
    tags.add(COMM(encoding=3, lang="eng", desc="iTunSMPB", text=[" 0 1 2"]))
    tags.save(path)
    mp3 = MP3File(path=path)
    load_tags(mp3)
    assert mp3.comment == ""
    mp3.apply_tags({"comment": "typed by hand"})
    assert save_tags(mp3)
    comms = {f.desc: f.text[0] for f in _frames(path).values() if f.FrameID == "COMM"}
    assert comms == {"iTunSMPB": " 0 1 2", "": "typed by hand"}


# --- card 3: a Year that is not a date --------------------------------------------------------------------------


def test_card3_a_year_that_is_not_a_date_is_refused_and_the_old_year_stays(tmp_path):
    path = _copy(tmp_path)
    mp3 = MP3File(path=path)
    mp3.apply_tags({"year": "1975"})
    assert save_tags(mp3)
    for bad in ("c. 1975", "1999?", "Unknown"):
        mp3.apply_tags({"year": bad})
        assert save_tags(mp3) is False and "not a date" in mp3.save_error and mp3.dirty
    check = MP3File(path=path)
    load_tags(check)
    assert check.year == "1975"
    for good in ("1980-05", "1980-05-17", "2001"):
        mp3.apply_tags({"year": good})
        assert save_tags(mp3), mp3.save_error


# --- card 4: Notes dialog, filter on, a pick removes the row ----------------------------------------------------


def test_card4_the_dialogs_index_follows_the_panel_after_a_row_leaves_the_filtered_list():
    from PyQt6.QtCore import QPoint, Qt
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QApplication
    from tests.test_notes_list_stable import make_dialog, settle

    dlg = make_dialog(n=12)
    dlg.filter_check.setChecked(True)  # "Only uncertain or not found"
    settle()
    tree = dlg.tree
    first = tree.topLevelItem(0)
    rect = tree.visualItemRect(first)
    QTest.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(100, rect.center().y()))
    QApplication.processEvents()
    picked = first.data(0, Qt.ItemDataRole.UserRole)
    assert dlg.index == picked and dlg.session.items[picked].proposal.candidates
    QTest.mouseClick(dlg.list_panel.candidate_button, Qt.MouseButton.LeftButton)  # the row turns confident and leaves
    settle()
    current = tree.currentItem()
    assert current is not None
    shown = current.data(0, Qt.ItemDataRole.UserRole)
    assert shown != picked and dlg.index == shown
    dlg.done(0)


# --- card 5: CLI convert --trash-original -----------------------------------------------------------------------


def test_card5_cli_keeps_the_original_when_the_new_mp3_does_not_check_out(tmp_path, capsys, monkeypatch):
    from mp3cli import cmd_files
    from core.mp3_file import STATUS_OK as OK
    from tests.test_cli import run as cli_run

    src = tmp_path / "a.flac"
    src.write_bytes(b"fLaC pretend")
    monkeypatch.setattr(cmd_files, "convert_to_mp3", lambda s, d, **k: (d.write_bytes(b"short"), (OK, ""))[1])
    trashed = []
    monkeypatch.setattr(cmd_files, "move_to_trash", trashed.append)
    cli_run(capsys, "convert", str(src), "--trash-original")
    assert trashed == [] and src.exists()


# --- card 6: multi-value tags -----------------------------------------------------------------------------------


def test_card6_tidy_leaves_a_multi_value_field_alone(tmp_path):
    from mutagen.id3 import TCON, TPE1

    path = _copy(tmp_path)
    tags = _frames(path)
    tags.add(TPE1(encoding=3, text=["Artist A ", "Artist B"]))
    tags.add(TCON(encoding=3, text=["Rock", "Pop"]))
    tags.save(path, v2_version=4)
    mp3 = MP3File(path=path)
    load_tags(mp3)
    assert mp3.artist == "Artist A " and "artist" in mp3.multi_value_keys
    assert not [fix for fix in fixes_for(mp3) if fix.key in ("artist", "genre")]
    mp3.apply_tags({"title": "Changed"})  # a save of another field keeps both
    assert save_tags(mp3)
    assert list(_frames(path)["TPE1"].text) == ["Artist A ", "Artist B"]
    assert list(_frames(path)["TCON"].text) == ["Rock", "Pop"]


# --- card 7: detection values are not rewritten on every save ----------------------------------------------------


def test_card7_a_save_of_another_field_leaves_bpm_key_and_gain_as_they_are(tmp_path):
    from mutagen.id3 import TBPM, TKEY, TXXX

    path = _copy(tmp_path)
    tags = _frames(path)
    tags.add(TBPM(encoding=3, text=["127.6"]))
    tags.add(TKEY(encoding=3, text=["Am"]))
    tags.add(TXXX(encoding=3, desc="REPLAYGAIN_TRACK_GAIN", text=["-7.831 dB"]))
    tags.save(path)
    mp3 = MP3File(path=path)
    load_tags(mp3)
    mp3.apply_tags({"title": "Only the title"})
    assert save_tags(mp3)
    tags = _frames(path)
    assert str(tags["TBPM"].text[0]) == "127.6"
    assert str(tags["TKEY"].text[0]) == "Am"
    assert str(tags["TXXX:REPLAYGAIN_TRACK_GAIN"].text[0]) == "-7.831 dB"
    mp3.bpm = 130.2  # a new detection IS written
    assert save_tags(mp3)
    assert str(_frames(path)["TBPM"].text[0]) == "130"


# --- card 8: Loudness step with an unchanged gain ---------------------------------------------------------------


def test_card8_loudness_that_rounds_to_the_held_gain_changes_nothing(tmp_path, monkeypatch):
    mp3 = make_file(tmp_path, loudness_gain_db=-4.0, loudness_lufs=-14.0, loudness_status=STATUS_OK)
    before = mp3.path.read_bytes()
    monkeypatch.setattr(rs, "measure_loudness", lambda p, override_path=None: (-14.0, -4.001, STATUS_OK, ""))
    entry, _ = run(mp3, {"loudness"})
    assert entry.status is FileStatus.UNCHANGED and mp3.path.read_bytes() == before
    monkeypatch.setattr(rs, "measure_loudness", lambda p, override_path=None: (-12.0, -6.0, STATUS_OK, ""))
    entry, _ = run(mp3, {"loudness"})
    assert entry.status is FileStatus.CHANGED


# --- card 9: the undo log cannot be written -----------------------------------------------------------------------


def test_card9_an_unwritable_undo_log_leaves_the_original_where_it_is(tmp_path):
    from tests.test_conversion_policy import _pair

    src, dest = _pair(tmp_path)
    blocker = tmp_path / "log-folder-is-a-file"
    blocker.write_bytes(b"x")
    policy = pm.ConversionPolicy(originals=pm.ORIGINALS_QUARANTINE, recent_minutes=0, undo_log_folder=str(blocker))
    outcome = cs.handle_original(src, dest, policy)
    assert outcome.action == cs.LEFT and src.exists()


def test_card9_a_log_that_fails_after_the_move_puts_the_original_back(tmp_path, monkeypatch):
    from tests.test_conversion_policy import _pair

    src, dest = _pair(tmp_path)
    real_log = cs._log
    calls = []

    def flaky(path, new_path, old):
        calls.append(old)
        if len(calls) == 2:
            raise OSError("the stick was unplugged")
        real_log(path, new_path, old)

    monkeypatch.setattr(cs, "_log", flaky)
    outcome = cs.handle_original(src, dest, pm.ConversionPolicy(originals=pm.ORIGINALS_QUARANTINE, recent_minutes=0))
    assert outcome.action == cs.LEFT and src.exists()


# --- card 10: the cover the Look Up dialog embeds ---------------------------------------------------------------


def test_card10_musicbrainz_lookup_fetches_the_embed_size_not_the_preview(monkeypatch):
    import gui.musicbrainz_lookup_dialog as dlg_module

    asked = []
    monkeypatch.setattr(dlg_module, "fetch_front_cover", lambda rid, size=250, **k: asked.append((rid, size)))
    dlg_module.MusicBrainzLookupDialog._cover(None, SimpleNamespace(id="rel"))
    assert asked == [("rel", rs.COVER_ART_SIZE)]


# --- card 11: undo ----------------------------------------------------------------------------------------------


def test_card11_undo_restores_the_edit_but_not_the_analysis_results_or_a_saved_cover(window):
    import gui.main_window as mw

    mp3 = MP3File(path=Path("song.mp3"))
    mp3.title = "Old"
    snapshot = mw.MainWindow._snapshot_mp3(mp3)
    mp3.title = "New"
    mp3.bpm, mp3.bpm_status = 123.4, STATUS_OK
    mp3.key_value, mp3.key_status = "Am", STATUS_OK
    mp3.loudness_gain_db, mp3.loudness_status = -3.0, STATUS_OK
    mp3.integrity_status = STATUS_OK
    mp3.has_cover = True  # a cover set and already saved: nothing pending
    mw.MainWindow._restore_mp3(mp3, snapshot)
    assert mp3.title == "Old"
    assert mp3.bpm == 123.4 and mp3.key_value == "Am" and mp3.loudness_gain_db == -3.0
    assert mp3.integrity_status == STATUS_OK
    assert mp3.has_cover is True


# --- card 12: nothing to change / an older backup -----------------------------------------------------------------


def test_card12_a_rebuild_equal_to_the_original_touches_nothing(tmp_path, monkeypatch):
    path = tmp_path / "s.mp3"
    path.write_bytes(_empty_rebuild_sample())
    (tmp_path / "s.mp3.bak").write_bytes(b"older good copy")
    data = path.read_bytes()
    monkeypatch.setattr(mv, "validate_bytes", lambda d, name, fix=False: mv.ValidationResult(["WARNING: x"], True, d))
    lines = mv.fix_file(path)
    assert path.read_bytes() == data and (tmp_path / "s.mp3.bak").read_bytes() == b"older good copy"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["s.mp3", "s.mp3.bak"] and not any("FIXED" in x for x in lines)


# --- card 13: leftover temp copies --------------------------------------------------------------------------------


def test_card13_a_failed_save_leaves_no_temp_copy_and_the_sweep_removes_old_ones(tmp_path, monkeypatch):
    import core.tag_writer as tw

    path = _copy(tmp_path)
    mp3 = MP3File(path=path)
    mp3.apply_tags({"title": "x"})

    def boom(*a, **k):
        raise RuntimeError("something unexpected")

    monkeypatch.setattr(tw, "apply_tags_to_file", boom)
    assert save_tags(mp3) is False and "unexpected" in mp3.save_error
    assert [p.name for p in tmp_path.iterdir()] == ["song.mp3"]
    stale = tmp_path / ".mp3redactor-abc123.tmp"
    stale.write_bytes(b"x" * 10)
    assert rs.remove_stale_scratch_files([mp3]) == 1 and not stale.exists()


# --- card 14: the unsaved-changes question ------------------------------------------------------------------------


def test_card14_the_discard_question_defaults_to_no(window, monkeypatch):
    seen = {}

    def question(*args, **kwargs):
        seen["args"] = args
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "question", staticmethod(question))
    assert window._confirm_discard("open another folder") is False
    assert seen["args"][-1] == QMessageBox.StandardButton.No


# --- card 15: the rename undo log ----------------------------------------------------------------------------------


def test_card15_a_rename_log_that_cannot_be_written_is_reported(window, monkeypatch):
    import gui.main_window as mw

    class Broken:
        def record(self, label, renames):
            return False

    monkeypatch.setattr(mw, "_rename_log", lambda: Broken())
    errors: list[str] = []
    window._record_renames("Rename by Pattern", [("a", "b")], errors)
    assert errors and "Undo Last Rename" in errors[0]

    class Raises:
        def record(self, label, renames):
            raise OSError("locked")

    monkeypatch.setattr(mw, "_rename_log", lambda: Raises())
    errors = []
    window._record_renames("Rename by Pattern", [("a", "b")], errors)
    assert errors


# --- card 16: creation date ---------------------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform != "win32", reason="Windows creation time")
def test_card16_save_keeps_the_files_creation_date(tmp_path):
    from redactor_common.core.os_utils import _set_creation_time_windows

    path = _copy(tmp_path)
    old = 1_577_836_800.0  # 2020-01-01
    _set_creation_time_windows(str(path), old)
    assert abs(os.stat(path).st_ctime - old) < 2
    mp3 = MP3File(path=path)
    mp3.apply_tags({"title": "Changed"})
    assert save_tags(mp3)
    assert abs(os.stat(path).st_ctime - old) < 2


# --- card 17: a huge folder image ---------------------------------------------------------------------------------


def test_card17_an_oversize_folder_image_goes_to_review_not_into_every_track(tmp_path, monkeypatch):
    monkeypatch.setattr(rs, "MAX_FOLDER_COVER_BYTES", 100)
    (tmp_path / "cover.jpg").write_bytes(JPEG + b"\x00" * 200)
    mp3 = make_file(tmp_path)
    before = mp3.path.read_bytes()
    entry, _ = run(mp3, {"cover"})
    assert entry.status is FileStatus.NEEDS_REVIEW and mp3.path.read_bytes() == before
