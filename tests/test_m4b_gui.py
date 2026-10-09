"""Tools: File > Create M4B Audiobook... -- the dialog (headless) and the main-window flow."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt  # noqa: E402
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
    dialog.series_edit.setText("Saga")
    dialog.series_number_edit.setText("2")
    dialog.sidecar_check.setChecked(True)
    spec = dialog.spec()
    assert spec.bitrate_kbps == 96 and spec.narrator == "N. Reader" and spec.output.name == "Renamed.m4b"
    assert spec.title == "The Book"
    assert spec.series == "Saga" and spec.series_index == "2" and spec.write_sidecar is True


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


# --- one book per folder ---------------------------------------------------------------


def _books_in_folders(window, tmp_path, folders):
    """Real tone MP3s, two per folder, tagged album/artist; loaded into the window."""
    from core.mp3_file import MP3File
    from core.tag_reader import load_tags
    from tests.test_m4b_builder import _tone_mp3

    files = []
    for folder, (album, artist) in folders.items():
        (tmp_path / folder).mkdir()
        for number in (1, 2):
            path = _tone_mp3(tmp_path / folder / f"{number:02d}.mp3", 1.0)
            mp3 = MP3File(path=path)
            load_tags(mp3)
            mp3.title, mp3.album, mp3.artist, mp3.track = f"Part {number}", album, artist, str(number)
            files.append(mp3)
    window.files = files
    window._rebuild_table()
    _select_all(window)
    return files


def test_the_batch_dialog_lists_a_book_per_folder_and_builds_specs(window, tmp_path):
    from core.m4b_builder import group_by_folder
    from gui.m4b_batch_dialog import LAYOUT_LIBRARY, M4bBatchDialog

    files = []
    for folder, album, artist in (("A", "First Book", "Writer One"), ("B", "Second Book", "Writer Two")):
        for number in (2, 1):
            f = _load(window, tmp_path, [f"{folder}/{number}.mp3"])[0]
            f.album, f.artist, f.track = album, artist, str(number)
            files.append(f)
    books = group_by_folder(files)
    dialog = M4bBatchDialog(books, bitrate_kbps=96, library_root=str(tmp_path / "lib"))
    assert dialog.table.rowCount() == 2 and dialog.table.item(0, 1).text() == "First Book"
    assert dialog.table.item(1, 2).text() == "Writer Two"

    beside = dialog.specs()
    assert [s.output.name for s in beside] == ["First Book.m4b", "Second Book.m4b"]
    assert beside[0].output.parent == tmp_path / "A" and [c.path.name for c in beside[0].chapters] == ["1.mp3", "2.mp3"]
    assert beside[0].bitrate_kbps == 96 and beside[0].write_sidecar is False

    dialog.table.item(0, 3).setText("Saga")
    dialog.table.item(0, 4).setText("3")
    dialog.library_radio.setChecked(True)
    dialog.sidecar_check.setChecked(True)
    lib = dialog.specs()
    assert dialog.layout_choice() == LAYOUT_LIBRARY
    assert lib[0].output == tmp_path / "lib" / "Writer One" / "Saga" / "First Book" / "First Book.m4b"
    assert lib[1].output == tmp_path / "lib" / "Writer Two" / "Second Book" / "Second Book.m4b"
    assert lib[0].series == "Saga" and lib[0].series_index == "3" and lib[0].write_sidecar is True

    dialog.table.item(1, 0).setCheckState(Qt.CheckState.Unchecked)
    assert [s.title for s in dialog.specs()] == ["First Book"]


@requires_ffmpeg
def test_two_folders_become_two_audiobooks_in_the_library_layout_with_sidecars(window, tmp_path, monkeypatch):
    from gui.m4b_batch_dialog import M4bBatchDialog

    _books_in_folders(window, tmp_path, {"one": ("Book One", "Ann"), "two": ("Book Two", "Bob")})
    monkeypatch.setattr(mw, "save_settings", lambda *a, **k: None)
    reports = []
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: reports.append(a[2])))

    class Auto(M4bBatchDialog):
        def exec(self):
            self.library_radio.setChecked(True)
            self.library_edit.setText(str(tmp_path / "library"))
            self.sidecar_check.setChecked(True)
            return self.DialogCode.Accepted

    monkeypatch.setattr(mw, "M4bBatchDialog", Auto)
    window.create_m4b_dialog()

    library = tmp_path / "library"
    assert (library / "Ann" / "Book One" / "Book One.m4b").exists()
    assert (library / "Bob" / "Book Two" / "Book Two.m4b").exists()
    assert (library / "Ann" / "Book One" / "metadata.opf").exists()
    assert "<dc:creator opf:role=\"aut\">Bob</dc:creator>" in (library / "Bob" / "Book Two" / "metadata.opf").read_text(encoding="utf-8")
    assert reports == ["2 of 2 audiobook(s) created."]
    assert window.settings.m4b_batch_layout == "library" and window.settings.library_root == str(library)
    assert window.settings.m4b_sidecar is True


@requires_ffmpeg
def test_an_existing_audiobook_is_skipped_in_a_batch_not_replaced(window, tmp_path, monkeypatch):
    from gui.m4b_batch_dialog import M4bBatchDialog

    _books_in_folders(window, tmp_path, {"one": ("Book One", "Ann"), "two": ("Book Two", "Bob")})
    monkeypatch.setattr(mw, "save_settings", lambda *a, **k: None)
    existing = tmp_path / "one" / "Book One.m4b"
    existing.write_bytes(b"precious")
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: warnings.append(a[2])))

    class Auto(M4bBatchDialog):
        def exec(self):
            return self.DialogCode.Accepted

    monkeypatch.setattr(mw, "M4bBatchDialog", Auto)
    window.create_m4b_dialog()

    assert existing.read_bytes() == b"precious"
    assert (tmp_path / "two" / "Book Two.m4b").exists()
    assert warnings and "1 of 2" in warnings[0] and "already exists" in warnings[0]
