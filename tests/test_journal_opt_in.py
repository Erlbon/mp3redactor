"""
The change history is OPT-IN (owner, 2026-10-14: "we don't start logging a
lot of data without a user request"): off by default, nothing is recorded and
no file is created by Save, Save All, Redact or the command line until the
user turns it on in Preferences > History or says yes to Tools > History's
question. Setting [history] enabled in the ini file.
"""

import os
import shutil
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtWidgets import QMessageBox  # noqa: E402

from core import history  # noqa: E402
from core import settings as settings_module  # noqa: E402
from core.mp3_file import MP3File  # noqa: E402
from core.scan_service import save_dirty_tags  # noqa: E402
from core.settings import Settings, load_settings, save_settings  # noqa: E402
from core.tag_reader import load_tags  # noqa: E402
from core.tag_writer import save_tags  # noqa: E402
from tests.test_cover_gui import _app, window  # noqa: E402,F401

FIXTURE = Path(__file__).parent / "fixtures" / "tiny.mp3"


def _song(tmp_path, name="song.mp3", **tags) -> MP3File:
    path = tmp_path / name
    shutil.copyfile(FIXTURE, path)
    mp3 = MP3File(path=path)
    load_tags(mp3)
    if tags:
        mp3.apply_tags(tags)
        assert save_tags(mp3, journal_kind=None)
    fresh = MP3File(path=path)
    load_tags(fresh)
    return fresh


def _nothing_recorded(journal) -> bool:
    return not os.path.exists(journal.path) and journal.entries() == []


# --- O1: the setting, default off ------------------------------------------------------------------


def test_the_setting_is_off_by_default_and_round_trips_in_the_ini(tmp_path):
    assert Settings().history_enabled is False
    assert load_settings(tmp_path).history_enabled is False  # no file at all
    s = Settings(history_enabled=True)
    save_settings(s, tmp_path)
    text = (tmp_path / settings_module.SETTINGS_FILENAME).read_text(encoding="utf-8")
    assert "[history]" in text and "enabled = True" in text
    assert load_settings(tmp_path).history_enabled is True


@pytest.mark.parametrize("ini", ["[history]\nenabled = perhaps\n", "[history]\n", "[general]\nlast_directory = x\n", "garbage ["])
def test_a_missing_or_garbled_value_is_off(tmp_path, monkeypatch, journal, ini):
    (tmp_path / settings_module.SETTINGS_FILENAME).write_text(ini, encoding="utf-8")
    monkeypatch.setattr(settings_module, "base_dir", lambda: tmp_path)
    history.set_journal_for_tests(journal, enabled=None)  # read the (test) settings file
    assert history.history_enabled() is False


def test_history_enabled_reads_the_settings_file(tmp_path, monkeypatch, journal):
    save_settings(Settings(history_enabled=True), tmp_path)
    monkeypatch.setattr(settings_module, "base_dir", lambda: tmp_path)
    history.set_journal_for_tests(journal, enabled=None)
    assert history.history_enabled() is True


# --- default off: nothing is written anywhere ------------------------------------------------------


def test_off_save_and_save_all_record_nothing_and_create_no_file(tmp_path, journal):
    a = _song(tmp_path, "a.mp3", title="A")
    b = _song(tmp_path, "b.mp3", title="B")
    a.apply_tags({"title": "A2"})
    assert save_tags(a)
    b.apply_tags({"title": "B2"})
    save_dirty_tags([b])
    assert _nothing_recorded(journal)


def test_off_redact_records_nothing(tmp_path, journal, monkeypatch):
    from tests.test_redact_steps import _patch_musicbrainz, run

    _patch_musicbrainz(monkeypatch)
    mp3 = _song(tmp_path, title="Song", album="Album", artist="Artist")
    run(mp3, {"tags"})
    assert mp3.track == "1"  # the redact did change the file
    assert _nothing_recorded(journal)


def test_off_command_line_set_records_nothing(tmp_path, journal):
    from mp3cli.main import main

    mp3 = _song(tmp_path, artist="Old")
    assert main(["set", str(mp3.path), "-s", "artist=Queen", "-q"]) == 0
    assert _nothing_recorded(journal)


def test_turning_it_on_records_and_off_again_stops(tmp_path, journal):
    from mp3cli.main import main

    mp3 = _song(tmp_path, title="One")
    history.set_history_enabled(True)
    mp3.apply_tags({"title": "Two"})
    assert save_tags(mp3)
    assert main(["set", str(mp3.path), "-s", "title=Three", "-q"]) == 0
    assert [e.changes["title"] for e in journal.entries()] == [("Two", "Three"), ("One", "Two")]
    history.set_history_enabled(False)
    mp3.apply_tags({"title": "Four"})
    assert save_tags(mp3)
    assert main(["set", str(mp3.path), "-s", "title=Five", "-q"]) == 0
    assert len(journal.entries()) == 2


# --- O2: Preferences ---------------------------------------------------------------------------------


