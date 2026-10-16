"""
core/notes_review.py

The rules behind the Create Notes review page (gui/notes_dialog.py) are redactor_common's
(redactor_common/core/notes_review.py, shared with videoredactor). This file only fixes what is mp3redactor's:
the kinds that make an owner folder (artist, genre) with album children, and the write function (write_proposal).
"""

from __future__ import annotations

from typing import Callable, Iterable

from core.notes_flow import Proposal, write_proposal
from redactor_common.core import notes_review as _shared
from redactor_common.core.notes import NoteWriteResult
from redactor_common.core.notes_review import (  # noqa: F401 -- re-exported: the dialog and the tests import them from here
    CONFIDENCE_WORDS, ERROR, EXISTS, PENDING, SKIPPED, STATE_WORDS, TOO_LARGE, VIEW_LIST, VIEW_ONE, WRITTEN, ReviewItem,
    ReviewTotals, note_diff,
)


def choose_view(targets: Iterable) -> str:
    """'one' when the scan found a single artist folder (its artist note and its album notes) or a single album,
    'list' otherwise."""
    return _shared.choose_view(targets, owner_kinds=("artist", "genre"), child_kind="album")


class ReviewSession(_shared.ReviewSession):
    def __init__(self, proposals: Iterable[Proposal], write_fn: Callable[..., NoteWriteResult] = write_proposal):
        super().__init__(proposals, write_fn)
