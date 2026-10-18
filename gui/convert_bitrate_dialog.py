"""
The Convert to MP3 bitrate question, with a one-line hint under the choice that names the band of the chosen value by
the table's colours (core/audio_quality.convert_hint): "128 kbps is poor by the table's colours (yellow)...".
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtWidgets import QCheckBox, QComboBox, QDialog, QDialogButtonBox, QLabel, QVBoxLayout

from core.audio_quality import convert_hint


class ConvertBitrateDialog(QDialog):
    def __init__(self, parent, choices_kbps: list[int], default_kbps: int, band_settings, dry_run: bool = False) -> None:
        super().__init__(parent)
        self.setWindowTitle("Convert to MP3")
        self._choices = list(choices_kbps)
        self._band_settings = band_settings
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Bitrate:"))
        self.combo = QComboBox()
        for kbps in self._choices:
            self.combo.addItem(f"{kbps} kbps")
        if default_kbps in self._choices:
            self.combo.setCurrentIndex(self._choices.index(default_kbps))
        layout.addWidget(self.combo)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        # The shared conversion policy's "dry run first" (Preferences > Conversion): list what would be converted and what
        # would happen to each original, change nothing.
        self.dry_run = QCheckBox("Dry run: list what would be converted and what happens to the originals, change nothing")
        self.dry_run.setChecked(dry_run)
        layout.addWidget(self.dry_run)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.combo.currentIndexChanged.connect(self._update_hint)
        self._update_hint()

    def chosen_kbps(self) -> int:
        return self._choices[self.combo.currentIndex()]

    def _update_hint(self, *_args) -> None:
        self.hint.setText(convert_hint(self.chosen_kbps(), self._band_settings))


def ask_convert_bitrate(parent, choices_kbps: list[int], default_kbps: int, band_settings,
                        dry_run: bool = False, answer: Optional[dict] = None) -> Optional[int]:
    """The chosen bitrate in kbps, or None when cancelled. `dry_run` is the box's starting state; the final state is
    put into `answer["dry_run"]` when a dict is given."""
    dialog = ConvertBitrateDialog(parent, choices_kbps, default_kbps, band_settings, dry_run)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    if answer is not None:
        answer["dry_run"] = dialog.dry_run.isChecked()
    return dialog.chosen_kbps()
