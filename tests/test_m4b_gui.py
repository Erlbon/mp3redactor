"""Tools: File > Create M4B Audiobook... -- the dialog (headless) and the main-window flow."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QMessageBox  # noqa: E402

import gui.main_window as mw  # noqa: E402
from core.mp3_file import STATUS_OK  # noqa: E402
from gui.m4b_dialog import M4bDialog  # noqa: E402
from tests.test_cover_gui import _app, _load, _select_all, window  # noqa: E402,F401
from tests.test_m4b_builder import _FFMPEG, _FFPROBE, requires_ffmpeg  # noqa: E402,F401


def _tagged(window, tmp_path, names):
    files = _load(window, tmp_path, names)
    for number, mp3 in enumerate(files, start=1):
        mp3.title = f"Chapter {number}"
        mp3.artist = "A. Writer"
        mp3.album = "The Book"
        mp3.year = "2020"
        mp3.track = str(number)
    return files


def test_the_dialog_is_filled_from_the_first_file_in_reading_order(window, tmp_path):
    files = _tagged(window, tmp_path, ["b.mp3", "a.mp3"])
    files[0].track, files[1].track = "2", "1"  # a.mp3 is chapter one
    dialog = M4bDialog(files)
    assert dialog.title_edit.text() == "The Book" and dialog.author_edit.text() == "A. Writer"
    assert dialog.year_edit.text() == "2020" and dialog.genre_edit.text() == "Audiobook"
    assert [c.path.name for c in dialog.chapters()] == ["a.mp3", "b.mp3"]
    assert dialog.output_path().name == "The Book.m4b" and dialog.output_path().parent == tmp_path


def test_chapters_can_be_reordered_renamed_and_reset_from_file_names(window, tmp_path):
    dialog = M4bDialog(_tagged(window, tmp_path, ["a.mp3", "b.mp3", "c.mp3"]))
    dialog.table.selectRow(0)
    dialog.down_btn.click()
    assert [c.path.name for c in dialog.chapters()] == ["b.mp3", "a.mp3", "c.mp3"]
    assert [c.title for c in dialog.chapters()] == ["Chapter 2", "Chapter 1", "Chapter 3"]  # titles travel with files
    dialog.table.item(2, 1).setText("   ")  # a blank title falls back to the file name
    assert dialog.chapters()[2].title == "c"
    dialog.names_btn.click()
    assert [c.title for c in dialog.chapters()] == ["b", "a", "c"]
    dialog.table.selectRow(0)
    dialog.up_btn.click()  # already first: nothing moves
    assert [c.path.name for c in dialog.chapters()] == ["b.mp3", "a.mp3", "c.mp3"]


def test_the_spec_carries_the_choices_and_adds_the_m4b_extension(window, tmp_path):
    dialog = M4bDialog(_tagged(window, tmp_path, ["a.mp3"]), bitrate_kbps=96)
    dialog.narrator_edit.setText("N. Reader")
    dialog.output_edit.setText(str(tmp_path / "out" / "Renamed"))
    spec = dialog.spec()
    assert spec.bitrate_kbps == 96 and spec.narrator == "N. Reader" and spec.output.name == "Renamed.m4b"
    assert spec.title == "The Book"


def test_no_files_says_so_without_opening_the_dialog(window, monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: shown.append(a[1])))
    monkeypatch.setattr(mw, "M4bDialog", lambda *a, **k: (_ for _ in ()).throw(AssertionError("dialog opened")))
    window.create_m4b_dialog()
    assert shown == ["Create M4B Audiobook"]


@requires_ffmpeg
def test_the_menu_action_builds_the_audiobook_and_leaves_the_list_alone(window, tmp_path, monkeypatch):
    from tests.test_m4b_builder import _tone_mp3

    names = ["01.mp3", "02.mp3"]
    files = _load(window, tmp_path, [])  # no fixture copies: real tone files instead
    from core.tag_reader import load_tags
    from core.mp3_file import MP3File

    files = []
    for number, name in enumerate(names, start=1):
        path = _tone_mp3(tmp_path / name, 1.0 + number)
        mp3 = MP3File(path=path)
        load_tags(mp3)
        mp3.title, mp3.album, mp3.artist, mp3.track = f"Part {number}", "Tone Book", "Tester", str(number)
        files.append(mp3)
    window.files = files
    window._rebuild_table()
    _select_all(window)

    saved = []
    monkeypatch.setattr(mw, "save_settings", lambda s, *a, **k: saved.append(s.m4b_bitrate_kbps))
    infos = []
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: infos.append(a[2])))

    class AutoAccept(M4bDialog):
        def exec(self):
            self.bitrate_combo.setCurrentIndex(self.bitrate_combo.findData(48))
            return self.DialogCode.Accepted

    monkeypatch.setattr(mw, "M4bDialog", AutoAccept)
    window.create_m4b_dialog()

    out = tmp_path / "Tone Book.m4b"
    assert out.exists() and infos and "2 chapters" in infos[0]
    assert saved == [48]  # the quality choice is remembered
    assert window.files == files  # the .m4b is a new file, not loaded into the list
    assert sorted(p.name for p in tmp_path.iterdir() if p.suffix == ".m4b") == ["Tone Book.m4b"]
