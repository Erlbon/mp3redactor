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

import logging
import os
import time
from pathlib import Path
from typing import Callable, Optional

from PyQt6.QtCore import QSize, Qt, QThread, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QDesktopServices, QTextDocument
from PyQt6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMessageBox, QPushButton, QRadioButton, QSizePolicy, QStackedWidget, QTableWidget, QTableWidgetItem, QTabWidget, QTextBrowser, QTextEdit,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from core.notes_flow import (
    LEVEL_LOCAL_TAGS, Candidate, Confidence, Fetchers, NotesEngine, Proposal, build_proposals, scan,
)
from core.notes_level import SUGGEST_ALBUM, SUGGEST_ARTIST, SUGGEST_COLLECTION, SUGGEST_SKIP
from core.notes_local import ALL_KINDS
from core import notes_debug
from core.notes_kinds import kinds_text, parse_kinds
from core.notes_link import LinkError
from core.notes_local import LIBRARY_ROOT_MESSAGE
from core.notes_scope import ScopeChoice, count_targets, folder_caption, folder_info, kinds_sentence
from core.notes_timing import lookup_sentence, progress_suffix, scope_hint
from core.notes_local import KIND_ALBUM, KIND_ARTIST
from redactor_common.core.notes import STATE_LEGACY
from core.notes_review import (
    note_diff,
    CONFIDENCE_WORDS, ERROR, EXISTS, PENDING, SKIPPED, TOO_LARGE, VIEW_LIST, VIEW_ONE, WRITTEN, ReviewItem,
    ReviewSession, choose_view,
)
from redactor_common.core.notes import parse_front_matter
from gui.columns import columns_resizable
from redactor_common.gui.progress import run_busy
from redactor_common.gui.work_progress import run_work_with_progress

log = logging.getLogger("mp3redactor.notes")

DISCOGS_NOTE = "Discogs token not set: notes will be thinner (Tools > API Keys…)."
DEFAULT_SIZE = (1100, 700)

# Confidence colours: a tinted background with dark text, readable in a light and a dark theme.
BADGE_STYLES = {
    Confidence.CONFIDENT: "background:#c9ecd1; color:#12401f;",
    Confidence.UNCERTAIN: "background:#fbe3a6; color:#4d3600;",
    Confidence.NONE: "background:#dcdcdc; color:#333333;",
}
TEXT_COLOURS = {Confidence.CONFIDENT: "#1b7f3b", Confidence.UNCERTAIN: "#a86a00", Confidence.NONE: "#707070"}

OUTDATED_CHECK_TEXT = "Also review notes made by an older version"
UPDATE_BADGE_TEXT = "Update"
UPDATE_BADGE_STYLE = "background:#cfe0f7; color:#10325c; border-radius:4px; padding:3px 8px; font-weight:bold;"
LEGACY_WARNING = "Made by the first versions: cannot tell whether you edited it. Look at Before / after before you update it."
RECYCLE_NOTE = "The old note goes to the Recycle Bin. You can restore it from the Recycle Bin."


def diff_html(old_text: str, new_text: str) -> str:
    """The unified diff of the note now against the new draft as HTML: removed lines red, added lines green."""
    from html import escape

    diff = note_diff(old_text, new_text)
    if not diff:
        return "<p>The new note is the same as the note now.</p>"
    rows = []
    for line in diff.split("\n"):
        colour = ("#9b1c1c" if line.startswith("-") and not line.startswith("---")
                  else "#17622b" if line.startswith("+") and not line.startswith("+++")
                  else "#555555" if line.startswith(("@@", "---", "+++")) else "")
        style = f' style="color:{colour}"' if colour else ""
        rows.append(f"<span{style}>{escape(line) or '&nbsp;'}</span>")
    return "<pre style='font-family:Consolas,monospace'>" + "<br>".join(rows) + "</pre>"


LEVEL_LABELS = {
    SUGGEST_ARTIST: "Artist folder (artist.md)",
    SUGGEST_COLLECTION: "Genre or collection (about.md)",
    SUGGEST_ALBUM: "Album folder (album.md)",
    SUGGEST_SKIP: "Skip this folder",
    LEVEL_LOCAL_TAGS: "a minimal note from the tags",
}
LOCAL_BUTTON_TEXT = "Create a minimal note from the tags"
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


def left_alone_sentence(found) -> str:
    """'N notes were left alone: edited by a person or not ours.' (and how many are already current)."""
    left = sum(1 for x in found.skipped if x.state in ("edited", "foreign"))
    current = sum(1 for x in found.skipped if x.state == "current")
    return (f"{left} notes were left alone: edited by a person or not ours."
            + (f" {current} are already current." if current else ""))


