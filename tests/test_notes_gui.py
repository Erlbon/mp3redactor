"""Tools > Create Notes... through the REAL main window: real tagged MP3 files, the real engine and the real write
function, the recorded MusicBrainz / Wikipedia replies as the network (nothing reaches the internet), the dialogs driven
by their widgets with QTest clicks. The script is a generator stepped by a timer, so the modal dialogs can be driven."""

import os
import time
import traceback

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtCore import Qt, QTimer  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox  # noqa: E402

from core.notes_flow import Fetchers  # noqa: E402
from gui.notes_dialog import NotesReviewDialog  # noqa: E402
from tests.notes_helpers import FakeFetch, make_mp3, musicbrainz_routes, ultravox_fetchers, wikipedia_routes, RECORDED  # noqa: E402
from tests.test_cover_gui import window  # noqa: E402,F401


class Script:
    def __init__(self, generator):
        self.gen, self.error, self.done = generator, "", False
        self.timer = QTimer()
        self.timer.timeout.connect(self.tick)
        self.busy = False
        self.timer.start(20)

    def tick(self):
        if self.busy or self.done:
            return
        self.busy = True
        try:
            next(self.gen)
        except StopIteration:
            self.done = True
        except BaseException:  # noqa: BLE001
            self.error, self.done = traceback.format_exc(), True
        finally:
            self.busy = False
        if self.done:
            self.timer.stop()
            for w in QApplication.topLevelWidgets():
                if isinstance(w, NotesReviewDialog) and w.isVisible():
                    w.done(0)


def review():
    found = [w for w in QApplication.topLevelWidgets() if isinstance(w, NotesReviewDialog) and w.isVisible()]
    return found[0] if found else None


def wait(cond, what, timeout=30):
    end = time.perf_counter() + timeout
    while not cond():
        assert time.perf_counter() < end, what
        yield


def click(widget):
    QTest.mouseClick(widget, Qt.MouseButton.LeftButton)


@pytest.fixture
def boxes(monkeypatch):
    seen = []
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda p, t, x, *a, **k: seen.append((t, x)) or QMessageBox.StandardButton.Ok))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda p, t, x, *a, **k: seen.append((t, x)) or QMessageBox.StandardButton.Ok))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda p, t, x, *a, **k: seen.append((t, x)) or QMessageBox.StandardButton.No))
    return seen


def run(window, monkeypatch, folder, fetchers, scenario, must_finish=True):
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: str(folder)))
    window.table.clearSelection()
    window.notes_fetchers = lambda: fetchers
    script = Script(scenario())
    window.action_registry["create_notes"].trigger()
    script.timer.stop()
    assert script.error == "", script.error
    assert script.done or not must_finish, "the scenario did not finish"


def vienna(folder, artist="Ultravox"):
    for i, title in enumerate(["Astradyne", "New Europeans", "Private Lives"], start=1):
        make_mp3(folder / "Vienna (1980)" / f"{i:02d} {title}.mp3", title, artist, "Vienna", i, "1980")


def test_a_single_artist_opens_one_at_a_time_and_skip_and_write_do_what_they_say(window, monkeypatch, tmp_path, boxes):
    vienna(tmp_path / "Ultravox")

    def scenario():
        yield from wait(lambda: review() is not None, "review page")
        dlg = review()
        assert dlg.view == "one" and dlg.header_label.text() == "Create notes  1 of 2"
        assert dlg.one_panel.path_label.text().endswith("artist.md") and dlg.one_panel.badge.text() == "Confident"
        assert "Ultravox were a British new wave band" in dlg.one_panel.preview.toPlainText()
        assert dlg.one_panel.raw.toPlainText().startswith("---\ntitle: Ultravox")
        assert "Discogs token not set" in dlg.banner_label.text()
        click(dlg.skip_button)
        assert dlg.header_label.text() == "Create notes  2 of 2" and not (tmp_path / "Ultravox" / "artist.md").exists()
        expected = dlg.session.items[1].proposal.note_text
        click(dlg.write_button)
        yield from wait(lambda: review() is None, "closed after the last note", 10)
        album = tmp_path / "Ultravox" / "Vienna (1980)" / "album.md"
        assert album.read_text(encoding="utf-8") == expected.replace("\r\n", "\n").rstrip("\n") + "\n"
        assert "status: draft" in expected.split("---")[1]
        assert not (tmp_path / "Ultravox" / "artist.md").exists()

    run(window, monkeypatch, tmp_path, ultravox_fetchers(), scenario)
    assert any("Written: 1" in text and "Skipped: 1" in text for _title, text in boxes)
    assert window.settings.notes_last_folder == str(tmp_path)


