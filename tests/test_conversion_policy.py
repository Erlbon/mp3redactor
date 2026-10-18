"""The shared conversion policy (redactor_common conversion_policy) in mp3redactor: the legacy defaults, the three
on_open values, keep / quarantine / recycle for the original with its undo log, the dry run, and the Preferences >
Conversion page. Offscreen, tmp_path, real small audio (wav; ffmpeg only where noted)."""

import os
import shutil
import wave
from datetime import date
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtWidgets import QMessageBox  # noqa: E402

import gui.main_window as mw  # noqa: E402
from core import conversion_safety as cs  # noqa: E402
from core.mp3_file import STATUS_OK  # noqa: E402
from core.settings import Settings  # noqa: E402
from core.tool_locator import find_tool  # noqa: E402
from redactor_common.core import conversion_policy as pm  # noqa: E402
from tests.test_cover_gui import FIXTURE, window  # noqa: E402,F401

DAY = date(2026, 10, 18)


def _wav(path: Path, seconds: float = 0.5) -> Path:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x00" * int(8000 * seconds))
    return path


def _pair(tmp_path, name="song"):
    """A real wav and a 'converted' mp3 beside it (the fixture mp3: readable, its length within tolerance)."""
    src = _wav(tmp_path / f"{name}.wav")
    dest = tmp_path / f"{name}.mp3"
    shutil.copyfile(FIXTURE, dest)
    return src, dest


def _policy(originals, **kw):
    kw.setdefault("recent_minutes", 0)
    return pm.ConversionPolicy(originals=originals, **kw)


# -- defaults equal the old behaviour -----------------------------------------------------------------------------------


def test_a_never_saved_policy_is_what_the_app_did_before():
    policy = cs.get_policy(Settings())
    assert (policy.on_open, policy.originals, policy.dry_run_first) == (pm.ON_OPEN_ASK, pm.ORIGINALS_KEEP, False)


def test_the_policy_round_trips_through_the_settings_file():
    settings = Settings()
    cs.save_policy(settings, pm.ConversionPolicy(on_open=pm.ON_OPEN_AUTO, originals=pm.ORIGINALS_RECYCLE, dry_run_first=False))
    again = Settings.from_config(settings.to_config())
    assert cs.get_policy(again).on_open == pm.ON_OPEN_AUTO and cs.get_policy(again).originals == pm.ORIGINALS_RECYCLE
    assert Settings.from_config(Settings().to_config()).conversion_policy == {}


# -- the original: keep / quarantine / recycle --------------------------------------------------------------------------


def test_keep_leaves_the_original(tmp_path):
    src, dest = _pair(tmp_path)
    out = cs.handle_original(src, dest, _policy(pm.ORIGINALS_KEEP), day=DAY)
    assert out.action == cs.KEPT and src.exists() and dest.exists()
    assert not (tmp_path / f"converted originals {DAY.isoformat()}").exists()


def test_quarantine_moves_the_verified_original_and_writes_an_undo_log(tmp_path):
    src, dest = _pair(tmp_path)
    out = cs.handle_original(src, dest, _policy(pm.ORIGINALS_QUARANTINE), day=DAY)
    folder = tmp_path / f"converted originals {DAY.isoformat()}"
    assert out.action == cs.QUARANTINED and out.moved_to == folder / "song.wav"
    assert not src.exists() and (folder / "song.wav").exists() and dest.exists()
    log = tmp_path / f"converted originals {DAY.isoformat()}.undo.tsv"
    assert log.read_text(encoding="utf-8").splitlines() == [f"{dest}\tCREATED", f"{folder / 'song.wav'}\t{src}"]
    assert cs.undo_conversion(log) == []
    assert src.exists() and not (folder / "song.wav").exists() and dest.exists()  # the new mp3 is never deleted


