"""Metadata > Look Up > MusicBrainz (Local Database)...: the review dialog over a SMALL SYNTHETIC
database (tests/mb_fixture.py) and its wiring in the main window. Nothing here reaches the network."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox  # noqa: E402

import gui.main_window as mw  # noqa: E402
from core import musicbrainz_local as ml  # noqa: E402
from core.acoustid_lookup import AcoustIdError  # noqa: E402
from core.musicbrainz_import import BuildOptions, build_musicbrainz_database  # noqa: E402
from core.mp3_file import MP3File  # noqa: E402
from gui import musicbrainz_local_dialog as dlg_module  # noqa: E402
from gui import musicbrainz_settings_dialog as settings_module  # noqa: E402
from redactor_common.core.local_db import forget_cached  # noqa: E402
from tests.mb_fixture import GID, make_dump  # noqa: E402
from tests.test_cover_gui import _app, _load, _select_all, window  # noqa: E402,F401

_qapp = QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def db_path(tmp_path_factory):
    folder = tmp_path_factory.mktemp("mb_local_gui")
    dest = str(folder / "mb.db")
    build_musicbrainz_database(make_dump(folder / "mbdump.tar.bz2"), dest, BuildOptions(track_index=True))
    yield dest
    forget_cached(dest)


@pytest.fixture
def db(db_path):
    return ml.open_database(db_path)


def abbey(tmp_path, **extra):
    folder = tmp_path / "The Beatles - Abbey Road (1969)"
    folder.mkdir(exist_ok=True)
    titles = ["Come Together", "Something"]
    return [
        MP3File(path=folder / f"{n + 1:02d}.mp3", title=titles[n], track=str(n + 1), album="Abbey Road",
                artist="Beatles, The", **extra)
        for n in range(2)
    ]


def make_dialog(files, db, monkeypatch, **kwargs):
    monkeypatch.setattr(dlg_module.MusicBrainzLocalLookupDialog, "_local_cover", staticmethod(lambda album: None))
    return dlg_module.MusicBrainzLocalLookupDialog(dlg_module.group_by_folder(files), db, **kwargs)


# --- the dialog ---------------------------------------------------------------------------------


def test_dialog_finds_the_release_and_builds_per_file_changes(tmp_path, db, monkeypatch):
    dialog = make_dialog(abbey(tmp_path), db, monkeypatch)
    assert dialog.windowTitle() == "Look Up via MusicBrainz (Local Database)"
    result = dialog._row_results[0]
    assert result.found and result.fields["album"] == "Abbey Road" and result.fields["album artist"] == "The Beatles"
    assert result.fields["label"] and result.fields["match"].startswith("2 of 2 files matched")
    assert result.cover_bytes is None  # the Cover Art Archive is online
    assert result.used_query == {"artist": "Beatles, The", "album": "Abbey Road", "year": ""}
    assert len(result.alternatives) == 1  # the other edition of the album
    changes = {m.path.name: fields for m, fields in dialog.file_changes()}
    assert changes["02.mp3"]["title"] == "Something" and changes["02.mp3"]["track"] == "2"
    assert changes["02.mp3"]["albumartist"] == "The Beatles" and changes["02.mp3"]["year"] == "1969"
    assert changes["02.mp3"]["publisher"] in ("Apple Records", "EMI") and changes["02.mp3"]["catalognumber"]
    assert all("genre" not in fields for fields in changes.values())


def test_a_files_own_release_id_is_the_exact_match(tmp_path, db, monkeypatch):
    files = abbey(tmp_path, musicbrainz_albumid=GID["abbey_lp"])
    dialog = make_dialog(files, db, monkeypatch)
    fields = dialog.file_changes()[0][1]
    assert fields["musicbrainz_albumid"] == GID["abbey_lp"] and fields["publisher"] == "EMI"
    assert fields["releasecountry"] == "GB"


def test_picking_another_edition_changes_what_is_applied(tmp_path, db, monkeypatch):
    dialog = make_dialog(abbey(tmp_path, musicbrainz_albumid=GID["abbey_cd"]), db, monkeypatch)
    alternative = dialog._row_results[0].alternatives[0]
    dialog._resolve(dialog.items[0], alternative.data)
    assert dialog.file_changes()[0][1]["musicbrainz_albumid"] == GID["abbey_lp"]


def test_corrected_query_searches_again_and_a_barcode_works(tmp_path, db, monkeypatch):
    dialog = make_dialog(abbey(tmp_path), db, monkeypatch)
    result = dialog._search_one(dialog.items[0], {"artist": "", "album": "0077774644624", "year": ""})
    assert result.found and result.fields["album"] == "Abbey Road"
    none = dialog._search_one(dialog.items[0], {"artist": "Nobody", "album": "No Such Album", "year": ""})
    assert not none.found and none.error is None
    assert dialog._matches.get(id(dialog.items[0])) is None


def test_unticked_row_applies_nothing(tmp_path, db, monkeypatch):
    dialog = make_dialog(abbey(tmp_path), db, monkeypatch)
    dialog._checkboxes[0].setChecked(False)
    assert dialog.file_changes() == []


def test_offline_acoustid_does_not_stop_the_local_lookup(tmp_path, db, monkeypatch):
    from pathlib import Path

    def offline(paths, fpcalc, post=None):
        raise AcoustIdError("AcoustID couldn't be reached")

    monkeypatch.setattr(dlg_module, "AcoustIdError", AcoustIdError)
    import gui.musicbrainz_lookup_dialog as base

    monkeypatch.setattr(base, "identify_files", offline)
    dialog = make_dialog(abbey(tmp_path), db, monkeypatch, fpcalc=Path("fpcalc"))
    assert dialog._row_results[0].found and dialog._row_results[0].error is None


def test_a_database_error_is_shown_as_the_rows_error(tmp_path, db, monkeypatch):
    def broken(*args, **kwargs):
        raise ml.MusicBrainzLocalError("Database query failed: disk I/O error")

    monkeypatch.setattr(dlg_module, "find_album_local", broken)
    dialog = make_dialog(abbey(tmp_path), db, monkeypatch)
    assert "disk I/O" in dialog._row_results[0].error and dialog.file_changes() == []


# --- the main window ------------------------------------------------------------------------------


def test_menu_entries_exist_and_are_wired(window):
    registry = window.action_registry
    assert registry["musicbrainz_local_lookup"].text() == "Music&Brainz (Local Database)…"
    assert registry["musicbrainz_settings"].text() == "&MusicBrainz Database…"
    for key in ("musicbrainz_local_lookup", "musicbrainz_settings"):
        assert registry[key].isEnabled() and registry[key].receivers(registry[key].triggered) > 0


def test_lookup_without_a_database_offers_the_settings_dialog(window, tmp_path, monkeypatch):
    _load(window, tmp_path, ["a.mp3"])
    _select_all(window)
    window.settings.musicbrainz_database = ""
    asked, opened = [], []
    monkeypatch.setattr(QMessageBox, "question", staticmethod(
        lambda parent, title, text, *a, **k: asked.append(text) or QMessageBox.StandardButton.Yes))
    monkeypatch.setattr(settings_module.MusicBrainzSettingsDialog, "exec", lambda self: opened.append(True) or 0)
    monkeypatch.setattr(dlg_module, "MusicBrainzLocalLookupDialog", lambda *a, **k: pytest.fail("no database, no lookup"))
    window.open_musicbrainz_local_lookup_dialog()
    assert opened == [True] and "Tools > MusicBrainz Database" in asked[0] and "7 GB" in asked[0]


def test_declining_the_prompt_does_nothing(window, tmp_path, monkeypatch):
    _load(window, tmp_path, ["a.mp3"])
    _select_all(window)
    window.settings.musicbrainz_database = ""
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.No))
    monkeypatch.setattr(settings_module.MusicBrainzSettingsDialog, "exec", lambda self: pytest.fail("declined"))
    window.open_musicbrainz_local_lookup_dialog()


def test_an_unreadable_database_is_reported_with_where_to_fix_it(window, tmp_path, monkeypatch):
    _load(window, tmp_path, ["a.mp3"])
    _select_all(window)
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"not sqlite" * 100)
    window.settings.musicbrainz_database = str(bad)
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda parent, title, text, *a, **k: shown.append(text)))
    monkeypatch.setattr(dlg_module, "MusicBrainzLocalLookupDialog", lambda *a, **k: pytest.fail("bad database"))
    window.open_musicbrainz_local_lookup_dialog()
    assert shown and "Tools > MusicBrainz Database" in shown[0]


def test_lookup_applies_through_the_overwrite_review_and_undoes(window, tmp_path, db_path, monkeypatch):
    (a,) = _load(window, tmp_path, ["a.mp3"])
    _select_all(window)
    window.settings.musicbrainz_database = db_path
    built = {}

    class FakeLookup:
        DialogCode = QDialog.DialogCode

        def __init__(self, albums, database, parent=None, fpcalc=None):
            built["files"] = [m for album in albums for m in album.files]
            built["db"] = database

        def exec(self):
            return self.DialogCode.Accepted

        def file_changes(self):
            return [(built["files"][0], {"publisher": "Apple Records", "catalognumber": "CDP 7 46446 2"})]

    monkeypatch.setattr(dlg_module, "MusicBrainzLocalLookupDialog", FakeLookup)
    reviewed = {}

    def review(parent, items, proposed, label_for):
        reviewed["label"] = label_for("catalognumber")
        return proposed

    import redactor_common.gui.overwrite_review_dialog as review_module

    monkeypatch.setattr(review_module, "resolve_overwrite_conflicts", review)
    window.open_musicbrainz_local_lookup_dialog()
    assert isinstance(built["db"], ml.MusicBrainzLocalDatabase) and reviewed["label"] == "Catalog Number"
    assert (a.publisher, a.catalognumber) == ("Apple Records", "CDP 7 46446 2") and a.dirty
    window.undo_last_action()
    assert a.publisher == "" and a.catalognumber == ""
