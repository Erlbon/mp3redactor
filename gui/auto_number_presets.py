"""
gui/auto_number_presets.py

Edit > Auto-Number: redactor_common's shared AutoNumberingDialog plus a
"Preset" choice that fills in its fields. The one preset replaces the old
Metadata > Number Tracks item (removed 2026-10-14): Track, start at 1, +1
per file, no zero-padding, in the files' current table order -- exactly how
Number Tracks numbered them. "Custom" leaves the fields alone.

Choosing a preset does not overwrite the zero-pad width the app remembers
for Auto-Number (only a change the user makes by hand is remembered).
"""

from __future__ import annotations

from PyQt6.QtWidgets import QComboBox, QHBoxLayout, QLabel

from redactor_common.gui.auto_numbering_dialog import AutoNumberingDialog as _SharedAutoNumberingDialog

PRESET_CUSTOM = "Custom"
PRESET_NUMBER_TRACKS = "Number Tracks (Track 1..N in table order)"

# preset name -> (field key, start, increment, zero-pad width)
PRESETS: dict[str, tuple[str, int, int, int]] = {
    PRESET_NUMBER_TRACKS: ("track", 1, 1, 0),
}


class AutoNumberingDialog(_SharedAutoNumberingDialog):
    def __init__(self, *args, preset: str | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        row = QHBoxLayout()
        row.addWidget(QLabel("Preset:"))
        self.preset_combo = QComboBox()
        self.preset_combo.addItem(PRESET_CUSTOM)
        for name in PRESETS:
            self.preset_combo.addItem(name)
        row.addWidget(self.preset_combo, 1)
        self.layout().insertLayout(1, row)  # under the intro line, above Field/Start
        self.preset_combo.currentTextChanged.connect(self.apply_preset)
        if preset:
            self.preset_combo.setCurrentText(preset)

    def apply_preset(self, name: str) -> None:
        """Fills Field, Start at, Increment and Zero-pad to from preset `name`."""
        values = PRESETS.get(name)
        if values is None:
            return
        field_key, start, increment, padding = values
        index = self.field_combo.findData(field_key)
        if index < 0:
            return
        remember = self._on_padding_changed
        self._on_padding_changed = None
        try:
            self.field_combo.setCurrentIndex(index)
            self.start_spin.setValue(start)
            self.increment_spin.setValue(increment)
            self.padding_spin.setValue(padding)
        finally:
            self._on_padding_changed = remember
        self._refresh_preview()
