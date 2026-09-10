from core.settings import (
    Settings,
    dedupe_and_trim_pattern_history,
    directory_for,
    load_settings,
    resolve_start_directory,
    save_settings,
)


def test_default_settings_keep_backups():
    settings = Settings()
    assert settings.delete_backup_after_fix is False


def test_default_settings_have_empty_tool_paths():
    settings = Settings()
    assert settings.mp3val_path == ""
    assert settings.keyfinder_cli_path == ""


def test_default_settings_have_empty_last_directory():
    assert Settings().last_directory == ""


def test_save_then_load_round_trips_last_directory(tmp_path):
    save_settings(Settings(last_directory=str(tmp_path)), target_dir=tmp_path)
    loaded = load_settings(target_dir=tmp_path)
    assert loaded.last_directory == str(tmp_path)


def test_resolve_start_directory_returns_empty_for_blank():
    assert resolve_start_directory("") == ""


def test_resolve_start_directory_returns_empty_for_nonexistent_path(tmp_path):
    missing = tmp_path / "does-not-exist"
    assert resolve_start_directory(str(missing)) == ""


def test_resolve_start_directory_returns_path_when_still_valid(tmp_path):
    assert resolve_start_directory(str(tmp_path)) == str(tmp_path)


def test_directory_for_a_folder_returns_itself(tmp_path):
    assert directory_for(str(tmp_path)) == str(tmp_path)


def test_directory_for_a_file_returns_its_parent(tmp_path):
    f = tmp_path / "song.mp3"
    f.write_bytes(b"")
    assert directory_for(str(f)) == str(tmp_path)


def test_save_then_load_round_trips_tool_paths(tmp_path):
    save_settings(
        Settings(mp3val_path="C:/tools/mp3val.exe", keyfinder_cli_path="C:/tools/keyfinder-cli.exe"),
        target_dir=tmp_path,
    )
    loaded = load_settings(target_dir=tmp_path)
    assert loaded.mp3val_path == "C:/tools/mp3val.exe"
    assert loaded.keyfinder_cli_path == "C:/tools/keyfinder-cli.exe"


def test_load_settings_returns_defaults_when_no_file_exists(tmp_path):
    settings = load_settings(target_dir=tmp_path)
    assert settings.delete_backup_after_fix is False


def test_save_then_load_round_trips(tmp_path):
    save_settings(Settings(delete_backup_after_fix=True), target_dir=tmp_path)
    loaded = load_settings(target_dir=tmp_path)
    assert loaded.delete_backup_after_fix is True


def test_save_then_load_round_trips_false_explicitly(tmp_path):
    # Round-trip the default value too, not just the non-default one --
    # a naive implementation could accidentally treat "False" as falsy
    # and fall through to some other default.
    save_settings(Settings(delete_backup_after_fix=True), target_dir=tmp_path)
    save_settings(Settings(delete_backup_after_fix=False), target_dir=tmp_path)
    loaded = load_settings(target_dir=tmp_path)
    assert loaded.delete_backup_after_fix is False


def test_load_settings_handles_corrupt_file_gracefully(tmp_path):
    bad_file = tmp_path / "settings.ini"
    bad_file.write_text("this is not valid ini content [[[", encoding="utf-8")
    settings = load_settings(target_dir=tmp_path)
    assert settings.delete_backup_after_fix is False


def test_default_settings_have_empty_column_and_genre_language_lists():
    settings = Settings()
    assert settings.hidden_columns == []
    assert settings.column_order == []
    assert settings.has_column_preference is False
    assert settings.custom_genres == []
    assert settings.hidden_default_genres == []
    assert settings.custom_languages == []
    assert settings.hidden_default_languages == []


def test_save_then_load_round_trips_hidden_and_ordered_columns(tmp_path):
    save_settings(
        Settings(hidden_columns=["bpm", "key"], column_order=["filename", "title", "artist"]),
        target_dir=tmp_path,
    )
    loaded = load_settings(target_dir=tmp_path)
    assert loaded.hidden_columns == ["bpm", "key"]
    assert loaded.column_order == ["filename", "title", "artist"]


def test_save_then_load_round_trips_has_column_preference(tmp_path):
    # Explicit-empty-list-but-configured case: has_column_preference is
    # the signal that distinguishes "never touched" from "deliberately
    # shows everything" (hidden_columns == [] either way).
    save_settings(Settings(hidden_columns=[], has_column_preference=True), target_dir=tmp_path)
    loaded = load_settings(target_dir=tmp_path)
    assert loaded.hidden_columns == []
    assert loaded.has_column_preference is True


def test_save_then_load_round_trips_custom_and_hidden_genres(tmp_path):
    save_settings(
        Settings(custom_genres=["Vaporwave", "Lo-Fi Hip Hop"], hidden_default_genres=["Polka"]),
        target_dir=tmp_path,
    )
    loaded = load_settings(target_dir=tmp_path)
    assert loaded.custom_genres == ["Vaporwave", "Lo-Fi Hip Hop"]
    assert loaded.hidden_default_genres == ["Polka"]


def test_save_then_load_round_trips_custom_and_hidden_languages(tmp_path):
    save_settings(
        Settings(
            custom_languages=[("nld", "Dutch"), ("isl", "Icelandic")],
            hidden_default_languages=["und"],
        ),
        target_dir=tmp_path,
    )
    loaded = load_settings(target_dir=tmp_path)
    assert loaded.custom_languages == [("nld", "Dutch"), ("isl", "Icelandic")]
    assert loaded.hidden_default_languages == ["und"]


def test_load_settings_tolerates_malformed_list_values(tmp_path):
    bad_file = tmp_path / "mp3redactor_settings.ini"
    bad_file.write_text(
        "[general]\nhidden_columns = not valid json [[[\ncustom_genres = 42\n",
        encoding="utf-8",
    )
    settings = load_settings(target_dir=tmp_path)
    assert settings.hidden_columns == []
    assert settings.custom_genres == []


def test_default_settings_have_empty_pattern_history():
    assert Settings().pattern_history == []


def test_save_then_load_round_trips_pattern_history(tmp_path):
    save_settings(
        Settings(pattern_history=["%artist% - %title%", "%track% - %title%"]),
        target_dir=tmp_path,
    )
    loaded = load_settings(target_dir=tmp_path)
    assert loaded.pattern_history == ["%artist% - %title%", "%track% - %title%"]


def test_dedupe_and_trim_pattern_history_moves_repeat_to_front():
    history = ["%artist% - %title%", "%track% - %title%"]
    result = dedupe_and_trim_pattern_history(history, "%track% - %title%")
    assert result == ["%track% - %title%", "%artist% - %title%"]


def test_dedupe_and_trim_pattern_history_inserts_new_pattern_first():
    result = dedupe_and_trim_pattern_history(["%artist% - %title%"], "%track% - %title%")
    assert result == ["%track% - %title%", "%artist% - %title%"]


def test_dedupe_and_trim_pattern_history_ignores_blank_pattern():
    history = ["%artist% - %title%"]
    assert dedupe_and_trim_pattern_history(history, "   ") == history


def test_dedupe_and_trim_pattern_history_caps_length():
    history = [f"%field{i}%" for i in range(20)]
    result = dedupe_and_trim_pattern_history(history, "%new%")
    assert len(result) == 15
    assert result[0] == "%new%"
