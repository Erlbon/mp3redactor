"""
Single source of truth for the app's version string.

Format matches the sibling projects (epub/video redactors): YYYY-MM-DD#NN,
bumped once per delivery by bump_version.py. Do not hand-edit APP_VERSION;
run bump_version.py instead so the counter/date logic stays consistent.
"""

APP_NAME = "The \u026fP3 Redactor"  # turned-m (\u026f = LATIN SMALL LETTER TURNED M) standing in for the M in MP3
APP_VERSION = "2026-09-10#04"
RELEASE_LABEL = ""  # optional tagline shown in About dialog; empty until requested
APP_REPO_URL = "https://github.com/erlbon/mp3redactor"
