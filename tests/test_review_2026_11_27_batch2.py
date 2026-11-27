"""Review of 2026-11-27, batch 2 (cards 18-36: crashes and wrong results). One regression test per card."""

import ast
import os
import re
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

import shutil  # noqa: E402
from pathlib import Path  # noqa: E402

import core.redact_steps as rs  # noqa: E402
from core.tag_reader import load_tags  # noqa: E402
from core.tag_writer import save_tags  # noqa: E402
from tests.test_redact_steps import make_file, run  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "tiny.mp3"


def _copy(tmp_path, name="song.mp3") -> Path:
    dest = tmp_path / name
    shutil.copyfile(FIXTURE, dest)
    return dest


# --- card 18: the spec must not exclude a module that core/ uses --------------------------------

def _spec_excludes() -> list[str]:
    text = (ROOT / "mp3redactor.spec").read_text(encoding="utf-8")
    match = re.search(r"^\s*excludes=\[([^\]]*)\]", text, re.MULTILINE)
    assert match, "no excludes list found in mp3redactor.spec"
    return re.findall(r"[\"']([\w.]+)[\"']", match.group(1))


def _used_modules(tree: ast.AST) -> set[str]:
    """Dotted module names a file imports or reaches through an imported alias (np.fft.rfft -> numpy.fft)."""
    used: set[str] = set()
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                used.add(a.name)
                aliases[a.asname or a.name.split(".")[0]] = a.name if a.asname else a.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            used.add(node.module)
            for a in node.names:
                used.add(f"{node.module}.{a.name}")
                aliases[a.asname or a.name] = f"{node.module}.{a.name}"
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            parts = []
            cur: ast.AST = node
            while isinstance(cur, ast.Attribute):
                parts.append(cur.attr)
                cur = cur.value
            if isinstance(cur, ast.Name) and cur.id in aliases:
                base = aliases[cur.id]
                chain = list(reversed(parts))
                for i in range(1, len(chain) + 1):
                    used.add(".".join([base] + chain[:i]))
    return used


def test_spec_excludes_nothing_core_uses():
    excludes = _spec_excludes()
    assert excludes, "the excludes list was not parsed"
    clashes = []
    for py in sorted((ROOT / "core").glob("*.py")):
        used = _used_modules(ast.parse(py.read_text(encoding="utf-8")))
        for ex in excludes:
            if any(u == ex or u.startswith(ex + ".") for u in used):
                clashes.append(f"{py.name} uses {ex}")
    assert not clashes, "mp3redactor.spec excludes modules core/ uses: " + ", ".join(clashes)




# --- GUI cards: 19, 20, 27, 28, 29 ---------------------------------------------------------------------------

from PyQt6.QtCore import QItemSelectionModel  # noqa: E402
from PyQt6.QtWidgets import QDialog, QMessageBox  # noqa: E402

import gui.main_window as mw  # noqa: E402
from core.mp3_file import STATUS_OK, STATUS_WARNING, MP3File  # noqa: E402
from tests.test_cover_gui import _load, _select_all, window  # noqa: E402,F401
from tests.test_delete_key_asks import env  # noqa: E402,F401


def test_card19_a_file_that_was_only_checked_can_be_deleted(env, monkeypatch):
    window, files, trashed, asked, answers = env
    files[0].record_scan("integrity", STATUS_OK, "")  # a completed check marks the file dirty (its stamp)
    assert files[0].dirty and files[0].stamp_only_dirty
    _select_all(window)
    window.delete_files()
    assert sorted(os.path.basename(p) for p in trashed) == ["a.mp3", "b.mp3"]


def test_card19_the_duplicate_dialogs_trash_step_takes_a_checked_file(env, monkeypatch):
    window, files, trashed, asked, answers = env
    files[0].record_scan("integrity", STATUS_OK, "")
    seen = []
    monkeypatch.setattr(mw, "move_to_trash", seen.append)
    window._trash_unless_unsaved(str(files[0].path))
    assert seen == [str(files[0].path)]
    files[1].dirty = True  # a real edit is still refused
    with pytest.raises(mw.TrashError):
        window._trash_unless_unsaved(str(files[1].path))


def test_card20_selection_is_in_table_order_not_click_order(window, tmp_path):
    _load(window, tmp_path, ["a.mp3", "b.mp3", "c.mp3"])
    window.table.clearSelection()
    window.table.selectRow(2)
    index = window.table.model().index(0, 0)
    window.table.selectionModel().select(
        index, QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows
    )
    by_row = [window.table.item(r, 0).data(mw.Qt.ItemDataRole.UserRole) for r in range(window.table.rowCount())]
    expected = [m for m in by_row if m in window._selected_files()]
    assert len(expected) == 2 and window._selected_files() == expected


