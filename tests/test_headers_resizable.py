"""House rule (owner, 2026-10-15): every table's column headers can be dragged. The lint over the gui sources, and a real
offscreen check of each dialog that has a table or tree: every section Interactive and resizeSection really resizes."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path  # noqa: E402

from PyQt6.QtWidgets import QApplication, QHeaderView  # noqa: E402

from core.notes_flow import NotesEngine, build_proposals, scan  # noqa: E402
from core.notes_local import KIND_ARTIST  # noqa: E402
from core.notes_review import ReviewSession, choose_view  # noqa: E402
from gui.notes_dialog import NotesReviewDialog  # noqa: E402
from redactor_common.gui.table_columns import lint_resizable_headers  # noqa: E402
from tests.notes_helpers import make_mp3, ultravox_fetchers, ultravox_library  # noqa: E402
from tests.test_cover_gui import _app, _load, window  # noqa: E402,F401
from tests.test_m4b_gui import _tagged  # noqa: E402

GUI = Path(__file__).parent.parent / "gui"


def header_of(view):
    return view.horizontalHeader() if hasattr(view, "horizontalHeader") else view.header()


def assert_draggable(view, label):
    header = header_of(view)
    assert header.count() > 1, label
    for section in range(header.count()):
        assert header.sectionResizeMode(section) == QHeaderView.ResizeMode.Interactive, f"{label}: section {section}"
    # a real drag is a resize of the section: it must take
    target = header.sectionSize(0) + 25
    header.resizeSection(0, target)
    assert header.sectionSize(0) == target, label


def test_the_gui_sources_have_no_stretch_resize_to_contents_or_fixed_headers():
    assert lint_resizable_headers(GUI) == []


def test_the_notes_review_page_tree_and_candidate_table_are_draggable(tmp_path):
    ultravox_library(tmp_path / "Rock")
    friends = tmp_path / "Rock" / "Ultravox & Friends"
    for album, year in (("Vienna", "1980"), ("Quartet", "1982")):
        make_mp3(friends / f"{album} ({year})" / "01 a.mp3", "a", "Ultravox & Friends", album, 1, year)
    engine = NotesEngine(ultravox_fetchers())
    found = scan([tmp_path / "Rock"], kinds=(KIND_ARTIST,))
    proposals = build_proposals(found.targets, engine=engine)
    assert any(p.candidates for p in proposals)
    session = ReviewSession(proposals)
    dlg = NotesReviewDialog(session, "list", lambda p, c, l: engine.rebuild(p, c, l), engine.can_choose)
    dlg.show()
    QApplication.processEvents()
    assert dlg.view == "list"
    assert_draggable(dlg.tree, "review tree")
    # the candidate table of the uncertain artist
    uncertain = next(i for i, item in enumerate(session.items) if item.proposal.candidates)
    dlg.index = uncertain
    dlg._select_tree_index(uncertain)
    dlg.toggle_view()
    QApplication.processEvents()
    dlg.index = uncertain
    dlg._show_one()
    QApplication.processEvents()
    assert dlg.one_panel.candidate_table.rowCount() > 0
    assert_draggable(dlg.one_panel.candidate_table, "candidate table")
    # the widths survive the next item being shown (a person's drag is not undone by a refill)
    width = dlg.one_panel.candidate_table.horizontalHeader().sectionSize(0)
    dlg._show_one()
    assert dlg.one_panel.candidate_table.horizontalHeader().sectionSize(0) == width
    dlg.grab().save(str(tmp_path / "review.png"))
    dlg.done(0)


def test_the_m4b_chapter_table_is_draggable(window, tmp_path):
    from gui.m4b_dialog import M4bDialog

    dialog = M4bDialog(_tagged(window, tmp_path, ["a.mp3", "b.mp3", "c.mp3"]))
    dialog.show()
    QApplication.processEvents()
    assert_draggable(dialog.table, "m4b chapters")
    dialog.grab().save(str(tmp_path / "m4b.png"))
    dialog.close()


def test_the_m4b_batch_table_is_draggable(window, tmp_path):
    from core.m4b_builder import group_by_folder
    from gui.m4b_batch_dialog import M4bBatchDialog

    files = []
    for folder, album, artist in (("A", "First Book", "Writer One"), ("B", "Second Book", "Writer Two")):
        for number in (2, 1):
            f = _load(window, tmp_path, [f"{folder}/{number}.mp3"])[0]
            f.album, f.artist, f.track = album, artist, str(number)
            files.append(f)
    dialog = M4bBatchDialog(group_by_folder(files), bitrate_kbps=96, library_root=str(tmp_path / "lib"))
    dialog.show()
    QApplication.processEvents()
    assert_draggable(dialog.table, "m4b batch")
    dialog.close()


def test_the_m4b_lookup_table_is_draggable_after_a_search(window):
    from gui.m4b_lookup_dialog import M4bLookupDialog

    dialog = M4bLookupDialog("A Title", "An Author", 600.0, fetch=lambda url: b"{}")
    dialog.show()
    QApplication.processEvents()
    assert_draggable(dialog.table, "m4b lookup")
    dialog.close()
