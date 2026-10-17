"""Batch 4 (2026-10-17): written notes undone from Tools > History (created and refreshed), a refresh on a network drive,
the --link folder match, drive/share roots in the notes scope, and the evidence-cache race. The Recycle Bin is never
touched (the autouse recycle_bin fixture fakes it); a network drive is faked by declaring the drive unreliable."""

import os
import sys
import threading
import time
from pathlib import Path

import pytest
from PyQt6.QtWidgets import QApplication, QMessageBox

from core import history, recycle
from core.notes_flow import NotesEngine, build_proposals, scan, undo_note_entry, write_proposal
from core.settings import Settings
from redactor_common.core import trash as trash_module
from redactor_common.core.notes import NoteFrontMatter, note_status, render_note
from redactor_common.gui.history_dialog import HistoryDialog
from tests.notes_helpers import ultravox_fetchers, ultravox_library

_app = QApplication.instance() or QApplication([])
_REAL_MOVE_TO_TRASH = trash_module.move_to_trash  # captured before conftest's autouse fake replaces it


def _old_note(artist):
    text = render_note(NoteFrontMatter(title="Ultravox", generator="mp3redactor 2026-10-01#01", note_format=1),
                       "# Ultravox\n\nOld text.")
    (artist / "artist.md").write_bytes(text.encode("utf-8"))
    return text


def _proposals(tmp_path, **kw):
    return build_proposals(scan([tmp_path], kinds=("artist",), **kw).targets, ultravox_fetchers())


def _dialog():
    return HistoryDialog(history.get_journal(), lambda path, values: "", undo_handlers=history.note_undo_handlers())


def _yes(monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: warnings.append(a[2])))
    return warnings


@pytest.fixture(autouse=True)
def _no_backup_folder():
    recycle.set_recycle_fallback_folder("")
    yield
    recycle.set_recycle_fallback_folder(None)


# -- item 3: undo from the History dialog ----------------------------------------------------------------------------------


def test_a_created_note_is_undone_from_the_history_dialog(tmp_path, recycle_bin, monkeypatch):
    history.set_history_enabled(True)
    artist = ultravox_library(tmp_path)
    [proposal] = [p for p in _proposals(tmp_path) if p.path.endswith("artist.md")]
    path = write_proposal(proposal).path
    dialog = _dialog()
    dialog.table.selectRow(0)
    assert dialog.restore_button.isEnabled()  # it was disabled for every note entry
    _yes(monkeypatch)
    assert dialog.restore_selected() is True
    assert not (artist / "artist.md").exists() and recycle_bin.trashed == [path]
    assert [e.status for e in history.get_journal().entries()] == ["restored"]


def test_a_refreshed_note_is_undone_and_the_older_text_is_back(tmp_path, recycle_bin, monkeypatch):
    history.set_history_enabled(True)
    artist = ultravox_library(tmp_path)
    old = _old_note(artist)
    [update] = [p for p in _proposals(tmp_path, include_outdated=True) if p.is_update]
    assert write_proposal(update).ok
    [entry] = history.get_journal().entries()
    assert entry.kind == "note-update"
    backup = Path(history.notes_backup_base()) / "notes_backup" / f"{entry.id}.md"
    assert backup.read_bytes() == old.encode("utf-8")
    dialog = _dialog()
    dialog.table.selectRow(0)
    _yes(monkeypatch)
    assert dialog.restore_selected() is True
    assert (artist / "artist.md").read_bytes() == old.encode("utf-8")
    assert note_status(str(artist / "artist.md")).kind == "outdated-untouched"
    assert not backup.exists() and len(recycle_bin.trashed) == 2


def test_with_history_off_a_refresh_stores_nothing(tmp_path, recycle_bin):
    artist = ultravox_library(tmp_path)
    _old_note(artist)
    [update] = [p for p in _proposals(tmp_path, include_outdated=True) if p.is_update]
    assert write_proposal(update).ok
    assert not (Path(history.notes_backup_base()) / "notes_backup").exists()
    assert not os.path.exists(history.get_journal().path)


def test_an_edited_note_is_not_undone(tmp_path, recycle_bin):
    history.set_history_enabled(True)
    artist = ultravox_library(tmp_path)
    [proposal] = [p for p in _proposals(tmp_path) if p.path.endswith("artist.md")]
    write_proposal(proposal)
    (artist / "artist.md").write_bytes(b"my own words\n")
    [entry] = history.get_journal().entries()
    assert "changed" in undo_note_entry(entry)
    assert (artist / "artist.md").exists() and recycle_bin.trashed == []


