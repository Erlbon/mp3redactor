"""Shared test setup."""

import pytest


@pytest.fixture(autouse=True)
def _isolated_rename_log(monkeypatch, tmp_path):
    """File > Undo Last Rename's log (redactor_common's RenameLog) lives
    next to the settings -- the project folder when running from source.
    Tests that rename files point it at a temporary folder instead."""
    import gui.main_window as main_window
    from redactor_common.core.rename_log import RenameLog

    log = RenameLog(str(tmp_path / "rename_log.json"))
    monkeypatch.setattr(main_window, "_rename_log", lambda: log)
