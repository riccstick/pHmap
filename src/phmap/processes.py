"""Keep desktop child processes hidden and cancel their whole process tree."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from contextlib import suppress
from typing import Any


def hidden_process_options() -> dict[str, Any]:
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


def terminate_tree(process: subprocess.Popen[bytes], *, force: bool = False) -> None:
    if sys.platform == "win32":
        # Terminating only Python leaves APBS/PyMOL running on Windows. taskkill
        # targets this exact owned PID and its descendants, not executable names.
        if process.poll() is None:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                **hidden_process_options(),
            )
    else:
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL if force else signal.SIGTERM)
