"""
Entry point. Startup (crash logging, taskbar icon ID, theme, message-box
width) is redactor_common.gui.app_bootstrap.run_app(), shared with the
other Redactor apps. Crash logging is installed before MainWindow (and
the rest of the GUI) is even imported, so a failure there is logged too.
The update framework's flags (--check-update, --update) are answered
first, with no window; a first argument that is a command name runs the
command line instead (mp3cli); anything else starts the window.
"""

import sys

from core import crash_log
from core.app_paths import asset_path
from core.version import APP_NAME, APP_REPO_URL, APP_VERSION
from mp3cli import COMMANDS, cli_requested
from redactor_common.gui.app_bootstrap import run_app


def update_command(args) -> bool:
    """Whether the family's update command line (redactor_common.core.update_cli) answers these arguments: --check-update
    and --update, and --version when it is asked for a --result-file (the release gate reads the bare build number from
    there; the windowed exe has no console). A plain `mp3redactor --version` stays the command line's, and so does
    every argument after a command name: `notes PATH --update` is the notes command's own flag (refresh old notes)."""
    if args and args[0] in COMMANDS:
        return False
    return "--check-update" in args or "--update" in args or ("--version" in args and "--result-file" in args)


def _window():
    from gui.main_window import MainWindow, open_paths_at_start

    window = MainWindow()
    # `mp3redactor.exe D:\Music` (no command name): open that folder or file, like File > Open Folder / Files.
    open_paths_at_start(window, sys.argv[1:])
    return window


def main() -> int:
    if update_command(sys.argv[1:]):
        # The update framework (redactor_common docs/update-framework.md): what this copy would do with a release, or do it.
        from redactor_common.core import update_cli

        return update_cli.handle(sys.argv[1:], APP_NAME, APP_VERSION, APP_REPO_URL)
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