def multi_fetchers():
    def search(url):
        return RECORDED["mb_artist_search_cristina"] if "Cristina" in url else RECORDED["mb_artist_search_ultravox"]

    mb = FakeFetch([(r"ws/2/artist\?query", search),
                    (r"ws/2/release-group\?artist=", lambda u: RECORDED["mb_rgs_davena"] if "cd0871f1" in u else RECORDED["mb_rgs_ultravox"])]
                   + musicbrainz_routes())
    return Fetchers(mb=mb, wiki=FakeFetch(wikipedia_routes()))


def test_several_artists_open_the_list_with_the_tick_rules_a_pick_and_a_write(window, monkeypatch, tmp_path, boxes):
    vienna(tmp_path / "Rock" / "Ultravox")
    make_mp3(tmp_path / "Rock" / "Cristina" / "Cristina (1980)" / "01 a.mp3", "a", "Cristina", "Cristina", 1, "1980")
    friends = tmp_path / "Rock" / "Ultravox & Friends"
    for album, year in (("Vienna", "1980"), ("Quartet", "1982")):
        make_mp3(friends / f"{album} ({year})" / "01 a.mp3", "a", "Ultravox & Friends", album, 1, year)

    def scenario():
        yield from wait(lambda: review() is not None, "review page")
        dlg = review()
        s = dlg.session
        assert dlg.view == "list"
        rows = {dlg.tree.topLevelItem(i).text(0).split("\\")[-1]: dlg.tree.topLevelItem(i) for i in range(dlg.tree.topLevelItemCount())}
        assert rows["Ultravox"].checkState(0) == Qt.CheckState.Checked
        cristina = rows["Cristina"]
        assert not (cristina.flags() & Qt.ItemFlag.ItemIsUserCheckable) and cristina.checkState(0) != Qt.CheckState.Checked
        cristina.setCheckState(0, Qt.CheckState.Checked)
        assert not s.items[cristina.data(0, Qt.ItemDataRole.UserRole)].ticked
        # an uncertain one: pick the candidate, it becomes confident and ticked
        dlg.tree.setCurrentItem(rows["Ultravox & Friends"])
        i = rows["Ultravox & Friends"].data(0, Qt.ItemDataRole.UserRole)
        assert s.items[i].proposal.confidence.value == "uncertain"
        click(dlg.list_panel.candidate_button)
        assert s.items[i].proposal.confidence.value == "confident" and s.items[i].ticked
        # an existing note that appears after the scan blocks its write and is never touched
        blocker = tmp_path / "Rock" / "Ultravox" / "Artist.md"
        blocker.write_text("mine\n", encoding="utf-8")
        n = len(s.ticked_pending())
        click(dlg.write_ticked_button)
        assert blocker.read_text(encoding="utf-8") == "mine\n"
        assert "Blocked by an existing note: 1" in dlg.last_summary and f"Written: {n - 1}" in dlg.last_summary
        assert (friends / "artist.md").is_file() and (tmp_path / "Rock" / "Ultravox" / "Vienna (1980)" / "album.md").is_file()
        assert not (tmp_path / "Rock" / "Cristina" / "artist.md").exists()
        dlg.done(0)

    run(window, monkeypatch, tmp_path, multi_fetchers(), scenario)


def test_nothing_to_do_says_so_and_opens_no_page(window, monkeypatch, tmp_path, boxes):
    (tmp_path / "empty").mkdir()
    run(window, monkeypatch, tmp_path, Fetchers(), lambda: iter(()), must_finish=False)
    assert any("No artist or album folders" in text for _t, text in boxes)


def test_create_notes_is_in_the_tools_menu_and_the_context_menu_without_a_shortcut(window):
    action = window.action_registry["create_notes"]
    assert action.text().replace("&", "") == "Create Notes…" and action.shortcuts() == []
