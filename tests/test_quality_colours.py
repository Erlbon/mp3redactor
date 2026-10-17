"""
The Bitrate column's quality colours (2026-10-17), built on redactor_common's shared classify_audio() and
apply_band_colour(): yellow = poor, green = fine, none = unknown; the explanation is the tooltip; Preferences > Table
colours set the limits live and travel in Export/Import Settings. Real MainWindow, offscreen, real MP3s made by ffmpeg
(plus controlled MP3File fields where a file cannot be made).
"""

import os
import shutil
import subprocess
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtGui import QColor  # noqa: E402

from core.audio_quality import (  # noqa: E402
    band_settings, convert_hint, display_bitrate, is_vbr, normalise_limits, sample_rate_result,
)
from core.mp3_file import MP3File  # noqa: E402
from core.settings import Settings, load_settings, save_settings  # noqa: E402
from core.tag_reader import load_tags  # noqa: E402
from redactor_common.gui.colors import DIRTY_COLOR  # noqa: E402
from tests.test_cover_gui import FIXTURE, _app, window  # noqa: E402,F401

YELLOW, GREEN = QColor("#fde047"), QColor("#86efac")
FFMPEG = shutil.which("ffmpeg")


def _make(path: Path, *args: str, rate: int = 44100, channels: int = 2, noise: bool = False) -> Path:
    """A 2 second MP3. A pure sine is so cheap to encode that even V2 averages about 50 kbps, so `noise` makes two
    different noise channels, which need a realistic VBR bitrate (V2 about 186 kbps)."""
    if noise:
        inputs = ["-f", "lavfi", "-i", f"anoisesrc=color=white:duration=2:sample_rate={rate}:seed=1",
                  "-f", "lavfi", "-i", f"anoisesrc=color=white:duration=2:sample_rate={rate}:seed=2",
                  "-filter_complex", "[0][1]amerge=inputs=2"]
    else:
        inputs = ["-f", "lavfi", "-i", f"sine=frequency=440:duration=2:sample_rate={rate}", "-ac", str(channels)]
    subprocess.run(
        [FFMPEG, "-y", "-loglevel", "error", *inputs, "-c:a", "libmp3lame", *args, str(path)],
        check=True, stdin=subprocess.DEVNULL,
    )
    return path


@pytest.fixture(scope="module")
def real_files(tmp_path_factory):
    if not FFMPEG:
        pytest.skip("ffmpeg not available")
    folder = tmp_path_factory.mktemp("quality")
    return {
        "cbr192": _make(folder / "cbr192.mp3", "-b:a", "192k"),
        "cbr96": _make(folder / "cbr96.mp3", "-b:a", "96k"),
        "v2": _make(folder / "v2.mp3", "-q:a", "2", noise=True),
        "v9": _make(folder / "v9.mp3", "-q:a", "9"),
        "low_rate": _make(folder / "low_rate.mp3", "-b:a", "192k", rate=22050),
    }


def _load_paths(window, tmp_path, paths):
    files = []
    for path in paths:
        dest = tmp_path / Path(path).name
        shutil.copyfile(path, dest)
        mp3 = MP3File(path=dest)
        load_tags(mp3)
        files.append(mp3)
    window.files = files
    window._rebuild_table()
    return files


def _cell(window, mp3, key):
    for row in range(window.table.rowCount()):
        item = window.table.item(row, window._col_index["filename"])
        if item.data(Qt.ItemDataRole.UserRole) is mp3:
            return window.table.item(row, window._col_index[key])
    raise AssertionError("row not found")


def _bg(item):
    brush = item.background()
    return brush.color() if brush.style() != Qt.BrushStyle.NoBrush else None


# -- the pure layer -------------------------------------------------------------------------------------------------------

def test_default_limits_and_normalising():
    assert normalise_limits(128, 192) == (128, 192)
    assert normalise_limits(200, 192) == (200, 201)  # good raised just above poor
    assert normalise_limits(320, 100) == (319, 320)  # at the top poor is lowered instead
    assert normalise_limits(5, 9999) == (32, 320)  # clamped
    assert normalise_limits("x", None) == (128, 192)  # bad values get the defaults
    assert band_settings().poor_kbps == 128 and band_settings().good_kbps == 192