def test_card27_rename_dialog_offers_the_saved_zero_pad_width(window):
    for width in (0, 2, 5, 10, 99):
        window.settings.rename_zero_pad_width = width
        widths = window._rename_pad_widths()
        assert {2, 3, 4} <= set(widths) and min(width, 10) in widths and list(widths) == sorted(widths)


def test_card27_preferences_limit_the_widths_to_what_the_dialogs_show():
    from gui.preferences import PAD_WIDTH_MAX, preference_sections

    specs = [spec for section in preference_sections() for spec in section.specs if "zero" in spec.key or "padding" in spec.key]
    widths = [s for s in specs if s.kind == "int"]
    assert widths and all(s.maximum <= PAD_WIDTH_MAX <= 10 for s in widths)


def test_card28_auto_number_leaves_out_files_that_failed_to_load(window, tmp_path, monkeypatch):
    files = _load(window, tmp_path, ["a.mp3", "b.mp3", "c.mp3"])
    files[1].load_error = "unreadable"
    captured = []

    class FakeDialog:
        DialogCode = QDialog.DialogCode

        def __init__(self, targets, *args, **kwargs):
            captured.append(list(targets))

        def exec(self):
            return QDialog.DialogCode.Rejected

    monkeypatch.setattr(mw, "AutoNumberingDialog", FakeDialog)
    _select_all(window)
    window.open_auto_numbering_dialog()
    assert sorted(m.filename for m in captured[0]) == ["a.mp3", "c.mp3"]


def test_card29_nothing_selected_with_a_filter_takes_only_the_shown_files(window, tmp_path, monkeypatch):
    _load(window, tmp_path, ["a.mp3", "b.mp3", "c.mp3"])
    window.set_list_filter("b.mp3")
    window.table.clearSelection()
    questions = []

    def question(parent, title, text, *args, **kwargs):
        questions.append(text)
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", staticmethod(question))
    targets = window._redact_targets()
    assert [m.filename for m in targets] == ["b.mp3"]
    assert "1 file(s) shown" in questions[0] and "3 loaded" in questions[0]


# --- card 21: the settings file -------------------------------------------------------------------------------


def test_card21_an_ansi_settings_file_does_not_stop_the_app_and_is_kept(tmp_path):
    from core.settings import SETTINGS_FILENAME, Settings, load_settings, save_settings

    save_settings(Settings(library_root="D:/Musikk/Bjorn"), tmp_path)
    path = tmp_path / SETTINGS_FILENAME
    path.write_bytes(path.read_bytes().replace(b"Bjorn", "Bj\u00f8rn".encode("cp1252")))
    loaded = load_settings(tmp_path)  # used to raise UnicodeDecodeError
    assert loaded.library_root == ""
    assert (tmp_path / (SETTINGS_FILENAME + ".bad")).exists()


def test_card21_a_bom_does_not_reset_the_settings(tmp_path):
    from core.settings import SETTINGS_FILENAME, Settings, load_settings, save_settings

    save_settings(Settings(library_root="D:/Musikk"), tmp_path)
    path = tmp_path / SETTINGS_FILENAME
    path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())
    assert load_settings(tmp_path).library_root == "D:/Musikk"


# --- card 22 and 32: the local MusicBrainz search ---------------------------------------------------------------

from core import musicbrainz_local as ml  # noqa: E402
from core.musicbrainz_local import LocalQuery  # noqa: E402
from tests.mb_fixture import GID  # noqa: E402
from tests.test_musicbrainz_local import ABBEY, db  # noqa: E402,F401


def test_card22_a_symbol_only_album_does_not_break_the_search(db):
    assert ml.search_releases_local(db, LocalQuery(artist="Ed Sheeran", album="+")) == []
    assert ml.search_releases_local(db, LocalQuery(artist="", album="!!!")) == []


def test_card22_the_exact_release_id_survives_a_symbol_only_album(db):
    matches = ml.find_album_local(
        db, ABBEY, LocalQuery(artist="Beatles", album="+"), album_ids=(GID["abbey_cd"],), alternatives=True
    )  # alternatives: every stage runs, the text stage included
    assert matches and matches[0].via == "mbid" and matches[0].release.id == GID["abbey_cd"]


