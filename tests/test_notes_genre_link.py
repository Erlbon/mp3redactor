"""Create Notes, 2026-10-15: 'Look up by link' also works for a genre / collection folder (the link is the genre's Wikipedia
article), and choosing a level builds at once, like a pasted link does (there was a second 'Build' press; only 'Skip' keeps a
button, because a skipped row leaves the list). Real review dialog (offscreen), the real engine for the link."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path  # noqa: E402

import pytest  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from core.notes_flow import Confidence, Fetchers, NotesEngine, Proposal, build_proposals, scan  # noqa: E402
from core.notes_link import LinkError  # noqa: E402
from core.notes_local import Target  # noqa: E402
from core.notes_review import ReviewSession  # noqa: E402
from gui.notes_dialog import NotesReviewDialog  # noqa: E402
from tests.notes_helpers import FakeFetch, make_mp3  # noqa: E402
from tests.test_notes_list_stable import click_row, settle  # noqa: E402

_app = QApplication.instance() or QApplication([])

SKA_PUNK = {"type": "standard", "title": "Ska punk", "description": "Music genre",
            "extract": "Ska punk is a fusion genre that combines ska and punk rock.",
            "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Ska_punk"}}}


def genre_proposal():
    target = Target(Path("C:/lib/Genres/1.61 - Ska"), "genre", "Genres")
    return Proposal(target, Confidence.UNCERTAIN, level="genre", suggested_levels=["genre-collection", "skip"],
                    reasons=["no Wikipedia page about a music genre called 'Ska' could be verified"])


def dialog(proposals, rebuild):
    dlg = NotesReviewDialog(ReviewSession(proposals), "list", rebuild, lambda p, c: True)
    dlg.show()
    dlg.resize(1100, 700)
    settle()
    click_row(dlg, 0)
    return dlg


# -- the engine: a Wikipedia link names the genre ------------------------------------------------------------------

def tree_with_a_genre(root):
    for artist in ("Specials", "Madness"):
        make_mp3(root / "Genres" / "Ska" / artist / "First (1979)" / "01 a.mp3", "a", artist, "First", 1, "1979")
    return root / "Genres"


def test_a_wikipedia_link_builds_the_genre_note_from_that_article(tmp_path):
    genres = tree_with_a_genre(tmp_path)
    wiki = FakeFetch([(r"search/title", {"pages": []}), (r"page/summary/Ska_punk$", SKA_PUNK), (r"page/summary/", None)])
    engine = NotesEngine(Fetchers(mb=lambda r: (_ for _ in ()).throw(AssertionError("no MusicBrainz for a genre")), wiki=wiki))
    [proposal] = build_proposals(scan([genres], kinds=("genre",)).targets, engine=engine)
    assert proposal.note_kind == "about" and proposal.confidence != Confidence.CONFIDENT      # nothing found by name
    built = engine.rebuild(proposal, link="https://en.wikipedia.org/wiki/Ska_punk")
    assert built.confidence == Confidence.CONFIDENT and built.note_kind == "about" and "fusion genre" in built.note_text
    assert built.genres == ["Ska punk"] and "wikipedia" in built.sources
    assert any(r.startswith("chosen by you from a link") for r in built.reasons), built.reasons
    assert "https://en.wikipedia.org/wiki/Ska_punk" in built.note_text


@pytest.mark.parametrize("link,words", [
    ("https://musicbrainz.org/artist/2bf704b3-1111-4222-8333-444455556666", "Wikipedia article about the genre"),
    ("https://en.wikipedia.org/wiki/No_such_page", "No Wikipedia article"),
])
def test_a_link_that_cannot_name_a_genre_is_refused_in_plain_words(tmp_path, link, words):
    genres = tree_with_a_genre(tmp_path)
    wiki = FakeFetch([(r"search/title", {"pages": []}), (r"page/summary/", None)])
    engine = NotesEngine(Fetchers(mb=lambda r: None, wiki=wiki))
    [proposal] = build_proposals(scan([genres], kinds=("genre",)).targets, engine=engine)
    with pytest.raises(LinkError, match=words):
        engine.rebuild(proposal, link=link)


# -- the review page -------------------------------------------------------------------------------------------------

def test_a_genre_row_has_the_link_box_for_a_wikipedia_article_only():
    calls = []

    def rebuild(proposal, candidate, level, **extra):
        calls.append(extra)
        return proposal

    dlg = dialog([genre_proposal()], rebuild)
    panel = dlg.list_panel
    assert panel.link_box.isVisibleTo(panel) and "article about the genre" in panel.link_edit.placeholderText()
    panel.link_edit.setText("https://musicbrainz.org/artist/2bf704b3-1111-4222-8333-444455556666")
    panel._link_clicked()
    assert panel.link_error.isVisibleTo(panel) and "Wikipedia article" in panel.link_error.text() and calls == []
    panel.link_edit.setText("https://en.wikipedia.org/wiki/Ska_punk")
    panel._link_clicked()
    settle()
    assert calls == [{"link": "https://en.wikipedia.org/wiki/Ska_punk"}]
    dlg.done(0)


def test_choosing_a_level_builds_at_once_and_only_skip_keeps_a_button():
    calls = []

    def rebuild(proposal, candidate, level, **extra):
        calls.append(level)
        return Proposal(proposal.target, Confidence.CONFIDENT, note_text="---\ntitle: X\n---\n\n# X\n", title="X",
                        sources=["wikipedia"], level=level or "genre")

    target = Target(Path("C:/lib/D/1.61 - Ska"), "artist", "D")               # taken for an artist: it may be a genre / collection folder
    uncertain = Proposal(target, Confidence.UNCERTAIN, level="artist", level_reasons=["x"],
                         suggested_levels=["artist", "genre-collection", "skip"])
    dlg = dialog([uncertain], rebuild)
    panel = dlg.list_panel
    combo = panel.level_combo
    assert combo.itemText(0).startswith("Choose") and combo.currentIndex() == 0 and not panel.level_button.isVisibleTo(panel)
    assert calls == []                                                   # showing the row builds nothing
    skip = combo.findData("skip")
    combo.setCurrentIndex(skip)
    combo.activated.emit(skip)
    assert panel.level_button.isVisibleTo(panel) and calls == []        # Skip waits for its own click
    combo.setCurrentIndex(combo.findData("genre-collection"))
    combo.activated.emit(combo.currentIndex())
    settle()
    assert calls == ["genre-collection"]                                # the level built at once, no Build press
    dlg.done(0)