def test_vbr_detection_and_display():
    mp3 = MP3File(path=Path("x.mp3"), bitrate_kbps=190, bitrate_mode="VBR")
    assert is_vbr(mp3) is True and display_bitrate(mp3) == "VBR ~190 kbps"
    mp3.bitrate_mode = "ABR"
    assert is_vbr(mp3) is True
    mp3.bitrate_mode = "CBR"
    assert is_vbr(mp3) is False and display_bitrate(mp3) == "190 kbps"
    mp3.bitrate_mode = ""
    assert is_vbr(mp3) is None
    assert display_bitrate(MP3File(path=Path("y.mp3"))) == ""


def test_the_sample_rate_cell_is_yellow_only_when_the_rate_is_the_reason():
    low = MP3File(path=Path("a.mp3"), bitrate_kbps=192, sample_rate_hz=22050, channels=2)
    assert sample_rate_result(low, band_settings()).band.value == "poor"
    mono = MP3File(path=Path("b.mp3"), bitrate_kbps=64, sample_rate_hz=22050, channels=1)
    assert sample_rate_result(mono, band_settings()) is None
    fine = MP3File(path=Path("c.mp3"), bitrate_kbps=192, sample_rate_hz=44100, channels=2)
    assert sample_rate_result(fine, band_settings()) is None


def test_the_convert_hint_names_the_band():
    settings = band_settings()
    assert convert_hint(192, settings).startswith("192 kbps is fine by the table's colours")
    assert convert_hint(96, settings).startswith("96 kbps is poor by the table's colours")
    # 128 is NOT below the default poor limit of 128, so it is fine; a higher limit makes it poor
    assert convert_hint(128, settings).startswith("128 kbps is fine")
    assert convert_hint(128, band_settings(160, 220)).startswith("128 kbps is poor")


# -- the real table ----------------------------------------------------------------------------------------------------

def test_a_real_file_is_read_with_its_mode_rate_and_channels(real_files, window, tmp_path):
    mp3, = _load_paths(window, tmp_path, [real_files["v2"]])
    assert mp3.bitrate_mode == "VBR" and mp3.sample_rate_hz == 44100 and mp3.channels == 2
    assert display_bitrate(mp3).startswith("VBR ~")


def test_cell_colours_tooltips_and_vbr_average_on_real_files(real_files, window, tmp_path):
    cbr192, cbr96, v2, v9, low = _load_paths(
        window, tmp_path, [real_files[k] for k in ("cbr192", "cbr96", "v2", "v9", "low_rate")]
    )
    assert _bg(_cell(window, cbr192, "bitrate")) == GREEN
    assert _cell(window, cbr192, "bitrate").text() == "192 kbps"
    assert _bg(_cell(window, cbr96, "bitrate")) == YELLOW
    assert _bg(_cell(window, v2, "bitrate")) == GREEN  # V2 on a demanding source averages well above 128 kbps
    assert v2.bitrate_kbps >= 128 and v9.bitrate_kbps < 128
    assert _bg(_cell(window, v9, "bitrate")) == YELLOW  # V9 averages about 65 kbps
    assert _cell(window, v2, "bitrate").text().startswith("VBR ~")
    assert "VBR: the average bitrate is used" in _cell(window, v2, "bitrate").toolTip()
    tip = _cell(window, cbr96, "bitrate").toolTip()
    assert tip.startswith("MP3, 96 kbps, 44.1 kHz, stereo. Poor: the bitrate is below 128 kbps")
    assert _cell(window, cbr192, "bitrate").toolTip().startswith("MP3, 192 kbps, 44.1 kHz, stereo. Fine.")
    # text colour is the dark highlight colour on a coloured cell
    assert _cell(window, cbr192, "bitrate").foreground().color() == QColor("#000000")
    # a low sample rate: both the Bitrate and the Sample Rate cell are yellow, with their own tooltips
    assert _bg(_cell(window, low, "bitrate")) == YELLOW
    assert _bg(_cell(window, low, "sample_rate")) == YELLOW
    assert "below 32 kHz for stereo music" in _cell(window, low, "sample_rate").toolTip()
    assert _bg(_cell(window, cbr192, "sample_rate")) is None


def test_unknown_bitrate_has_no_colour_and_a_failed_file_is_never_painted(window, tmp_path):
    ok = MP3File(path=tmp_path / "a.mp3", bitrate_kbps=None)
    failed = MP3File(path=tmp_path / "b.mp3", bitrate_kbps=64, sample_rate_hz=22050, channels=2, load_error="broken")
    good = MP3File(path=tmp_path / "c.mp3", bitrate_kbps=192, bitrate_mode="CBR")
    window.files = [ok, failed, good]
    window._rebuild_table()
    assert _bg(_cell(window, ok, "bitrate")) is None
    assert "unknown" in _cell(window, ok, "bitrate").toolTip()
    for key in ("bitrate", "sample_rate"):
        assert _bg(_cell(window, failed, key)) is None  # the failure tint is never overpainted
    assert _cell(window, failed, "bitrate").toolTip()  # but the explanation is still the tooltip
    assert _bg(_cell(window, good, "bitrate")) == GREEN