def test_card22_a_symbol_only_artist_still_searches_by_album(db):
    found = ml.search_releases_local(db, LocalQuery(artist="!!!", album="Abbey Road"))
    assert {r.id for r in found} >= {GID["abbey_cd"]}


def test_card32_an_album_of_digits_is_also_searched_by_title(db, monkeypatch):
    asked = []

    def fake_search(database, query, limit=ml.TEXT_POOL):
        asked.append(query.album)
        return []

    monkeypatch.setattr(ml, "search_releases_local", fake_search)
    ml.find_album_local(db, ABBEY, LocalQuery(artist="Somebody", album="1980 - 1990"))
    assert asked == ["1980 - 1990"]  # no barcode matched, so the title search ran


# --- card 23: AcoustID answers ----------------------------------------------------------------------------------

import http.client  # noqa: E402
import json  # noqa: E402

from core import acoustid_lookup as ac  # noqa: E402


def _lookup(post):
    return ac.lookup(ac.Fingerprint(duration=10, fingerprint="abc"), post)


def test_card23_a_cut_off_answer_is_an_acoustid_error():
    def cut(url, data):
        raise http.client.IncompleteRead(b"par")

    with pytest.raises(ac.AcoustIdError):
        _lookup(cut)


def test_card23_an_answer_of_the_wrong_shape_is_an_acoustid_error():
    with pytest.raises(ac.AcoustIdError):
        _lookup(lambda url, data: b"[]")
    bad_score = json.dumps({"status": "ok", "results": [{"score": "high", "recordings": []}]}).encode()
    with pytest.raises(ac.AcoustIdError):
        _lookup(lambda url, data: bad_score)
    list_result = json.dumps({"status": "ok", "results": ["x"]}).encode()
    with pytest.raises(ac.AcoustIdError):
        _lookup(lambda url, data: list_result)


# --- card 24 and 35: notes ---------------------------------------------------------------------------------------

from core.notes_flow import NotesEngine, build_proposals, scan  # noqa: E402
from core.notes_link import LinkError  # noqa: E402
from core.notes_local import KIND_ALBUM, KIND_ARTIST  # noqa: E402
from tests.notes_helpers import FakeFetch, cristina_fetchers, ultravox_fetchers, ultravox_library  # noqa: E402


def _targets(root):
    found = scan([root], kinds=(KIND_ARTIST, KIND_ALBUM))
    return {(t.kind, t.folder.name): t for t in found.targets}


def test_card24_a_busy_musicbrainz_does_not_spoil_the_artists_other_albums(tmp_path, monkeypatch):
    import urllib.error

    from core import notes_musicbrainz

    monkeypatch.setattr(notes_musicbrainz, "_sleep", lambda _s: None)
    ultravox_library(tmp_path)
    fetchers = ultravox_fetchers()

    def busy(url):
        raise urllib.error.HTTPError(url, 503, "Service Unavailable", {}, None)

    fetchers.mb.routes.insert(0, (r"ws/2/artist\?query", busy))
    engine = NotesEngine(fetchers)
    found = _targets(tmp_path)
    build_proposals([found[("album", "Vienna (1980)")]], engine=engine)
    first = fetchers.mb.count(r"ws/2/artist\?query")
    assert first >= 1
    engine._network_failures = 0
    build_proposals([found[("album", "Quartet (1982)")]], engine=engine)
    assert fetchers.mb.count(r"ws/2/artist\?query") > first  # asked again, not answered from a cached failure


def _artist_proposal(tmp_path, fetchers):
    ultravox_library(tmp_path)
    engine = NotesEngine(fetchers)
    [proposal] = build_proposals([_targets(tmp_path)[("artist", "Ultravox")]], engine=engine)
    return engine, proposal


def test_card35_a_bad_musicbrainz_id_from_wikidata_is_a_link_error(tmp_path, monkeypatch):
    engine, proposal = _artist_proposal(tmp_path, ultravox_fetchers())
    monkeypatch.setattr(
        "core.notes_flow.wp.music_identifiers",
        lambda **kw: {"qid": "Q1", "musicbrainz": "../../evil?x=1", "discogs": ""},
    )
    with pytest.raises(LinkError):
        engine.candidate_from_link(proposal, "https://www.wikidata.org/wiki/Q1")


