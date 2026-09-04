"""
Suppresses the console window Windows can pop up for a shelled-out
console program (mp3val.exe, and later keyfinder-cli.exe), even when
the calling app itself is built --windowed. Same class of bug the epub
tool hit and fixed for its Calibre CLI calls (v35): a --windowed
PyInstaller build has no console of its own, but the CHILD process
(mp3val.exe etc.) still tries to allocate one when launched, and without
suppressing that, a console window can briefly flash or steal focus.

subprocess.CREATE_NO_WINDOW only exists on Windows -- referencing it
unconditionally would raise AttributeError on Linux/macOS, so this is
gated on sys.platform, and returns an empty kwargs dict everywhere else
(a no-op on platforms where the problem doesn't exist).
"""

import subprocess
import sys


def no_window_kwargs() -> dict:
    """
    Extra kwargs to splat into subprocess.run()/Popen() so a launched
    console program doesn't pop up (or briefly flash) its own window.
    Empty dict on non-Windows platforms.
    """
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}
