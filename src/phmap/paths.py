"""Stable per-user storage shared by local interfaces and desktop releases."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def data_directory() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "pHmap"
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "pHmap"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "phmap"