def test_card35_a_non_numeric_discogs_id_from_wikidata_is_a_link_error(tmp_path, monkeypatch):
    engine, proposal = _artist_proposal(tmp_path, cristina_fetchers())
    monkeypatch.setattr(
        "core.notes_flow.wp.music_identifiers",
        lambda **kw: {"qid": "Q1", "musicbrainz": "", "discogs": "abc"},
    )
    with pytest.raises(LinkError):
        engine.candidate_from_link(proposal, "https://www.wikidata.org/wiki/Q1")


# --- card 25: BPM tag "inf" ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("text", ["inf", "nan", "-inf"])
def test_card25_a_non_finite_bpm_tag_is_ignored_and_saves_still_work(tmp_path, text):
    from mutagen.id3 import ID3, TBPM

    path = _copy(tmp_path)
    tags = ID3(path)
    tags.add(TBPM(encoding=3, text=text))
    tags.save(path)
    mp3 = MP3File(path=path)
    load_tags(mp3)
    assert mp3.bpm is None
    mp3.title = "Changed"
    mp3.dirty = True
    assert save_tags(mp3)


# --- card 26: the ffmpeg path ---------------------------------------------------------------------------------------

import sys  # noqa: E402
import types  # noqa: E402

FFMPEG = "C:/tools/ffmpeg.exe"


def test_card26_bpm_fallback_uses_the_external_tools_ffmpeg(monkeypatch):
    from core import bpm_detector

    monkeypatch.setitem(sys.modules, "aubio", types.ModuleType("aubio"))

    def cannot_decode(aubio, path):
        raise RuntimeError("could not find RIFF header")

    seen = []

    def fake_find_tool(name, override=None):
        seen.append(override)
        return None

    monkeypatch.setattr(bpm_detector, "_analyze", cannot_decode)
    monkeypatch.setattr(bpm_detector, "find_tool", fake_find_tool)
    bpm_detector.detect_bpm(Path("song.mp3"), ffmpeg_path=FFMPEG)
    assert seen == [FFMPEG]


def test_card26_key_fallback_uses_the_external_tools_ffmpeg(monkeypatch):
    from core import key_detector, tool_locator

    monkeypatch.setitem(sys.modules, "numpy", types.ModuleType("numpy"))

    def cannot_decode(path):
        raise RuntimeError("no decoder")

    seen = []

    def fake_find_tool(name, override=None):
        seen.append(override)
        return None

    monkeypatch.setattr(key_detector, "_aubio_blocks", cannot_decode)
    monkeypatch.setattr(tool_locator, "find_tool", fake_find_tool)
    key_detector.detect_key_native(Path("song.mp3"), ffmpeg_path=FFMPEG)
    assert seen == [FFMPEG]


def test_card26_detectors_are_handed_the_path_by_the_batch_runs(monkeypatch, tmp_path):
    from core import keyfinder_runner, scan_service

    got = {}
    monkeypatch.setattr(scan_service, "detect_bpm", lambda path, ffmpeg_path=None: got.setdefault("bpm", ffmpeg_path) and (120.0, STATUS_OK, ""))
    monkeypatch.setattr(
        scan_service, "detect_key",
        lambda path, override_path=None, ffmpeg_path=None: got.setdefault("key", ffmpeg_path) and ("A", STATUS_OK, ""),
    )
    mp3 = MP3File(path=_copy(tmp_path))
    scan_service.run_bpm_check([mp3], ffmpeg_path=FFMPEG)
    scan_service.run_key_detection([mp3], ffmpeg_path=FFMPEG)
    assert got == {"bpm": FFMPEG, "key": FFMPEG}

    seen = {}
    monkeypatch.setattr(
        keyfinder_runner, "detect_key_native", lambda path, ffmpeg_path=None: seen.setdefault("ff", ffmpeg_path) and ("A", STATUS_OK, "")
    )
    keyfinder_runner.detect_key(Path("song.mp3"), ffmpeg_path=FFMPEG)
    assert seen == {"ff": FFMPEG}


def test_card26_redact_steps_pass_the_path_too(tmp_path, monkeypatch):
    from core.settings import Settings

    got = {}

    def fake_bpm(path, ffmpeg_path=None):
        got["ffmpeg"] = ffmpeg_path
        return None, STATUS_OK, "no beats"

    monkeypatch.setattr(rs, "detect_bpm", fake_bpm)
    run(make_file(tmp_path), {"bpm"}, settings=Settings(ffmpeg_path=FFMPEG))
    assert got["ffmpeg"] == FFMPEG


# --- card 30: Redact's integrity messages ---------------------------------------------------------------------------


