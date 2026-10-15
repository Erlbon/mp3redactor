"""The 'Look up by link' text and the 'Genres covered' text belong to ONE row of the review page: selecting another row
starts with an empty link field and its own genres (a link pasted for the previous band was offered for the next one,
reported by the owner on 2026-10-15). The same row shown again (a rebuild in place) keeps what was typed."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tests.test_notes_list_stable import click_row, make_dialog  # noqa: E402


def _uncertain_artist_rows(dlg, count=2):
    rows = [i for i, item in enumerate(dlg.session.items)
            if item.proposal.kind == "artist" and item.proposal.confidence.name != "CONFIDENT" and item.proposal.candidates]
    assert len(rows) >= count
    return rows[:count]


def test_a_pasted_link_does_not_follow_the_selection_to_another_row():
    dlg = make_dialog(n=24)
    first, second = _uncertain_artist_rows(dlg)
    click_row(dlg, first)
    panel = dlg.list_panel
    assert panel.link_box.isVisible()
    panel.link_edit.setText("https://en.wikipedia.org/wiki/Leningrad_(band)")
    panel.set_link_error("Not a link I know")
    click_row(dlg, second)
    assert panel.link_edit.text() == ""
    assert not panel.link_error.isVisible()
    click_row(dlg, first)  # and back: the field is not kept for the first row either (nothing was submitted)
    assert panel.link_edit.text() == ""
    dlg.hide()  # close() would ask about the ticked unwritten notes (a modal box)


def test_the_same_row_shown_again_keeps_what_was_typed():
    dlg = make_dialog(n=24)
    first, _second = _uncertain_artist_rows(dlg)
    click_row(dlg, first)
    panel = dlg.list_panel
    panel.link_edit.setText("https://musicbrainz.org/artist/2bf704b3-4c05-441f-ad54-52722c1bdba9")
    panel.show_item(dlg.session.items[first])  # a refresh of the same row (state change, rebuild in place)
    assert panel.link_edit.text().startswith("https://musicbrainz.org/artist/")
    panel.show_item(None)  # nothing selected any more: the text belongs to no row
    assert panel.link_edit.text() == ""
    dlg.hide()  # close() would ask about the ticked unwritten notes (a modal box)
