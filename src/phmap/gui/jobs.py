"""A serial local job queue with isolated CLI workers and manifest-based history."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from contextlib import suppress
from dataclasses import replace
from pathlib import Path
from typing import Any

from phmap.errors import ConfigurationError
from phmap.manifest import utc_now
from phmap.models import RunInputs
from phmap.processes import hidden_process_options, terminate_tree
from phmap.workflow import WorkflowOptions
from phmap.workspace import default_run_id

from .locking import LocalLock

TERMINAL = frozenset({"completed", "failed", "cancelled", "interrupted"})
_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")


def read_json(path: Path) -> dict[str, Any]:
    """Read atomically written state, tolerating an absent or damaged old run."""

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_json(path: Path, data: dict[str, Any]) -> None:
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def build_worker_argv(inputs: RunInputs, options: WorkflowOptions) -> list[str]:
    """Pass validated values to the existing CLI, without invoking a shell."""

    argv = [sys.executable, "-m", "phmap", "--debug", "run"]
    argv.extend(str(protein.path) for protein in inputs.proteins)
    for ph in inputs.ph_values:
        argv.extend(("--ph", str(ph)))
    for protein in inputs.proteins:
        argv.extend(("--protein-id", protein.protein_id, "--label", protein.label))
    argv.extend((
        "--output-dir", str(options.output_dir),
        "--size", str(options.size), "--dpi", str(options.dpi),
        "--level", str(options.potential_level), "--foreground", options.foreground,
        "--force-field", options.force_field, "--timeout", str(options.timeout),
        "--ramp-colors", *options.ramp_colors,
        "--pdb2pqr-executable", str(options.pdb2pqr_executable),
        "--apbs-executable", str(options.apbs_executable),
        "--pymol-executable", str(options.pymol_executable),
    ))
    if options.background is not None:
        argv.extend(("--background", options.background))
    if not options.ray_trace:
        argv.append("--no-ray")
    if inputs.proteins[0].ligand is not None:
        argv.extend(("--ligand", str(inputs.proteins[0].ligand)))
    return argv


class JobManager:
    """Own one worker at a time; completed CLI runs need no database import."""

    def __init__(self, runs_dir: Path) -> None:
        self.runs_dir = runs_dir.expanduser().resolve()
        self.control = self.runs_dir / ".gui"
        self.uploads = self.control / "inputs"
        self.records = self.control / "jobs"
        self._condition = threading.Condition(threading.RLock())
        self._queue: deque[str] = deque()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._cancelled: set[str] = set()
        self._active: str | None = None
        self._process: subprocess.Popen[bytes] | None = None
        self._thread: threading.Thread | None = None
        self._closing = False
        self._lock = LocalLock(self.control / "service.lock")

    def start(self) -> None:
        self.uploads.mkdir(parents=True, exist_ok=True)
        self.records.mkdir(parents=True, exist_ok=True)
        self._lock.acquire()
        for path in self.records.glob("*.json"):
            record = read_json(path)
            if not record or not _RUN_ID.fullmatch(path.stem):
                continue
            if record.get("status") not in TERMINAL:
                record.update(status="interrupted", error="GUI stopped before this run finished")
                write_json(path, record)
                self._mark_manifest(path.stem, "interrupted", record["error"])
            self._jobs[path.stem] = record
        self._thread = threading.Thread(target=self._work, name="phmap-jobs", daemon=True)
        self._thread.start()

    def _save(self, run_id: str) -> None:
        self._jobs[run_id]["updated_at"] = utc_now()
        write_json(self.records / f"{run_id}.json", self._jobs[run_id])

    def submit(self, inputs: RunInputs, options: WorkflowOptions, *, name: str = "") -> str:
        with self._condition:
            if self._closing or self._thread is None:
                raise ConfigurationError("The calculation queue is unavailable")
            if len(self._queue) >= 20:
                raise ConfigurationError("The queue is full; wait for a run to finish")
            run_id = default_run_id()
            destination = self.uploads / run_id
            destination.mkdir()
            proteins = []
            for protein in inputs.proteins:
                pdb = destination / f"{protein.protein_id}.pdb"
                shutil.copy2(protein.path, pdb)
                ligand = None
                if protein.ligand is not None:
                    ligand = destination / "ligand.mol2"
                    if not ligand.exists():
                        shutil.copy2(protein.ligand, ligand)
                proteins.append(type(protein)(pdb, protein.protein_id, protein.label, ligand))
            staged = RunInputs(proteins, inputs.ph_values, view=inputs.view, ph_mode=inputs.ph_mode)
            options = replace(options, output_dir=self.runs_dir / run_id)
            argv = build_worker_argv(staged, options)
            if inputs.view is not None:
                view_path = destination / "view.txt"
                view_path.write_text(
                    ", ".join(repr(value) for value in inputs.view.values), encoding="utf-8"
                )
                argv.extend(("--view", str(view_path)))
            self._jobs[run_id] = {
                "run_id": run_id, "name": name.strip()[:120] or " / ".join(
                    protein.label for protein in inputs.proteins
                ),
                "status": "queued", "created_at": utc_now(), "argv": argv,
                "configuration": {
                    "proteins": [
                        {"id": p.protein_id, "label": p.label} for p in inputs.proteins
                    ],
                    "ph_values": [str(ph) for ph in inputs.ph_values],
                    "ph_mode": inputs.ph_mode.value,
                },
            }
            self._save(run_id)
            self._queue.append(run_id)
            self._condition.notify()
            return run_id

    def _work(self) -> None:
        while True:
            with self._condition:
                self._condition.wait_for(lambda: bool(self._queue) or self._closing)
                if self._closing:
                    return
                run_id = self._queue.popleft()
                if self._jobs[run_id]["status"] != "queued":
                    continue
                self._active = run_id
                self._jobs[run_id]["status"] = "running"
                self._save(run_id)
            try:
                self._execute(run_id)
            except Exception as exc:
                with self._condition:
                    self._jobs[run_id].update(status="failed", error=str(exc))
                    self._save(run_id)
            finally:
                with self._condition:
                    self._active = None
                    self._process = None

    def _execute(self, run_id: str) -> None:
        with (
            (self.records / f"{run_id}.stdout.log").open("wb") as stdout,
            (self.records / f"{run_id}.stderr.log").open("wb") as stderr,
        ):
            with self._condition:
                process = subprocess.Popen(
                    self._jobs[run_id]["argv"], cwd=self.runs_dir,
                    stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                    shell=False, start_new_session=os.name == "posix", **hidden_process_options(),
                )
                self._process = process
            cancellation_started: float | None = None
            while process.poll() is None:
                with self._condition:
                    cancelled = run_id in self._cancelled
                if cancelled:
                    if cancellation_started is None:
                        cancellation_started = time.monotonic()
                        terminate_tree(process)
                    elif time.monotonic() - cancellation_started > 3:
                        terminate_tree(process, force=True)
                with suppress(subprocess.TimeoutExpired):
                    process.wait(timeout=0.2)
            with self._condition:
                manifest = read_json(self.runs_dir / run_id / "manifest.json")
                if run_id in self._cancelled:
                    # A descendant may survive even after its parent exits.
                    terminate_tree(process, force=True)
                    status, error = "cancelled", "Calculation cancelled"
                    self._mark_manifest(run_id, status, error)
                elif process.returncode == 0 and manifest.get("status") == "completed":
                    status, error = "completed", None
                else:
                    status = "failed"
                    error = manifest.get("error") or self.log_tail(
                        self.records / f"{run_id}.stderr.log"
                    ) or f"Worker exited with status {process.returncode}"
                self._jobs[run_id].update(
                    status=status, error=error, returncode=process.returncode,
                    finished_at=utc_now(),
                )
                self._save(run_id)

    def _mark_manifest(self, run_id: str, status: str, error: str) -> None:
        path = self.runs_dir / run_id / "manifest.json"
        manifest = read_json(path)
        if manifest:
            manifest.update(status=status, error=error, finished_at=utc_now(), updated_at=utc_now())
            for task in manifest.get("tasks", []):
                if task.get("status") == "running":
                    task.update(status=status, error=error, finished_at=utc_now())
                    task.pop("current_stage", None)
            write_json(path, manifest)

    def cancel(self, run_id: str) -> None:
        with self._condition:
            record = self._jobs.get(run_id)
            if record is None:
                raise ConfigurationError("Only runs started in this GUI can be cancelled")
            if record["status"] in TERMINAL:
                return
            self._cancelled.add(run_id)
            record["status"] = "cancelling" if run_id == self._active else "cancelled"
            self._save(run_id)

    def close(self) -> None:
        with self._condition:
            self._closing = True
            for run_id, record in self._jobs.items():
                if record["status"] not in TERMINAL:
                    self.cancel(run_id)
            self._condition.notify_all()
        if self._thread is not None:
            self._thread.join(timeout=10)
        self._lock.release()

    def run_directory(self, run_id: str) -> Path:
        if not _RUN_ID.fullmatch(run_id) or run_id in {".", ".."}:
            raise FileNotFoundError("Invalid run ID")
        path = (self.runs_dir / run_id).resolve()
        if path.parent != self.runs_dir:
            raise FileNotFoundError("Run is outside the configured directory")
        return path

    def snapshot(self, run_id: str) -> dict[str, Any]:
        root = self.run_directory(run_id)
        with self._condition:
            record = self._jobs.get(run_id, {}).copy()
        manifest = read_json(root / "manifest.json")
        if not record and not manifest:
            raise FileNotFoundError("Run not found")
        configuration = manifest.get("configuration") or record.get("configuration", {})
        proteins = configuration.get("proteins", [])
        ph_values = configuration.get("ph_values", [])
        tasks = manifest.get("tasks", [])
        task_map = {(task["protein_id"], task["ph"]): task for task in tasks}
        completed = sum(task.get("status") == "completed" for task in tasks)
        status = record.get("status") or manifest.get("status", "unknown")
        # A CLI run can finish before the queue thread updates its own record.
        if status == "running" and manifest.get("status") in TERMINAL:
            status = manifest["status"]
        cells = []
        for protein in proteins:
            for ph in ph_values:
                task = task_map.get((protein["id"], ph), {})
                cells.append({
                    "protein": protein["label"], "protein_id": protein["id"], "ph": ph,
                    "status": task.get("status", "pending"),
                    "stage": task.get("current_stage"), "stages": task.get("stages", []),
                    "error": task.get("error"),
                })
        logs = []
        if (root / "logs").is_dir():
            logs = [str(path.relative_to(root)) for path in sorted((root / "logs").rglob("*.log"))]
        figure = "output/pHmap.png" if (root / "output/pHmap.png").is_file() else None
        tiles = []
        if (root / "renders").is_dir():
            tiles = [str(p.relative_to(root)) for p in sorted((root / "renders").rglob("*.png"))]
        total = len(proteins) * len(ph_values)
        return {
            "run_id": run_id, "name": record.get("name") or " / ".join(
                protein["label"] for protein in proteins
            ),
            "status": status, "source": "gui" if record else "cli",
            "created_at": record.get("created_at") or manifest.get("created_at"),
            "updated_at": manifest.get("updated_at") or record.get("updated_at"),
            "directory": str(root), "configuration": configuration,
            "completed": completed, "total": total,
            "progress": round(100 * completed / total) if total else 0,
            "cells": cells, "figure": figure, "tiles": tiles, "logs": logs,
            "manifest": "manifest.json" if manifest else None,
            "error": record.get("error") or manifest.get("error"),
            "can_cancel": bool(record) and status not in TERMINAL,
            "worker_log": self.log_tail(self.records / f"{run_id}.stderr.log") if record else "",
        }

    def history(self) -> list[dict[str, Any]]:
        with self._condition:
            run_ids = set(self._jobs)
        if self.runs_dir.exists():
            run_ids.update(
                path.name for path in self.runs_dir.iterdir()
                if path.is_dir() and not path.name.startswith(".")
                and (path / "manifest.json").is_file()
            )
        results = []
        for run_id in run_ids:
            try:
                data = self.snapshot(run_id)
            except (FileNotFoundError, KeyError, TypeError):
                continue
            results.append({key: data[key] for key in (
                "run_id", "name", "status", "source", "created_at", "completed", "total"
            )})
        return sorted(results, key=lambda item: item.get("created_at") or "", reverse=True)

    @staticmethod
    def log_tail(path: Path, limit: int = 32_768) -> str:
        try:
            with path.open("rb") as handle:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                handle.seek(max(0, size - limit))
                return handle.read(limit).decode("utf-8", errors="replace")
        except OSError:
            return ""
