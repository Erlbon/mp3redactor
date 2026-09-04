from core.settings import Settings, load_settings, save_settings


def test_default_settings_keep_backups():
    settings = Settings()
    assert settings.delete_backup_after_fix is False


def test_default_settings_have_empty_tool_paths():
    settings = Settings()
    assert settings.mp3val_path == ""
    assert settings.keyfinder_cli_path == ""


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