def test_delete_history_also_removes_the_kept_note_text(tmp_path, recycle_bin):
    history.set_history_enabled(True)
    artist = ultravox_library(tmp_path)
    _old_note(artist)
    [update] = [p for p in _proposals(tmp_path, include_outdated=True) if p.is_update]
    write_proposal(update)
    folder = Path(history.notes_backup_base()) / "notes_backup"
    assert folder.is_dir()
    assert history.delete_journal() == "" and not folder.exists()


# -- item 4: a network drive ---------------------------------------------------------------------------------------------


def test_a_refresh_on_a_network_drive_is_refused_with_the_plain_wording_until_a_backup_folder_is_set(tmp_path, monkeypatch):
    artist = ultravox_library(tmp_path)
    old = _old_note(artist)
    # the REAL trash code on a fake network drive: the drive is declared unreliable, and the real bin cannot be reached
    monkeypatch.setattr(trash_module, "move_to_trash", _REAL_MOVE_TO_TRASH)
    monkeypatch.setattr(trash_module, "trash_is_reliable", lambda p, drive_type=None: False)
    monkeypatch.setattr("send2trash.send2trash", lambda p: pytest.fail("the real Recycle Bin was touched"), raising=False)
    [update] = [p for p in _proposals(tmp_path, include_outdated=True) if p.is_update]
    refused = write_proposal(update)
    assert not refused.ok
    assert "On a network drive there is no Recycle Bin: set a backup folder in Preferences" in refused.message
    assert (artist / "artist.md").read_bytes() == old.encode("utf-8")
    recycle.set_recycle_fallback_folder(str(tmp_path / "backups"))
    assert write_proposal(update).ok
    assert list((tmp_path / "backups").rglob("artist.md"))


def test_the_backup_folder_is_a_setting_a_preference_and_in_the_settings_bundle(tmp_path):
    from core.settings import load_settings, save_settings
    from core.settings_adapter import SECTION_KEYS, Mp3SettingsAdapter
    from gui.preferences import FIELD_BY_KEY, KEY_RECYCLE_FALLBACK, preference_sections, settings_backend

    settings = Settings(recycle_fallback_folder=str(tmp_path / "bk"))
    save_settings(settings, tmp_path)
    assert load_settings(tmp_path).recycle_fallback_folder == str(tmp_path / "bk")
    specs = [spec for section in preference_sections() for spec in section.specs]
    spec = [s for s in specs if s.key == KEY_RECYCLE_FALLBACK][0]
    assert FIELD_BY_KEY[KEY_RECYCLE_FALLBACK] == "recycle_fallback_folder"
    assert "no Recycle Bin: set a backup folder here or updates of notes there are refused" in spec.help
    assert "recycle_fallback_folder" in SECTION_KEYS["folders"]
    saved = []
    backend = settings_backend(settings, saved.append)
    backend.set_many({KEY_RECYCLE_FALLBACK: str(tmp_path / "other")})
    assert recycle.recycle_fallback_folder() == str(tmp_path / "other") and saved
    adapter = Mp3SettingsAdapter(lambda: settings, target_dir=tmp_path)
    assert adapter.read_section("folders")["recycle_fallback_folder"] == str(tmp_path / "other")


def test_the_review_page_says_it_plainly():
    from gui import notes_dialog

    assert ("On a network drive there is no Recycle Bin: set a backup folder in Preferences or updates are refused."
            in notes_dialog.RECYCLE_NOTE)


# -- item 5c: a drive or share root ------------------------------------------------------------------------------------


def test_a_drive_root_is_a_generic_place_not_a_genre_with_an_empty_name(tmp_path, monkeypatch):
    from core import notes_local, notes_scope

    root = Path(tmp_path.anchor)
    assert root.name == ""
    assert notes_local.is_generic_name("") and notes_local.is_generic_name(root.name)
    monkeypatch.setattr(notes_local, "classify_level", lambda *a, **k: "genre")
    monkeypatch.setattr(notes_local, "existing_note", lambda folder: None)
    monkeypatch.setattr(notes_local, "_entries", lambda folder: ([], []))
    monkeypatch.setattr(notes_local, "_resolve_roots", lambda paths: ([root], []))
    info = notes_scope.folder_info(str(root))
    assert info[1] is True  # placeholder
    assert notes_scope.folder_caption(info, root.name, "artist,album,genre").startswith("a library root")
    found = notes_local.scan([str(root)], kinds=("artist", "album", "genre"))
    assert found.targets == [] and found.library_root  # no 'genre note: ' proposal for a drive root


