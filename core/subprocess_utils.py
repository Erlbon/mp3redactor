"""
Thin re-export of redactor_common.core.subprocess_utils (2026-09-23) --
the no-console-window flag this module originally held is now shared
with epub and video, alongside run_tool() (no window, stdin=DEVNULL,
UTF-8 output decoding, timeout). Kept so existing imports still work.

StoppableProcesses (moved here from core/m4b_builder.py, 2026-10-13, so
the import conversion can use it too): run_tool() whose process a Cancel
kills at once instead of waiting for a long ffmpeg run to finish.
"""

import subprocess
import sys  # noqa: F401 -- tests patch sys.platform through this module
import threading
import time
from typing import Callable

from redactor_common.core.subprocess_utils import decode_output, no_window_kwargs, popen_tool, run_tool  # noqa: F401


class StoppableProcesses:
    """The external processes of one job. A Cancel stops them at once instead of waiting for a long encode or
    join to finish; workers poll `stopped`, the calling thread may also pass its own should_cancel."""

    def __init__(self) -> None:
        self.stopped = threading.Event()
        self._lock = threading.Lock()
        self._processes: set[subprocess.Popen] = set()

    def stop(self) -> None:
        self.stopped.set()
        with self._lock:
            processes = list(self._processes)
        for process in processes:
            try:
                process.kill()
            except OSError:
                pass

    def run(self, command: list, timeout: float, cancelled: Callable[[], bool] | None = None) -> subprocess.CompletedProcess:
        """run_tool() that can be stopped: the same flags (no window, stdin closed, UTF-8 output), polled every
        quarter second. Raises subprocess.TimeoutExpired like run_tool; a stopped process returns a non-zero code."""
        process = popen_tool([str(a) for a in command], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        with self._lock:
            self._processes.add(process)
        deadline = time.monotonic() + timeout
        try:
            while True:
                try:
                    out, err = process.communicate(timeout=0.25)
                    break
                except subprocess.TimeoutExpired:
                    if self.stopped.is_set() or (cancelled is not None and cancelled()):
                        self.stop()
                        out, err = process.communicate()
                        break
                    if time.monotonic() > deadline:
                        process.kill()
                        process.communicate()
                        raise subprocess.TimeoutExpired(command, timeout) from None
        finally:
            with self._lock:
                self._processes.discard(process)
        return subprocess.CompletedProcess(command, process.returncode, decode_output(out), decode_output(err))
