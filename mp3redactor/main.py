"""
Entry point. Crash logging is installed before anything else -- including
the Qt import -- so a failure anywhere downstream, Qt itself included,
still gets logged.
"""

import sys

from core import crash_log

crash_log.install()

from PyQt6.QtGui import QIcon  # noqa: E402 -- must follow crash_log.install()
from PyQt6.QtWidgets import QApplication  # noqa: E402 -- must follow crash_log.install()

from core.app_paths import asset_path  # noqa: E402
from gui.main_window import MainWindow  # noqa: E402
from redactor_common.gui.qmessagebox_style import apply_message_box_style  # noqa: E402


def main() -> int:
    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon(str(asset_path("assets/icon.ico"))))
    apply_message_box_style(app)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
