"""
Bumps core/version.py's APP_VERSION. Run this once per delivery, before
building -- do not hand-edit APP_VERSION.

Same date/counter logic as the epub tool's bump_version.py: if today's
date already appears in APP_VERSION, increments the #NN counter; if the
date has changed (including after an idle day), resets the counter to
#01 for the new date rather than continuing to increment.
"""

import re
from datetime import date
from pathlib import Path

VERSION_FILE = Path(__file__).parent / "core" / "version.py"
VERSION_PATTERN = re.compile(r'APP_VERSION = "(\d{4}-\d{2}-\d{2})#(\d+)"')


def bump() -> str:
    text = VERSION_FILE.read_text(encoding="utf-8")
    match = VERSION_PATTERN.search(text)
    if not match:
        raise SystemExit(f"Could not find APP_VERSION in {VERSION_FILE}")

    old_date, old_counter = match.group(1), int(match.group(2))
    today = date.today().isoformat()

    if old_date == today:
        new_counter = old_counter + 1
    else:
        new_counter = 1

    new_version = f'APP_VERSION = "{today}#{new_counter:02d}"'
    new_text = VERSION_PATTERN.sub(new_version, text, count=1)
    VERSION_FILE.write_text(new_text, encoding="utf-8")
    return f"{today}#{new_counter:02d}"


if __name__ == "__main__":
    print(f"Version bumped to {bump()}")
