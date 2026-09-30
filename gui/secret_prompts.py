"""
gui/secret_prompts.py

Saving a key into redactor_common's secret store, and -- when this
machine has no secure store -- asking the user once whether an UNENCRYPTED
file may be used instead (same wording and flow as cbzredactor's). A yes is
remembered through the `remember` callback (the main window keeps it in
Settings.allow_unencrypted_fallback and applies it at startup).
"""

from __future__ import annotations

from typing import Callable

from PyQt6.QtWidgets import QMessageBox

from redactor_common.core import secret_store


def ask_allow_unencrypted_fallback(parent) -> bool:
    answer = QMessageBox.question(
        parent,
        "No Secure Storage Available",
        "This computer has no usable secure credential store (such as Windows "
        "Credential Manager), so the key or token can't be stored safely.\n\n"
        "It can instead be saved in a plain, UNENCRYPTED file in your user "
        "profile folder. Anyone who can read your files could read it.\n\n"
        "Save it in an unencrypted file, and remember that choice?",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    return answer == QMessageBox.StandardButton.Yes


def save_with_fallback_prompt(
    parent, save: Callable[[bool | None], object], remember: Callable[[], None],
) -> bool:
    """Call save(None); on SecretStoreUnavailable ask the user and, if they
    agree, remember it (`remember()`, plus the process-wide opt-in) and call
    save(True). True when the save happened."""
    try:
        save(None)
        return True
    except secret_store.SecretStoreUnavailable:
        pass
    if not ask_allow_unencrypted_fallback(parent):
        return False
    remember()
    secret_store.set_allow_unencrypted_fallback(True)
    try:
        save(True)
    except secret_store.SecretStoreUnavailable as exc:  # e.g. the fallback file can't be written
        QMessageBox.warning(parent, "Couldn't Save", str(exc))
        return False
    return True