def _plural_n(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


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
    genresChosen = pyqtSignal(list)  # the genres a genre / collection folder covers, as edited
    linkChosen = pyqtSignal(str)  # a pasted link (Wikipedia, MusicBrainz, Discogs) or MusicBrainz id, already checked
    openNoteRequested = pyqtSignal()
    showFolderRequested = pyqtSignal()

    def __init__(self, can_choose: Callable[[Proposal, Candidate], bool], parent=None,
                 why_not: Optional[Callable[[Proposal, Candidate], str]] = None):
        super().__init__(parent)
        self._can_choose = can_choose
        self._why_not = why_not
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
        self.update_badge = QLabel(UPDATE_BADGE_TEXT)
        self.update_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.update_badge.setStyleSheet(UPDATE_BADGE_STYLE)
        self.update_badge.setVisible(False)
        self.state_label = QLabel()
        head = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.addWidget(self.path_label)
        title_box.addWidget(self.kind_label)
        head.addLayout(title_box, 1)
        head.addWidget(self.state_label)
        head.addWidget(self.update_badge, 0, Qt.AlignmentFlag.AlignTop)
        head.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignTop)
        self.legacy_label = QLabel(LEGACY_WARNING)
        self.legacy_label.setWordWrap(True)
        self.legacy_label.setStyleSheet("background:#fff0c2; color:#4d3600; border:1px solid #e0c36a; padding:4px 8px;")
        self.legacy_label.setVisible(False)

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
        self._candidate_columns_set = False
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

        # the explicit local-only note (never automatic, never in a batch)
        self.local_box = QWidget()
        local_row = QHBoxLayout(self.local_box)
        local_row.setContentsMargins(0, 0, 0, 0)
        self.local_button = _flat_button(LOCAL_BUTTON_TEXT)
        self.local_button.setToolTip("Title, year and tracklist read from the files (an artist: the album folder names), "
                                     "written as a draft that says it has no online source. Nothing is looked up.")
        self.local_button.clicked.connect(lambda: self.levelChosen.emit(LEVEL_LOCAL_TAGS))
        local_row.addWidget(self.local_button)
        local_row.addStretch(1)

        # 'Look up by link': for any artist / album row, paste the page that is the right one
        self.link_box = QWidget()
        link_layout = QVBoxLayout(self.link_box)
        link_layout.setContentsMargins(0, 0, 0, 0)
        link_row = QHBoxLayout()
        link_row.addWidget(QLabel("Link:"))
        self.link_edit = QLineEdit()
        self.link_edit.setPlaceholderText("Paste a Wikipedia, MusicBrainz or Discogs link (or a MusicBrainz id)")
        self.link_edit.setToolTip("The right page for this folder, when the search did not find it: a Wikipedia article, a "
                                  "MusicBrainz artist or release-group page, a Discogs artist / release / master page, or a "
                                  "bare MusicBrainz id. Only musicbrainz.org, discogs.com, wikipedia.org and wikidata.org are used.")
        self.link_button = _flat_button("Look up by link")
        self.link_button.clicked.connect(self._link_clicked)
        self.link_edit.returnPressed.connect(self._link_clicked)
        self.link_edit.textChanged.connect(lambda _t: self.set_link_error(""))
        link_row.addWidget(self.link_edit, 1)
        link_row.addWidget(self.link_button)
        self.link_error = QLabel()
        self.link_error.setStyleSheet("color:#b00020;")
        self.link_error.setWordWrap(True)
        self.link_error.setVisible(False)
        link_layout.addLayout(link_row)
        link_layout.addWidget(self.link_error)

        # a genre / collection note: which genres the folder covers ('1.61 - Ska' = Ska, Ska punk); edit and look up again
        self.genres_box = QWidget()
        genres_row = QHBoxLayout(self.genres_box)
        genres_row.setContentsMargins(0, 0, 0, 0)
        genres_row.addWidget(QLabel("Genres covered:"))
        self.genres_edit = QLineEdit()
        self.genres_edit.setPlaceholderText("one genre, or several separated by commas (Ska, Ska punk)")
        self.genres_edit.setToolTip("The genres this folder covers, separated by commas. Each is checked on Wikipedia as a "
                                    "music genre; the note gets one section per genre.")
        self.genres_button = _flat_button("Look up again")
        self.genres_button.clicked.connect(self._genres_clicked)
        self.genres_edit.returnPressed.connect(self._genres_clicked)
        genres_row.addWidget(self.genres_edit, 1)
        genres_row.addWidget(self.genres_button)

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
        self.diff_view = QTextBrowser()  # an UPDATE: the note now (red) against the new draft (green), as a diff
        self.diff_tab = self.tabs.addTab(self.diff_view, "Before / after")
        self.tabs.setTabVisible(self.diff_tab, False)

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
        layout.addWidget(self.legacy_label)
        layout.addWidget(self.reasons_label)
        layout.addWidget(self.problems_label)
        layout.addWidget(self.level_box)
        layout.addWidget(self.candidate_box)
        layout.addWidget(self.local_box)
        layout.addWidget(self.genres_box)
        layout.addWidget(self.link_box)
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
            self.update_badge.hide()
            self.legacy_label.hide()
            self.tabs.setTabVisible(self.diff_tab, False)
            self.diff_view.clear()
            self.level_box.hide()
            self.candidate_box.hide()
            self.local_box.hide()
            self.genres_box.hide()
            self.link_box.hide()
            self.preview.clear()
            self.raw.clear()
            self.open_note_button.hide()
            self.show_folder_button.hide()
            return
        p = item.proposal
        self.path_label.setText(p.path)
        level = f"  |  level: {p.level}" if p.level else ""
        self.kind_label.setText(f"Note kind: {p.note_kind}{level}")
        self.update_badge.setVisible(p.is_update)
        self.legacy_label.setVisible(p.is_update and p.old_state.kind == STATE_LEGACY)
        self.tabs.setTabVisible(self.diff_tab, p.is_update and bool(p.note_text))
        if p.is_update and p.note_text:
            self.diff_view.setHtml(diff_html(p.old_text, p.note_text))
        else:
            self.diff_view.clear()
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
        self.link_box.setVisible(p.kind in ("artist", "album") and item.state == PENDING and p.note_kind != "about")
        about = p.note_kind == "about" and item.state == PENDING
        self.genres_box.setVisible(about)
        if about and not self.genres_edit.hasFocus():
            self.genres_edit.setText(", ".join(p.genres) if p.genres else p.title or p.folder.name)
        self.local_box.setVisible(item.state == PENDING and p.kind in ("artist", "album") and not p.writable
                                  and p.note_kind in ("artist", "album"))
        self._fill_candidates(p)

        self.raw.setPlainText(p.note_text)
        if p.note_text:
            self.preview.document().setMarkdown(
                self.render_body(p.note_text),
                QTextDocument.MarkdownFeature.MarkdownDialectGitHub | QTextDocument.MarkdownFeature.MarkdownNoHTML)
        else:
            self.preview.document().setMarkdown(
                "*No draft yet.* " + ("Pick one of the candidates above, or a level, to build a note."
                                      if (self._candidates or alternatives) else
                                      "Nothing was found online for this folder (see the reasons above)."
                                      + (" You can still create a minimal note from the tags with the button above."
                                         if self.local_box.isVisibleTo(self) else "")),
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
            source = cand.source + (f" + {cand.also}" if cand.also else "")
            for col, text in enumerate((cand.name, cand.detail, f"{cand.score:.2f}", source, hits)):
                table.setItem(row, col, QTableWidgetItem(text))
                table.item(row, col).setToolTip(cand.note or text)
        if self._candidates:
            table.selectRow(0)
        if self._candidates and not self._candidate_columns_set:  # once: a person's dragged widths stay
            self._candidate_columns_set = True
            columns_resizable(table, 0)

    def selected_candidate(self) -> Optional[Candidate]:
        row = self.candidate_table.currentRow()
        return self._candidates[row] if 0 <= row < len(self._candidates) else None

    def _update_buttons(self) -> None:
        cand = self.selected_candidate()
        ok = bool(cand and self._item and self._can_choose(self._item.proposal, cand))
        self.candidate_button.setEnabled(ok)
        if ok or cand is None or self._item is None:
            self.candidate_note.setText("")
        else:
            why = self._why_not(self._item.proposal, cand) if self._why_not else ""
            self.candidate_note.setText(why or "A note cannot be built from this candidate.")

    def _candidate_clicked(self) -> None:
        cand = self.selected_candidate()
        if cand is not None:
            self.candidateChosen.emit(cand)

    def set_link_error(self, text: str) -> None:
        self.link_error.setText(text)
        self.link_error.setVisible(bool(text))

    def _link_clicked(self) -> None:
        from core.notes_link import LinkError, parse_link

        kind = self._item.proposal.kind if self._item is not None else "artist"
        text = self.link_edit.text().strip()
        try:
            parse_link(text, bare_as="release-group" if kind == "album" else "artist")  # checked here: nothing is fetched yet
        except LinkError as exc:
            self.set_link_error(str(exc))
            return
        self.set_link_error("")
        self.linkChosen.emit(text)

    def _genres_clicked(self) -> None:
        genres = [g.strip() for g in self.genres_edit.text().replace(";", ",").split(",") if g.strip()]
        if genres:
            self.genresChosen.emit(genres)

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
        why_not: Optional[Callable[[Proposal, Candidate], str]] = None, offer_albums: bool = False,
        lookup_note: str = "",
    ):
        super().__init__(parent)
        self.setWindowTitle("Create Notes")
        self._lookup_note = lookup_note  # 'Looked up N folders in H:MM:SS (R requests, C from the cache).'
        self.followup: list[str] = []  # the artist folders whose album notes the person asked for next
        self._best_guess = best_guess or (lambda _proposal: None)
        self.session = session
        self.view = view
        self.index = 0
        self.last_summary = ""
        self._rebuild = rebuild
        self._can_choose = can_choose
        self._why_not = why_not
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
        self._offer_albums = offer_albums
        self.followup_button = QPushButton("Create the album notes for these artists too")
        self.followup_button.setAutoDefault(False)
        self.followup_button.setToolTip("Closes this page and looks up only the albums of the artists whose notes "
                                        "were just written.")
        self.followup_button.setVisible(False)
        self.followup_button.clicked.connect(self._followup_clicked)
        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_one_page())
        self.stack.addWidget(self._build_list_page())

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.banner_label)
        layout.addWidget(self.followup_button, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.stack, 1)

        self._fill_tree()
        self._apply_view()

    # -- building --------------------------------------------------------------------------------------------

    def _make_panel(self) -> ProposalPanel:
        panel = ProposalPanel(self._can_choose, why_not=self._why_not)
        panel.candidateChosen.connect(self._choose_candidate)
        panel.levelChosen.connect(self._choose_level)
        panel.genresChosen.connect(self._choose_genres)
        panel.linkChosen.connect(self._choose_link)
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
        # A STABLE geometry (a person saw the rows jump up and down on a selection): both scroll bars are always
        # there (one appearing or disappearing changes the viewport by a scroll bar's width / height and moves every
        # row), scrolling is per pixel (a partly visible row is never snapped by a whole row), long paths are elided.
        self.tree.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.tree.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.tree.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self._tree_columns_set = False
        self.tree.currentItemChanged.connect(self._on_tree_current)
        self.tree.itemChanged.connect(self._on_tree_check)
        self.list_panel = self._make_panel()

        self.filter_check = QCheckBox("Only uncertain or not found")
        self.filter_check.toggled.connect(self._filter_toggled)
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
        self.row_hint = QLabel()  # the path of the current row and why it cannot be ticked: stable, under the list
        self.row_hint.setStyleSheet("color:#666666;")
        self.row_hint.setFixedHeight(2 * self.row_hint.fontMetrics().lineSpacing() + 4)  # two lines, always: the list never moves
        self.row_hint.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.row_hint.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        left_layout.addWidget(self.row_hint)

        from PyQt6.QtWidgets import QScrollArea, QSplitter

        # The panel's content changes with every row (candidate table, level box, local button, update badge, diff tab):
        # in a scroll area its size hints can never push the splitter or the whole dialog around.
        self.list_scroll = QScrollArea()
        self.list_scroll.setWidgetResizable(True)
        self.list_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.list_scroll.setWidget(self.list_panel)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(self.list_scroll)
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
        self._update_followup()

    def _written_artist_folders(self) -> list[str]:
        return [str(i.proposal.folder) for i in self.session.items if i.state == WRITTEN and i.proposal.kind == "artist"]

    def _update_followup(self) -> None:
        self.followup_button.setVisible(self._offer_albums and bool(self._written_artist_folders()))

    def _followup_clicked(self) -> None:
        self.followup = self._written_artist_folders()
        if self.followup:
            self.accept()

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
        self.write_button.setText("Update note" if item.proposal.is_update else "Write note")
        if item.tickable:
            self.write_button.setFocus()
        remaining = sum(1 for i in items if i.tickable)
        self.write_all_button.setText(f"Write all remaining confident ({remaining})")
        self.write_all_button.setEnabled(remaining > 0)
        self._update_followup()

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

    def _confirm_updates(self, indices: list[int]) -> bool:
        """An UPDATE replaces an existing note: ask once, say where the old one goes. True = go on."""
        n = sum(1 for i in indices if self.session.items[i].proposal.is_update)
        if not n:
            return True
        legacy = sum(1 for i in indices if self.session.items[i].proposal.is_update
                     and self.session.items[i].proposal.old_state.kind == STATE_LEGACY)
        text = f"Replace {_plural_n(n, 'note')} made by an older version?\n\n{RECYCLE_NOTE}"
        if legacy:
            text += (f"\n\n{legacy} of them {'was' if legacy == 1 else 'were'} made by the first versions: "
                     "it cannot be told whether you edited " + ("it." if legacy == 1 else "them."))
        answer = QMessageBox.question(self, "Create Notes", text,
                                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                      QMessageBox.StandardButton.No)
        return answer == QMessageBox.StandardButton.Yes

    def write_current(self) -> None:
        if not self._confirm_updates([self.index]):
            return
        self._write_index(self.index)
        self._advance()

    def write_remaining(self) -> None:
        wanted = [i for i, item in enumerate(self.session.items)
                  if item.tickable and (not item.proposal.is_update or item.ticked)]
        if not self._confirm_updates(wanted):
            return
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
        bad = [r for r in results if r.status in ("exists", "too_large", "error", "changed")]
        if bad and len(results) == 1:  # one note asked for: say at once why it was not written
            QMessageBox.warning(self, "Create Notes", f"The note was not written.\n\n{bad[0].message or bad[0].status}\n{bad[0].path}")

    def _finish(self) -> None:
        self._summary_box()
        folders = self._written_artist_folders() if self._offer_albums and not self.followup else []
        if folders:  # the page is closing: the one click for the albums of the artists just written
            answer = QMessageBox.question(
                self, "Create Notes",
                f"{_plural_n(len(folders), 'artist note')} written.\n\nCreate the album notes for "
                f"{'this artist' if len(folders) == 1 else 'these artists'} too? (slower: one lookup per album)",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if answer == QMessageBox.StandardButton.Yes:
                self.followup = folders
        self.accept()

    def _summary_box(self) -> None:
        totals = self.session.totals()
        self.last_summary = (totals.text() + (f"\n\n{self._lookup_note}" if self._lookup_note else "")
                             + (f"\n\n{self._history_note}" if self._history_note and totals.written else ""))
        QMessageBox.information(self, "Create Notes: summary", self.last_summary)

    # -- the list --------------------------------------------------------------------------------------------

    def _filter_toggled(self, _on: bool) -> None:
        """'Only uncertain or not found': the list is rebuilt, and the row that was current stays current (a rebuild empties
        the selection: the panel went on showing one folder while no row of the list was highlighted)."""
        self._fill_tree()
        self._select_tree_index(self.index)
        self._log("filter", on=_on)

    def _log(self, step: str, touched=(), **fields) -> None:
        """The opt-in troubleshooting log (core/notes_debug.py): counts, scroll values, indexes and folder NAMES only."""
        if not notes_debug.enabled():
            return
        tree = self.tree
        current = tree.currentItem()
        notes_debug.record(
            step, rows=tree.topLevelItemCount(), scroll=(tree.verticalScrollBar().value(), tree.horizontalScrollBar().value()),
            current=current.data(0, Qt.ItemDataRole.UserRole) if current is not None else None, index=self.index,
            touched=[self.session.items[i].proposal.folder.name for i in touched if 0 <= i < len(self.session.items)], **fields)

    def _update_row_hint(self) -> None:
        """Under the list, for the row that is current: its path, and why it has no tick box. (It was a tooltip on the
        row: it popped up at the mouse on every row the mouse crossed, and its last line seemed to follow the mouse.)"""
        row = self.tree.currentItem()
        if row is None:
            self.row_hint.setText("")
            return
        item = self.session.items[row.data(0, Qt.ItemDataRole.UserRole)]
        metrics = self.row_hint.fontMetrics()
        width = max(self.row_hint.width(), 240)
        lines = [metrics.elidedText(item.proposal.path, Qt.TextElideMode.ElideMiddle, width)]  # one line, never wrapped
        if not item.tickable and item.state == PENDING:
            lines.append("Not tickable yet: pick a candidate or a level first.")
        self.row_hint.setText("\n".join(lines))

    def _style_row(self, row: QTreeWidgetItem, i: int, item) -> None:
        """Everything a row of the list shows, set from the session item `i` (a new row, or one updated in place)."""
        p = item.proposal
        level_text = (p.level or p.kind) + (" - update" if p.is_update else "")
        for column, text in enumerate((short_path(p.folder, self._root), level_text, item.confidence_word, item.state_word)):
            row.setText(column, text)
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
        # no tooltip on the rows: see _update_row_hint()

    def _update_rows(self, indices) -> None:
        """After a pick (candidate, level, best guesses) the rows of those session items are UPDATED IN PLACE: the same
        row objects, the same positions, the same scroll position and selection; nothing is rebuilt (a rebuild is what made
        the list jump). With the 'Only uncertain' filter on, a row that became confident leaves the list."""
        tree = self.tree
        only_open = self.filter_check.isChecked()
        tree.blockSignals(True)
        try:
            for index in indices:
                for r in range(tree.topLevelItemCount()):
                    row = tree.topLevelItem(r)
                    if row.data(0, Qt.ItemDataRole.UserRole) != index:
                        continue
                    item = self.session.items[index]
                    if only_open and item.proposal.confidence == Confidence.CONFIDENT:
                        tree.takeTopLevelItem(r)
                    else:
                        self._style_row(row, index, item)
                    break
        finally:
            tree.blockSignals(False)
        current = tree.currentItem()
        if current is None and tree.topLevelItemCount():
            tree.setCurrentItem(tree.topLevelItem(0))
        elif current is not None:
            self.list_panel.show_item(self.session.items[current.data(0, Qt.ItemDataRole.UserRole)])
        else:
            self.list_panel.show_item(None)
        self._refresh_list_header()
        self._update_row_hint()
        self._log("update_rows", touched=list(indices))

    def _fill_tree(self) -> None:
        tree = self.tree
        selected = self.index
        scroll_y, scroll_x = tree.verticalScrollBar().value(), tree.horizontalScrollBar().value()
        tree.blockSignals(True)
        tree.clear()
        only_open = self.filter_check.isChecked()
        for i, item in enumerate(self.session.items):
            p = item.proposal
            if only_open and p.confidence == Confidence.CONFIDENT:
                continue
            row = QTreeWidgetItem()
            self._style_row(row, i, item)
            tree.addTopLevelItem(row)
        tree.blockSignals(False)
        tree.doItemsLayout()  # the scroll range is only updated by a layout
        tree.verticalScrollBar().setValue(scroll_y)  # clear() resets the view to the top: put it back where it was
        tree.horizontalScrollBar().setValue(scroll_x)
        if not self._tree_columns_set:  # once, with the first fill: a person's dragged widths stay on later refills
            self._tree_columns_set = True
            columns_resizable(tree, 0)
        self.index = selected
        self._refresh_list_header()
        self._update_row_hint()
        self._log("fill_tree")

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
        self._update_row_hint()
        self._log("current", touched=[self.index])

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
        self._update_rows(done)  # the rows that were rebuilt, in place
        self._update_followup()

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
        if not self._confirm_updates(self.session.ticked_pending()):
            return
        results = self.session.write_ticked()
        self._report_problems(results)
        self._summary_box()
        self._fill_tree()
        self._select_tree_index(self.index)
        self._update_followup()

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

    def _choose_genres(self, genres: list) -> None:
        self._resolve(None, "", genres=genres)

    def _choose_link(self, link: str) -> None:
        self._resolve(None, "", link=link)

    def _resolve(self, candidate: Optional[Candidate], level: str, genres: Optional[list] = None,
                 link: Optional[str] = None) -> None:
        item = self.session.items[self.index]
        what = (candidate.name if candidate else ("the link" if link else (", ".join(genres) if genres
                                                                            else LEVEL_LABELS.get(level, level))))
        extra = {}  # (a rebuild callable without these keeps working)
        if genres is not None:
            extra["genres"] = genres
        if link is not None:
            extra["link"] = link
        try:
            rebuilt = run_busy(self, f"Looking up {what}…", lambda: self._rebuild(item.proposal, candidate, level, **extra),
                               title="Create Notes")
        except LinkError as exc:  # the link cannot be used: said next to the line, nothing changes
            (self.list_panel if self.view == VIEW_LIST else self.one_panel).set_link_error(str(exc))
            return
        except Exception as exc:  # noqa: BLE001 -- shown as one readable message, never a traceback
            QMessageBox.warning(self, "Create Notes", f"The lookup failed: {exc}")
            return
        self.session.replace(self.index, rebuilt)
        if self.view == VIEW_LIST:
            self._update_rows([self.index])  # that one row, in place
            self._update_followup()
        else:
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
    open_path: Callable[[str], None] = open_in_system, kinds: tuple = ALL_KINDS, cache=None,
    include_outdated: bool = False, discogs_mode: str = "fallback", singles: bool = True,
) -> Optional[NotesReviewDialog]:
    """Scan `paths`, look every folder up under a cancellable progress dialog, then open the review page. Returns
    the (closed) dialog, or None when there was nothing to review."""
    title = "Create Notes"
    scan_started = time.perf_counter()
    try:
        found = run_busy(parent, "Looking at the folders…", lambda: scan(paths, include_subfolders, kinds=kinds, include_outdated=include_outdated), title=title)
    except Exception as exc:  # noqa: BLE001
        QMessageBox.warning(parent, title, f"The folders could not be read: {exc}")
        return None
    if not found.targets:
        if found.only_a_library_root:  # an empty or unreadable folder, or a library root (a generic place name)
            QMessageBox.information(parent, title, LIBRARY_ROOT_MESSAGE)
            return None
        why = []
        if found.skipped:
            why.append(f"{len(found.skipped)} folder(s) already have a note and were left alone.")
        if found.groupings:
            why.append(f"{len(found.groupings)} alphabetical grouping folder(s) were skipped.")
        if found.missing:
            why.append("Not found: " + ", ".join(str(m) for m in found.missing))
        if include_outdated:
            why.append(left_alone_sentence(found))
        QMessageBox.information(
            parent, title, "No artist or album folders without a note were found.\n\n" + "\n".join(why) if why
            else "No artist or album folders without a note were found.")
        return None

    fetchers = make_fetchers()
    engine = NotesEngine(fetchers, cache=cache, discogs_mode=discogs_mode, singles=singles)
    targets = found.targets
    built: list[Proposal] = []
    failure: list[str] = []

    engine.phases.add("scan", time.perf_counter() - scan_started, count=len(targets))
    current = [""]  # the progress label as it stands
    started = time.perf_counter()
    marks: list[float] = []  # when each folder was started: the gaps are how long the folders took

    def work(progress):
        # Runs on the worker thread: it only talks to the progress object (which marshals to the GUI thread).
        def report(done: int, total: int, _label: str) -> None:
            if done < total:
                folder = targets[done].folder
                name = f"{folder.parent.name} / {folder.name}" if targets[done].kind == "album" else folder.name
                now = time.perf_counter()
                marks.append(now)
                durations = [b - a for a, b in zip(marks, marks[1:])]
                progress.set_value(done)
                label = f"Looking up {name} ({done + 1} of {total})…" + progress_suffix(done, total, now - started, durations)
                current[0] = label
                progress.set_label(label)

        # 'MusicBrainz is busy, waiting 4 s' in the progress label: never wait silently (the label is set through the
        # progress object, which marshals to the GUI thread)
        engine.on_status = lambda text: progress.set_label(f"{text}…  {current[0]}")

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
    notes = [kinds_sentence(kinds)]
    if fetchers.discogs is None:
        notes.append(DISCOGS_NOTE)
    if cancelled:
        notes.append(f"Cancelled: {len(built)} of {len(targets)} folders were looked up; the rest are not in this list.")
    if failure:
        notes.append(f"The lookup stopped early: {failure[0]}")
    if found.skipped:
        notes.append(f"{len(found.skipped)} folder(s) already have a note and are not listed.")
    if found.groupings:
        notes.append(f"{len(found.groupings)} alphabetical grouping folder(s) (A, B-C, 0-9...) skipped: no note, no proposal.")
    if include_outdated:
        updates = sum(1 for t in found.targets if t.update is not None)
        notes.append(f"Notes made by an older version: {updates} can be updated (never pre-ticked). " + left_alone_sentence(found))
    unreachable = [p for p in session.problems() if p.lower().startswith("could not reach")]
    if unreachable:
        notes.append(f"The internet services could not be reached: {unreachable[0]}")
    lookup_note = lookup_sentence(len(built), time.perf_counter() - started, engine.stats.requests,
                                  engine.stats.from_cache)
    notes.append(lookup_note)
    time_line = engine.phases.time_line()
    log.debug("%s | %s", lookup_note, engine.phases.detail())
    from core.history import history_enabled

    dialog = NotesReviewDialog(
        session, choose_view(targets), _rebuild_with(engine), engine.can_choose,
        banner="  ".join(notes) + (f"\n{time_line}" if time_line else ""), parent=parent, size=parse_size(size_text),
        on_size=(lambda w, h: on_size(f"{w}x{h}")) if on_size else None, open_path=open_path,
        history_note="Recorded in Tools > History (it is switched on)." if history_enabled() else "",
        best_guess=engine.best_guess, why_not=engine.why_cannot_choose, offer_albums=kinds == (KIND_ARTIST,),
        lookup_note=lookup_note)
    dialog.exec()
    if dialog.followup:  # 'Create the album notes for these artists too': only those artists' albums
        return create_notes(parent, dialog.followup, True, make_fetchers, size_text, on_size, open_path,
                            kinds=(KIND_ALBUM,), cache=cache, include_outdated=include_outdated,
                            discogs_mode=discogs_mode, singles=singles)
    return dialog


