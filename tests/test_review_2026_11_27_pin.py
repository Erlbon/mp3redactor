"""Review of 2026-11-27, card 66 (redactor_common pin 2026-11-27-05) and card 53 (lyrics pacing): the Play window
lets go of a file before Save and the filename Search/Replace touch it, the lyrics bulk fetch is paced, and pywin32
is listed for Windows."""

import os
import threading
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import redactor_common.gui.media_preview as media_preview  # noqa: E402

from core import scan_service  # noqa: E402
from core.mp3_file import STATUS_ERROR  # noqa: E402
from core.mp3_file import MP3File  # noqa: E402
from tests.test_cover_gui import _app, _load, window  # noqa: E402,F401

ROOT = Path(__file__).resolve().parent.parent


def test_requirements_pin_and_pywin32():
    text = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert 'pywin32>=306; sys_platform == "win32"' in text
    assert "redactor_common.git@2026-11-26-02" not in text


def test_save_releases_the_play_window_first(window, tmp_path, monkeypatch):
    files = _load(window, tmp_path, ["a.mp3"])
    released = []
    monkeypatch.setattr(media_preview, "release_previews_of", lambda paths: released.append(list(paths)))
    files[0].apply_tags({"title": "New title"})
    window._save_dirty_files([files[0]])
    assert [str(files[0].path)] in released


def test_filename_search_replace_releases_the_play_window_first(window, tmp_path, monkeypatch):
    files = _load(window, tmp_path, ["a.mp3"])
    old = str(files[0].path)
    released = []
    monkeypatch.setattr(media_preview, "release_previews_of", lambda paths: released.append(list(paths)))
    window._apply_filename_search_replace(files, {0: "b"})
    assert [old] in released
    assert files[0].path.name == "b.mp3"


def test_lyrics_fetch_is_paced(monkeypatch):
    starts = []
    lock = threading.Lock()
    in_flight = {"now": 0, "max": 0}

    def fake_fetch(query, artist, title):
        with lock:
            starts.append(time.monotonic())
            in_flight["now"] += 1
            in_flight["max"] = max(in_flight["max"], in_flight["now"])
        time.sleep(0.05)
        with lock:
            in_flight["now"] -= 1
        return "", STATUS_ERROR, "not found"

    monkeypatch.setattr(scan_service, "fetch_lyrics", fake_fetch)
    monkeypatch.setattr(scan_service, "LYRICS_MIN_INTERVAL", 0.1)
    files = [MP3File(path=Path(f"C:/x/{i}.mp3")) for i in range(5)]
    scan_service.run_lyrics_fetch(files)
    starts.sort()
    gaps = [b - a for a, b in zip(starts, starts[1:])]
    assert len(starts) == 5
    assert min(gaps) >= 0.06  # LYRICS_MIN_INTERVAL 0.1, less Windows' ~16 ms clock tick on each end
    assert in_flight["max"] <= scan_service.LYRICS_MAX_WORKERS
