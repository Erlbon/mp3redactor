"""Help > Check for Updates and Preferences > Updates are wired to redactor_common's self-updater; the daily check
is off by default."""

import sys

from PyQt6.QtWidgets import QApplication

import gui.main_window as mw
from gui import preferences
from redactor_common.core import selfupdate

_app = QApplication.instance() or QApplication(sys.argv)


def _help_texts(window):
    help_menu = next(a.menu() for a in window.menuBar().actions() if a.menu() and a.text().replace("&", "") == "Help")
    return help_menu, ["---" if a.isSeparator() else a.text().replace("&", "") for a in help_menu.actions()]


def test_help_menu_check_for_updates_is_wired(monkeypatch):
    calls = []
    monkeypatch.setattr(mw.UpdateController, "check_now", lambda self: calls.append(self))
    window = mw.MainWindow()
    menu, texts = _help_texts(window)
    assert "Check for Updates…" in texts
    action = next(a for a in menu.actions() if a.text().replace("&", "") == "Check for Updates…")
    assert action.isEnabled()
    action.trigger()
    assert calls == [window.updates]
    assert window.updates.repo.lower() == "erlbon/mp3redactor"
    assert not window.updates.settings.enabled and window.updates.start_auto_check() is False


def test_preferences_updates_switch_is_saved_in_its_own_file(tmp_path):
    assert any(s.key == selfupdate.SECTION_UPDATES for s in preferences.preference_sections())
    window = mw.MainWindow()
    saved = []
    backend = preferences.settings_backend(window.settings, saved.append)
    assert backend.get(selfupdate.KEY_CHECK_FOR_UPDATES) is False
    backend.set_many({selfupdate.KEY_CHECK_FOR_UPDATES: True})
    assert window.updates.settings.enabled and saved == []