@pytest.mark.skipif(sys.platform != "win32", reason="UNC paths are a Windows form")
def test_a_unc_share_root_is_a_generic_place_too(monkeypatch):
    from core import notes_local, notes_scope

    share = Path("//server/Music")
    assert share.name == "" and share.parent == share
    monkeypatch.setattr(notes_local, "classify_level", lambda *a, **k: "genre")
    monkeypatch.setattr(notes_local, "existing_note", lambda folder: None)
    info = notes_scope.folder_info(str(share))
    assert notes_scope.folder_caption(info, share.name, "genre").startswith("a library root")


# -- item 5d: --link needs the whole folder name -------------------------------------------------------------------------


def test_a_short_link_name_does_not_match_a_numbered_or_self_titled_folder(tmp_path):
    engine = NotesEngine(ultravox_fetchers())
    url = "https://en.wikipedia.org/wiki/Leningrad_(band)"
    engine.set_links("Leningrad", url)
    artist = tmp_path / "Leningrad"
    assert engine._link_override(artist) == url
    assert engine._link_override(artist / "Leningrad") == ""  # a self-titled album in its artist's folder
    assert engine._link_override(tmp_path / "1999 - Leningrad") == ""  # not the same full name
    assert engine._link_override(tmp_path / "Leningrad Cowboys") == ""
    engine.set_links(str(artist / "Leningrad"), "https://en.wikipedia.org/wiki/Leningrad_(album)")  # the path pins THAT folder
    assert engine._link_override(artist / "Leningrad").endswith("(album)")
    assert engine._link_override(artist) == url


# -- item 5a: the evidence cache under threads --------------------------------------------------------------------------


def test_the_evidence_cache_survives_workers_adding_while_the_gui_thread_reads(monkeypatch):
    from core.notes_evidence import Evidence
    import gui.notes_evidence_view as view_module

    view_module.clear_cache()
    monkeypatch.setattr(view_module, "load_evidence", lambda folder, kind, loader=None: Evidence(kind=kind))
    monkeypatch.setattr(view_module, "CACHE_LIMIT", 4)
    old_interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)  # a thread switch between the get and the move_to_end becomes likely
    view = view_module.LocalEvidenceView()
    try:
        for number in range(3000):
            # the GUI thread asks for a fresh folder (a worker will add it and evict the oldest) and for older ones
            for wanted in (number, number - 1, number - 2, number - 3):
                view.show_folder(Path(f"/lib/{wanted}"), "album")
    finally:
        sys.setswitchinterval(old_interval)
        deadline = time.time() + 5
        while threading.active_count() > 1 and time.time() < deadline:
            time.sleep(0.01)
        view_module.clear_cache()


def test_toggling_the_notes_scope_never_runs_two_count_walks_at_once(tmp_path, monkeypatch):
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QWidget

    import gui.notes_dialog as nd
    from core.notes_scope import ScopeChoice

    lock = threading.Lock()
    state = {"now": 0, "most": 0, "calls": []}

    def slow_count(folder, include):
        with lock:
            state["now"] += 1
            state["most"] = max(state["most"], state["now"])
            state["calls"].append(include)
        time.sleep(0.4)
        with lock:
            state["now"] -= 1
        return (1, 1, 1)

    monkeypatch.setattr(nd, "count_targets", slow_count)
    (tmp_path / "A").mkdir()
    parent = QWidget()
    dialog = nd.NotesScopeDialog(parent, [ScopeChoice(str(tmp_path / "A"), "folder A", True)], True)
    dialog.show()
    for number in range(12):  # a stress loop of toggles, each far enough apart to pass the 150 ms debounce
        dialog.include_box.setChecked(number % 2 == 1)
        QTest.qWait(170)
    final = dialog.include_box.isChecked()
    deadline = time.time() + 8
    while time.time() < deadline and not (dialog.counts is not None and state["now"] == 0 and state["calls"][-1] == final):
        QTest.qWait(20)
    assert state["most"] == 1, state  # the old behaviour started a walk per toggle: several at once
    assert state["calls"][-1] == final
    dialog.close()
