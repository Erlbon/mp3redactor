"""
gui/tag_panel.py

The bulk-edit tag panel, mp3redactor's version of the mp3tag-style
workflow every sibling Redactor project uses (see the epub tool's
gui/tag_panel.py for the fullest version -- covers and external
metadata lookups don't have an MP3-tag equivalent yet, so this is
deliberately the "basic" subset: plain text fields, per core.fields.
FIELDS, plus a quick-pick "+" button on Genre/Language
(core.fields.QUICK_PICK_FIELDS) since those two have an obvious
family-standard curated-list-plus-custom-entries convenience, same as
every sibling project's own Genre/Language fields).

- Select one or more rows in the file table.
- Each field shows the shared value if every selected file agrees, or
  a "<multiple values>" placeholder if they differ (scroll the field
  with the mouse wheel to cycle through and pick one of the differing
  values, rather than only being told they differ).
- Tick a field's checkbox and type a value to stage it for the whole
  selection -- typing (or scroll-picking an alternative, or picking
  from the Genre/Language "+" button) implicitly ticks the box too,
  matching mp3tag. Unticked fields are left completely alone, so
  applying never blanks a field just because it happened to be empty
  on-screen.
- "Apply to N file(s)" (a toolbar/menu action MainWindow owns, see
  apply_bulk_edit() below) writes the checked fields into memory for
  every selected file and marks each dirty -- does not touch disk,
  that's the Save action.

This panel is deliberately dumb about Settings, same as the sibling
projects' equivalents: the "+" button doesn't own or read the
Genre/Language quick-pick lists itself (that's Settings-backed state,
MainWindow's job) -- it just emits quickPickRequested(field_key,
editor) and MainWindow opens redactor_common.gui.quick_pick_dialog.
QuickPickDialog with its own settings access, then calls
set_picked_value() back to apply the result.
"""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.fields import FIELDS, QUICK_PICK_FIELDS
from core.mp3_file import MP3File
from redactor_common.gui.collapsible_splitter import CollapseToggleButton
from redactor_common.gui.grid_utils import absorb_extra_row_space

MULTIPLE_VALUES_PLACEHOLDER = "<multiple values>"
COLLAPSE_BUTTON_WIDTH = 26


class MultiValueLineEdit(QLineEdit):
    """A QLineEdit that can additionally hold a small set of alternative
    values -- the distinct non-empty values present across a
    multi-file selection -- and cycle through them via mouse wheel.
    Lets you actually see and pick one of several differing values,
    instead of just being told "<multiple values>" with no way to
    inspect what they are. Never includes a blank/empty value among the
    alternatives to cycle through -- an absent value on some files isn't
    a usable "pick this for everyone" option the way an actual value
    is. Lifted from the epub tool's tag_panel.py (not yet promoted to
    redactor_common)."""

    valuePicked = pyqtSignal()  # emitted after the wheel cycles to a new value

    def __init__(self, parent=None):
        super().__init__(parent)
        self._alternatives: list[str] = []
        self._alt_index = -1

    def set_alternatives(self, values: list[str]) -> None:
        self._alternatives = values
        self._alt_index = -1

    def wheelEvent(self, event) -> None:  # noqa: N802 - Qt override signature
        if not self._alternatives:
            super().wheelEvent(event)
            return
        delta = event.angleDelta().y()
        if delta == 0:
            super().wheelEvent(event)
            return
        if self._alt_index == -1:
            self._alt_index = 0
        else:
            step = -1 if delta > 0 else 1
            self._alt_index = (self._alt_index + step) % len(self._alternatives)
        self.setText(self._alternatives[self._alt_index])
        self.setPlaceholderText("")
        self.valuePicked.emit()
        event.accept()


