"""Export/Import Settings: the Mp3SettingsAdapter over core.settings.Settings
and its wiring into File > Export Settings / Import Settings."""

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from core import settings_adapter as sa  # noqa: E402
from core.settings import Settings, load_settings  # noqa: E402
from core.settings_adapter import Mp3SettingsAdapter  # noqa: E402
from redactor_common.core import settings_bundle as sb  # noqa: E402
from tests.test_cover_gui import _app, window  # noqa: E402,F401


def _adapter(tmp_path, settings=None, **kw):
    holder = {"s": settings or Settings()}
    return holder, Mp3SettingsAdapter(lambda: holder["s"], "test", target_dir=tmp_path, **kw)


def _export(adapter, sections=None):
    chosen = sections if sections is not None else sb.default_selection(adapter)
    return sb.dump_bundle(sb.build_bundle(adapter, chosen))


def test_round_trip_restores_settings(tmp_path):
    original = Settings(
        redact_recipe='{"steps": []}',
        pattern_history=["%artist% - %title%", "%a%/%b%"],
        rename_pattern="%track% %title%",
        move_pattern="%albumartist%/%album%",
        hidden_columns=["bpm"],
        column_order=["title", "artist"],
        has_column_preference=True,
        ascii_filenames=True,
        rename_zero_pad=True,
        rename_zero_pad_width=3,
        auto_number_padding=4,
        custom_genres=["Chiptune"],
        custom_languages=[("tlh", "Klingon")],
    )
    _holder, adapter = _adapter(tmp_path, original)
    text = _export(adapter)

    holder, adapter2 = _adapter(tmp_path, Settings())
    bundle = sb.parse_bundle(text, "mp3redactor")
    result = sb.apply_bundle(adapter2, bundle, sb.default_selection(adapter2))
    assert result.failed == {}
    restored = holder["s"]
    for name in ("redact_recipe", "pattern_history", "rename_pattern", "move_pattern",
                 "hidden_columns", "column_order", "has_column_preference", "ascii_filenames",
                 "rename_zero_pad", "rename_zero_pad_width", "auto_number_padding",
                 "custom_genres", "custom_languages"):
        assert getattr(restored, name) == getattr(original, name), name
    # and it was persisted through the atomic ini save
    assert load_settings(tmp_path).pattern_history == original.pattern_history


def test_export_then_change_then_import_restores(tmp_path):
    holder, adapter = _adapter(tmp_path, Settings(rename_zero_pad_width=3))
    text = _export(adapter)
    holder["s"].rename_zero_pad_width = 5
    sb.apply_bundle(adapter, sb.parse_bundle(text, "mp3redactor"), sb.default_selection(adapter))
    assert holder["s"].rename_zero_pad_width == 3


def test_read_section_normalises_to_json_types(tmp_path):
    _holder, adapter = _adapter(tmp_path, Settings(custom_languages=[("tlh", "Klingon")]))
    for spec in adapter.sections():
        json.dumps(adapter.read_section(spec.key))  # must be JSON-able
    assert adapter.read_section("genres_languages")["custom_languages"] == [["tlh", "Klingon"]]
    defaults = adapter.read_section("field_defaults")
    assert defaults["rename_zero_pad"] is False
    assert defaults["rename_zero_pad_width"] == 2 and isinstance(defaults["rename_zero_pad_width"], int)
    assert isinstance(adapter.read_section("columns")["hidden_columns"], list)


def test_read_section_covers_every_declared_key(tmp_path):
    _holder, adapter = _adapter(tmp_path)
    for key, fields in sa.SECTION_KEYS.items():
        assert set(adapter.read_section(key)) == set(fields)


def test_machine_specific_sections_unticked_by_default(tmp_path):
    _holder, adapter = _adapter(tmp_path)
    default = sb.default_selection(adapter)
    assert default == {"recipe", "patterns", "columns", "field_defaults", "genres_languages"}
    machine = {s.key for s in adapter.sections() if not s.portable}
    assert machine == {"tools", "folders"}
    assert all("this computer only" in s.label for s in adapter.sections() if not s.portable)
    exported = json.loads(_export(adapter))
    assert not machine & set(exported["sections"])


def test_machine_specific_exported_only_on_opt_in(tmp_path):
    _holder, adapter = _adapter(tmp_path, Settings(ffmpeg_path="C:/x/ffmpeg.exe", library_root="/music"))
    exported = json.loads(_export(adapter, {"tools", "folders"}))
    assert exported["sections"]["tools"]["items"]["ffmpeg_path"] == "C:/x/ffmpeg.exe"
    assert exported["sections"]["folders"]["items"]["library_root"] == "/music"


