"""Shared atomic JSON persistence for manifests and local queue records."""

from __future__ import annotations

import json
import tempfile
import time
from collections.abc import Callable
from contextlib import suppress
from decimal import Decimal
from functools import partial
from pathlib import Path
from typing import Any


def read_json(path: Path) -> dict[str, Any]:
    """Tolerate missing or damaged historical state without hiding valid objects."""

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _json_default(value: object) -> str:
    if isinstance(value, (Path, Decimal)):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _retry_windows_file_operation(operation: Callable[[], object]) -> None:
    """Allow brief Windows file locks to clear, with 0.63 seconds of total backoff."""

    for delay in (0.01, 0.02, 0.04, 0.08, 0.16, 0.32, None):
        try:
            operation()
            return
        except PermissionError as error:
            # Access denied, sharing violation, or lock violation. Other errors
            # (including POSIX permission failures) should surface immediately.
            if delay is None or getattr(error, "winerror", None) not in (5, 32, 33):
                raise
            time.sleep(delay)


def write_json(path: Path, data: dict[str, Any], *, sort_keys: bool = False) -> None:
    """Replace state atomically, with a separate temporary file for each writer."""

    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(data, handle, indent=2, sort_keys=sort_keys, default=_json_default)
            handle.write("\n")
        _retry_windows_file_operation(partial(temporary.replace, path))
        temporary = None  # A successful rename leaves no temporary file to delete.
    finally:
        if temporary is not None:
            # Cleanup must not mask the original write/replace failure.
            with suppress(OSError):
                _retry_windows_file_operation(partial(temporary.unlink, missing_ok=True))
