"""
gui/columns.py -- the house rule for table headers (owner, 2026-10-15): every column header can be dragged.

redactor_common's make_columns_resizable() does the work (every section Interactive, the last one fills the rest, sensible
starting widths, one wide column); this wrapper calls it twice: at once, so the header modes are right straight away, and
once more when the event loop starts, when the dialog has been laid out and the real width is known (a dialog built in
__init__ has a tiny viewport until then). Call it after the table is filled, once per full rebuild, never on a small update;
redactor_common.gui.table_columns.lint_resizable_headers(gui folder) checks the rule in the tests.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QTimer

from redactor_common.gui.table_columns import make_columns_resizable


def columns_resizable(view, wide_column: Optional[int] = None) -> None:
    make_columns_resizable(view, wide_column)

    def later() -> None:
        try:
            make_columns_resizable(view, wide_column)
        except RuntimeError:  # the dialog was closed and deleted before the event loop got here
            pass

    QTimer.singleShot(0, later)