def test_quarantine_uses_the_set_folders_and_never_overwrites(tmp_path):
    q, logs = tmp_path / "q", tmp_path / "logs"
    policy = _policy(pm.ORIGINALS_QUARANTINE, quarantine_folder=str(q), undo_log_folder=str(logs))
    for sub in ("a", "b"):
        (tmp_path / sub).mkdir()
        src, dest = _pair(tmp_path / sub)
        assert cs.handle_original(src, dest, policy, day=DAY).action == cs.QUARANTINED
    names = sorted(p.name for p in (q / f"converted originals {DAY.isoformat()}").iterdir())
    assert names == ["song (2).wav", "song.wav"]
    assert (logs / f"converted originals {DAY.isoformat()}.undo.tsv").is_file()


def test_recycle_sends_the_original_to_the_bin(tmp_path):
    src, dest = _pair(tmp_path)
    trashed = []
    out = cs.handle_original(src, dest, _policy(pm.ORIGINALS_RECYCLE), trash=trashed.append)
    assert out.action == cs.RECYCLED and trashed == [str(src)]


def test_a_failing_recycle_bin_keeps_the_original(tmp_path):
    src, dest = _pair(tmp_path)

    def broken(_path):
        raise RuntimeError("no bin here")

    out = cs.handle_original(src, dest, _policy(pm.ORIGINALS_RECYCLE), trash=broken)
    assert out.action == cs.LEFT and "no bin here" in out.detail and src.exists()


@pytest.mark.parametrize("originals", [pm.ORIGINALS_QUARANTINE, pm.ORIGINALS_RECYCLE])
def test_an_unverified_result_never_touches_the_original(tmp_path, originals):
    src = _wav(tmp_path / "song.wav")
    trashed = []
    missing = cs.handle_original(src, tmp_path / "song.mp3", _policy(originals), trash=trashed.append)
    assert missing.action == cs.LEFT and "missing" in missing.detail
    garbage = tmp_path / "song.mp3"
    garbage.write_bytes(b"not an mp3 at all")
    assert cs.handle_original(src, garbage, _policy(originals), trash=trashed.append).action == cs.LEFT
    long_src = _wav(tmp_path / "long.wav", seconds=5)  # the new mp3 is 0.37 s: more than 1.5 s short
    shutil.copyfile(FIXTURE, tmp_path / "long.mp3")
    out = cs.handle_original(long_src, tmp_path / "long.mp3", _policy(originals), trash=trashed.append)
    assert out.action == cs.LEFT and "original" in out.detail
    assert src.exists() and long_src.exists() and trashed == []


def test_a_recently_modified_original_is_left(tmp_path):
    src, dest = _pair(tmp_path)
    out = cs.handle_original(src, dest, _policy(pm.ORIGINALS_QUARANTINE, recent_minutes=10))
    assert out.action == cs.LEFT and "10 minutes" in out.detail and src.exists()


def test_a_dry_run_changes_nothing(tmp_path):
    src, dest = _pair(tmp_path)
    before = sorted(p.name for p in tmp_path.iterdir())
    for originals in (pm.ORIGINALS_QUARANTINE, pm.ORIGINALS_RECYCLE, pm.ORIGINALS_KEEP):
        out = cs.handle_original(src, dest, _policy(originals), dry_run=True, day=DAY, trash=lambda p: pytest.fail("touched"))
        assert out.done is False
    lines = cs.plan_originals([(src, dest)], _policy(pm.ORIGINALS_QUARANTINE))
    assert "would be moved to" in lines[0] and str(dest) in lines[0]
    assert sorted(p.name for p in tmp_path.iterdir()) == before


# -- File > Import and Convert, and the Open Folder offer ---------------------------------------------------------------


def _fake_convert(calls=None):
    def fake(conversions, bitrate_kbps, progress, ffmpeg_path, should_cancel):
        if calls is not None:
            calls.append(bitrate_kbps)
        results = []
        for i, (source, dest) in enumerate(conversions, start=1):
            shutil.copyfile(FIXTURE, dest)
            results.append((source, dest, STATUS_OK, ""))
            progress(i, len(conversions))
        return results
    return fake


def _folder_with_flac(tmp_path):
    src = _wav(tmp_path / "a.wav")
    return src


