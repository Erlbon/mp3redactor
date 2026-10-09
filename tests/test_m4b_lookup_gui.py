"""The Look Up button in Create M4B Audiobook: the lookup dialog (canned answers) and how a chosen book
fills the single-book and batch dialogs."""

import io
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PIL import Image  # noqa: E402

import gui.m4b_lookup_dialog as lookup_mod  # noqa: E402
from core.m4b_builder import group_by_folder  # noqa: E402
from gui.m4b_batch_dialog import M4bBatchDialog  # noqa: E402
from gui.m4b_dialog import M4bDialog  # noqa: E402
from gui.m4b_lookup_dialog import M4bLookupDialog  # noqa: E402
from tests.test_audiobook_lookup import AUDIBLE  # noqa: E402
from tests.test_cover_gui import _app, _load, window  # noqa: E402,F401


def _jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (40, 40), (20, 90, 160)).save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture
def fetch(monkeypatch):
    monkeypatch.setattr(lookup_mod, "call_in_background", lambda fn, *a, **k: fn(*a))
    cover = _jpeg()
    seen = []

    def _fetch(url):
        seen.append(url)
        if "catalog/products" in url:
            return json.dumps(AUDIBLE).encode()
        if url.endswith(".jpg"):
            return cover
        return json.dumps({"docs": []}).encode()

    _fetch.seen = seen
    _fetch.cover = cover
    return _fetch


def _files(window, tmp_path, names, album="Wizard's First Rule", artist="Terry Goodkind"):
    files = _load(window, tmp_path, names)
    for number, f in enumerate(files, start=1):
        f.album, f.artist, f.track, f.duration_seconds = album, artist, str(number), 2040 * 60 / len(files)
    return files


def test_the_lookup_dialog_lists_ranked_results_with_cover_and_description(window, fetch):
    dialog = M4bLookupDialog("Wizard's First Rule", "Terry Goodkind", 2040, fetch=fetch)
    dialog.search_btn.click()
    assert dialog.table.rowCount() == 2
    assert dialog.table.item(0, 1).text() == "Wizard's First Rule" and dialog.table.item(0, 3).text() == "Sam Tsoutsouvas"
    assert dialog.table.item(0, 7).text() == "34:06" and "minutes against your files" in dialog.table.item(0, 7).toolTip()
    assert dialog.selected_match().asin == "B0LONG" and dialog.use_btn.isEnabled()  # the best one is preselected
    assert "A wizard & a girl." in dialog.description_view.toPlainText()
    assert dialog.cover_label.pixmap() is not None and not dialog.cover_label.pixmap().isNull()
    assert dialog.chosen_cover() == (fetch.cover, "image/jpeg")
    dialog.cover_check.setChecked(False)
    assert dialog.chosen_cover() is None


def test_a_failed_search_says_so_in_the_dialog(window, monkeypatch):
    monkeypatch.setattr(lookup_mod, "call_in_background", lambda fn, *a, **k: fn(*a))

    def down(url):
        raise lookup_mod.AudiobookLookupError("no network")

    dialog = M4bLookupDialog("Anything", "", None, fetch=down)
    dialog.search_btn.click()
    assert "no network" in dialog.status_label.text() and dialog.table.rowCount() == 0
    assert not dialog.use_btn.isEnabled()


def test_a_chosen_book_fills_the_single_dialog_and_keeps_what_it_has_nothing_for(window, tmp_path, fetch):
    dialog = M4bDialog(_files(window, tmp_path, ["a.mp3", "b.mp3"]), lookup_fetch=fetch)
    dialog.year_edit.setText("1999")
    matches = lookup_mod.search("Wizard's First Rule", "Terry Goodkind", 2040, fetch)
    dialog.apply_match(matches.matches[1], None)  # the abridged one: no narrator, no series, no publisher
    assert dialog.title_edit.text() == "Wizard's First Rule (Abridged)" and dialog.narrator_edit.text() == ""
    dialog.apply_match(matches.matches[0], (fetch.cover, "image/jpeg"))
    spec = dialog.spec()
    assert spec.title == "Wizard's First Rule" and spec.author == "Terry Goodkind" and spec.narrator == "Sam Tsoutsouvas"
    assert spec.series == "Sword of Truth" and spec.series_index == "1" and spec.publisher == "Brilliance Audio"
    assert spec.year == "2008" and spec.language == "eng" and spec.description.startswith("A wizard & a girl.")
    assert spec.cover == fetch.cover and spec.cover_mime == "image/jpeg"


def test_the_look_up_button_opens_the_lookup_and_applies_the_pick(window, tmp_path, fetch, monkeypatch):
    dialog = M4bDialog(_files(window, tmp_path, ["a.mp3"]), lookup_fetch=fetch, region="co.uk")
    shown = []

    class Pick(M4bLookupDialog):
        def exec(self):
            shown.append((self.title_edit.text(), self.author_edit.text(), round(self._runtime), self.region()))
            self.search_btn.click()
            return self.DialogCode.Accepted

    monkeypatch.setattr("gui.m4b_lookup_dialog.M4bLookupDialog", Pick)
    dialog.lookup_btn.click()
    assert shown == [("Wizard's First Rule", "Terry Goodkind", 2040, "co.uk")]
    assert dialog.narrator_edit.text() == "Sam Tsoutsouvas" and dialog.series_edit.text() == "Sword of Truth"
    assert dialog.region() == "co.uk"


def test_a_chosen_book_fills_one_row_of_the_batch_dialog(window, tmp_path, fetch):
    files = _files(window, tmp_path, ["A/1.mp3", "B/1.mp3"])
    dialog = M4bBatchDialog(group_by_folder(files), lookup_fetch=fetch)
    match = lookup_mod.search("Wizard's First Rule", "Terry Goodkind", 2040, fetch).matches[0]
    dialog.apply_match(1, match, (fetch.cover, "image/jpeg"))
    first, second = dialog.specs()
    assert second.narrator == "Sam Tsoutsouvas" and second.series == "Sword of Truth" and second.series_index == "1"
    assert second.publisher == "Brilliance Audio" and second.cover == fetch.cover and second.description
    assert first.narrator == "" and first.description == "" and first.cover != fetch.cover  # the other row is untouched
    assert dialog.table.item(1, 3).text() == "Sam Tsoutsouvas"