def _rebuild_with(engine: NotesEngine):
    def rebuild(proposal: Proposal, candidate: Optional[Candidate], level: str, genres: Optional[list] = None,
                link: Optional[str] = None) -> Proposal:
        return engine.rebuild(proposal, candidate=candidate, level=level, genres=genres, link=link)

    return rebuild


_running_counts: set = set()  # count threads still running when their dialog is gone: kept alive until they end


class _CountThread(QThread):
    """Counts the folders a scope would look up, off the GUI thread (a walk of a big library takes a moment)."""

    counted = pyqtSignal(object, object)  # (folder, include_subfolders), (artists, albums, genres)

    def __init__(self, folder: str, include_subfolders: bool):
        super().__init__()
        self.key = (folder, include_subfolders)

    def run(self) -> None:
        try:
            counts = count_targets(*self.key)
        except Exception:  # noqa: BLE001 -- only a hint: no counts, no warning
            counts = None
        self.counted.emit(self.key, counts)


class _DescribeThread(QThread):
    """What each folder of the question would get (core/notes_scope.py folder_info), off the GUI thread."""

    described = pyqtSignal(object)  # {path: info}

    def __init__(self, paths: list[str]):
        super().__init__()
        self.paths = list(paths)

    def run(self) -> None:
        infos = {}
        for path in self.paths:
            try:
                infos[path] = folder_info(path)
            except Exception:  # noqa: BLE001 -- only a caption
                pass
        self.described.emit(infos)