def test_on_open_never_offers_nothing(window, tmp_path, monkeypatch):
    _folder_with_flac(tmp_path)
    window.settings.conversion_policy = {}
    cs.save_policy(window.settings, pm.ConversionPolicy(on_open=pm.ON_OPEN_NEVER))
    monkeypatch.setattr(mw, "offer_conversion", lambda *a, **k: pytest.fail("offered"))
    monkeypatch.setattr(mw, "find_convertible", lambda *a, **k: pytest.fail("looked"))
    window._load_paths([tmp_path])
    assert window.files == []


def test_on_open_ask_is_todays_dialog(window, tmp_path, monkeypatch):
    _folder_with_flac(tmp_path)
    window.settings.conversion_policy = {}
    seen = {}

    def offer(parent, found, loaded, **kw):
        seen.update(kw)
        return mw.CONVERT_YES

    converted = []
    monkeypatch.setattr(mw, "offer_conversion", offer)
    monkeypatch.setattr(window, "_convert_paths_and_load", lambda paths, **k: converted.append((paths, k)))
    window._load_paths([tmp_path])
    assert converted == [([str(tmp_path / "a.wav")], {})]
    assert "beside the originals, which are kept" in seen["question"]  # legacy wording while nothing is saved


def test_on_open_auto_converts_without_asking_and_reports(window, tmp_path, monkeypatch):
    src = _folder_with_flac(tmp_path)
    window.settings.conversion_policy = {}
    cs.save_policy(window.settings, pm.ConversionPolicy(
        on_open=pm.ON_OPEN_AUTO, originals=pm.ORIGINALS_QUARANTINE, recent_minutes=0, dry_run_first=False))
    monkeypatch.setattr(mw, "offer_conversion", lambda *a, **k: pytest.fail("asked"))
    monkeypatch.setattr(mw, "ask_convert_bitrate", lambda *a, **k: pytest.fail("asked the bitrate"))
    calls, reports = [], []
    monkeypatch.setattr(mw, "run_import_conversion", _fake_convert(calls))
    monkeypatch.setattr(QMessageBox, "exec", lambda self: reports.append((self.text(), self.detailedText())) or 0)
    window._load_paths([tmp_path])
    assert calls == [mw.DEFAULT_BITRATE_KBPS]
    assert [m.filename for m in window.files] == ["a.mp3"]
    assert not src.exists()  # verified, so quarantined
    folder = tmp_path / f"converted originals {date.today().isoformat()}"
    assert (folder / "a.wav").exists()
    assert len(reports) == 1 and "Converted and loaded 1 file" in reports[0][0] and "a.wav" in reports[0][1]


def test_import_keeps_the_original_by_default(window, tmp_path, monkeypatch):
    src = _wav(tmp_path / "a.wav")
    window.settings.conversion_policy = {}
    monkeypatch.setattr(mw, "ask_convert_bitrate", lambda *a, **k: 192)
    monkeypatch.setattr(mw, "run_import_conversion", _fake_convert())
    window._convert_paths_and_load([str(src)])
    assert src.exists() and (tmp_path / "a.mp3").exists()
    assert not any(p.name.startswith("converted originals") for p in tmp_path.iterdir())


def test_import_with_recycle_sends_the_original_to_the_bin(window, tmp_path, monkeypatch):
    src = _wav(tmp_path / "a.wav")
    window.settings.conversion_policy = {}
    cs.save_policy(window.settings, pm.ConversionPolicy(originals=pm.ORIGINALS_RECYCLE, recent_minutes=0))
    trashed = []
    monkeypatch.setattr(cs, "handle_original", lambda s, d, p, **k: trashed.append(str(s)) or cs.OriginalOutcome(s, cs.RECYCLED))
    monkeypatch.setattr(mw, "ask_convert_bitrate", lambda *a, **k: 192)
    monkeypatch.setattr(mw, "run_import_conversion", _fake_convert())
    window._convert_paths_and_load([str(src)])
    assert trashed == [str(src)]


