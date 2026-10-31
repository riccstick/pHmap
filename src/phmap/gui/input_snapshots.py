"""Remove only verified, superseded GUI input snapshots, never user directories."""

from __future__ import annotations

import re
import shutil
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from functools import partial
from pathlib import Path
from typing import Any

from phmap.errors import ConfigurationError
from phmap.json_state import _retry_windows_file_operation, read_json
from phmap.manifest import sha256_file
from phmap.models import PyMOLView


def retire_input_snapshot(run_id: str, record: dict[str, Any]) -> dict[str, Any]:
    """Keep a small cleanup ticket until the old bytes have been removed."""
    return {
        "input_folder": record.get("input_folder", run_id),
        "inputs": record["inputs"],
        "view": record.get("configuration", {}).get("view"),
    }


def _snapshot_directory(uploads: Path, run_id: str, folder: Any) -> Path:
    if not isinstance(folder, str) or not (
        folder == run_id
        or re.fullmatch(re.escape(run_id) + r"-resume-\d{8}T\d{6}Z-[0-9a-f]{6}", folder)
    ):
        raise ConfigurationError("Unrecognized superseded input folder; left untouched")
    directory = uploads / folder
    if (
        uploads.is_symlink() or uploads.parent.is_symlink() or directory.is_symlink()
        or directory.resolve().parent != uploads
    ):
        raise ConfigurationError("Input snapshot cleanup must not follow symbolic links")
    return directory


@contextmanager
def stage_input_snapshot(
    uploads: Path, run_id: str, folder: str, record_path: Path, previous_digest: str,
) -> Iterator[Path]:
    """Discard failed staging only when its replacement record was never published."""
    directory = _snapshot_directory(uploads, run_id, folder)
    directory.mkdir()
    try:
        yield directory
    except BaseException:
        # This directory was created by this call. Never remove it if the durable
        # record changed, or if publication cannot be checked safely.
        with suppress(OSError, ConfigurationError):
            if sha256_file(record_path) == previous_digest:
                _snapshot_directory(uploads, run_id, folder)
                _retry_windows_file_operation(partial(shutil.rmtree, directory))
        raise


def remove_input_snapshot(uploads: Path, run_id: str, snapshot: dict[str, Any]) -> None:
    directory = _snapshot_directory(uploads, run_id, snapshot.get("input_folder"))
    if not directory.exists():
        return  # Cleanup completed before a crash could remove its ticket.
    if not directory.is_dir():
        raise ConfigurationError("Superseded input snapshot is not a directory")

    files: dict[str, str] = {}
    sources: dict[str, dict[str, Any]] = {}
    for item in snapshot["inputs"]:
        path = Path(item["staged_path"])
        digest = item["sha256"]
        if path.parent != directory or not isinstance(digest, str):
            raise ConfigurationError("Superseded inputs do not belong to their snapshot")
        files[path.name] = digest
        if item.get("source"):
            sources[path.name + ".source.json"] = item["source"]
    if not files:
        raise ConfigurationError("No recorded inputs identify the superseded snapshot")
    view = snapshot.get("view")
    expected = files.keys() | sources.keys() | ({"view.txt"} if view is not None else set())
    entries = list(directory.iterdir())
    for path in entries:
        if path.name not in expected or path.is_symlink() or not path.is_file():
            raise ConfigurationError("Snapshot contains additional user files; left untouched")
        if path.name in files and sha256_file(path) != files[path.name]:
            raise ConfigurationError("Superseded input was modified; left untouched")
        if path.name in sources and read_json(path) != sources[path.name]:
            raise ConfigurationError("Input provenance was modified; left untouched")
        if path.name == "view.txt" and view is not None:
            text = ", ".join(repr(value) for value in PyMOLView(view).values)
            if path.read_text(encoding="utf-8") != text:
                raise ConfigurationError("Saved camera was modified; left untouched")
    _retry_windows_file_operation(partial(shutil.rmtree, directory))
