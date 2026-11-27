"""Review of 2026-11-27, batch 3 (cards 37-42: threads, Cancel, slow work on the GUI thread; Import Settings).
One regression test per card."""

import os
import threading
import urllib.error
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtCore import QObject, pyqtSignal  # noqa: E402

import core.redact_steps as rs  # noqa: E402
from core import acoustid_lookup as ac  # noqa: E402
from core.settings import Settings  # noqa: E402
from core.settings_adapter import Mp3SettingsAdapter  # noqa: E402
from tests.test_cover_gui import _app, _load, _select_all, window  # noqa: E402,F401


# --- card 37: the AcoustID pass can be cancelled, and stops after the first timeout ----------------

def test_card37_identify_files_stops_on_cancel_and_after_the_first_timeout(tmp_path, monkeypatch):
    seen = []

    def fake_fingerprint(path, _fpcalc):
        seen.append(path.name)
        return ac.Fingerprint(100, path.name)

    monkeypatch.setattr(ac, "fingerprint_file", fake_fingerprint)
    paths = [tmp_path / f"{i}.mp3" for i in range(5)]

    def dead_post(_url, _data):
        raise TimeoutError("timed out")

    hits, problems = ac.identify_files(paths, Path("fpcalc"), dead_post)
    assert len(hits) == 5 and len(problems) == 1 and seen == ["0.mp3"]  # one timeout, not five

    seen.clear()
    calls = []

    def cancel_after_two():
        calls.append(1)
        return len(calls) > 2

    hits, problems = ac.identify_files(paths, Path("fpcalc"), lambda _u, _d: b'{"status": "ok", "results": []}',
                                       should_cancel=cancel_after_two)
    assert seen == ["0.mp3", "1.mp3"] and len(hits) == 5 and problems == []


def test_card37_redact_hands_its_cancel_state_to_the_fingerprint_pass(tmp_path, monkeypatch):
    from tests.test_redact_steps import make_file

    mp3 = make_file(tmp_path)
    env = rs.RedactEnv(Settings())
    env.begin([mp3])
    given = {}

    def fake_identify(paths, fpcalc, post=None, should_cancel=None):
        given["should_cancel"] = should_cancel
        raise ac.AcoustIdError("stop here")

    monkeypatch.setattr(rs, "identify_files", fake_identify)
    monkeypatch.setattr(rs, "find_tool", lambda *a, **k: Path("fpcalc"))
    folder = os.path.dirname(os.path.realpath(mp3.path))
    rs._lookup_folder(env, folder, True)
    assert given["should_cancel"]() is False
    env.cancel_event.set()
    assert given["should_cancel"]() is True
    env.begin([mp3])  # the next run starts clean
    assert not env.cancel_event.is_set()


def test_card37_lookup_dialog_cancel_sets_the_searchs_event_and_is_not_cached(tmp_path, monkeypatch):
    from core.mp3_file import MP3File
    from gui import musicbrainz_lookup_dialog as dlg_module
    from tests.test_acoustid_lookup import FakeMusicBrainz

    monkeypatch.setattr(dlg_module, "fetch_front_cover", lambda _id, **_kw: None)
    monkeypatch.setattr(dlg_module, "read_cover", lambda _path: None)
    folder = tmp_path / "New Folder (3)"
    folder.mkdir()
    files = [MP3File(path=folder / "a.mp3", duration_seconds=100.0)]
    dialog = dlg_module.MusicBrainzLookupDialog(
        dlg_module.group_by_folder(files), fetch=FakeMusicBrainz([], search_hits=[]), fpcalc=None)

    class Source(QObject):
        canceled = pyqtSignal()

    class Reporter:
        def __init__(self, dialog):
            self.dialog = dialog

    source = Source()
    before = dialog._cancel_event
    assert dialog._cancel_signal(Reporter(source)) is not None
    current = dialog._cancel_event
    assert current is not before and not current.is_set()
    source.canceled.emit()
    assert current.is_set()  # Cancel reaches the running pass

    fingerprinted = []
    monkeypatch.setattr(ac, "fingerprint_file", lambda path, _f: fingerprinted.append(path) or ac.Fingerprint(1, "x"))
    dialog._fpcalc = Path("fpcalc")
    album = dialog.items[0]
    dialog._fingerprint(album)
    assert fingerprinted == []  # a cancelled pass asks for no file
    assert id(album) not in dialog._fingerprints  # and a half-done pass is not kept as the answer


# --- card 38: the folder decision is made once per folder, on a sample -------------------------------

