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


class FakeRecycleBin:
    """Stands in for the Recycle Bin in Redact tests: moves the file to a
    folder and remembers it, so nothing real is ever trashed."""

    def __init__(self, folder):
        self.folder = folder
        self.trashed: list[str] = []
        self.fail_with: Exception | None = None

    def __call__(self, path: str) -> None:
        import os
        import shutil

        if self.fail_with is not None:
            raise self.fail_with
        self.folder.mkdir(exist_ok=True)
        shutil.move(path, str(self.folder / f"{len(self.trashed)}-{os.path.basename(path)}"))
        self.trashed.append(path)


@pytest.fixture(autouse=True)
def recycle_bin(monkeypatch, tmp_path):
    """Redact's default trash function is the real Recycle Bin; tests get
    the fake one (RedactEnv(trash=None) looks move_to_trash up at call time)."""
    import core.redact_steps as redact_steps

    bin_ = FakeRecycleBin(tmp_path / "_recycle_bin")
    monkeypatch.setattr(redact_steps, "move_to_trash", bin_)
    return bin_