def _prefs(settings, saved):
    from gui.preferences import build_preferences_dialog

    return build_preferences_dialog(settings, lambda s: saved.append(s.history_enabled))


def test_preferences_checkbox_is_off_and_cancel_changes_nothing(journal):
    from gui.preferences import HISTORY_LABEL, KEY_HISTORY

    settings, saved = Settings(), []
    dialog = _prefs(settings, saved)
    box = dialog.control(KEY_HISTORY)
    assert box.text().replace("&", "") == HISTORY_LABEL and not box.isChecked()
    assert history.journal_path() in box.accessibleDescription()
    assert "90 days" in box.accessibleDescription() and "50,000" in box.accessibleDescription()
    box.setChecked(True)
    dialog.reject()
    assert settings.history_enabled is False and saved == [] and history.history_enabled() is False


def test_preferences_checkbox_round_trips_on_apply_and_ok(journal):
    from gui.preferences import KEY_HISTORY

    settings, saved = Settings(), []
    dialog = _prefs(settings, saved)
    dialog.control(KEY_HISTORY).setChecked(True)
    dialog.apply()
    assert settings.history_enabled is True and saved[-1] is True and history.history_enabled() is True
    dialog.reject()

    again = _prefs(settings, saved)
    assert again.control(KEY_HISTORY).isChecked()
    again.control(KEY_HISTORY).setChecked(False)
    again.accept()
    assert settings.history_enabled is False and saved[-1] is False and history.history_enabled() is False


def test_delete_history_asks_then_sends_the_file_to_the_recycle_bin(journal, monkeypatch, recycle_bin):
    history.set_history_enabled(True)
    journal.record("x.mp3", history.KIND_SAVE, {"title": ("a", "b")})
    assert os.path.exists(journal.path)
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))
    answers = [QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Yes]
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: answers.pop(0)))
    dialog = _prefs(Settings(history_enabled=True), [])
    # a PrefAction on the History section (redactor_common), not a hand-made button
    (button,) = [b for b in dialog.action_buttons if b.text() == "Delete History…"]
    button.click()
    assert os.path.exists(journal.path)  # Cancel keeps it
    button.click()
    assert not os.path.exists(journal.path)
    assert recycle_bin.trashed == [journal.path]  # owner rule: deletes go to the Recycle Bin
    dialog.reject()


def test_delete_history_is_a_pref_action_on_the_history_section():
    from gui.preferences import preference_sections

    (section,) = [s for s in preference_sections() if s.key == "history"]
    assert [a.label for a in section.actions] == ["Delete History…"]


# --- O3: Tools > History ----------------------------------------------------------------------------


class _FakeDialog:
    made: list = []

    def __init__(self, journal_, apply_restore, parent, title, undo_handlers=None):
        self.journal, self.apply_restore, self.title = journal_, apply_restore, title
        _FakeDialog.made.append(self)

    def exec(self):
        return 0


@pytest.fixture
def fake_dialog(monkeypatch):
    import gui.main_window as mw

    _FakeDialog.made = []
    monkeypatch.setattr(mw, "HistoryDialog", _FakeDialog)
    return _FakeDialog.made


def test_tools_history_when_off_and_no_file_asks_and_no_does_nothing(window, journal, fake_dialog, monkeypatch):
    asked = []
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: asked.append(a[2]) or QMessageBox.StandardButton.No))
    window.action_registry["history"].trigger()
    assert len(asked) == 1 and asked[0].startswith("History is off. Turn it on?")
    assert "Nothing has been recorded so far." in asked[0]
    assert fake_dialog == [] and history.history_enabled() is False and not window.settings.history_enabled


def test_tools_history_yes_turns_it_on_and_opens_the_dialog(window, journal, fake_dialog, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    window.action_registry["history"].trigger()
    assert history.history_enabled() is True and window.settings.history_enabled is True
    (dialog,) = fake_dialog
    assert dialog.title == "History" and dialog.journal is journal


def test_tools_history_when_off_with_a_file_opens_read_only(window, journal, fake_dialog, monkeypatch, tmp_path):
    history.set_history_enabled(True)
    mp3 = _song(tmp_path, title="Old")
    mp3.apply_tags({"title": "New"})
    assert save_tags(mp3)
    history.set_history_enabled(False)
    lines = Path(journal.path).read_text(encoding="utf-8")

    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: pytest.fail("must not ask")))
    window.action_registry["history"].trigger()
    (dialog,) = fake_dialog
    assert dialog.title == "History (recording is off)"
    (entry,) = dialog.journal.entries()
    # what the real dialog does on Restore: write, then record -- the record is a no-op while off
    assert dialog.apply_restore(entry.path, {"title": "Old"}) == ""
    assert dialog.journal.record(entry.path, history.KIND_RESTORE, {"title": ("New", "Old")}) is None
    dialog.journal.mark_restored(entry.id, None)
    assert Path(journal.path).read_text(encoding="utf-8") == lines
    back = MP3File(path=mp3.path)
    load_tags(back)
    assert back.title == "Old"
