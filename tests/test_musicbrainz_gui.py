"""Tools > MusicBrainz Database...: the settings dialog (offscreen, with a SMALL SYNTHETIC
dump from tests/mb_fixture.py), the new Settings fields and their Export/Import Settings
handling, and the menu entry."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json  # noqa: E402

import pytest  # noqa: E402
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox  # noqa: E402

from core.musicbrainz_import import DEFAULT_TYPES, BuildOptions  # noqa: E402
from core.settings import Settings, load_settings, save_settings  # noqa: E402
from core.settings_adapter import Mp3SettingsAdapter  # noqa: E402
from gui import musicbrainz_settings_dialog as dlg_module  # noqa: E402
from redactor_common.core import settings_bundle as sb  # noqa: E402
from tests.mb_fixture import make_dump  # noqa: E402
from tests.test_cover_gui import _app, window  # noqa: E402,F401

_qapp = QApplication.instance() or QApplication([])


@pytest.fixture
def boxes(monkeypatch):
    """Records every message box instead of showing it; question() answers Yes."""
    shown = []

    def record(kind, answer=QMessageBox.StandardButton.Ok):
        def show(parent, title, text, *args, **kwargs):
            shown.append((kind, title, text))
            return answer
        return staticmethod(show)

    monkeypatch.setattr(QMessageBox, "information", record("information"))
    monkeypatch.setattr(QMessageBox, "warning", record("warning"))
    monkeypatch.setattr(QMessageBox, "question", record("question", QMessageBox.StandardButton.Yes))
    return shown


def make_dialog(settings=None):
    saved = []
    settings = settings or Settings()
    return dlg_module.MusicBrainzSettingsDialog(settings, None, save=lambda s: saved.append(json.dumps(vars(s)))), saved


def test_dialog_starts_with_the_default_options():
    dialog, _ = make_dialog()
    assert dialog.windowTitle() == "MusicBrainz Database"
    options = dialog.build_options()
    assert options == BuildOptions()
    assert tuple(n for n, b in dialog.type_boxes.items() if b.isChecked()) == DEFAULT_TYPES
    assert dialog.track_index.isEnabled() and not dialog.track_index.isChecked()
    assert "No database yet" in dialog.status_label.text()
    assert "Free space" in dialog.space_label.text()


def test_dialog_names_the_download_page_and_the_licence():
    text = dlg_module.INSTRUCTIONS
    assert "musicbrainz.org/doc/MusicBrainz_Database/Download" in text
    assert "mbdump.tar.bz2" in text and "CC0" in text and "never used" in text


def test_dialog_shows_saved_settings_and_options():
    options = BuildOptions(official_only=False, types=("Album",), skip_undated=True, include_tracks=False)
    dialog, _ = make_dialog(Settings(musicbrainz_dump="D:/dump/mbdump.tar.bz2", musicbrainz_database="D:/mb.db",
                                     musicbrainz_options=options.to_json()))
    assert dialog.dump_edit.text() == "D:/dump/mbdump.tar.bz2" and dialog.path_edit.text() == "D:/mb.db"
    assert dialog.build_options() == options
    assert not dialog.track_index.isEnabled()  # no track titles, no track index


def test_dialog_save_stores_paths_and_options():
    settings = Settings()
    dialog, saved = make_dialog(settings)
    dialog.dump_edit.setText("/dump/mbdump.tar.bz2")
    dialog.path_edit.setText("/data/mb.db")
    dialog.type_boxes["EP"].setChecked(False)
    dialog.accept()
    assert settings.musicbrainz_dump == "/dump/mbdump.tar.bz2" and settings.musicbrainz_database == "/data/mb.db"
    assert "EP" not in BuildOptions.from_json(settings.musicbrainz_options).types
    assert len(saved) == 1


def test_build_without_a_dump_says_so(boxes):
    dialog, _ = make_dialog()
    assert dialog._build_database() is None
    assert boxes[-1][0] == "information" and "core dump" in boxes[-1][2]


def test_build_with_no_type_ticked_says_so(boxes, tmp_path):
    dialog, _ = make_dialog()
    dialog.dump_edit.setText(str(tmp_path / "x.tar.bz2"))
    for box in dialog.type_boxes.values():
        box.setChecked(False)
    assert dialog._build_database() is None
    assert "release type" in boxes[-1][2]


def test_build_database_end_to_end(boxes, tmp_path):
    dump = make_dump(tmp_path / "mbdump.tar.bz2")
    dest = str(tmp_path / "mb.db")
    settings = Settings()
    dialog, saved = make_dialog(settings)
    dialog.dump_edit.setText(dump)
    dialog.path_edit.setText(dest)
    assert dialog._build_database() == dest
    assert os.path.isfile(dest)
    assert boxes[-1][0] == "information" and "6 releases kept" in boxes[-1][2]
    assert "6 releases, 11 tracks" in dialog.status_label.text()
    assert settings.musicbrainz_database == dest and settings.musicbrainz_dump == dump  # remembered
    ok, message = dialog.check_result()
    assert ok and "official releases only" in message


def test_build_failure_is_shown_not_raised(boxes, tmp_path):
    dump = make_dump(tmp_path / "mbdump.tar.bz2", schema=99)
    dialog, _ = make_dialog()
    dialog.dump_edit.setText(dump)
    dialog.path_edit.setText(str(tmp_path / "mb.db"))
    assert dialog._build_database() is None
    kind, title, text = boxes[-1]
    assert kind == "warning" and "schema 99" in text
    assert not (tmp_path / "mb.db").exists()


def test_cancelled_build_leaves_nothing(boxes, tmp_path, monkeypatch):
    dump = make_dump(tmp_path / "mbdump.tar.bz2")
    dialog, _ = make_dialog()
    dialog.dump_edit.setText(dump)
    dialog.path_edit.setText(str(tmp_path / "mb.db"))
    monkeypatch.setattr(dlg_module, "run_dump_import", lambda *a, **k: None)  # the user pressed Cancel
    assert dialog._build_database() is None
    assert not (tmp_path / "mb.db").exists()


def test_check_file_reports_a_foreign_database(tmp_path):
    other = tmp_path / "other.db"
    other.write_bytes(b"not sqlite" * 100)
    dialog, _ = make_dialog()
    dialog.path_edit.setText(str(other))
    ok, message = dialog.check_result()
    assert not ok and message


def test_browse_dump_fills_the_field(monkeypatch):
    dialog, _ = make_dialog()
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: ("/x/mbdump.tar.bz2", "")))
    dialog._browse_dump()
    assert dialog.dump_edit.text() == "/x/mbdump.tar.bz2"


def test_free_space_warning_when_the_drive_is_small(monkeypatch, tmp_path):
    from collections import namedtuple

    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(dlg_module.shutil, "disk_usage", lambda _p: usage(100, 90, 5 << 30))
    dialog, _ = make_dialog()
    dialog.path_edit.setText(str(tmp_path / "not" / "yet" / "mb.db"))  # nearest existing folder is measured
    text = dialog.free_space_text()
    assert "5.0 GB" in text and "may not be enough" in text
    monkeypatch.setattr(dlg_module.shutil, "disk_usage", lambda _p: usage(100, 10, 500 << 30))
    assert "may not be enough" not in dialog.free_space_text()


# --- settings ---------------------------------------------------------------------------


def test_settings_round_trip_through_the_ini(tmp_path):
    save_settings(Settings(musicbrainz_database="D:/mb.db", musicbrainz_dump="D:/mbdump.tar.bz2",
                           musicbrainz_options=BuildOptions(skip_undated=True).to_json()), tmp_path)
    loaded = load_settings(tmp_path)
    assert (loaded.musicbrainz_database, loaded.musicbrainz_dump) == ("D:/mb.db", "D:/mbdump.tar.bz2")
    assert BuildOptions.from_json(loaded.musicbrainz_options).skip_undated
    assert load_settings(tmp_path / "nothing-here").musicbrainz_database == ""


def _adapter(tmp_path, settings):
    holder = {"s": settings}
    return holder, Mp3SettingsAdapter(lambda: holder["s"], "test", target_dir=tmp_path)


def test_paths_are_machine_specific_and_not_exported_by_default(tmp_path):
    holder, adapter = _adapter(tmp_path, Settings(musicbrainz_database="/mb.db", musicbrainz_dump="/dump"))
    default = json.loads(sb.dump_bundle(sb.build_bundle(adapter, sb.default_selection(adapter))))
    assert "folders" not in default["sections"]
    chosen = json.loads(sb.dump_bundle(sb.build_bundle(adapter, {"folders"})))
    items = chosen["sections"]["folders"]["items"]
    assert items["musicbrainz_database"] == "/mb.db" and items["musicbrainz_dump"] == "/dump"
    assert "MusicBrainz" in next(s.label for s in adapter.sections() if s.key == "folders")


def test_imported_paths_count_only_if_the_file_exists(tmp_path):
    real = tmp_path / "mb.db"
    real.write_bytes(b"x")
    holder, adapter = _adapter(tmp_path, Settings(musicbrainz_database="/mine/mb.db", musicbrainz_dump="/mine/dump"))
    adapter.write_section("folders", {"musicbrainz_database": str(real), "musicbrainz_dump": str(tmp_path / "missing"),
                                      "library_root": "/music"})
    assert holder["s"].musicbrainz_database == str(real)  # exists: imported
    assert holder["s"].musicbrainz_dump == "/mine/dump"  # no such file here: kept
    assert holder["s"].library_root == "/music"
    adapter.write_section("folders", {"musicbrainz_database": ""})
    assert holder["s"].musicbrainz_database == str(real)  # an empty path never clears it


# --- menu --------------------------------------------------------------------------------


def test_tools_menu_opens_the_dialog(window, monkeypatch):
    opened = []
    monkeypatch.setattr(dlg_module.MusicBrainzSettingsDialog, "exec", lambda self: opened.append(self) or 0)
    window.action_registry["musicbrainz_settings"].trigger()
    assert len(opened) == 1 and opened[0]._settings is window.settings
