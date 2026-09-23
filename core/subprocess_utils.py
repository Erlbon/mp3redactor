"""
Thin re-export of redactor_common.core.subprocess_utils (2026-09-23) --
the no-console-window flag this module originally held is now shared
with epub and video, alongside run_tool() (no window, stdin=DEVNULL,
UTF-8 output decoding, timeout). Kept so existing imports still work.
"""

import sys  # noqa: F401 -- tests patch sys.platform through this module

from redactor_common.core.subprocess_utils import no_window_kwargs, run_tool  # noqa: F401