def test_card30_integrity_messages_name_the_song_not_the_scratch_copy(tmp_path, monkeypatch):
    mp3 = make_file(tmp_path)

    def check(path, override_path=None):
        return STATUS_WARNING, f'WARNING: "{path}": Garbage at the end'

    monkeypatch.setattr(rs, "check_integrity", check)
    monkeypatch.setattr(rs, "fix_integrity", lambda *a, **k: pytest.fail("must not fix"))
    entry, _ = run(mp3, {"integrity"}, {"integrity": {"fix": False}})
    text = " ".join(entry.applied + entry.notes)
    assert str(mp3.path) in text
    assert rs.REDACT_SCRATCH_PREFIX not in text


# --- card 31: lyrics for a title of symbols ----------------------------------------------------------------------------


def test_card31_lyrics_match_an_artist_and_title_made_of_symbols():
    from core.lyrics_fetcher import result_matches

    assert result_matches("!!! - !!! (Louden Up Now)", "!!!", "!!!")
    assert result_matches("Heart - !!! (Louden Up Now)", "!!!", "Heart")
    assert not result_matches("Other Song - Other Artist (X)", "Artist Name", "Heart")  # real names still checked


# --- card 33: AcoustID problems in the Look Up dialog --------------------------------------------------------------------


def test_card33_a_failed_fingerprint_pass_is_not_kept_and_is_said(tmp_path, monkeypatch):
    import gui.musicbrainz_lookup_dialog as base

    passes = []

    def identify(paths, fpcalc, post=None):
        passes.append(1)
        return [[] for _ in paths], (["a.mp3: AcoustID couldn't be reached"] if len(passes) == 1 else [])

    monkeypatch.setattr(base, "identify_files", identify)
    fake = types.SimpleNamespace(_fpcalc=Path("fpcalc"), _post=None, _fingerprints={}, _fingerprint_problem={})
    album = types.SimpleNamespace(files=[types.SimpleNamespace(path=tmp_path / "a.mp3")])
    base.MusicBrainzLookupDialog._fingerprint(fake, album)
    assert fake._fingerprint_problem[id(album)].endswith("couldn't be reached")
    assert id(album) not in fake._fingerprints
    base.MusicBrainzLookupDialog._fingerprint(fake, album)  # connection back: asks again
    assert len(passes) == 2 and id(album) in fake._fingerprints


# --- card 34: the Local files tab ----------------------------------------------------------------------------------------


def test_card34_a_read_error_is_not_cached(tmp_path, monkeypatch):
    import time

    import gui.notes_evidence_view as view_module
    from core.notes_evidence import Evidence

    view_module.clear_cache()
    monkeypatch.setattr(
        view_module, "load_evidence", lambda folder, kind, loader=None: Evidence(kind=kind, error="could not be read")
    )
    view = view_module.LocalEvidenceView()
    view.show_folder(tmp_path, "album")
    deadline = time.time() + 5
    while view.evidence is None and time.time() < deadline:
        mw.QApplication.processEvents()
        time.sleep(0.01)
    assert view.evidence is not None and view.evidence.error
    assert view_module._cache_get((str(tmp_path), "album")) is None
    view_module.clear_cache()


# --- card 36: audiobook cover URL ------------------------------------------------------------------------------------------

from core import audiobook_lookup as al  # noqa: E402


@pytest.mark.parametrize("url", ["file:///C:/secret.jpg", "ftp://host/x.jpg", "//m.media-amazon.com/x.jpg", "x.jpg"])
def test_card36_only_http_and_https_covers_are_followed(url):
    def never(_url):
        pytest.fail("fetched a URL that is not http or https")

    with pytest.raises(al.AudiobookLookupError):
        al.download_cover(url, never)
    with pytest.raises(al.AudiobookLookupError):
        al.default_fetch(url)


def test_card36_a_malformed_url_is_a_lookup_error_not_a_crash():
    with pytest.raises(al.AudiobookLookupError):
        al.default_fetch("http://[bad/x.jpg")


def test_card36_a_runaway_answer_is_cut_off(monkeypatch):
    monkeypatch.setattr(al, "MAX_FETCH_BYTES", 10)

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n=-1):
            return b"x" * (n if n >= 0 else 1000)

    monkeypatch.setattr(al.urllib.request, "urlopen", lambda *a, **k: Response())
    with pytest.raises(al.AudiobookLookupError, match="too large"):
        al.default_fetch("https://example.com/x.jpg")
