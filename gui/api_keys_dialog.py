"""
gui/api_keys_dialog.py

Tools > API Keys...: every online-source credential in one dialog, the
pattern the other Redactor apps use. Today that is the Discogs token (Look
Up via Discogs, and the Redact step of the same name). The token lives in
redactor_common's secret store (the OS credential store, or an opt-in
UNENCRYPTED file) and nowhere else -- never in the settings file.

Semantics (redactor_common's SecretField): an empty box keeps the stored
token, typing replaces it, Remove deletes it; nothing is written until
Save. The box never shows the stored value.
"""

from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QVBoxLayout,
)

from core.discogs_lookup import SECRET_APP, SECRET_NAME, TOKEN_ENV_VAR, TOKEN_HELP_URL
from gui.secret_prompts import save_with_fallback_prompt
from redactor_common.gui.secret_field import SecretField


class ApiKeysDialog(QDialog):
    def __init__(self, parent=None, remember_fallback: Callable[[], None] = lambda: None):
        """`remember_fallback` is called when the user agrees to an
        unencrypted file (the caller records that in its settings)."""
        super().__init__(parent)
        self._remember_fallback = remember_fallback
        self.setWindowTitle("API Keys")
        self.setMinimumWidth(480)
        outer = QVBoxLayout(self)

        discogs = QGroupBox("Discogs")
        layout = QVBoxLayout(discogs)
        intro = QLabel(
            "Your Discogs personal access token, for Look Up via Discogs and the Redact step of the same "
            f'name. Create one at <a href="{TOKEN_HELP_URL}">discogs.com</a> under Settings &gt; Developers '
            "(Generate new token). Leave the box empty to keep the stored token."
        )
        intro.setWordWrap(True)
        intro.setOpenExternalLinks(True)
        intro.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(intro)
        form = QFormLayout()
        self.token_field = SecretField(SECRET_APP, SECRET_NAME, env_var=TOKEN_ENV_VAR)
        form.addRow("Token:", self.token_field)
        layout.addLayout(form)
        outer.addWidget(discogs)

        note = QLabel(
            "Tokens are kept in your computer's secure credential store, not in this app's settings file."
        )
        note.setWordWrap(True)
        note.setStyleSheet("font-size: 11px;")
        outer.addWidget(note)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    def _save(self, allow: bool | None) -> None:
        self.token_field.apply(allow)

    def accept(self) -> None:
        if not save_with_fallback_prompt(self, self._save, self._remember_fallback):
            return  # declined: stay open, the typed token is kept
        super().accept()