def test_card38_selection_scope_reads_tags_once_per_folder_and_only_a_sample(tmp_path):
    from core.notes_flow import NotesEngine, selection_scope
    from core.notes_local import default_loader
    from tests.notes_helpers import make_mp3

    artist = tmp_path / "Ultravox"
    selected = []
    for album in ("Vienna (1980)", "Quartet (1982)", "Lament (1984)", "Rage in Eden (1981)", "Systems of Romance (1978)"):
        for i in range(1, 13):
            path = make_mp3(artist / album / f"{i:02d} t{i}.mp3", f"T{i}", "Ultravox", album.split(" (")[0], i,
                            album[-5:-1])
            if album == "Vienna (1980)":
                selected.append(path)
    assert len(selected) == 12
    read = []

    def counting_loader(paths):
        read.extend(paths)
        return default_loader(paths)

    folders = selection_scope(selected, counting_loader)
    assert [p.name for p in folders] == ["Vienna (1980)", "Ultravox"]
    limit = NotesEngine.SAMPLE_ALBUMS * NotesEngine.SAMPLE_FILES
    assert len(read) <= limit, f"{len(read)} tag reads for 12 selected files (60 files in the artist folder)"


# --- card 39: the MusicBrainz busy-wait watches the cancel test ---------------------------------------

def _busy_fetch():
    def fetch(url):
        raise urllib.error.HTTPError(url, 503, "Service Unavailable", {}, None)

    return fetch


def test_card39_a_cancel_ends_the_busy_wait_within_a_slice(monkeypatch):
    from core import notes_musicbrainz as nmb

    pauses = []
    monkeypatch.setattr(nmb, "_sleep", pauses.append)
    state = {"asks": 0}

    def should_cancel():
        state["asks"] += 1
        return state["asks"] > 3

    fetch = _busy_fetch()
    fetch.should_cancel = should_cancel
    with pytest.raises(nmb.MusicBrainzError, match="Cancelled"):
        nmb._get("artist/x", fetch)
    assert pauses and max(pauses) <= 0.25  # slices, never one 1.5 s sleep


def test_card39_build_proposals_gives_the_cancel_test_to_the_musicbrainz_fetch(tmp_path):
    from core.notes_flow import NotesEngine, build_proposals

    engine = NotesEngine()
    cancel = lambda: False  # noqa: E731
    build_proposals([], engine=engine, cancel=cancel)
    assert engine._mb.should_cancel is cancel


# --- card 40: saving one or two files shows the progress dialog -----------------------------------

def test_card40_save_shows_the_progress_dialog_from_the_first_file(window, tmp_path, monkeypatch):
    import gui.main_window as mw

    files = _load(window, tmp_path, ["a.mp3"])
    given = {}

    class Stop(Exception):
        pass

    def fake_run(parent, items, step, label, threshold=3, **kwargs):
        given["threshold"] = threshold
        raise Stop

    monkeypatch.setattr(mw, "run_with_progress", fake_run)
    with pytest.raises(Stop):
        window._save_dirty_files(files)
    assert given["threshold"] == 1


# --- card 41: Redact's start-up cleanup is off the GUI thread -----------------------------------------

def test_card41_redact_start_up_cleanup_runs_off_the_gui_thread(window, tmp_path, monkeypatch):
    import gui.main_window as mw

    _load(window, tmp_path, ["a.mp3", "b.mp3"])
    _select_all(window)
    where = {}
    real_begin = rs.RedactEnv.begin

    def spying_begin(self, items):
        where["off_gui_thread"] = threading.current_thread() is not threading.main_thread()
        real_begin(self, items)

    monkeypatch.setattr(rs.RedactEnv, "begin", spying_begin)
    monkeypatch.setattr(mw, "run_redact_dialog", lambda *a, **k: None)
    monkeypatch.setattr(mw.MainWindow, "_after_cover_change", lambda self: None)
    window.redact_files()
    assert where.get("off_gui_thread") is True


# --- card 42: Import Settings accepts only real tools by the tool's name -------------------------------

def _tool_adapter(tmp_path, settings):
    holder = {"s": settings}
    return Mp3SettingsAdapter(lambda: holder["s"], "test", target_dir=tmp_path)


def test_card42_imported_tool_paths_must_exist_and_be_named_like_the_tool(tmp_path, monkeypatch):
    import core.settings_adapter as sa

    monkeypatch.setattr(sa, "save_settings", lambda *a, **k: None)
    good = tmp_path / "ffmpeg.exe"
    good.write_bytes(b"x")
    bare = tmp_path / "FFprobe"
    bare.write_bytes(b"x")
    evil = tmp_path / "downloaded.exe"
    evil.write_bytes(b"x")
    wrong_tool = tmp_path / "ffmpeg2.exe"  # exists, but is not called ffmpeg
    wrong_tool.write_bytes(b"x")
    missing = str(tmp_path / "mp3val.exe")  # named right, not on this computer
    settings = Settings(fpcalc_path="old-fpcalc")
    adapter = _tool_adapter(tmp_path, settings)
    adapter.write_section("tools", {
        "ffmpeg_path": str(good), "ffprobe_path": str(bare), "keyfinder_cli_path": str(evil),
        "mp3val_path": missing, "fpcalc_path": str(wrong_tool),
    })
    assert settings.ffmpeg_path == str(good)
    assert settings.ffprobe_path == str(bare)  # no .exe, any case: accepted
    assert settings.keyfinder_cli_path == ""  # any other program: refused
    assert settings.mp3val_path == ""  # not on this computer: refused
    assert settings.fpcalc_path == "old-fpcalc"  # a wrong name keeps the current value
