"""The Discogs lookup dialog, the API Keys dialog and their wiring in the
main window -- all with mocked Discogs replies and the in-memory secret
backend (tests/conftest.py); nothing here reaches the network or the real
Windows Credential Manager."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import urllib.error  # noqa: E402

import pytest  # noqa: E402
from PyQt6.QtWidgets import QApplication, QDialog, QLabel, QMessageBox  # noqa: E402

import gui.main_window as mw  # noqa: E402
from core import discogs_lookup as dl  # noqa: E402
from core.mp3_file import MP3File  # noqa: E402
from core.settings import Settings  # noqa: E402
from core.settings_adapter import SECTION_KEYS  # noqa: E402
from gui import api_keys_dialog as keys_module  # noqa: E402
from gui import discogs_lookup_dialog as dlg_module  # noqa: E402
from redactor_common.core import secret_store  # noqa: E402
from tests.test_cover_gui import _app, _load, _select_all, window  # noqa: E402,F401
from tests.test_discogs_lookup import TOKEN, FakeDiscogs, _fast_throttle, _hit, _http_error, _release  # noqa: E402

_qapp = QApplication.instance() or QApplication([])


def _folder(tmp_path, name="Miles Davis - Kind Of Blue (1959)", tracks=((3, "Blue In Green"), (4, "All Blues"))):
    folder = tmp_path / name
    folder.mkdir(exist_ok=True)
    return [
        MP3File(path=folder / f"{n:02d} - {title}.mp3", title=title, track=str(n), artist="Miles Davis", album="Kind Of Blue")
        for n, title in tracks
    ]


def _two_pressings():
    return FakeDiscogs(
        search_results=[_hit(1, "Miles Davis - Kind Of Blue"), _hit(2, "Miles Davis - Kind Of Blue", year="1997", label=("Legacy",))],
        releases={1: _release(1), 2: _release(2, year=1997, label="Legacy", catno="CK 64935", country="UK", styles=())},
        images={"https://i.discogs.com/primary.jpg": b"not-really-an-image"},
    )


def _dialog(files, fake, monkeypatch):
    monkeypatch.setattr(dlg_module, "read_cover", lambda _path: None)
    client = dl.DiscogsClient(TOKEN, fetch=fake, throttle=_fast_throttle())
    return dlg_module.DiscogsLookupDialog(dlg_module.group_by_folder(files), client)


# ---------------------------------------------------------------------------
# The lookup dialog
# ---------------------------------------------------------------------------

def test_dialog_matches_a_folder_and_builds_per_file_changes(tmp_path, monkeypatch):
    fake = _two_pressings()
    dialog = _dialog(_folder(tmp_path), fake, monkeypatch)

    result = dialog._row_results[0]
    assert result.found and result.fields["match"].startswith("2 of 2 files matched")
    assert result.fields["label"] == "Columbia CL 1355" and result.fields["genre"] == "Jazz; Modal"
    assert result.cover_bytes == b"not-really-an-image"
    assert len(result.alternatives) == 1 and "Legacy" in result.alternatives[0].label  # the other pressing
    assert result.used_query == {"artist": "Miles Davis", "album": "Kind Of Blue", "year": "1959"}

    changes = {mp3.path.name: fields for mp3, fields in dialog.file_changes()}
    assert changes["03 - Blue In Green.mp3"]["track"] == "3"
    assert changes["04 - All Blues.mp3"] == {
        "title": "All Blues", "artist": "Miles Davis", "albumartist": "Miles Davis", "album": "Kind Of Blue",
        "track": "4", "year": "1959", "genre": "Jazz; Modal", "publisher": "Columbia",
        "catalognumber": "CL 1355", "releasecountry": "US",
    }


def test_dialog_picking_another_pressing_changes_what_is_applied(tmp_path, monkeypatch):
    dialog = _dialog(_folder(tmp_path), _two_pressings(), monkeypatch)
    alternative = dialog._row_results[0].alternatives[0]
    dialog._resolve(dialog.items[0], alternative.data)
    fields = dialog.file_changes()[0][1]
    assert (fields["publisher"], fields["catalognumber"], fields["releasecountry"], fields["year"]) == ("Legacy", "CK 64935", "UK", "1997")


def test_dialog_styles_toggle_changes_summary_and_applied_genre(tmp_path, monkeypatch):
    dialog = _dialog(_folder(tmp_path), _two_pressings(), monkeypatch)
    assert dialog.styles_button.isChecked()
    dialog.styles_button.setChecked(False)
    assert dialog._row_results[0].fields["genre"] == "Jazz"
    assert {f["genre"] for _m, f in dialog.file_changes()} == {"Jazz"}
    dialog.styles_button.setChecked(True)
    assert {f["genre"] for _m, f in dialog.file_changes()} == {"Jazz; Modal"}


def test_dialog_shows_the_discogs_attribution(tmp_path, monkeypatch):
    dialog = _dialog(_folder(tmp_path), _two_pressings(), monkeypatch)
    assert any("Data from" in label.text() and "Discogs" in label.text() for label in dialog.findChildren(QLabel))


def test_dialog_unticked_row_applies_nothing(tmp_path, monkeypatch):
    dialog = _dialog(_folder(tmp_path), _two_pressings(), monkeypatch)
    dialog._checkboxes[0].setChecked(False)
    assert dialog.file_changes() == []


def test_dialog_corrected_query_searches_again(tmp_path, monkeypatch):
    fake = _two_pressings()
    dialog = _dialog(_folder(tmp_path), fake, monkeypatch)
    before = len(fake.searches())
    result = dialog._search_one(dialog.items[0], {"album": "Sketches of Spain", "year": ""})
    assert result.used_query["album"] == "Sketches of Spain"
    assert fake.searches()[before]["release_title"] == ["Sketches of Spain"]
    assert "year" not in fake.searches()[before]


def test_dialog_no_match_is_reported_not_applied(tmp_path, monkeypatch):
    fake = FakeDiscogs(search_results=[])
    dialog = _dialog(_folder(tmp_path), fake, monkeypatch)
    assert not dialog._row_results[0].found and dialog.file_changes() == []


def test_dialog_rate_limit_stops_the_batch_with_a_clear_message(tmp_path, monkeypatch):
    fake = FakeDiscogs(raises=_http_error(429, "Too Many Requests"))
    files = _folder(tmp_path, "Miles Davis - Kind Of Blue") + _folder(tmp_path, "Miles Davis - Sketches of Spain")
    dialog = _dialog(files, fake, monkeypatch)
    assert len(dialog.items) == 2
    assert all("429" in (r.error or "") for r in dialog._row_results.values())
    assert len(fake.requests) == 1  # the second folder never asked
    assert "429" in dialog.status_label.text() and TOKEN not in dialog.status_label.text()
    assert dialog.file_changes() == []


def test_dialog_network_error_is_shown_as_the_rows_error(tmp_path, monkeypatch):
    dialog = _dialog(_folder(tmp_path), FakeDiscogs(raises=urllib.error.URLError("offline")), monkeypatch)
    assert "offline" in dialog._row_results[0].error and TOKEN not in dialog._row_results[0].error


# ---------------------------------------------------------------------------
# API Keys dialog
# ---------------------------------------------------------------------------

KEY = ("redactor/mp3redactor", "discogs_token")


def test_api_keys_saves_the_token_in_the_secret_store_only(fake_keyring, tmp_path):
    dialog = keys_module.ApiKeysDialog()
    dialog.token_field.edit.setText(TOKEN)
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert fake_keyring.items[KEY] == TOKEN
    assert dl.load_token() == TOKEN
    # never in a settings file: nothing of this dialog goes through Settings at all
    assert TOKEN not in str(Settings().to_config()._sections)
    assert not (tmp_path / "secret_fallback").exists()


def test_api_keys_empty_box_keeps_the_stored_token(fake_keyring):
    secret_store.set_secret("mp3redactor", "discogs_token", TOKEN)
    dialog = keys_module.ApiKeysDialog()
    assert dialog.token_field.edit.text() == ""  # the stored value is never shown
    assert TOKEN not in dialog.token_field.edit.placeholderText() and TOKEN not in dialog.token_field.source_label.text()
    dialog.accept()
    assert fake_keyring.items[KEY] == TOKEN


def test_api_keys_typing_replaces_and_remove_deletes(fake_keyring):
    secret_store.set_secret("mp3redactor", "discogs_token", "old-token")
    dialog = keys_module.ApiKeysDialog()
    dialog.token_field.edit.setText(TOKEN)
    dialog.accept()
    assert fake_keyring.items[KEY] == TOKEN
    dialog = keys_module.ApiKeysDialog()
    dialog.token_field.remove_button.click()
    dialog.accept()
    assert KEY not in fake_keyring.items and dl.load_token() == ""


def test_api_keys_dialog_has_a_link_to_where_the_token_is_created():
    dialog = keys_module.ApiKeysDialog()
    texts = " ".join(label.text() for label in dialog.findChildren(QLabel))
    assert "discogs.com/settings/developers" in texts and "Developers" in texts


class _BrokenKeyring:
    def get_password(self, service, name):
        return None

    def set_password(self, service, name, value):
        raise RuntimeError("no secure store here")

    def delete_password(self, service, name):
        pass


def _ask(monkeypatch, answer):
    asked = []

    def question(parent, title, text, *a, **k):
        asked.append(text)
        return answer

    monkeypatch.setattr(QMessageBox, "question", staticmethod(question))
    return asked


def test_consent_declined_saves_nothing_and_keeps_the_dialog_open(monkeypatch, tmp_path):
    secret_store.set_backend(_BrokenKeyring())
    asked = _ask(monkeypatch, QMessageBox.StandardButton.No)
    remembered = []
    dialog = keys_module.ApiKeysDialog(remember_fallback=lambda: remembered.append(True))
    dialog.token_field.edit.setText(TOKEN)
    dialog.accept()
    assert len(asked) == 1 and "UNENCRYPTED" in asked[0] and TOKEN not in asked[0]
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert dialog.token_field.edit.text() == TOKEN  # kept so the user can retry
    assert remembered == [] and not secret_store.allow_unencrypted_fallback_enabled()
    assert not (tmp_path / "secret_fallback").exists()
    assert dl.load_token() == ""


def test_consent_granted_uses_the_unencrypted_file_and_is_remembered(monkeypatch, tmp_path):
    secret_store.set_backend(_BrokenKeyring())
    _ask(monkeypatch, QMessageBox.StandardButton.Yes)
    remembered = []
    dialog = keys_module.ApiKeysDialog(remember_fallback=lambda: remembered.append(True))
    dialog.token_field.edit.setText(TOKEN)
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert remembered == [True] and secret_store.allow_unencrypted_fallback_enabled()
    assert secret_store.fallback_path("mp3redactor").parent == tmp_path / "secret_fallback"  # the temp folder of the fixture
    assert dl.load_token() == TOKEN
    assert secret_store.secret_source("mp3redactor", "discogs_token") == secret_store.SOURCE_FILE


def test_with_consent_already_remembered_there_is_no_prompt(monkeypatch):
    secret_store.set_backend(_BrokenKeyring())
    secret_store.set_allow_unencrypted_fallback(True)
    asked = _ask(monkeypatch, QMessageBox.StandardButton.No)
    dialog = keys_module.ApiKeysDialog()
    dialog.token_field.edit.setText(TOKEN)
    dialog.accept()
    assert asked == [] and dialog.result() == QDialog.DialogCode.Accepted and dl.load_token() == TOKEN


# ---------------------------------------------------------------------------
# Settings flag (a yes/no, never a secret)
# ---------------------------------------------------------------------------

def test_the_fallback_consent_round_trips_in_the_settings_and_is_not_exported():
    settings = Settings(allow_unencrypted_fallback=True)
    assert Settings.from_config(settings.to_config()).allow_unencrypted_fallback is True
    assert Settings().allow_unencrypted_fallback is False
    assert not any("allow_unencrypted_fallback" in keys for keys in SECTION_KEYS.values())
    assert not any("token" in name.lower() or "secret" in name.lower() for name in Settings.__dataclass_fields__)


def test_preferences_dialog_keeps_every_other_setting():
    from gui.preferences import build_preferences_dialog

    original = Settings(redact_recipe='{"order":[]}', library_root="/music", allow_unencrypted_fallback=True, pattern_history=["%a%"])
    saved = []
    dlg = build_preferences_dialog(original, saved.append)
    dlg.set_value("delete_backup_after_fix", True)
    dlg.accept()
    assert saved and saved[-1] is original
    assert (original.redact_recipe, original.library_root, original.allow_unencrypted_fallback, original.pattern_history) == (
        '{"order":[]}', "/music", True, ["%a%"])
    assert original.delete_backup_after_fix is True


def test_preferences_dialog_maps_shared_keys_onto_existing_settings_fields():
    from gui.preferences import build_preferences_dialog

    settings = Settings(ascii_filenames=False, rename_zero_pad=False, rename_zero_pad_width=2, auto_number_padding=2)
    saved = []
    dlg = build_preferences_dialog(settings, saved.append)
    assert dlg.page_titles() == ["Filenames", "Library", "Fixing", "Tools"]
    dlg.set_value("ascii_filenames", True)
    dlg.set_value("zero_pad_numbers", True)
    dlg.set_value("zero_pad_width", 12)  # mp3 allows up to 99 digits
    dlg.set_value("auto_number_padding", 0)
    dlg.accept()
    assert (settings.ascii_filenames, settings.rename_zero_pad, settings.rename_zero_pad_width, settings.auto_number_padding) == (
        True, True, 12, 0)
    assert len(saved) == 1  # one save for the whole write


def test_preferences_cancel_changes_nothing():
    from gui.preferences import build_preferences_dialog

    settings = Settings()
    saved = []
    dlg = build_preferences_dialog(settings, saved.append)
    dlg.set_value("ascii_filenames", True)
    dlg.reject()
    assert settings.ascii_filenames is False and not saved


def test_preferences_tools_page_saves_paths_on_ok():
    from gui.external_tools_dialog import ToolPathsWidget
    from gui.preferences import build_preferences_dialog

    settings = Settings()
    saved = []
    dlg = build_preferences_dialog(settings, saved.append)
    page = dlg.findChildren(ToolPathsWidget)[0]
    page._mp3val_row.path_edit.setText("/opt/mp3val")
    dlg.accept()
    assert settings.mp3val_path == "/opt/mp3val" and saved


def test_startup_applies_the_remembered_consent(monkeypatch):
    monkeypatch.setattr(mw, "load_settings", lambda: Settings(allow_unencrypted_fallback=True))
    monkeypatch.setattr(mw, "save_settings", lambda *a, **k: None)
    w = mw.MainWindow()
    try:
        assert secret_store.allow_unencrypted_fallback_enabled()
    finally:
        w.close()


# ---------------------------------------------------------------------------
# Main window wiring
# ---------------------------------------------------------------------------

def test_menu_entries_exist_and_are_wired(window):
    registry = window.action_registry
    assert registry["discogs_lookup"].text() == "&Discogs…"
    assert registry["api_keys"].text() == labels_api_keys()
    for key in ("discogs_lookup", "api_keys"):
        assert registry[key].isEnabled() and registry[key].receivers(registry[key].triggered) > 0


def labels_api_keys():
    from redactor_common.core import labels

    return labels.API_KEYS


def test_lookup_without_a_token_opens_the_api_keys_dialog_first(window, tmp_path, monkeypatch):
    _load(window, tmp_path, ["a.mp3"])
    _select_all(window)
    opened = []
    monkeypatch.setattr(keys_module.ApiKeysDialog, "exec", lambda self: opened.append(True) or 0)
    monkeypatch.setattr(dlg_module, "DiscogsLookupDialog", lambda *a, **k: pytest.fail("no token, no lookup"))
    window.open_discogs_lookup_dialog()
    assert opened == [True]


def test_lookup_after_entering_a_token_goes_on_and_applies_through_the_review(window, tmp_path, monkeypatch):
    (a,) = _load(window, tmp_path, ["a.mp3"])
    _select_all(window)

    def enter_token(self):
        secret_store.set_secret("mp3redactor", "discogs_token", TOKEN)
        return 0

    monkeypatch.setattr(keys_module.ApiKeysDialog, "exec", enter_token)
    built = {}

    class FakeLookup:
        DialogCode = QDialog.DialogCode

        def __init__(self, albums, client, parent=None):
            built["token_ok"] = repr(client) == "DiscogsClient()"
            built["files"] = [m for album in albums for m in album.files]

        def exec(self):
            return self.DialogCode.Accepted

        def file_changes(self):
            return [(built["files"][0], {"publisher": "Columbia", "catalognumber": "CL 1355"})]

    monkeypatch.setattr(dlg_module, "DiscogsLookupDialog", FakeLookup)
    reviewed = {}

    def review(parent, items, proposed, label_for):
        reviewed["proposed"] = proposed
        reviewed["label"] = label_for("catalognumber")
        return proposed

    import redactor_common.gui.overwrite_review_dialog as review_module

    monkeypatch.setattr(review_module, "resolve_overwrite_conflicts", review)
    window.open_discogs_lookup_dialog()
    assert built["token_ok"] and reviewed["label"] == "Catalog Number"
    assert (a.publisher, a.catalognumber) == ("Columbia", "CL 1355") and a.dirty
    window.undo_last_action()
    assert a.publisher == "" and a.catalognumber == ""


def test_the_consent_is_remembered_in_the_settings_by_the_window(window):
    window._remember_unencrypted_fallback()
    assert window.settings.allow_unencrypted_fallback is True