class TagPanel(QWidget):
    applyRequested = pyqtSignal(dict)  # {field_key: value} for checked fields only
    selectionCountChanged = pyqtSignal(int)  # lets MainWindow mirror this in its toolbar Apply action
    collapseToggleRequested = pyqtSignal()  # the panel doesn't control its own width -- MainWindow does
    quickPickRequested = pyqtSignal(str)  # field_key -- see set_picked_value()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._checkboxes: dict[str, QCheckBox] = {}
        self._editors: dict[str, MultiValueLineEdit] = {}
        self._labels: dict[str, QLabel] = {}
        # The widget actually placed in the grid's 3rd column for each
        # field -- the editor itself, or (for a quick-pick field) the
        # small container wrapping the editor + "+" button. What
        # set_visible_fields() hides/shows; _editors above always keeps
        # holding the real editor regardless of visibility, since
        # set_selection()/apply_bulk_edit() read/write every field
        # unconditionally -- a hidden field's data still round-trips,
        # only the on-screen row disappears.
        self._row_widgets: dict[str, QWidget] = {}
        self._current_files: list[MP3File] = []
        self._build_ui()
        self.set_selection([])

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)

        # Sits at the very top regardless of how narrow the panel gets,
        # so the toggle stays reachable even when minimized -- MainWindow
        # collapses to a slim strip, not to zero width, for exactly this
        # reason.
        toggle_row = QHBoxLayout()
        toggle_row.addStretch(1)
        self.collapse_toggle_btn = CollapseToggleButton(width=COLLAPSE_BUTTON_WIDTH)
        self.collapse_toggle_btn.clicked.connect(self.collapseToggleRequested.emit)
        toggle_row.addWidget(self.collapse_toggle_btn)
        outer.addLayout(toggle_row)

        fields_box = QGroupBox("Bulk Edit Tags")
        grid = QGridLayout(fields_box)
        grid.setColumnStretch(2, 1)
        for row, (key, label, _multiline) in enumerate(FIELDS):
            cb = QCheckBox()
            cb.setToolTip("Tick to include this field when applying to the selection")
            self._checkboxes[key] = cb

            editor = MultiValueLineEdit()
            # Typing in a field implicitly opts it in -- convenient, and
            # matches how mp3tag behaves (any edit means "change this").
            # textEdited (not textChanged) fires only on user interaction,
            # never from our own programmatic setText() in set_selection().
            editor.textEdited.connect(lambda _text, k=key: self._checkboxes[k].setChecked(True))
            # Scrolling to a different alternative counts as picking it,
            # same as typing -- opts the field in automatically too.
            editor.valuePicked.connect(lambda k=key: self._checkboxes[k].setChecked(True))
            self._editors[key] = editor

            row_widget = self._wrap_with_quick_pick(key, editor) if key in QUICK_PICK_FIELDS else editor
            self._row_widgets[key] = row_widget

            label_widget = QLabel(label)
            self._labels[key] = label_widget

            grid.addWidget(cb, row, 0)
            grid.addWidget(label_widget, row, 1)
            grid.addWidget(row_widget, row, 2)

        # Without this, the gap between every field visibly grows as the
        # window/panel is resized taller -- see grid_utils.py's docstring.
        absorb_extra_row_space(grid, len(FIELDS))

        outer.addWidget(fields_box, 1)

    def _wrap_with_quick_pick(self, key: str, editor: QLineEdit) -> QWidget:
        """A QLineEdit plus a small "+" button requesting a quick-pick
        dialog from MainWindow (see class docstring) -- used for Genre
        and Language, the two fields with a curated default list plus
        user-manageable custom entries (Settings > Add/Remove
        Genres.../Add/Remove Languages...)."""
        container = QWidget()
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(editor, 1)

        button = QToolButton()
        button.setText("+")
        button.setToolTip("Pick from list, or add a custom entry")
        button.clicked.connect(lambda: self.quickPickRequested.emit(key))
        row.addWidget(button)
        return container

    def set_picked_value(self, key: str, value: str) -> None:
        """Called back by MainWindow once a quick-pick dialog it opened
        (in response to quickPickRequested) is accepted -- sets the
        field's text and ticks its checkbox, same as if the value had
        been typed directly. MainWindow reaches into this panel only
        through this one method, never the private widget dicts."""
        editor = self._editors.get(key)
        if editor is None:
            return
        editor.setText(value)
        self._checkboxes[key].setChecked(True)

    def _uncheck_all(self) -> None:
        for cb in self._checkboxes.values():
            cb.setChecked(False)

    def set_collapsed_indicator(self, collapsed: bool) -> None:
        """Updates the panel's own toggle button to reflect whether it's
        currently minimized. MainWindow owns the actual collapsed/expanded
        state (via the splitter, since resizing is its job, not this
        widget's) and calls this after any change -- the toggle button
        click, or the user dragging the splitter handle by hand."""
        self.collapse_toggle_btn.set_collapsed(collapsed)

    def set_visible_fields(self, visible_keys: set[str]) -> None:
        """Shows/hides each field's row to match `visible_keys` (the
        set of field keys whose table column is currently shown) --
        called by MainWindow whenever column visibility changes
        (Settings > Add/Remove Columns..., or right-clicking a
        header), so a column you've hidden from the table also stops
        cluttering this panel, and vice versa.

        Hides widgets rather than rebuilding rows from scratch: nothing
        is ever destroyed, so there's no risk of losing an in-progress
        edit sitting in a widget mid-hide, and set_selection()/
        apply_bulk_edit() keep working exactly as before, completely
        unaware of visibility -- a hidden field still exists and still
        gets applied/saved, only the on-screen row disappears."""
        for key, row_widget in self._row_widgets.items():
            visible = key in visible_keys
            row_widget.setVisible(visible)
            self._labels[key].setVisible(visible)
            self._checkboxes[key].setVisible(visible)

    # ------------------------------------------------------------------

    def set_selection(self, files: list[MP3File]) -> None:
        """files: the currently selected MP3File objects. Repopulates
        every field, showing the common value or a <multiple values>
        placeholder, and unchecks everything."""
        self._current_files = files
        self.selectionCountChanged.emit(len(files))

        for key, _label, _multiline in FIELDS:
            editor = self._editors[key]
            self._checkboxes[key].setChecked(False)

            if not files:
                editor.set_alternatives([])
                editor.setText("")
                editor.setPlaceholderText("")
                continue

            values = {getattr(f, key, "") or "" for f in files}
            alternatives = sorted(v for v in values if v) if len(values) > 1 else []
            editor.set_alternatives(alternatives)
            editor.setToolTip(
                f"Scroll to cycle through {len(alternatives)} different values in the selection"
                if alternatives else ""
            )
            if len(values) == 1:
                editor.setText(values.pop())
                editor.setPlaceholderText("")
            else:
                editor.setText("")
                editor.setPlaceholderText(MULTIPLE_VALUES_PLACEHOLDER)

    def apply_bulk_edit(self) -> None:
        """Collects every checked field's value and emits applyRequested.
        Public (not a private click handler) since Apply lives as a
        toolbar/menu action in MainWindow, matching the epub tool."""
        result = {}
        for key, _label, _multiline in FIELDS:
            if self._checkboxes[key].isChecked():
                result[key] = self._editors[key].text().strip()
        if result:
            self.applyRequested.emit(result)
        self._uncheck_all()
