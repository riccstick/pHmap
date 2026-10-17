"""Shared atomic JSON persistence for manifests and local queue records."""

from __future__ import annotations

import json
import tempfile
from decimal import Decimal
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
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
