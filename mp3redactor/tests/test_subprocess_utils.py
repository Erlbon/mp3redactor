from core import subprocess_utils
from core.subprocess_utils import no_window_kwargs


def test_no_window_kwargs_empty_on_non_windows(monkeypatch):
    monkeypatch.setattr(subprocess_utils.sys, "platform", "linux")
    assert no_window_kwargs() == {}


def test_no_window_kwargs_sets_creationflags_on_windows(monkeypatch):
    monkeypatch.setattr(subprocess_utils.sys, "platform", "win32")
    # subprocess.CREATE_NO_WINDOW only exists as an attribute on Windows
    # Python builds, so it can't be referenced directly in a
    # platform-agnostic test -- patch it onto the subprocess module here
    # the same way it would genuinely be present on a real Windows build.
    import subprocess

    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    result = no_window_kwargs()
    assert result == {"creationflags": 0x08000000}
