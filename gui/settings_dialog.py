"""
Settings dialog. Just one preference so far (delete_backup_after_fix),
but kept as its own dialog/module rather than inlined in MainWindow so
later settings (key/cover/lyrics will likely want their own toggles)
have an obvious place to land.
"""

import dataclasses

from PyQt6.QtWidgets import QCheckBox, QDialog, QDialogButtonBox, QLabel, QVBoxLayout

from core.settings import Settings


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self._settings = settings

        self._delete_backup_checkbox = QCheckBox(
            "Delete .bak backup files automatically after a successful fix"
        )
        self._delete_backup_checkbox.setChecked(settings.delete_backup_after_fix)

        note = QLabel(
            "Off by default: mp3val keeps a .bak copy of each original file\n"
            "when fixing integrity issues. Turn this on to have it delete\n"
            "those backups once a fix completes."
        )
        note.setWordWrap(True)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(self._delete_backup_checkbox)
        layout.addWidget(note)
        layout.addWidget(buttons)

    def result_settings(self) -> Settings:
        # A copy of the settings it was opened with, changing only what this
        # dialog edits: a fresh Settings() here used to reset every other
        # preference (recipe, patterns, columns, tool paths) on OK.
        return dataclasses.replace(
            self._settings, delete_backup_after_fix=self._delete_backup_checkbox.isChecked()
        )