def test_the_dirty_row_tint_stays_and_the_band_colour_comes_after_it(window, tmp_path):
    poor = MP3File(path=tmp_path / "a.mp3", bitrate_kbps=96, bitrate_mode="CBR", title="x")
    unknown = MP3File(path=tmp_path / "b.mp3", bitrate_kbps=None, title="y")
    window.files = [poor, unknown]
    poor.title = "changed"
    unknown.title = "changed"
    poor.dirty = unknown.dirty = True
    window._rebuild_table()
    assert _bg(_cell(window, poor, "title")) == DIRTY_COLOR  # the row is still tinted
    assert _bg(_cell(window, poor, "bitrate")) == YELLOW  # the band colour is painted over it
    assert _bg(_cell(window, unknown, "bitrate")) == DIRTY_COLOR  # no band colour: the row tint shows
    for mp3 in (poor, unknown):
        mp3.dirty = False


def test_the_column_is_visible_by_default_sorts_numerically_and_has_a_legend(window, tmp_path):
    from gui.main_window import DEFAULT_HIDDEN_COLUMNS

    assert "bitrate" not in DEFAULT_HIDDEN_COLUMNS
    col = window._col_index["bitrate"]
    assert not window.table.isColumnHidden(col)
    assert window.table.horizontalHeaderItem(col).text() == "Bitrate"
    assert window.table.horizontalHeaderItem(col).toolTip() == "Bitrate: yellow = poor, green = fine"
    files = [
        MP3File(path=tmp_path / "a.mp3", bitrate_kbps=320, bitrate_mode="CBR"),
        MP3File(path=tmp_path / "b.mp3", bitrate_kbps=96, bitrate_mode="CBR"),
        MP3File(path=tmp_path / "c.mp3", bitrate_kbps=190, bitrate_mode="VBR"),
        MP3File(path=tmp_path / "d.mp3", bitrate_kbps=128, bitrate_mode="CBR"),
    ]
    window.files = files
    window._rebuild_table()
    window.table.sortItems(col, Qt.SortOrder.AscendingOrder)
    order = [window.table.item(r, col).text() for r in range(window.table.rowCount())]
    assert order == ["96 kbps", "128 kbps", "VBR ~190 kbps", "320 kbps"]  # numeric, not text ("128" < "320" < "96")


def test_filter_list_still_works_with_the_colours(window, tmp_path):
    a = MP3File(path=tmp_path / "keep.mp3", bitrate_kbps=96, bitrate_mode="CBR")
    b = MP3File(path=tmp_path / "other.mp3", bitrate_kbps=192, bitrate_mode="CBR")
    window.files = [a, b]
    window._rebuild_table()
    window.set_list_filter("keep")
    visible = {window.table.item(r, 0).text() for r in range(window.table.rowCount()) if not window.table.isRowHidden(r)}
    assert len(visible) == 1
    assert _bg(_cell(window, a, "bitrate")) == YELLOW
    window.set_list_filter("")


def test_the_limits_change_the_colours_live_and_persist(window, tmp_path, monkeypatch):
    from gui.preferences import KEY_BAND_GOOD, KEY_BAND_POOR, settings_backend

    mp3 = MP3File(path=tmp_path / "a.mp3", bitrate_kbps=160, bitrate_mode="CBR")
    window.files = [mp3]
    window._rebuild_table()
    assert _bg(_cell(window, mp3, "bitrate")) == GREEN
    backend = settings_backend(window.settings, lambda s: save_settings(s, tmp_path))
    backend.set_many({KEY_BAND_POOR: 200, KEY_BAND_GOOD: 250})
    window._repaint_band_colours()  # what closing Preferences does
    assert _bg(_cell(window, mp3, "bitrate")) == YELLOW
    assert "Poor below 200 kbps" in _cell(window, mp3, "bitrate").toolTip()
    loaded = load_settings(tmp_path)
    assert (loaded.band_poor_kbps, loaded.band_good_kbps) == (200, 250)
    backend.set_many({KEY_BAND_POOR: 100})
    window._repaint_band_colours()
    assert _bg(_cell(window, mp3, "bitrate")) == GREEN
    backend.set_many({KEY_BAND_POOR: 300, KEY_BAND_GOOD: 250})  # poor not below good: good is raised
    assert (window.settings.band_poor_kbps, window.settings.band_good_kbps) == (300, 301)


