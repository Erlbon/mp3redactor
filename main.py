"""
Entry point. Startup (crash logging, taskbar icon ID, theme, message-box
width) is redactor_common.gui.app_bootstrap.run_app(), shared with the
other Redactor apps. Crash logging is installed before MainWindow (and
the rest of the GUI) is even imported, so a failure there is logged too.
"""

import sys

from core import crash_log
from core.app_paths import asset_path
from core.version import APP_NAME
from mp3cli import cli_requested
from redactor_common.gui.app_bootstrap import run_app


def _window():
    from gui.main_window import MainWindow

    return MainWindow()


def main() -> int:
    if cli_requested(sys.argv):
        # One exe: `mp3redactor info ...` is the command line (no window). See mp3cli/main.py.
        from mp3cli.main import main as cli_main
        from redactor_common.cli import run

        return run(cli_main, sys.argv[1:])
    return run_app(
        app_name=APP_NAME,
        window_factory=_window,
        crash_log_path=crash_log.log_path(),
        app_user_model_id="Erlbon.Mp3Redactor.GUI.1",
        icon_path=asset_path("assets/icon.ico"),
    )


if __name__ == "__main__":
    sys.exit(main())