def test_the_dry_run_option_changes_nothing(window, tmp_path, monkeypatch):
    src = _wav(tmp_path / "a.wav")
    window.settings.conversion_policy = {}
    cs.save_policy(window.settings, pm.ConversionPolicy(originals=pm.ORIGINALS_QUARANTINE, dry_run_first=True))
    started = {}

    def ask(parent, choices, default, bands, dry_run=False, answer=None):
        started["dry_run"] = dry_run  # the box starts as the policy says
        answer["dry_run"] = dry_run
        return 192

    reports = []
    monkeypatch.setattr(mw, "ask_convert_bitrate", ask)
    monkeypatch.setattr(mw, "run_import_conversion", lambda *a, **k: pytest.fail("converted in a dry run"))
    monkeypatch.setattr(QMessageBox, "exec", lambda self: reports.append((self.text(), self.detailedText())) or 0)
    window._convert_paths_and_load([str(src)])
    assert started["dry_run"] is True
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.wav"] and window.files == []
    assert "Nothing was changed" in reports[0][0] and "would be moved to" in reports[0][1]


def test_the_bitrate_box_has_the_dry_run_check_box(window):
    from gui.convert_bitrate_dialog import ConvertBitrateDialog

    box = ConvertBitrateDialog(window, [128, 192], 192, window.settings.audio_band_settings(), dry_run=True)
    assert box.dry_run.isChecked()
    assert not ConvertBitrateDialog(window, [128, 192], 192, window.settings.audio_band_settings()).dry_run.isChecked()


@pytest.mark.skipif(find_tool("ffmpeg.exe") is None, reason="ffmpeg not available")
def test_quarantine_with_a_real_ffmpeg_conversion(window, tmp_path, monkeypatch):
    src = _wav(tmp_path / "tone.wav", seconds=2)
    window.settings.conversion_policy = {}
    window.settings.ffmpeg_path = ""
    cs.save_policy(window.settings, pm.ConversionPolicy(originals=pm.ORIGINALS_QUARANTINE, recent_minutes=0, dry_run_first=False))
    monkeypatch.setattr(mw, "ask_convert_bitrate", lambda *a, **k: 128)
    window._convert_paths_and_load([str(src)])
    assert (tmp_path / "tone.mp3").is_file() and not src.exists()
    assert (tmp_path / f"converted originals {date.today().isoformat()}" / "tone.wav").is_file()


# -- Preferences > Conversion --------------------------------------------------------------------------------------------


def test_the_page_shows_the_current_policy_and_saves_it_on_ok(window):
    from gui.preferences import build_preferences_dialog
    from redactor_common.gui.conversion_policy_widget import ConversionPolicyWidget

    settings = Settings()
    saved = []
    dlg = build_preferences_dialog(settings, saved.append)
    assert "Conversion" in dlg.page_titles()
    widget = dlg.findChild(ConversionPolicyWidget)
    assert widget.policy() == cs.legacy_policy()
    widget._on_open[pm.ON_OPEN_AUTO].setChecked(True)
    widget._recycle.setChecked(True)
    dlg.accept()
    policy = cs.get_policy(settings)
    assert (policy.on_open, policy.originals) == (pm.ON_OPEN_AUTO, pm.ORIGINALS_RECYCLE)
    assert saved and settings.conversion_policy["saved"] == "1"


def test_cancel_drops_the_page_and_an_untouched_page_saves_nothing(window):
    from gui.preferences import build_preferences_dialog
    from redactor_common.gui.conversion_policy_widget import ConversionPolicyWidget

    settings = Settings()
    dlg = build_preferences_dialog(settings, lambda s: None)
    dlg.findChild(ConversionPolicyWidget)._on_open[pm.ON_OPEN_NEVER].setChecked(True)
    dlg.reject()
    assert settings.conversion_policy == {}
    untouched = build_preferences_dialog(settings, lambda s: None)
    untouched.accept()
    assert settings.conversion_policy == {}  # still "never saved": the legacy behaviour stays