def test_the_preferences_page_exists_with_validated_ranges():
    from gui.preferences import KEY_BAND_GOOD, KEY_BAND_POOR, preference_sections

    section = next(s for s in preference_sections() if s.title == "Table colours")
    specs = {spec.key: spec for spec in section.specs}
    assert specs[KEY_BAND_POOR].default == 128 and specs[KEY_BAND_GOOD].default == 192
    assert specs[KEY_BAND_POOR].minimum == 32 and specs[KEY_BAND_GOOD].maximum == 320


def test_a_bad_stored_pair_is_repaired_on_load(tmp_path):
    save_settings(Settings(band_poor_kbps=250, band_good_kbps=100), tmp_path)
    loaded = load_settings(tmp_path)
    assert (loaded.band_poor_kbps, loaded.band_good_kbps) == (250, 251)


def test_export_import_round_trip(tmp_path):
    from core.settings_adapter import Mp3SettingsAdapter
    from redactor_common.core import settings_bundle as sb

    source = Settings(band_poor_kbps=96, band_good_kbps=224)
    adapter = Mp3SettingsAdapter(lambda: source, "t", target_dir=tmp_path)
    assert "table_colours" in sb.default_selection(adapter)  # portable: ticked by default
    text = sb.dump_bundle(sb.build_bundle(adapter, {"table_colours"}))
    target = Settings()
    other = Mp3SettingsAdapter(lambda: target, "t", target_dir=tmp_path / "t2")
    other.write_section("table_colours", {"band_poor_kbps": 96, "band_good_kbps": 224})
    assert (target.band_poor_kbps, target.band_good_kbps) == (96, 224)
    assert '"band_poor_kbps": 96' in text
    other.write_section("table_colours", {"band_poor_kbps": 1000, "band_good_kbps": "x"})  # wrong values are skipped
    assert (target.band_poor_kbps, target.band_good_kbps) == (96, 224)
    other.write_section("table_colours", {"band_poor_kbps": 300})  # a pair that breaks poor < good is repaired
    assert (target.band_poor_kbps, target.band_good_kbps) == (300, 301)


def test_the_convert_dialog_shows_the_hint_of_the_chosen_value(window):
    from gui.convert_bitrate_dialog import ConvertBitrateDialog

    dialog = ConvertBitrateDialog(window, [128, 192, 256, 320], 192, band_settings(160, 220))
    assert dialog.chosen_kbps() == 192
    assert dialog.hint.text().startswith("192 kbps is fine")
    dialog.combo.setCurrentIndex(0)
    assert dialog.chosen_kbps() == 128 and dialog.hint.text().startswith("128 kbps is poor by the table's colours")


def test_conversion_reload_and_tag_reread_refresh_the_colours(real_files, window, tmp_path):
    mp3, = _load_paths(window, tmp_path, [real_files["cbr96"]])
    assert _bg(_cell(window, mp3, "bitrate")) == YELLOW
    # the file is replaced on disk by a better one and the list is re-read (what Refresh List does: load, rebuild)
    shutil.copyfile(real_files["cbr192"], mp3.path)
    fresh = MP3File(path=mp3.path)
    load_tags(fresh)
    window.files = [fresh]
    window._rebuild_table()
    assert _bg(_cell(window, fresh, "bitrate")) == GREEN


def test_convert_to_mp3_end_to_end_colours_the_new_row(real_files, window, tmp_path, monkeypatch):
    """The real Convert to MP3 path (ffmpeg through the app's own converter): 96 kbps lands yellow, 192 green."""
    import gui.main_window as mw

    for kbps, colour in ((96, YELLOW), (192, GREEN)):
        folder = tmp_path / f"c{kbps}"
        folder.mkdir()
        wav = folder / "song.wav"
        subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=duration=2", str(wav)],
                       check=True, stdin=subprocess.DEVNULL)
        monkeypatch.setattr(mw, "ask_convert_bitrate", lambda *a, _k=kbps, **k: _k)
        window.files = []
        window._convert_paths_and_load([str(wav)])
        assert [m.filename for m in window.files] == ["song.mp3"]
        assert _bg(_cell(window, window.files[0], "bitrate")) == colour
