"""Run provenance and task status recording."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from functools import wraps
from pathlib import Path
from threading import RLock
from typing import Any, Concatenate, ParamSpec, TypeVar

from phmap.json_state import write_json

_P = ParamSpec("_P")
_T = TypeVar("_T")


def _synchronized(
    method: Callable[Concatenate[RunManifest, _P], _T],
) -> Callable[Concatenate[RunManifest, _P], _T]:
    @wraps(method)
    def locked(self: RunManifest, /, *args: _P.args, **kwargs: _P.kwargs) -> _T:
        with self._lock:
            return method(self, *args, **kwargs)

    return locked


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
    task: dict[str, Any],
    status: str,
    *,
    error: str | None = None,
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
    _lock: RLock = field(default_factory=RLock, init=False, repr=False)

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

    @_synchronized
    def write(self) -> None:
        """Atomically write the current manifest."""

        self.data["updated_at"] = utc_now()
        write_json(self.path, self.data, sort_keys=True)

    @_synchronized
    def set_status(self, status: str, *, error: str | None = None) -> None:
        self.data["status"] = status
        if error is not None:
            self.data["error"] = error
        if status in {"completed", "failed", "cancelled", "interrupted"}:
            self.data["finished_at"] = utc_now()
        self.write()

    @_synchronized
    def set_configuration(self, configuration: dict[str, Any]) -> None:
        self.data["configuration"] = configuration
        self.write()

    @_synchronized
    def record_tool(self, name: str, details: dict[str, Any]) -> None:
        self.data["tools"][name] = details
        self.write()

    @_synchronized
    def record_input(
        self,
        role: str,
        original: Path,
        staged: Path,
        *,
        source: dict[str, Any] | None = None,
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

    @_synchronized
    def prepare_tasks(self, cells: Sequence[tuple[str, Decimal]]) -> None:
        self.data["tasks"] = [
            {
                "task_id": f"{protein}@{ph}",
                "protein_id": protein,
                "ph": str(ph),
                "status": "pending",
                "stages": [],
            }
            for protein, ph in cells
        ]
        self.write()

    @_synchronized
    def cancel_unfinished(self) -> None:
        """Finalize unfinished tasks after the cell scheduler has drained."""

        for task in self.data["tasks"]:
            if task["status"] in {"pending", "running"}:
                finish_task_state(
                    task, "cancelled", error="Calculation stopped before this cell finished"
                )
        self.write()

    @_synchronized
    def start_task(
        self,
        task_id: str,
        protein_id: str,
        ph: Decimal,
        *,
        metrics: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        task = next((task for task in self.data["tasks"] if task["task_id"] == task_id), None)
        if task is None:
            task = {}
            self.data["tasks"].append(task)
        task.update(
            {
                "task_id": task_id,
                "protein_id": protein_id,
                "ph": str(ph),
                "status": "running",
                "started_at": utc_now(),
                "stages": [],
            }
        )
        if metrics is not None:
            task["scientific_metrics"] = metrics
        self.write()
        return task

    @_synchronized
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

    @_synchronized
    def start_stage(self, task: dict[str, Any], name: str) -> None:
        """Expose the active stage to CLI and GUI progress readers."""

        task["current_stage"] = name
        task["stages"].append({"name": name, "status": "running", "started_at": utc_now()})
        self.write()

    @_synchronized
    def finish_task(self, task: dict[str, Any], status: str, *, error: str | None = None) -> None:
        finish_task_state(task, status, error=error)
        self.write()

    @_synchronized
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