def test_no_exported_key_looks_secret(tmp_path):
    _holder, adapter = _adapter(tmp_path)
    for spec in adapter.sections():
        assert not [k for k in adapter.read_section(spec.key) if sb.looks_secret(k)]


def test_secret_looking_key_in_file_is_never_applied(tmp_path):
    holder, adapter = _adapter(tmp_path)
    doc = {"format": "redactor-settings", "version": 1, "app": "mp3redactor", "sections": {
        "patterns": {"items": {"api_key": "hunter2", "rename_pattern": "%title%"}}}}
    bundle = sb.parse_bundle(json.dumps(doc), "mp3redactor")
    sb.apply_bundle(adapter, bundle, {"patterns"})
    assert holder["s"].rename_pattern == "%title%"
    assert not hasattr(holder["s"], "api_key")
    assert "hunter2" not in (tmp_path / "mp3redactor_settings.ini").read_text(encoding="utf-8")


def test_unknown_keys_and_sections_are_ignored(tmp_path):
    holder, adapter = _adapter(tmp_path)
    before = Settings()
    doc = {"format": "redactor-settings", "version": 1, "app": "mp3redactor", "sections": {
        "patterns": {"items": {"nonsense": 1, "last_directory": "/elsewhere"}},
        "mystery": {"items": {"x": 1}}}}
    bundle = sb.parse_bundle(json.dumps(doc), "mp3redactor")
    sb.apply_bundle(adapter, bundle, {"patterns", "mystery"})
    assert holder["s"] == before  # last_directory (another section's key) untouched


def test_wrong_typed_values_are_skipped(tmp_path):
    holder, adapter = _adapter(tmp_path, Settings(rename_zero_pad_width=3))
    adapter.write_section("field_defaults", {
        "rename_zero_pad_width": "wide", "rename_zero_pad": "yes", "auto_number_padding": True,
        "ascii_filenames": True,
    })
    s = holder["s"]
    assert s.rename_zero_pad_width == 3 and s.rename_zero_pad is False and s.auto_number_padding == 2
    assert s.ascii_filenames is True
    adapter.write_section("genres_languages", {"custom_languages": [["only-one"]], "custom_genres": ["A"]})
    assert s.custom_languages == [] and s.custom_genres == ["A"]


def test_file_from_another_app_is_rejected():
    doc = {"format": "redactor-settings", "version": 1, "app": "cbzredactor", "sections": {}}
    with pytest.raises(sb.SettingsBundleError):
        sb.parse_bundle(json.dumps(doc), "mp3redactor")


def test_redetect_tools_only_offered_when_supplied(tmp_path):
    _h, plain = _adapter(tmp_path)
    assert plain.redetect_tools is None
    _h, with_cb = _adapter(tmp_path, redetect_tools=lambda: None)
    assert callable(with_cb.redetect_tools)


# -- GUI wiring ---------------------------------------------------------------


def test_menu_actions_enabled_and_invoke_dialogs(window, monkeypatch):
    import gui.main_window as mw

    calls = []
    monkeypatch.setattr(mw, "export_settings", lambda parent, adapter: calls.append(("export", adapter)))
    monkeypatch.setattr(
        mw, "import_settings",
        lambda parent, adapter, on_applied=None: calls.append(("import", adapter, on_applied)))
    export_act = window.action_registry["export_settings"]
    import_act = window.action_registry["import_settings"]
    assert export_act.isEnabled() and import_act.isEnabled()
    export_act.trigger()
    import_act.trigger()
    assert [c[0] for c in calls] == ["export", "import"]
    assert all(isinstance(c[1], Mp3SettingsAdapter) and c[1].app_slug == "mp3redactor" for c in calls)
    assert calls[1][2] == window._on_settings_imported


def test_import_applies_columns_live(window, monkeypatch):
    monkeypatch.setattr(sa, "save_settings", lambda *a, **k: None)
    adapter = window._settings_adapter()
    order = list(reversed(window._column_keys))
    hide = next(k for k in window._column_keys if k not in __import__("gui.main_window").main_window.PROTECTED_COLUMNS)
    adapter.write_section("columns", {"column_order": order, "hidden_columns": [hide],
                                      "has_column_preference": True})
    window._on_settings_imported(sb.ApplyResult(applied=["columns"]))
    header = window.table.horizontalHeader()
    assert [window._column_keys[header.logicalIndex(v)] for v in range(header.count())] == order
    assert window.table.isColumnHidden(window._col_index[hide])
