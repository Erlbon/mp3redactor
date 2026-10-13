"""
The documentation matches the app (2026-10-13 tidy-up): README.md's menu
table names every real menu item, Help > About shows ABOUT.md and the exe
bundles it, and the README no longer calls shipped features unbuilt.
"""

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import gui.main_window as mw  # noqa: E402
from tests.test_cover_gui import _app, window  # noqa: E402,F401

ROOT = Path(__file__).parent.parent


def _section(text: str, heading: str) -> str:
    start = text.index(heading)
    end = text.find("\n## ", start + len(heading))
    return text[start:end if end != -1 else None]


def _menu_items(menu, path=()):
    for action in menu.actions():
        if action.isSeparator():
            continue
        label = action.text().replace("&", "")
        if action.menu() is not None:
            yield from _menu_items(action.menu(), path + (label,))
        else:
            yield path, label, [k.toString() for k in action.shortcuts()]


def test_the_readme_menu_table_names_every_menu_item_and_shortcut(window):
    section = _section((ROOT / "README.md").read_text(encoding="utf-8"), "## Menus and shortcuts")
    missing = []
    for top in window.menuBar().actions():
        for path, label, keys in _menu_items(top.menu(), (top.text().replace("&", ""),)):
            if label.startswith("Apply to "):
                label = "Apply to N Selected"  # the count changes with the selection
            if label.startswith("Undo") or label.startswith("Redo"):
                label = label.split()[0]
            if label not in section:
                missing.append(" > ".join(path + (label,)))
            missing += [f"{label} [{key}]" for key in keys if key not in section]
    assert missing == [], f"README.md's Menus and shortcuts section does not mention: {missing}"


def test_the_readme_has_no_old_menu_names():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    for old in ("Operations menu", "Import menu", "Settings > Locate", "Settings > Add/Remove", "(Import menu, `F3`)"):
        assert old not in text, old
    assert "not yet built" not in text


def test_help_about_shows_about_md_and_the_exe_bundles_it():
    assert mw.ABOUT_FILE == "ABOUT.md" and (ROOT / "ABOUT.md").is_file()
    assert '("ABOUT.md", ".")' in (ROOT / "mp3redactor.spec").read_text(encoding="utf-8")


def test_about_md_documents_redact_subfolders_m4b_and_the_convert_offer():
    about = (ROOT / "ABOUT.md").read_text(encoding="utf-8")
    for topic in ("Redact", "Include Subfolders", "M4B", "no MP3 at all", "Find Duplicates", "dropping files"):
        assert topic in about, topic
