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
from redactor_common.gui.app_bootstrap import run_app


def _window():
    from gui.main_window import MainWindow

    return MainWindow()


def main() -> int:
    return run_app(
        app_name=APP_NAME,
        window_factory=_window,
        crash_log_path=crash_log.log_path(),
        app_user_model_id="Erlbon.Mp3Redactor.GUI.1",
        icon_path=asset_path("assets/icon.ico"),
    )


if __name__ == "__main__":
    sys.exit(main())
