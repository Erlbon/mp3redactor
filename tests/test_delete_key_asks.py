"""Delete Files (Recycle Bin) and the Delete key: the key asks Remove from List / Delete Files / Cancel, Remove
keeps the files, Delete trashes them with no second question, the menu item still confirms, and a file with
unsaved changes is never trashed. The Recycle Bin is faked (nothing real is trashed, nothing sleeps)."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtGui import QKeySequence  # noqa: E402
from PyQt6.QtWidgets import QMessageBox  # noqa: E402

import gui.main_window as mw  # noqa: E402
from redactor_common.gui.remove_or_delete import DELETE, REMOVE  # noqa: E402
from tests.test_cover_gui import _app, _load, _select_all, window  # noqa: E402,F401


@pytest.fixture
def env(window, tmp_path, monkeypatch):
    files = _load(window, tmp_path, ["a.mp3", "b.mp3"])
    trashed: list[str] = []

    def fake_trash(path):
        trashed.append(path)
        os.remove(path)

    monkeypatch.setattr(mw, "move_to_trash_retrying", fake_trash)
    asked = {"question": 0, "remove_or_delete": 0}
    answers = {"question": QMessageBox.StandardButton.Yes, "choice": REMOVE}

    def fake_question(*a, **k):
        asked["question"] += 1
        return answers["question"]

    def fake_ask(parent, count, names=(), noun="file", exec_box=None):
        asked["remove_or_delete"] += 1
        asked["noun"] = noun
        asked["names"] = list(names)
        return answers["choice"]

    monkeypatch.setattr(QMessageBox, "question", staticmethod(fake_question))
    monkeypatch.setattr(mw, "ask_remove_or_delete", fake_ask)
    return window, files, trashed, asked, answers


def test_key_bindings(window):
    reg = window.action_registry
    assert reg["remove_or_delete"].shortcuts() == [QKeySequence("Delete")]
    assert reg["delete_files"].shortcuts() == [QKeySequence("Shift+Delete")]
    assert reg["remove_from_list"].shortcuts() == []
    assert "Recycle Bin" in reg["delete_files"].text()
    assert "Recycle Bin" in reg["delete_files"].toolTip()


def test_delete_key_remove_keeps_the_files(env):
    window, files, trashed, asked, answers = env
    _select_all(window)
    answers["choice"] = REMOVE
    window.on_delete_key()
    assert asked["remove_or_delete"] == 1 and asked["noun"] == "track"
    assert sorted(asked["names"]) == ["a.mp3", "b.mp3"]
    assert window.files == []
    assert trashed == []
    assert all(os.path.exists(str(m.path)) for m in files)


def test_delete_key_delete_trashes_without_a_second_question(env):
    window, files, trashed, asked, answers = env
    _select_all(window)
    answers["choice"] = DELETE
    window.on_delete_key()
    assert asked["remove_or_delete"] == 1
    assert asked["question"] == 0
    assert sorted(os.path.basename(p) for p in trashed) == ["a.mp3", "b.mp3"]
    assert window.files == []
    assert "Recycle Bin" in window.statusBar().currentMessage()


def test_delete_key_cancel_changes_nothing(env):
    window, files, trashed, asked, answers = env
    _select_all(window)
    answers["choice"] = None
    window.on_delete_key()
    assert asked["remove_or_delete"] == 1
    assert len(window.files) == 2 and trashed == []
    assert all(os.path.exists(str(m.path)) for m in files)


def test_delete_key_with_nothing_selected_asks_nothing(env):
    window, files, trashed, asked, answers = env
    window.table.clearSelection()
    window.on_delete_key()
    assert asked["remove_or_delete"] == 0 and asked["question"] == 0
    assert len(window.files) == 2 and trashed == []
    assert "select" in window.statusBar().currentMessage().lower()


def test_menu_item_still_confirms(env):
    window, files, trashed, asked, answers = env
    _select_all(window)
    window.action_registry["delete_files"].trigger()
    assert asked["question"] == 1 and asked["remove_or_delete"] == 0
    assert len(trashed) == 2 and window.files == []


def test_menu_item_declined_changes_nothing(env):
    window, files, trashed, asked, answers = env
    _select_all(window)
    answers["question"] = QMessageBox.StandardButton.No
    window.delete_files()
    assert asked["question"] == 1
    assert trashed == [] and len(window.files) == 2


def test_unsaved_file_is_refused_and_stays(env, monkeypatch):
    window, files, trashed, asked, answers = env
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: warnings.append(a[2])))
    files[0].dirty = True
    _select_all(window)
    window.delete_files(confirm=False)
    assert [os.path.basename(p) for p in trashed] == ["b.mp3"]
    assert [m.path.name for m in window.files] == ["a.mp3"]
    assert os.path.exists(str(files[0].path))
    assert len(warnings) == 1 and "a.mp3" in warnings[0] and "unsaved" in warnings[0]


def test_player_window_for_the_file_is_closed(env, monkeypatch):
    window, files, trashed, asked, answers = env
    from redactor_common.gui import media_preview

    closed = []

    class FakePlayer:
        def __init__(self, path):
            self._path = path

        def close(self):
            closed.append(self._path)

    monkeypatch.setattr(
        media_preview, "_open_windows", [FakePlayer(str(files[0].path)), FakePlayer(str(files[1].path) + ".other")]
    )
    _select_all(window)
    window.delete_files(confirm=False)
    assert closed == [str(files[0].path)]


def test_right_click_menu_offers_delete_files(window):
    assert window.action_delete_files is window.action_registry["delete_files"]