class NotesScopeDialog(QDialog):
    """The first step of Tools > Create Notes: which folder? A short list of radio buttons (the folder you opened,
    the artist / album of the selected files, one level up, the last folder, the library folder) and "Another
    folder...". The first useful one is ticked, so Enter just works. The selected files are only ever one of the
    choices (and the ticked one when there is a selection), never used without Continue.

    Include subfolders mirrors File > Include Subfolders: `on_include_changed(bool)` is called at every tick, the
    caller passes the shared action's setChecked, so there is still exactly one saved value."""

    MAX_PATH_CHARS = 70

    KIND_CHECKS = (
        ("genre", "Genre / collection notes (fast: about one Wikipedia lookup per folder, no tags read)"),
        ("artist", "Artist notes (about 5 requests each)"),
        ("album", "Album notes (slower: about 4 requests each)"),
    )

    def __init__(self, parent: QWidget, choices: list[ScopeChoice], include_subfolders: bool,
                 on_include_changed: Optional[Callable[[bool], None]] = None, start: str = "", kinds: str = "artist"):
        super().__init__(parent)
        self.outdated = False
        self.setWindowTitle("Create Notes: which folder?")
        self.start = start
        self._on_include_changed = on_include_changed
        self.folder = ""
        self.kinds = kinds
        self.picked = ""
        self._previous: Optional[QRadioButton] = None
        self._browsing = False
        self.radios: list[tuple[QRadioButton, str]] = []
        self.captions: list[QLabel] = []

        title = QLabel("Create notes: which folder?")
        font = title.font()
        font.setBold(True)
        title.setFont(font)
        layout = QVBoxLayout(self)
        layout.addWidget(title)
        self.group = QButtonGroup(self)
        for choice in choices:
            radio = QRadioButton(self._elide(choice.path))
            radio.setToolTip(choice.path)
            caption = QLabel(choice.caption[:1].upper() + choice.caption[1:])
            caption.setToolTip(choice.path)
            caption.setContentsMargins(22, 0, 0, 6)
            _shrinkable(caption)
            self.group.addButton(radio)
            self.radios.append((radio, choice.path))
            self.captions.append(caption)
            layout.addWidget(radio)
            layout.addWidget(caption)
            if choice.default:
                radio.setChecked(True)
        # Another folder...
        self.other_radio = QRadioButton("Another folder…")
        self.group.addButton(self.other_radio)
        self.browse_button = QPushButton("Browse…")
        self.browse_button.setAutoDefault(False)
        row = QHBoxLayout()
        row.addWidget(self.other_radio)
        row.addWidget(self.browse_button)
        row.addStretch(1)
        layout.addLayout(row)
        self.other_label = QLabel("")
        self.other_label.setContentsMargins(22, 0, 0, 6)
        _shrinkable(self.other_label)
        layout.addWidget(self.other_label)
        if not choices:
            self.other_radio.setChecked(True)
        self._previous = self.group.checkedButton()

        # What to create: artist notes are the big get and most of the lookups are albums
        layout.addSpacing(6)
        what = QLabel("What to create")
        what_font = what.font()
        what_font.setBold(True)
        what.setFont(what_font)
        layout.addWidget(what)
        self.kind_boxes: dict[str, QCheckBox] = {}
        wanted = parse_kinds(kinds)
        for key, text in self.KIND_CHECKS:
            box = QCheckBox(text)
            box.setChecked(key in wanted)
            box.toggled.connect(lambda *_: (self._update_hint(), self._update_continue()))
            self.kind_boxes[key] = box
            layout.addWidget(box)

        # Notes made by an older version of this app: never on by itself, never remembered
        layout.addSpacing(6)
        self.hint_label = QLabel()  # a one-line time warning for a big scope with album notes: only a hint
        self.hint_label.setWordWrap(True)
        self.hint_label.setStyleSheet("background:#fff6d6; color:#4d3600; border:1px solid #e6cf85; padding:3px 8px;")
        self.hint_label.setVisible(False)
        layout.insertWidget(layout.indexOf(self.kind_boxes["album"]) + 1, self.hint_label)
        self.outdated_box = QCheckBox(OUTDATED_CHECK_TEXT)
        self.outdated_box.setChecked(False)
        self.outdated_box.setToolTip("Lists the notes this app wrote in an older shape that nobody edited, and the notes "
                                     "of the first versions (no stamp: it cannot be known whether you edited them), "
                                     "as updates you can compare and replace one by one. A note you edited, or one "
                                     "this app did not write, is never listed.")
        layout.addWidget(self.outdated_box)
        self.include_box = QCheckBox("Include subfolders")
        self.include_box.setChecked(include_subfolders)
        self.include_note = QLabel()
        self.include_note.setContentsMargins(22, 0, 0, 0)
        _shrinkable(self.include_note)
        layout.addSpacing(6)
        layout.addWidget(self.include_box)
        layout.addWidget(self.include_note)
        self._update_include_note()

        self.continue_button = QPushButton("Continue")
        self.continue_button.setDefault(True)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setAutoDefault(False)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.continue_button)
        layout.addSpacing(6)
        layout.addLayout(buttons)

        self._base_captions = [label.text() for label in self.captions]
        self.infos: dict[str, tuple] = {}  # path -> folder_info: what each folder would get
        self.kind_boxes_connect = [box.toggled.connect(lambda *_: self._update_captions()) for box in self.kind_boxes.values()]
        self._describe([path for _radio, path in self.radios])
        self.counts: Optional[tuple[int, int, int]] = None  # (artists, albums, genres) of the chosen folder, once known
        self._count_key: Optional[tuple] = None
        self._count_threads: list = []
        self._count_timer = QTimer(self)
        self._count_timer.setSingleShot(True)
        self._count_timer.timeout.connect(self._start_count)
        self.group.buttonToggled.connect(lambda *_: self._count_timer.start(150))
        self.include_box.toggled.connect(lambda *_: self._count_timer.start(150))
        self._count_timer.start(0)
        self.group.buttonToggled.connect(self._radio_toggled)
        self.browse_button.clicked.connect(self._browse)
        self.include_box.toggled.connect(self._include_toggled)
        self.continue_button.clicked.connect(self._continue)
        self.cancel_button.clicked.connect(self.reject)
        self.setMinimumWidth(560)
        self._update_continue()
        if not choices:
            QTimer.singleShot(0, self._browse)

    def _elide(self, path: str) -> str:
        if len(path) <= self.MAX_PATH_CHARS:
            return path
        head = self.MAX_PATH_CHARS // 3
        return path[:head] + "…" + path[-(self.MAX_PATH_CHARS - head - 1):]

    def chosen_folder(self) -> str:
        """The folder the ticked radio stands for ("" when 'Another folder...' has none yet)."""
        button = self.group.checkedButton()
        if button is self.other_radio:
            return self.picked
        for radio, path in self.radios:
            if radio is button:
                return path
        return ""

    def _describe(self, paths: list[str]) -> None:
        thread = _DescribeThread([p for p in paths if p])
        thread.described.connect(self._described)
        thread.finished.connect(lambda t=thread: (_running_counts.discard(t), t.deleteLater()))
        _running_counts.add(thread)
        thread.start()

    def _described(self, infos) -> None:
        self.infos.update(infos)
        self._update_captions()

    def _update_captions(self) -> None:
        """Under each folder what it would get ('would get a genre note: Ska'), following the ticked kinds."""
        kinds = self.chosen_kinds() or "artist"
        for (radio, path), label, base in zip(self.radios, self.captions, self._base_captions):
            info = self.infos.get(path)
            if info is not None:
                text = folder_caption(info, Path(path).name, kinds)
                label.setText(f"{base[:1].upper() + base[1:]}  -  {text}" if base else text)
        if self.picked and self.picked in self.infos:
            self.other_label.setText(f"{self.picked}  -  {folder_caption(self.infos[self.picked], Path(self.picked).name, kinds)}")

    def _start_count(self) -> None:
        folder = self.chosen_folder()
        if not folder:
            return
        key = (folder, self.include_box.isChecked())
        if key == self._count_key and self.counts is not None:
            return
        self._count_key = key
        self.counts = None
        self._update_hint()
        thread = _CountThread(*key)
        thread.counted.connect(self._counted)
        thread.finished.connect(lambda t=thread: (_running_counts.discard(t), t.deleteLater()))
        _running_counts.add(thread)
        thread.start()

    def _counted(self, key, counts) -> None:
        if key != self._count_key:  # an answer for a folder that is no longer the chosen one
            return
        self.counts = counts
        self._update_hint()

    def _update_hint(self) -> None:
        text = scope_hint(*self.counts, self.chosen_kinds()) if self.counts and self.chosen_kinds() else ""
        self.hint_label.setText(text)
        self.hint_label.setVisible(bool(text))

    def chosen_kinds(self) -> str:
        """The ticked kinds as a comma list ('artist,genre'); '' when nothing is ticked."""
        ticked = [key for key, box in self.kind_boxes.items() if box.isChecked()]
        return kinds_text(ticked) if ticked else ""

    def _update_continue(self) -> None:
        self.continue_button.setEnabled(bool(self.chosen_folder()) and bool(self.chosen_kinds()))

    def _update_include_note(self) -> None:
        self.include_note.setText("Everything below the folder is looked at."
                                  if self.include_box.isChecked() else "Only this folder is looked at.")

    def _include_toggled(self, on: bool) -> None:
        self._update_include_note()
        if self._on_include_changed is not None:
            self._on_include_changed(bool(on))

    def _radio_toggled(self, button, checked: bool) -> None:
        if not checked:
            return
        if self._browsing:
            return
        if button is self.other_radio:
            if not self._browse():  # cancelled: back to where it was (or stay empty when nothing came before)
                if self._previous is not None and self._previous is not self.other_radio:
                    self._previous.setChecked(True)
                    return
        else:
            self._previous = button
        self._update_continue()

    def _browse(self) -> bool:
        self._browsing = True
        try:
            folder = ask_folder(self, self.picked or self.start)
        finally:
            self._browsing = False
        if not folder:
            self._update_continue()
            return False
        self.picked = folder
        if folder not in self.infos:
            self._describe([folder])
        self.other_label.setText(folder)
        self.other_label.setToolTip(folder)
        self._browsing = True
        self.other_radio.setChecked(True)
        self._browsing = False
        self._previous = self.other_radio
        self._update_continue()
        return True

    def _continue(self) -> None:
        self.folder = self.chosen_folder()
        self.kinds = self.chosen_kinds()
        self.outdated = self.outdated_box.isChecked()
        if self.folder:
            self.accept()


def ask_scope(parent: QWidget, choices: list[ScopeChoice], include_subfolders: bool,
              on_include_changed: Optional[Callable[[bool], None]] = None, start: str = "",
              kinds: str = "artist") -> tuple[str, str, bool]:
    """(folder, kinds, include_outdated): the folder to create notes for ("" = cancelled), what to create ('artist'
    / 'both' / 'all') and whether to also review notes made by an older version (always off at first)."""
    dialog = NotesScopeDialog(parent, choices, include_subfolders, on_include_changed, start, kinds)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return "", kinds, False
    return dialog.folder, dialog.kinds, dialog.outdated


def ask_folder(parent: QWidget, start: str) -> str:
    """The folder dialog of Create Notes without a selection (a library root, an artist or an album folder)."""
    return QFileDialog.getExistingDirectory(
        parent, "Create Notes: choose a library, artist or album folder", start)
