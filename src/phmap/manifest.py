"""Run provenance and task status recording."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from phmap.json_state import write_json


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


def finish_task_state(
    task: dict[str, Any], status: str, *, error: str | None = None,
) -> None:
    """Apply terminal state consistently, including an unfinished active stage."""

    task.update(status=status, finished_at=utc_now())
    task.pop("current_stage", None)
    if error is not None:
        task["error"] = error
    for stage in task.get("stages", []):
        if stage.get("status") == "running":
            stage.update(status=status, finished_at=task["finished_at"])
            if error is not None:
                stage["error"] = error


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
        write_json(self.path, self.data, sort_keys=True)

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

    def record_input(
        self, role: str, original: Path, staged: Path, *, source: dict[str, Any] | None = None,
        preparation: dict[str, str] | None = None,
    ) -> None:
        digest = sha256_file(staged)
        if source and source.get("sha256") != digest:
            source = None  # A concurrent cache refresh must not misattribute staged bytes.
        self.data["inputs"].append(
            {
                "role": role,
                "original_path": str(original.resolve()),
                "staged_path": str(staged),
                "sha256": digest,
                "size": staged.stat().st_size,
                **({"source": source} if source else {}),
                **({"preparation": preparation} if preparation else {}),
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
        if (
            task["stages"]
            and task["stages"][-1].get("name") == stage["name"]
            and task["stages"][-1].get("status") == "running"
        ):
            task["stages"][-1].update(stage)
        else:
            task["stages"].append(stage)
        self.write()

    def start_stage(self, task: dict[str, Any], name: str) -> None:
        """Expose the active stage to CLI and GUI progress readers."""

        task["current_stage"] = name
        task["stages"].append({"name": name, "status": "running", "started_at": utc_now()})
        self.write()

    def finish_task(self, task: dict[str, Any], status: str, *, error: str | None = None) -> None:
        finish_task_state(task, status, error=error)
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
