"""Run provenance and task status recording."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any


def utc_now() -> str:
    """Return an ISO-8601 UTC timestamp."""

    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    """Hash a file without loading it all into memory."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json_default(value: object) -> str:
    if isinstance(value, (Path, Decimal)):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


@dataclass(slots=True)
class RunManifest:
    """Mutable manifest persisted atomically after meaningful state changes."""

    path: Path
    run_id: str
    data: dict[str, Any] = field(init=False)

    def __post_init__(self) -> None:
        self.data = {
            "schema_version": 1,
            "run_id": self.run_id,
            "status": "created",
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "configuration": {},
            "tools": {},
            "inputs": [],
            "tasks": [],
            "outputs": [],
        }
        self.write()

    def write(self) -> None:
        """Atomically write the current manifest."""

        self.data["updated_at"] = utc_now()
        temporary = self.path.with_suffix(".json.tmp")
        payload = json.dumps(self.data, indent=2, sort_keys=True, default=_json_default) + "\n"
        temporary.write_text(payload, encoding="utf-8")
        os.replace(temporary, self.path)

    def set_status(self, status: str, *, error: str | None = None) -> None:
        self.data["status"] = status
        if error is not None:
            self.data["error"] = error
        if status in {"completed", "failed"}:
            self.data["finished_at"] = utc_now()
        self.write()

    def set_configuration(self, configuration: dict[str, Any]) -> None:
        self.data["configuration"] = configuration
        self.write()

    def record_tool(self, name: str, details: dict[str, Any]) -> None:
        self.data["tools"][name] = details
        self.write()

    def record_input(self, role: str, original: Path, staged: Path) -> None:
        self.data["inputs"].append(
            {
                "role": role,
                "original_path": str(original.resolve()),
                "staged_path": str(staged),
                "sha256": sha256_file(staged),
                "size": staged.stat().st_size,
            }
        )
        self.write()

    def start_task(self, task_id: str, protein_id: str, ph: Decimal) -> dict[str, Any]:
        task = {
            "task_id": task_id,
            "protein_id": protein_id,
            "ph": str(ph),
            "status": "running",
            "started_at": utc_now(),
            "stages": [],
        }
        self.data["tasks"].append(task)
        self.write()
        return task

    def record_stage(self, task: dict[str, Any], stage: dict[str, Any]) -> None:
        task["stages"].append(stage)
        self.write()

    def finish_task(self, task: dict[str, Any], status: str, *, error: str | None = None) -> None:
        task["status"] = status
        task["finished_at"] = utc_now()
        if error is not None:
            task["error"] = error
        self.write()

    def record_output(self, role: str, path: Path) -> None:
        self.data["outputs"].append(
            {
                "role": role,
                "path": str(path),
                "sha256": sha256_file(path),
                "size": path.stat().st_size,
            }
        )
        self.write()
