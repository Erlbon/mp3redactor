"""
gui/notes_dialog.py

Tools > Create Notes...: the REVIEW PAGE for the folder notes (core/notes_flow.py is the engine, core/notes_review.py
the rules). Nothing here touches the network on the GUI thread: the lookups run in run_work_with_progress() (a
cancellable counted dialog) and a rebuild after a person's choice in run_busy(); both call plain engine functions
that never touch a widget.

  create_notes(parent, paths, ...)   scan, look up (progress + Cancel), open the review page
  NotesReviewDialog                  two views of the same ReviewSession, switchable:
                                       one at a time  (one artist folder or one album): Skip / Write note / Back
                                       list           (several artist folders): ticks, Write N ticked notes
  ProposalPanel                      what the page shows of one proposal: path, confidence, reasons, candidates, the
                                     level choice, the note preview (read-only: the Pluteus web editor edits notes)

Nothing is written without an explicit click on Write note / Write N ticked notes / Write all remaining confident.
An uncertain or nothing-found proposal cannot be ticked or written until a candidate or a level was chosen (then it
is rebuilt by the engine and, if a note could be made, is confident).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Optional

from PyQt6.QtCore import QSize, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QDesktopServices, QTextDocument
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QHeaderView, QLabel,
    QMessageBox, QPushButton, QSizePolicy, QStackedWidget, QTableWidget, QTableWidgetItem, QTabWidget, QTextBrowser, QTextEdit,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from core.notes_flow import Candidate, Confidence, Fetchers, NotesEngine, Proposal, build_proposals, scan
from core.notes_level import SUGGEST_ALBUM, SUGGEST_ARTIST, SUGGEST_COLLECTION, SUGGEST_SKIP
from core.notes_local import ALL_KINDS
from core.notes_review import (
    CONFIDENCE_WORDS, ERROR, EXISTS, PENDING, SKIPPED, TOO_LARGE, VIEW_LIST, VIEW_ONE, WRITTEN, ReviewItem,
    ReviewSession, choose_view,
)
from redactor_common.core.notes import parse_front_matter
from redactor_common.gui.progress import run_busy
from redactor_common.gui.work_progress import run_work_with_progress

DISCOGS_NOTE = "Discogs token not set: notes will be thinner (Tools > API Keys…)."
DEFAULT_SIZE = (1100, 700)

# Confidence colours: a tinted background with dark text, readable in a light and a dark theme.
BADGE_STYLES = {
    Confidence.CONFIDENT: "background:#c9ecd1; color:#12401f;",
    Confidence.UNCERTAIN: "background:#fbe3a6; color:#4d3600;",
    Confidence.NONE: "background:#dcdcdc; color:#333333;",
}
TEXT_COLOURS = {Confidence.CONFIDENT: "#1b7f3b", Confidence.UNCERTAIN: "#a86a00", Confidence.NONE: "#707070"}

LEVEL_LABELS = {
    SUGGEST_ARTIST: "Artist folder (artist.md)",
    SUGGEST_COLLECTION: "Genre or collection (about.md)",
    SUGGEST_ALBUM: "Album folder (album.md)",
    SUGGEST_SKIP: "Skip this folder",
}
_CURRENT_LEVEL_KEY = {"collection": SUGGEST_COLLECTION, "genre": SUGGEST_COLLECTION, "artist": SUGGEST_ARTIST, "album": SUGGEST_ALBUM}


def level_alternatives(proposal: Proposal) -> list[str]:
    """The levels a person may switch this proposal to (never the one it already has), when there is a real choice
    beyond skipping."""
    current = _CURRENT_LEVEL_KEY.get(proposal.level, "")
    options = [s for s in proposal.suggested_levels if s != current]
    return options if any(o != SUGGEST_SKIP for o in options) else []


def short_path(folder: Path, root: Optional[str]) -> str:
    try:
        return os.path.relpath(str(folder), root) if root else str(folder)
    except ValueError:  # another drive
        return str(folder)


def open_in_system(path: str) -> None:
    QDesktopServices.openUrl(QUrl.fromLocalFile(path))


def _shrinkable(*labels: QLabel) -> None:
    """Labels whose text (a long path, a long banner) must never decide how wide the dialog is."""
    for label in labels:
        label.setWordWrap(True)
        label.setMinimumWidth(0)
        label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)


def _flat_button(text: str) -> QPushButton:
    button = QPushButton(text)
    button.setAutoDefault(False)
    return button


# ---------------------------------------------------------------------------------------------------------------
# One proposal
# ---------------------------------------------------------------------------------------------------------------

class ProposalPanel(QWidget):
    """The detail of one proposal. Read-only: a person's choices go out as signals."""

    candidateChosen = pyqtSignal(object)  # a Candidate
    levelChosen = pyqtSignal(str)  # a suggested level key
    openNoteRequested = pyqtSignal()
    showFolderRequested = pyqtSignal()

    def __init__(self, can_choose: Callable[[Proposal, Candidate], bool], parent=None):
        super().__init__(parent)
        self._can_choose = can_choose
        self._item: Optional[ReviewItem] = None
        self._candidates: list[Candidate] = []

        self.path_label = QLabel()
        self.path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.path_label.setWordWrap(True)
        font = self.path_label.font()
        font.setBold(True)
        self.path_label.setFont(font)
        self.kind_label = QLabel()
        self.kind_label.setWordWrap(True)
        self.badge = QLabel()
        self.badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.badge.setMinimumWidth(110)
        self.state_label = QLabel()
        head = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.addWidget(self.path_label)
        title_box.addWidget(self.kind_label)
        head.addLayout(title_box, 1)
        head.addWidget(self.state_label)
        head.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignTop)

        self.reasons_label = QLabel()
        self.reasons_label.setWordWrap(True)
        self.reasons_label.setTextFormat(Qt.TextFormat.RichText)
        self.problems_label = QLabel()
        self.problems_label.setWordWrap(True)
        self.problems_label.setTextFormat(Qt.TextFormat.RichText)

        # the level choice
        self.level_box = QWidget()
        level_row = QHBoxLayout(self.level_box)
        level_row.setContentsMargins(0, 0, 0, 0)
        self.level_caption = QLabel("Build this folder as:")
        level_row.addWidget(self.level_caption)
        self.level_combo = QComboBox()
        self.level_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.level_combo.setMinimumContentsLength(14)
        level_row.addWidget(self.level_combo, 1)
        self.level_button = _flat_button("Build")
        self.level_button.clicked.connect(self._level_clicked)
        level_row.addWidget(self.level_button)
        level_row.addStretch(1)

        # the candidates
        self.candidate_box = QWidget()
        cand_layout = QVBoxLayout(self.candidate_box)
        cand_layout.setContentsMargins(0, 0, 0, 0)
        self.candidate_caption = QLabel("Candidates found. Pick the right one to build the note from it:")
        cand_layout.addWidget(self.candidate_caption)
        self.candidate_table = QTableWidget(0, 5)
        self.candidate_table.setHorizontalHeaderLabels(["Name", "Details (year, country)", "Score", "Source", "Local albums found"])
        self.candidate_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.candidate_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.candidate_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.candidate_table.verticalHeader().setVisible(False)
        self.candidate_table.horizontalHeader().setStretchLastSection(True)
        self.candidate_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.candidate_table.setMaximumHeight(140)
        self.candidate_table.itemSelectionChanged.connect(self._update_buttons)
        cand_layout.addWidget(self.candidate_table)
        cand_row = QHBoxLayout()
        self.candidate_button = _flat_button("Use selected candidate")
        self.candidate_button.clicked.connect(self._candidate_clicked)
        self.candidate_note = QLabel()
        self.candidate_note.setWordWrap(True)
        cand_row.addWidget(self.candidate_button)
        cand_row.addWidget(self.candidate_note, 1)
        cand_layout.addLayout(cand_row)

        # the preview (rendered) and the raw text; read-only
        self.preview = QTextBrowser()
        self.preview.setOpenExternalLinks(True)
        self.raw = QTextEdit()
        self.raw.setReadOnly(True)
        self.raw.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        mono = self.raw.font()
        mono.setFamily("Consolas")
        self.raw.setFont(mono)
        self.tabs = QTabWidget()
        self.tabs.addTab(self.preview, "Preview")
        self.tabs.addTab(self.raw, "Raw text")

        self.sources_label = QLabel()
        self.sources_label.setWordWrap(True)
        self.open_note_button = _flat_button("Open note")
        self.open_note_button.clicked.connect(self.openNoteRequested.emit)
        self.show_folder_button = _flat_button("Show in folder")
        self.show_folder_button.clicked.connect(self.showFolderRequested.emit)
        foot = QHBoxLayout()
        foot.addWidget(self.sources_label, 1)
        foot.addWidget(self.open_note_button)
        foot.addWidget(self.show_folder_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(head)
        layout.addWidget(self.reasons_label)
        layout.addWidget(self.problems_label)
        layout.addWidget(self.level_box)
        layout.addWidget(self.candidate_box)
        layout.addWidget(self.tabs, 1)
        layout.addLayout(foot)
        _shrinkable(self.path_label, self.kind_label, self.reasons_label, self.problems_label, self.sources_label,
                    self.candidate_caption, self.candidate_note, self.level_caption)
        self.show_item(None)

    # -- showing ---------------------------------------------------------------------------------------------

    @staticmethod
    def render_body(text: str) -> str:
        """The note without its front matter (the raw tab shows everything)."""
        if not text:
            return ""
        _meta, body = parse_front_matter(text)
        return body or text

    def show_item(self, item: Optional[ReviewItem]) -> None:
        self._item = item
        if item is None:
            self.path_label.setText("Nothing selected")
            for w in (self.kind_label, self.state_label, self.badge, self.reasons_label, self.problems_label,
                      self.sources_label):
                w.clear()
            self.badge.setStyleSheet("")
            self.level_box.hide()
            self.candidate_box.hide()
            self.preview.clear()
            self.raw.clear()
            self.open_note_button.hide()
            self.show_folder_button.hide()
            return
        p = item.proposal
        self.path_label.setText(p.path)
        level = f"  |  level: {p.level}" if p.level else ""
        self.kind_label.setText(f"Note kind: {p.note_kind}{level}")
        self.badge.setText(CONFIDENCE_WORDS[p.confidence])
        self.badge.setStyleSheet(BADGE_STYLES[p.confidence] + " border-radius:4px; padding:3px 8px; font-weight:bold;")
        self.state_label.setText(item.state_word)
        if item.state == WRITTEN:
            self.state_label.setStyleSheet("color:#1b7f3b; font-weight:bold;")
        elif item.state in (EXISTS, ERROR, TOO_LARGE):
            self.state_label.setStyleSheet("color:#b00020; font-weight:bold;")
        else:
            self.state_label.setStyleSheet("")
        reasons = list(p.reasons) + [r for r in p.level_reasons if r not in p.reasons]
        if item.message and item.state in (EXISTS, ERROR, TOO_LARGE):
            reasons.insert(0, f"NOT written: {item.message}")
        self.reasons_label.setText(self._bullets(reasons) if reasons else "")
        self.problems_label.setText(
            "<span style='color:#b00020'>" + self._bullets(p.problems) + "</span>" if p.problems else "")
        self.reasons_label.setVisible(bool(reasons))
        self.problems_label.setVisible(bool(p.problems))

        alternatives = level_alternatives(p) if item.state == PENDING else []
        self.level_box.setVisible(bool(alternatives))
        self.level_combo.clear()
        for key in alternatives:
            self.level_combo.addItem(LEVEL_LABELS.get(key, key), key)

        self._candidates = list(p.candidates)
        show_candidates = bool(self._candidates) and item.state == PENDING and (
            p.confidence != Confidence.CONFIDENT or item.user_resolved)
        self.candidate_box.setVisible(show_candidates)
        self._fill_candidates(p)

        self.raw.setPlainText(p.note_text)
        if p.note_text:
            self.preview.document().setMarkdown(
                self.render_body(p.note_text),
                QTextDocument.MarkdownFeature.MarkdownDialectGitHub | QTextDocument.MarkdownFeature.MarkdownNoHTML)
        else:
            self.preview.document().setMarkdown(
                "*No draft yet.* " + ("Pick one of the candidates above, or a level, to build a note."
                                      if (self._candidates or alternatives) else "Nothing could be found for this folder."),
                QTextDocument.MarkdownFeature.MarkdownDialectGitHub | QTextDocument.MarkdownFeature.MarkdownNoHTML)
        self.sources_label.setText("Sources: " + ", ".join(p.sources) if p.sources else "Sources: none (no draft)")
        written = item.state == WRITTEN
        self.open_note_button.setVisible(written)
        self.show_folder_button.setVisible(written)
        self._update_buttons()

    @staticmethod
    def _bullets(lines: list[str]) -> str:
        from html import escape

        return "<br>".join(f"&bull; {escape(line)}" for line in lines)

    def _fill_candidates(self, p: Proposal) -> None:
        table = self.candidate_table
        table.setRowCount(0)
        for cand in self._candidates:
            row = table.rowCount()
            table.insertRow(row)
            hits = ", ".join(cand.hits) if cand.hits else "none"
            for col, text in enumerate((cand.name, cand.detail, f"{cand.score:.2f}", cand.source, hits)):
                table.setItem(row, col, QTableWidgetItem(text))
                table.item(row, col).setToolTip(cand.note or text)
        if self._candidates:
            table.selectRow(0)
        table.resizeColumnToContents(1)

    def selected_candidate(self) -> Optional[Candidate]:
        row = self.candidate_table.currentRow()
        return self._candidates[row] if 0 <= row < len(self._candidates) else None

    def _update_buttons(self) -> None:
        cand = self.selected_candidate()
        ok = bool(cand and self._item and self._can_choose(self._item.proposal, cand))
        self.candidate_button.setEnabled(ok)
        self.candidate_note.setText(
            "" if ok or cand is None else "A note cannot be built from this candidate (only MusicBrainz albums can).")

    def _candidate_clicked(self) -> None:
        cand = self.selected_candidate()
        if cand is not None:
            self.candidateChosen.emit(cand)

    def _level_clicked(self) -> None:
        key = self.level_combo.currentData()
        if key:
            self.levelChosen.emit(str(key))


# ---------------------------------------------------------------------------------------------------------------
# The review page
# ---------------------------------------------------------------------------------------------------------------

class NotesReviewDialog(QDialog):
    def __init__(
        self, session: ReviewSession, view: str,
        rebuild: Callable[[Proposal, Optional[Candidate], str], Proposal],
        can_choose: Callable[[Proposal, Candidate], bool],
        banner: str = "", parent=None, size: Optional[tuple[int, int]] = None,
        on_size: Optional[Callable[[int, int], None]] = None,
        open_path: Callable[[str], None] = open_in_system, history_note: str = "",
        best_guess: Optional[Callable[[Proposal], Optional[Candidate]]] = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Create Notes")
        self._best_guess = best_guess or (lambda _proposal: None)
        self.session = session
        self.view = view
        self.index = 0
        self.last_summary = ""
        self._rebuild = rebuild
        self._can_choose = can_choose
        self._on_size = on_size
        self._open_path = open_path
        self._history_note = history_note
        self._root = self._common_root()
        self.resize(*(size or DEFAULT_SIZE))
        self.setSizeGripEnabled(True)

        self.header_label = QLabel()
        head_font = self.header_label.font()
        head_font.setPointSize(head_font.pointSize() + 2)
        head_font.setBold(True)
        self.header_label.setFont(head_font)
        self.header_label.setWordWrap(True)
        self.switch_button = _flat_button("")
        self.switch_button.setFlat(True)
        self.switch_button.setStyleSheet("color:#1a5fb4; text-decoration:underline; border:none; padding:2px 6px;")
        self.switch_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.switch_button.clicked.connect(self.toggle_view)
        top = QHBoxLayout()
        top.addWidget(self.header_label, 1)
        top.addWidget(self.switch_button)

        self.banner_label = QLabel(banner)
        self.banner_label.setWordWrap(True)
        self.banner_label.setStyleSheet("background:#eef3fb; color:#1c2b45; border:1px solid #c5d3ea; padding:4px 8px;")
        self.banner_label.setVisible(bool(banner))

        _shrinkable(self.header_label, self.banner_label)
        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_one_page())
        self.stack.addWidget(self._build_list_page())

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.banner_label)
        layout.addWidget(self.stack, 1)

        self._fill_tree()
        self._apply_view()

    # -- building --------------------------------------------------------------------------------------------

    def _make_panel(self) -> ProposalPanel:
        panel = ProposalPanel(self._can_choose)
        panel.candidateChosen.connect(self._choose_candidate)
        panel.levelChosen.connect(self._choose_level)
        panel.openNoteRequested.connect(self.open_current_note)
        panel.showFolderRequested.connect(self.show_current_folder)
        return panel

    def _build_one_page(self) -> QWidget:
        page = QWidget()
        self.one_panel = self._make_panel()
        self.back_button = _flat_button("Back")
        self.skip_button = _flat_button("Skip")
        self.write_button = QPushButton("Write note")
        self.write_button.setDefault(True)
        self.write_button.setAutoDefault(True)
        self.write_all_button = _flat_button("Write all remaining confident")
        self.close_one_button = _flat_button("Close")
        self.back_button.clicked.connect(self.go_back)
        self.skip_button.clicked.connect(self.skip_current)
        self.write_button.clicked.connect(self.write_current)
        self.write_all_button.clicked.connect(self.write_remaining)
        self.close_one_button.clicked.connect(self.reject)
        row = QHBoxLayout()
        row.addWidget(self.back_button)
        row.addWidget(self.write_all_button)
        row.addStretch(1)
        row.addWidget(self.close_one_button)
        row.addWidget(self.skip_button)
        row.addWidget(self.write_button)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.one_panel, 1)
        layout.addLayout(row)
        return page

    def _build_list_page(self) -> QWidget:
        page = QWidget()
        self.tree = QTreeWidget()
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(["Folder", "Level", "Confidence", "State"])
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in (1, 2, 3):
            self.tree.header().setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.currentItemChanged.connect(self._on_tree_current)
        self.tree.itemChanged.connect(self._on_tree_check)
        self.list_panel = self._make_panel()

        self.filter_check = QCheckBox("Only uncertain or not found")
        self.filter_check.toggled.connect(lambda _on: self._fill_tree())
        self.tick_all_button = _flat_button("Tick all confident")
        self.untick_all_button = _flat_button("Untick all")
        self.tick_guesses_button = _flat_button("Tick best guesses")
        self.tick_guesses_button.setToolTip(
            "Uncertain matches that have a candidate and a clear level: use the top candidate and tick them. "
            "RISK: the match was not confirmed by your albums, so a note may describe the wrong artist or album. "
            "They are still written as drafts (status: draft) and nothing is written until you click Write.")
        self.tick_guesses_button.clicked.connect(self._tick_best_guesses)
        self.tick_all_button.clicked.connect(self._tick_all)
        self.untick_all_button.clicked.connect(self._untick_all)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(self.filter_check)
        left_layout.addWidget(self.tree, 1)

        from PyQt6.QtWidgets import QSplitter

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(self.list_panel)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([430, 640])
        self.splitter = splitter

        self.write_ticked_button = QPushButton("Write 0 ticked notes")
        self.write_ticked_button.setAutoDefault(False)
        self.write_ticked_button.clicked.connect(self.write_ticked)
        self.close_list_button = _flat_button("Close")
        self.close_list_button.clicked.connect(self.reject)
        row = QHBoxLayout()
        row.addWidget(self.tick_all_button)
        row.addWidget(self.untick_all_button)
        row.addWidget(self.tick_guesses_button)
        row.addStretch(1)
        row.addWidget(self.write_ticked_button)
        row.addWidget(self.close_list_button)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(splitter, 1)
        layout.addLayout(row)
        return page

    def _common_root(self) -> Optional[str]:
        folders = [str(item.proposal.folder.parent) for item in self.session.items]
        try:
            return os.path.commonpath(folders) if folders else None
        except ValueError:
            return None

    # -- views -----------------------------------------------------------------------------------------------

    def toggle_view(self) -> None:
        if self.view == VIEW_ONE:
            self.view = VIEW_LIST
            self.filter_check.setChecked(False)
        else:
            self.view = VIEW_ONE
            current = self.tree.currentItem()
            if current is not None:
                self.index = current.data(0, Qt.ItemDataRole.UserRole)
        self._apply_view()

    def _apply_view(self) -> None:
        one = self.view == VIEW_ONE
        self.stack.setCurrentIndex(0 if one else 1)
        self.switch_button.setText("Switch to list view" if one else "Switch to one-at-a-time view")
        if one:
            self._show_one()
        else:
            self._fill_tree()
            self._select_tree_index(self.index)
            self._refresh_list_header()

    def _refresh_all(self) -> None:
        if self.view == VIEW_ONE:
            self._show_one()
        else:
            self._fill_tree()
            self._select_tree_index(self.index)

    # -- one at a time ---------------------------------------------------------------------------------------

    def _show_one(self) -> None:
        items = self.session.items
        if not items:
            return
        self.index = max(0, min(self.index, len(items) - 1))
        item = items[self.index]
        self.header_label.setText(f"Create notes  {self.index + 1} of {len(items)}")
        self.one_panel.show_item(item)
        self.back_button.setEnabled(self.index > 0)
        self.skip_button.setEnabled(item.state == PENDING)
        self.write_button.setEnabled(item.tickable)
        if item.tickable:
            self.write_button.setFocus()
        remaining = sum(1 for i in items if i.tickable)
        self.write_all_button.setText(f"Write all remaining confident ({remaining})")
        self.write_all_button.setEnabled(remaining > 0)

    def go_back(self) -> None:
        if self.index > 0:
            self.index -= 1
            self._show_one()

    def _advance(self) -> None:
        nxt = self.session.next_pending(self.index)
        if nxt is None:
            self._finish()
        else:
            self.index = nxt
            self._show_one()

    def skip_current(self) -> None:
        self.session.skip(self.index)
        self._advance()

    def write_current(self) -> None:
        self._write_index(self.index)
        self._advance()

    def write_remaining(self) -> None:
        results = self.session.write_remaining_confident()
        self._report_problems(results)
        if self.view == VIEW_ONE and self.session.next_pending(-1) is None:
            self._finish()  # nothing left to decide: the summary, then the page closes
            return
        self._summary_box()
        self._refresh_all()

    def _write_index(self, index: int) -> None:
        result = self.session.write(index)
        self._report_problems([result])

    def _report_problems(self, results) -> None:
        bad = [r for r in results if r.status in ("exists", "too_large", "error")]
        if bad and len(results) == 1:  # one note asked for: say at once why it was not written
            QMessageBox.warning(self, "Create Notes", f"The note was not written.\n\n{bad[0].message or bad[0].status}\n{bad[0].path}")

    def _finish(self) -> None:
        self._summary_box()
        self.accept()

    def _summary_box(self) -> None:
        totals = self.session.totals()
        self.last_summary = totals.text() + (f"\n\n{self._history_note}" if self._history_note and totals.written else "")
        QMessageBox.information(self, "Create Notes: summary", self.last_summary)

    # -- the list --------------------------------------------------------------------------------------------

    def _fill_tree(self) -> None:
        tree = self.tree
        selected = self.index
        tree.blockSignals(True)
        tree.clear()
        only_open = self.filter_check.isChecked()
        for i, item in enumerate(self.session.items):
            p = item.proposal
            if only_open and p.confidence == Confidence.CONFIDENT:
                continue
            row = QTreeWidgetItem([short_path(p.folder, self._root), p.level or p.kind, item.confidence_word, item.state_word])
            row.setData(0, Qt.ItemDataRole.UserRole, i)
            flags = row.flags() | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled
            if item.tickable:
                flags |= Qt.ItemFlag.ItemIsUserCheckable
            else:
                flags &= ~Qt.ItemFlag.ItemIsUserCheckable
            row.setFlags(flags)
            if item.tickable:
                row.setCheckState(0, Qt.CheckState.Checked if item.ticked else Qt.CheckState.Unchecked)
            else:  # no checkbox at all: it has to be resolved (candidate / level) before it can be written
                row.setData(0, Qt.ItemDataRole.CheckStateRole, None)
            row.setForeground(2, QBrush(QColor(TEXT_COLOURS[p.confidence])))
            blocked = not item.tickable and item.state == PENDING
            row.setToolTip(0, p.path + ("\nNot tickable: pick a candidate or a level first" if blocked else ""))
            tree.addTopLevelItem(row)
        tree.blockSignals(False)
        self.index = selected
        self._refresh_list_header()

    def _select_tree_index(self, index: int) -> None:
        for r in range(self.tree.topLevelItemCount()):
            row = self.tree.topLevelItem(r)
            if row.data(0, Qt.ItemDataRole.UserRole) == index:
                self.tree.setCurrentItem(row)
                return
        if self.tree.topLevelItemCount():
            self.tree.setCurrentItem(self.tree.topLevelItem(0))
        else:
            self.list_panel.show_item(None)

    def _on_tree_current(self, current, _previous) -> None:
        if current is None:
            self.list_panel.show_item(None)
            return
        self.index = current.data(0, Qt.ItemDataRole.UserRole)
        self.list_panel.show_item(self.session.items[self.index])

    def _on_tree_check(self, row, column) -> None:
        if column != 0:
            return
        index = row.data(0, Qt.ItemDataRole.UserRole)
        wanted = row.checkState(0) == Qt.CheckState.Checked
        if not self.session.set_ticked(index, wanted):
            self.tree.blockSignals(True)
            row.setData(0, Qt.ItemDataRole.CheckStateRole, None)  # an unresolved proposal cannot be ticked
            self.tree.blockSignals(False)
        self._refresh_list_header()

    def _tick_all(self) -> None:
        self.session.tick_confident()
        self._fill_tree()
        self._select_tree_index(self.index)

    def guess_indices(self) -> list[int]:
        return [i for i, item in enumerate(self.session.items)
                if item.state == PENDING and not item.proposal.writable and self._best_guess(item.proposal) is not None]

    def _tick_best_guesses(self) -> None:
        """Rebuilds every uncertain proposal that has a usable top candidate and a clear level around that
        candidate (engine.rebuild), under a progress dialog; the rebuilt notes are ticked. Nothing is written."""
        indices = self.guess_indices()
        if not indices:
            QMessageBox.information(self, "Create Notes", "No uncertain match has a candidate and a clear level to guess from.")
            return
        done: list[int] = []

        def work(progress):
            for n, i in enumerate(indices):
                if progress.should_cancel():
                    break
                proposal = self.session.items[i].proposal
                progress.set_value(n)
                progress.set_label(f"Looking up {proposal.folder.name} ({n + 1} of {len(indices)})…")
                guess = self._best_guess(proposal)
                try:
                    rebuilt = self._rebuild(proposal, guess, "")
                except Exception:  # noqa: BLE001 -- that folder stays as it was
                    continue
                self.session.replace(i, rebuilt)
                done.append(i)

        run_work_with_progress(self, len(indices), "Looking up the best guesses…", work, cancellable=True, title="Create Notes")
        self._refresh_all()

    def _untick_all(self) -> None:
        for i in range(len(self.session.items)):
            self.session.set_ticked(i, False)
        self._fill_tree()
        self._select_tree_index(self.index)

    def _refresh_list_header(self) -> None:
        if self.view != VIEW_LIST:
            return
        items = self.session.items
        counts = {c: sum(1 for i in items if i.proposal.confidence == c) for c in Confidence}
        self.header_label.setText(
            f"Create notes  {len(items)} folders: {counts[Confidence.CONFIDENT]} confident, "
            f"{counts[Confidence.UNCERTAIN]} uncertain, {counts[Confidence.NONE]} nothing found")
        n = len(self.session.ticked_pending())
        self.write_ticked_button.setText(f"Write {n} ticked note{'s' if n != 1 else ''}")
        self.write_ticked_button.setEnabled(n > 0)

    def write_ticked(self) -> None:
        results = self.session.write_ticked()
        self._report_problems(results)
        self._summary_box()
        self._fill_tree()
        self._select_tree_index(self.index)

    # -- choices (candidate / level) -------------------------------------------------------------------------

    def _choose_candidate(self, candidate: Candidate) -> None:
        self._resolve(candidate, "")

    def _choose_level(self, level: str) -> None:
        if level == SUGGEST_SKIP:
            self.session.skip(self.index)
            if self.view == VIEW_ONE:
                self._advance()
            else:
                self._refresh_all()
            return
        self._resolve(None, level)

    def _resolve(self, candidate: Optional[Candidate], level: str) -> None:
        item = self.session.items[self.index]
        what = candidate.name if candidate else LEVEL_LABELS.get(level, level)
        try:
            rebuilt = run_busy(self, f"Looking up {what}…", self._rebuild, item.proposal, candidate, level, title="Create Notes")
        except Exception as exc:  # noqa: BLE001 -- shown as one readable message, never a traceback
            QMessageBox.warning(self, "Create Notes", f"The lookup failed: {exc}")
            return
        self.session.replace(self.index, rebuilt)
        self._refresh_all()

    # -- opening ---------------------------------------------------------------------------------------------

    def open_current_note(self) -> None:
        item = self.session.items[self.index]
        if item.written_path:
            self._open_path(item.written_path)

    def show_current_folder(self) -> None:
        item = self.session.items[self.index]
        if item.written_path:
            self._open_path(str(Path(item.written_path).parent))

    # -- closing ---------------------------------------------------------------------------------------------

    def reject(self) -> None:
        waiting = len(self.session.ticked_pending())
        if waiting:
            answer = QMessageBox.question(
                self, "Create Notes",
                f"{waiting} ticked note{'s have' if waiting != 1 else ' has'} not been written. Close anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
        super().reject()

    def done(self, result: int) -> None:
        if self._on_size is not None:
            self._on_size(self.width(), self.height())
        super().done(result)


# ---------------------------------------------------------------------------------------------------------------
# Running it
# ---------------------------------------------------------------------------------------------------------------

def parse_size(text: str) -> Optional[tuple[int, int]]:
    try:
        w, h = (int(part) for part in text.lower().split("x"))
    except (ValueError, AttributeError):
        return None
    return (w, h) if 500 <= w <= 5000 and 350 <= h <= 4000 else None


def create_notes(
    parent: QWidget, paths: list, include_subfolders: bool, make_fetchers: Callable[[], Fetchers],
    size_text: str = "", on_size: Optional[Callable[[str], None]] = None,
    open_path: Callable[[str], None] = open_in_system,
) -> Optional[NotesReviewDialog]:
    """Scan `paths`, look every folder up under a cancellable progress dialog, then open the review page. Returns
    the (closed) dialog, or None when there was nothing to review."""
    title = "Create Notes"
    try:
        found = run_busy(parent, "Looking at the folders…", lambda: scan(paths, include_subfolders, kinds=ALL_KINDS), title=title)
    except Exception as exc:  # noqa: BLE001
        QMessageBox.warning(parent, title, f"The folders could not be read: {exc}")
        return None
    if not found.targets:
        why = []
        if found.skipped:
            why.append(f"{len(found.skipped)} folder(s) already have a note and were left alone.")
        if found.missing:
            why.append("Not found: " + ", ".join(str(m) for m in found.missing))
        QMessageBox.information(
            parent, title, "No artist or album folders without a note were found.\n\n" + "\n".join(why) if why
            else "No artist or album folders without a note were found.")
        return None

    fetchers = make_fetchers()
    engine = NotesEngine(fetchers)
    targets = found.targets
    built: list[Proposal] = []
    failure: list[str] = []

    def work(progress):
        def report(done: int, total: int, _label: str) -> None:
            if done < total:
                folder = targets[done].folder
                name = f"{folder.parent.name} / {folder.name}" if targets[done].kind == "album" else folder.name
                progress.set_value(done)
                progress.set_label(f"Looking up {name} ({done + 1} of {total})…")

        try:
            build_proposals(targets, fetchers, progress=report, cancel=progress.should_cancel,
                            on_proposal=built.append, engine=engine)
        except Exception as exc:  # noqa: BLE001 -- the proposals built so far are kept
            failure.append(str(exc) or exc.__class__.__name__)

    _result, cancelled = run_work_with_progress(
        parent, len(targets), "Looking up the folders…", work, cancellable=True, title=title)

    if not built:
        QMessageBox.information(parent, title, "Cancelled before anything was looked up." if cancelled
                                else "Nothing could be looked up." + (f"\n\n{failure[0]}" if failure else ""))
        return None
    session = ReviewSession(built)
    notes = []
    if fetchers.discogs is None:
        notes.append(DISCOGS_NOTE)
    if cancelled:
        notes.append(f"Cancelled: {len(built)} of {len(targets)} folders were looked up; the rest are not in this list.")
    if failure:
        notes.append(f"The lookup stopped early: {failure[0]}")
    if found.skipped:
        notes.append(f"{len(found.skipped)} folder(s) already have a note and are not listed.")
    unreachable = [p for p in session.problems() if p.lower().startswith("could not reach")]
    if unreachable:
        notes.append(f"The internet services could not be reached: {unreachable[0]}")
    from core.history import history_enabled

    dialog = NotesReviewDialog(
        session, choose_view(targets), _rebuild_with(engine), engine.can_choose,
        banner="  ".join(notes), parent=parent, size=parse_size(size_text),
        on_size=(lambda w, h: on_size(f"{w}x{h}")) if on_size else None, open_path=open_path,
        history_note="Recorded in Tools > History (it is switched on)." if history_enabled() else "",
        best_guess=engine.best_guess)
    dialog.exec()
    return dialog


def _rebuild_with(engine: NotesEngine):
    def rebuild(proposal: Proposal, candidate: Optional[Candidate], level: str) -> Proposal:
        return engine.rebuild(proposal, candidate=candidate, level=level)

    return rebuild


class NotesScopeDialog(QDialog):
    """The first step of Tools > Create Notes: which folder? The selection is only ever a SUGGESTION here (the
    artist folder of the selected files), never used silently."""

    def __init__(self, parent: QWidget, start: str, suggestions: list[str], include_subfolders: bool):
        super().__init__(parent)
        self.setWindowTitle("Create Notes: which folder?")
        self.start = start
        self.folders: list[str] = []
        self.browsed = False
        title = QLabel("Create notes for which folder?")
        font = title.font()
        font.setBold(True)
        title.setFont(font)
        how = ("Include Subfolders is on: everything below the folder is looked at (including subfolders)."
               if include_subfolders else
               "Include Subfolders is off: only the folder itself is looked at (switch it on in the toolbar "
               "to cover a whole library).")
        info = QLabel("Pick a library, artist or album folder. " + how)
        info.setWordWrap(True)
        self.suggestion_button: Optional[QPushButton] = None
        layout = QVBoxLayout(self)
        layout.addWidget(title)
        layout.addWidget(info)
        if suggestions:
            if len(suggestions) == 1:
                text = f"Use the artist folder of the selected files:\n{suggestions[0]}"
            else:
                text = (f"Use the {len(suggestions)} artist folders of the selected files:\n"
                        + "\n".join(suggestions[:4]) + ("\n…" if len(suggestions) > 4 else ""))
            self.suggestion_button = QPushButton(text)
            self.suggestion_button.setAutoDefault(False)
            self.suggestion_button.setStyleSheet("text-align:left; padding:6px 10px;")
            self.suggestion_button.clicked.connect(lambda: self._use(list(suggestions), False))
            layout.addWidget(self.suggestion_button)
        self.choose_button = QPushButton("Choose another folder…")
        self.choose_button.setAutoDefault(suggestions == [])
        self.choose_button.clicked.connect(self._choose)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setAutoDefault(False)
        self.cancel_button.clicked.connect(self.reject)
        row = QHBoxLayout()
        row.addWidget(self.choose_button)
        row.addStretch(1)
        row.addWidget(self.cancel_button)
        layout.addLayout(row)
        self.setMinimumWidth(520)

    def _use(self, folders: list[str], browsed: bool) -> None:
        self.folders, self.browsed = folders, browsed
        self.accept()

    def _choose(self) -> None:
        folder = ask_folder(self, self.start)
        if folder:
            self._use([folder], True)


def ask_scope(parent: QWidget, start: str, suggestions: list[str], include_subfolders: bool) -> tuple[list[str], bool]:
    """(folders, browsed): the folders to create notes for ([] = cancelled) and whether the person browsed for
    them (so the caller can remember that folder as the next starting point)."""
    dialog = NotesScopeDialog(parent, start, suggestions, include_subfolders)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return [], False
    return dialog.folders, dialog.browsed


def ask_folder(parent: QWidget, start: str) -> str:
    """The folder dialog of Create Notes without a selection (a library root, an artist or an album folder)."""
    return QFileDialog.getExistingDirectory(
        parent, "Create Notes: choose a library, artist or album folder", start)
