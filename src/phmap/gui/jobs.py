"""A serial local job queue with isolated CLI workers and manifest-based history."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from contextlib import nullcontext, suppress
from dataclasses import replace
from pathlib import Path
from typing import Any

from phmap.configuration import WorkflowOptions, build_run_configuration
from phmap.errors import ConfigurationError
from phmap.json_state import read_json, write_json
from phmap.locking import LocalLock
from phmap.manifest import finish_task_state, sha256_file, utc_now
from phmap.models import RunInputs, structure_suffix
from phmap.processes import hidden_process_options, terminate_tree
from phmap.run_updates import RunUpdate, preserve_artifacts, recover_run_updates
from phmap.saved_runs import SavedRun, SavedRunWorkflow
from phmap.structures import copy_structure_source, structure_source
from phmap.workspace import default_run_id

from .input_snapshots import remove_input_snapshot, retire_input_snapshot, stage_input_snapshot
from .run_templates import RunTemplate

TERMINAL = frozenset({"completed", "failed", "cancelled", "interrupted"})
_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")


def build_worker_argv(inputs: RunInputs, options: WorkflowOptions) -> list[str]:
    """Pass validated values to the existing CLI, without invoking a shell."""

    argv = [sys.executable, "-m", "phmap", "--debug", "run"]
    argv.extend(str(protein.path) for protein in inputs.proteins)
    if inputs.ph_range is not None:
        argv.extend(
            (
                "--ph-range",
                str(inputs.ph_range.start),
                str(inputs.ph_range.stop),
                str(inputs.ph_range.step),
            )
        )
    else:
        for ph in inputs.ph_values:
            argv.extend(("--ph", str(ph)))
    for protein in inputs.proteins:
        argv.extend(("--protein-id", protein.protein_id, "--label", protein.label))
    argv.extend(build_options_argv(options))
    if inputs.proteins[0].ligand is not None:
        argv.extend(("--ligand", str(inputs.proteins[0].ligand)))
    return argv


def build_options_argv(options: WorkflowOptions) -> list[str]:
    """Serialize validated settings identically for normal and derivative workers."""

    argv: list[str] = []
    argv.extend(
        (
            "--output-dir",
            str(options.output_dir),
            "--workers",
            str(options.workers),
            "--size",
            str(options.size),
            "--dpi",
            str(options.dpi),
            "--level",
            str(options.potential_level),
            "--foreground",
            options.foreground,
            "--force-field",
            options.force_field,
            "--timeout",
            str(options.timeout),
            "--ramp-colors",
            *options.ramp_colors,
            "--surface-mode",
            str(options.surface_mode),
            "--surface-solvent" if options.surface_solvent else "--no-surface-solvent",
            "--surface-ramp-above-mode"
            if options.surface_ramp_above_mode
            else "--no-surface-ramp-above-mode",
            "--ligand-representation",
            options.ligand_representation or "none",
            "--font-size",
            str(options.font_size),
            "--outer-margin",
            str(options.outer_margin),
            "--column-gap",
            str(options.column_gap),
            "--row-gap",
            str(options.row_gap),
            "--label-gap",
            str(options.label_gap),
            "--legend" if options.show_colorbar else "--no-legend",
            "--pdb2pqr-executable",
            str(options.pdb2pqr_executable),
            "--apbs-executable",
            str(options.apbs_executable),
            "--pymol-executable",
            str(options.pymol_executable),
        )
    )
    if options.background is not None:
        argv.extend(("--background", options.background))
    if options.ph_label_precision is not None:
        argv.extend(("--ph-label-precision", str(options.ph_label_precision)))
    if not options.ray_trace:
        argv.append("--no-ray")
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
        self._updates: dict[str, RunUpdate] = {}
        self._lock = LocalLock(self.control / "service.lock")

    def start(self) -> None:
        self.uploads.mkdir(parents=True, exist_ok=True)
        self.records.mkdir(parents=True, exist_ok=True)
        self._lock.acquire()
        try:
            recover_run_updates(self.runs_dir)
        except BaseException:
            self._lock.release()
            raise
        for path in self.records.glob("*.json"):
            record = read_json(path)
            if not record or not _RUN_ID.fullmatch(path.stem):
                continue
            if record.get("update_in_place"):
                update = RunUpdate(self.run_directory(path.stem))
                try:
                    update.lock.acquire()
                except ConfigurationError:
                    pass  # An active CLI update owns it; never recover another writer's work.
                else:
                    try:
                        update.recover()
                    finally:
                        update.lock.release()
                saved = read_json(update.target / "manifest.json")
                installed = saved.get("last_update", {})
                if (
                    record.get("status") not in TERMINAL
                    and record.get("source_manifest_sha256")
                    and installed.get("previous_manifest_sha256")
                    == record["source_manifest_sha256"]
                ):
                    record.update(
                        status="completed", error=None, finished_at=installed["finished_at"]
                    )
                    write_json(path, record)
            if record.get("status") not in TERMINAL:
                record.update(status="interrupted", error="GUI stopped before this run finished")
                write_json(path, record)
                if not record.get("update_in_place"):
                    self._mark_manifest(path.stem, "interrupted", record["error"])
            self._jobs[path.stem] = record
        for run_id in self._jobs:
            self._cleanup_retired_inputs(run_id)
        self._thread = threading.Thread(target=self._work, name="phmap-jobs", daemon=True)
        self._thread.start()

    def _save(self, run_id: str) -> None:
        self._jobs[run_id]["updated_at"] = utc_now()
        write_json(self.records / f"{run_id}.json", self._jobs[run_id])

    def _cleanup_retired_inputs(self, run_id: str) -> None:
        """The replacement record is durable before any previous inputs are removed."""
        record = self._jobs[run_id]
        retired = record.get("retired_input_snapshots", [])
        if not retired:
            return
        remaining = []
        errors = []
        for snapshot in retired:
            try:
                folder = snapshot["input_folder"]
                if any(job.get("input_folder", key) == folder for key, job in self._jobs.items()):
                    raise ConfigurationError("Input snapshot is still referenced; left untouched")
                remove_input_snapshot(self.uploads, run_id, snapshot)
            except (
                OSError, ConfigurationError, ValueError, TypeError, KeyError, AttributeError,
            ) as exc:
                remaining.append(snapshot)
                errors.append(str(exc))
        if remaining:
            record["retired_input_snapshots"] = remaining
            record["input_cleanup_error"] = "; ".join(errors)
        else:
            record.pop("retired_input_snapshots", None)
            record.pop("input_cleanup_error", None)
        try:
            self._save(run_id)
        except OSError as exc:
            # The original durable tickets permit another attempt after restart.
            record["input_cleanup_error"] = str(exc)

    def submit(
        self,
        inputs: RunInputs,
        options: WorkflowOptions,
        *,
        name: str = "",
        based_on: dict[str, str] | None = None,
        update_run_id: str | None = None,
        expected_manifest: str | None = None,
    ) -> str:
        with self._condition:
            resume = None
            if update_run_id is not None and not self.run_directory(update_run_id).exists():
                record = self._jobs.get(update_run_id, {})
                if (
                    record.get("status") not in TERMINAL
                    or not record.get("inputs")
                    or expected_manifest is None
                    or sha256_file(self.records / f"{update_run_id}.json") != expected_manifest
                ):
                    raise ConfigurationError("Saved setup changed; reload it before resuming")
                resume = update_run_id
            update = self._begin_update(update_run_id, expected_manifest) if not resume else None
            try:
                staging = (
                    stage_input_snapshot(
                        self.uploads, resume, f"{resume}-resume-{default_run_id()}",
                        self.records / f"{resume}.json", expected_manifest or "",
                    ) if resume else nullcontext(None)
                )
                with staging as destination:
                    return self._submit(
                        inputs, options, name=name, based_on=based_on, update=update,
                        resume_run_id=resume, resume_input_folder=destination,
                    )
            except BaseException:
                self._discard_update(update_run_id)
                raise

    def _begin_update(self, run_id: str | None, digest: str | None) -> RunUpdate | None:
        if run_id is None:
            return None
        if self._closing or self._thread is None:
            raise ConfigurationError("The calculation queue is unavailable")
        if (
            run_id == self._active
            or run_id in self._updates
            or self._jobs.get(run_id, {}).get("status")
            in {
                "queued",
                "running",
                "cancelling",
            }
        ):
            raise ConfigurationError("This calculation is already being updated")
        if digest is None:
            raise ConfigurationError("Reload the calculation before updating it")
        update = RunUpdate(self.run_directory(run_id))
        update.begin(digest)
        self._updates[run_id] = update
        return update

    def _discard_update(self, run_id: str | None) -> None:
        update = self._updates.pop(run_id, None) if run_id is not None else None
        if update is not None:
            update.discard()
            if update.cleanup_error and run_id in self._jobs:
                self._jobs[run_id]["cleanup_error"] = update.cleanup_error

    def _submit(
        self,
        inputs: RunInputs,
        options: WorkflowOptions,
        *,
        name: str,
        based_on: dict[str, str] | None,
        update: RunUpdate | None,
        resume_run_id: str | None = None,
        resume_input_folder: Path | None = None,
    ) -> str:
        with self._condition:
            if self._closing or self._thread is None:
                raise ConfigurationError("The calculation queue is unavailable")
            if len(self._queue) >= 20:
                raise ConfigurationError("The queue is full; wait for a run to finish")
            run_id = resume_run_id or (update.target.name if update else default_run_id())
            destination = resume_input_folder or (
                update.folder / "uploads" if update else self.uploads / run_id
            )
            if resume_input_folder is None:
                destination.mkdir()
            proteins = []
            for protein in inputs.proteins:
                pdb = destination / f"{protein.protein_id}{structure_suffix(protein.path)}"
                shutil.copy2(protein.path, pdb)
                copy_structure_source(protein.path, pdb)
                ligand = None
                if protein.ligand is not None:
                    ligand = destination / "ligand.mol2"
                    if not ligand.exists():
                        shutil.copy2(protein.ligand, ligand)
                proteins.append(type(protein)(pdb, protein.protein_id, protein.label, ligand))
            staged = RunInputs(
                proteins,
                inputs.ph_values,
                view=inputs.view,
                ph_mode=inputs.ph_mode,
                ph_range=inputs.ph_range,
            )
            options = replace(
                options, output_dir=update.pending if update else self.runs_dir / run_id
            )
            argv = build_worker_argv(staged, options)
            if inputs.view is not None:
                view_path = destination / "view.txt"
                view_path.write_text(
                    ", ".join(repr(value) for value in inputs.view.values), encoding="utf-8"
                )
                argv.extend(("--view", str(view_path)))
            saved_inputs = []
            digests: dict[Path, str] = {}
            for protein in staged.proteins:
                for role, path in (
                    (f"protein:{protein.protein_id}", protein.path),
                    (f"ligand:{protein.protein_id}", protein.ligand),
                ):
                    if path is not None:
                        if path not in digests:
                            digests[path] = sha256_file(path)
                        source = structure_source(path) if path == protein.path else {}
                        saved_inputs.append(
                            {
                                "role": role,
                                "staged_path": str(path),
                                "sha256": digests[path],
                                **({"source": source} if source else {}),
                            }
                        )
            previous = self._jobs.get(run_id)
            retired = list(previous.get("retired_input_snapshots", [])) if previous else []
            if resume_run_id and previous:
                retired.append(retire_input_snapshot(run_id, previous))
            self._jobs[run_id] = {
                "run_id": run_id,
                "name": name.strip()[:120]
                or " / ".join(protein.label for protein in inputs.proteins),
                "status": "queued",
                "created_at": (
                    (
                        self._jobs.get(run_id, {}).get("created_at")
                        or (read_json(update.target / "manifest.json")["created_at"]
                            if update else utc_now())
                    )
                    if update or resume_run_id
                    else utc_now()
                ),
                "argv": argv,
                **({"based_on": based_on} if based_on else {}),
                "configuration": build_run_configuration(staged, options),
                "inputs": saved_inputs,
                "input_folder": destination.name,
                **({"retired_input_snapshots": retired} if retired else {}),
                "update_in_place": update is not None,
                "source_manifest_sha256": update.source_digest if update else None,
                **({"previous_name": self._jobs.get(run_id, {}).get("name")} if update else {}),
            }
            try:
                self._save(run_id)
            except BaseException:
                if previous is not None:
                    self._jobs[run_id] = previous
                else:
                    self._jobs.pop(run_id, None)
                raise
            self._queue.append(run_id)
            self._condition.notify()
            self._cleanup_retired_inputs(run_id)
            return run_id

    def submit_saved(
        self,
        workflow: SavedRunWorkflow,
        *,
        name: str = "",
        update_in_place: bool = False,
    ) -> str:
        with self._condition:
            run_id = workflow.source.root.name if update_in_place else None
            update = self._begin_update(run_id, workflow.source.digest)
            try:
                return self._submit_saved(workflow, name=name, update=update)
            except BaseException:
                self._discard_update(run_id)
                raise

    def _submit_saved(
        self,
        workflow: SavedRunWorkflow,
        *,
        name: str,
        update: RunUpdate | None,
    ) -> str:
        with self._condition:
            if self._closing or self._thread is None:
                raise ConfigurationError("The calculation queue is unavailable")
            if len(self._queue) >= 20:
                raise ConfigurationError("The queue is full; wait for a run to finish")
            run_id = update.target.name if update else default_run_id()
            destination = update.folder / "uploads" if update else self.uploads / run_id
            destination.mkdir()
            options = replace(
                workflow.options, output_dir=update.pending if update else self.runs_dir / run_id
            )
            argv = [
                sys.executable,
                "-m",
                "phmap",
                "--debug",
                workflow.operation,
                str(workflow.source.root),
                "--source-manifest-sha256",
                workflow.source.digest,
            ]
            argv.extend(build_options_argv(options))
            # Unlike the normal run command, omission means inherit saved settings.
            if options.background is None:
                argv.append("--transparent")
            if options.ph_label_precision is None:
                argv.append("--preserve-ph-labels")
            if options.ray_trace:
                argv.append("--ray")
            for key, label in workflow.proteins:
                argv.extend(("--protein-id", key, "--label", label))
            for ph in workflow.ph_values:
                argv.extend(("--ph", str(ph)))
            if workflow.view is None:
                argv.append("--auto-view")
            else:
                view = destination / "view.txt"
                view.write_text(", ".join(repr(value) for value in workflow.view), encoding="utf-8")
                argv.extend(("--view", str(view)))
            previous = self._jobs.get(run_id, {})
            self._jobs[run_id] = {
                "run_id": run_id,
                "name": name.strip()[:120] or f"{workflow.operation} · {workflow.source.root.name}",
                "status": "queued",
                "created_at": (
                    (
                        self._jobs.get(run_id, {}).get("created_at")
                        or workflow.source.manifest["created_at"]
                    )
                    if update
                    else utc_now()
                ),
                "argv": argv,
                "plan": workflow.plan(),
                "update_in_place": update is not None,
                "source_manifest_sha256": update.source_digest if update else None,
                **({"retired_input_snapshots": previous["retired_input_snapshots"]}
                   if previous.get("retired_input_snapshots") else {}),
                **({"previous_name": self._jobs.get(run_id, {}).get("name")} if update else {}),
                "configuration": {
                    "proteins": [{"id": key, "label": label} for key, label in workflow.proteins],
                    "ph_values": [str(ph) for ph in workflow.ph_values],
                },
            }
            self._save(run_id)
            self._queue.append(run_id)
            self._condition.notify()
            self._cleanup_retired_inputs(run_id)
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
                    self._cancelled.discard(run_id)
                    record = self._jobs[run_id]
                    previous_name = record.pop("previous_name", None)
                    if record["status"] != "completed" and previous_name:
                        record["name"] = previous_name
                    try:
                        self._discard_update(run_id)
                    except Exception as exc:
                        record["cleanup_error"] = str(exc)
                    self._save(run_id)

    def _execute(self, run_id: str) -> None:
        update = self._updates.get(run_id)
        worker_root = update.pending if update else self.runs_dir / run_id
        with (
            (self.records / f"{run_id}.stdout.log").open("wb") as stdout,
            (self.records / f"{run_id}.stderr.log").open("wb") as stderr,
        ):
            with self._condition:
                process = subprocess.Popen(
                    self._jobs[run_id]["argv"],
                    cwd=self.runs_dir,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout,
                    stderr=stderr,
                    shell=False,
                    start_new_session=os.name == "posix",
                    **hidden_process_options(),
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
            manifest = read_json(worker_root / "manifest.json")
            if (
                update is not None
                and process.returncode == 0
                and manifest.get("status") == "completed"
                and self._jobs[run_id].get("plan")
            ):
                preserve_artifacts(SavedRun(update.target), update.pending)
            with self._condition:
                manifest = read_json(worker_root / "manifest.json")
                if run_id in self._cancelled:
                    # A descendant may survive even after its parent exits.
                    terminate_tree(process, force=True)
                    status, error = "cancelled", "Calculation cancelled"
                    self._mark_manifest(run_id, status, error)
                elif process.returncode == 0 and manifest.get("status") == "completed":
                    if update is not None:
                        update.commit()
                    status, error = "completed", None
                else:
                    status = "failed"
                    error = (
                        manifest.get("error")
                        or self.log_tail(self.records / f"{run_id}.stderr.log")
                        or f"Worker exited with status {process.returncode}"
                    )
                based_on = self._jobs[run_id].get("based_on")
                if based_on and update is None:
                    path = self.runs_dir / run_id / "manifest.json"
                    manifest = read_json(path)
                    if manifest:
                        manifest["based_on"] = based_on
                        write_json(path, manifest)
                self._jobs[run_id].update(
                    status=status,
                    error=error,
                    returncode=process.returncode,
                    finished_at=utc_now(),
                )
                self._save(run_id)

    def _mark_manifest(self, run_id: str, status: str, error: str) -> None:
        if self._jobs.get(run_id, {}).get("update_in_place"):
            update = self._updates.get(run_id)
            if update is None:
                return  # A recovered/abandoned update must never mark the last good result failed.
            path = update.pending / "manifest.json"
        else:
            path = self.runs_dir / run_id / "manifest.json"
        manifest = read_json(path)
        if manifest:
            manifest.update(status=status, error=error, finished_at=utc_now(), updated_at=utc_now())
            for task in manifest.get("tasks", []):
                if task.get("status") in {"running", "pending"}:
                    finish_task_state(task, status, error=error)
            write_json(path, manifest)

    def cancel(self, run_id: str) -> None:
        with self._condition:
            record = self._jobs.get(run_id)
            if record is None:
                raise ConfigurationError("Only runs started in this GUI can be cancelled")
            if record["status"] in TERMINAL:
                return
            if run_id == self._active:
                self._cancelled.add(run_id)
                record["status"] = "cancelling"
            else:
                self._queue.remove(run_id)
                record.update(status="cancelled", finished_at=utc_now())
                previous_name = record.pop("previous_name", None)
                if previous_name:
                    record["name"] = previous_name
                self._discard_update(run_id)
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

    def _read_state(self, run_id: str) -> tuple[Path, dict[str, Any], dict[str, Any]]:
        root = self.run_directory(run_id)
        with self._condition:
            record = self._jobs.get(run_id, {}).copy()
            if run_id == self._active and record.get("status") in TERMINAL:
                record["status"] = "running"  # Publish completion after cleanup/unlocking.
        manifest = read_json(root / "manifest.json")
        if record.get("update_in_place") and record.get("status") not in TERMINAL:
            update = self._updates.get(run_id)
            manifest = read_json(update.pending / "manifest.json") if update else {}
        if not record and not manifest:
            raise FileNotFoundError("Run not found")
        return root, record, manifest

    def template(self, run_id: str) -> dict[str, Any]:
        _, record, manifest = self._read_state(run_id)
        name = self._summary(run_id, record, manifest)["name"]
        return self.load_template(run_id).response(name=name)

    def load_template(self, run_id: str) -> RunTemplate:
        root, record, _ = self._read_state(run_id)
        manifest = root / "manifest.json"
        if manifest.exists() or manifest.is_symlink():
            return RunTemplate(root)
        folder = record.get("input_folder", run_id)
        if not isinstance(folder, str) or not _RUN_ID.fullmatch(folder):
            raise ConfigurationError("Saved input folder is invalid")
        input_root = (self.uploads / folder).resolve()
        if input_root.parent != self.uploads:
            raise ConfigurationError("Saved input folder is outside GUI storage")
        return RunTemplate(
            root,
            submission_path=self.records / f"{run_id}.json",
            input_root=input_root,
        )

    @staticmethod
    def _summary(
        run_id: str,
        record: dict[str, Any],
        manifest: dict[str, Any],
    ) -> dict[str, Any]:
        """Read sidebar information without discovering artifacts or building cells."""

        configuration = manifest.get("configuration") or record.get("configuration", {})
        proteins = configuration.get("proteins", [])
        status = record.get("status") or manifest.get("status", "unknown")
        return {
            "run_id": run_id,
            "name": record.get("name") or " / ".join(protein["label"] for protein in proteins),
            "status": status,
            "source": "gui" if record else "cli",
            "created_at": record.get("created_at") or manifest.get("created_at"),
            "completed": sum(
                task.get("status") == "completed" for task in manifest.get("tasks", [])
            ),
            "total": len(proteins) * len(configuration.get("ph_values", [])),
        }

    def snapshot(self, run_id: str) -> dict[str, Any]:
        # A snapshot must not observe the short gap between commit's two renames.
        with self._condition:
            return self._snapshot(run_id)

    def _snapshot(self, run_id: str) -> dict[str, Any]:
        root, record, manifest = self._read_state(run_id)
        summary = self._summary(run_id, record, manifest)
        configuration = manifest.get("configuration") or record.get("configuration", {})
        proteins = configuration.get("proteins", [])
        ph_values = configuration.get("ph_values", [])
        tasks = manifest.get("tasks", [])
        task_map = {(task["protein_id"], task["ph"]): task for task in tasks}
        cells = []
        for protein in proteins:
            for ph in ph_values:
                task = task_map.get((protein["id"], ph), {})
                cells.append(
                    {
                        "protein": protein["label"],
                        "protein_id": protein["id"],
                        "ph": ph,
                        "status": task.get("status", "pending"),
                        "stage": task.get("current_stage"),
                        "stages": task.get("stages", []),
                        "metrics": task.get("scientific_metrics", {}),
                        "error": task.get("error"),
                    }
                )
        logs = []
        if (root / "logs").is_dir():
            logs = [str(path.relative_to(root)) for path in sorted((root / "logs").rglob("*.log"))]
        figure = "output/pHmap.png" if (root / "output/pHmap.png").is_file() else None
        tiles = []
        if (root / "renders").is_dir():
            tiles = [str(p.relative_to(root)) for p in sorted((root / "renders").rglob("*.png"))]
        total = summary["total"]
        return {
            **summary,
            "updated_at": manifest.get("updated_at") or record.get("updated_at"),
            "directory": str(root),
            "configuration": configuration,
            "progress": round(100 * summary["completed"] / total) if total else 0,
            "cells": cells,
            "figure": figure,
            "figure_revision": (
                str((root / "output/pHmap.png").stat().st_mtime_ns) if figure else None
            ),
            "tiles": tiles,
            "logs": logs,
            "manifest": "manifest.json" if manifest else None,
            "error": record.get("error") or manifest.get("error"),
            "can_cancel": bool(record) and summary["status"] not in TERMINAL,
            "can_template": (
                summary["status"] in TERMINAL
                and bool(manifest.get("inputs") or record.get("inputs"))
            ),
            "can_rework": summary["status"] in TERMINAL and bool(manifest.get("tasks")),
            "plan": manifest.get("plan") or record.get("plan"),
            "worker_log": self.log_tail(self.records / f"{run_id}.stderr.log") if record else "",
            "manifest_sha256": (
                sha256_file(root / "manifest.json")
                if summary["status"] in TERMINAL and (root / "manifest.json").is_file()
                else None
            ),
            "submission_sha256": (
                sha256_file(self.records / f"{run_id}.json")
                if summary["status"] in TERMINAL and record and not manifest else None
            ),
            "update_in_place": bool(record.get("update_in_place")),
        }

    def history(self) -> list[dict[str, Any]]:
        with self._condition:
            run_ids = set(self._jobs)
        if self.runs_dir.exists():
            run_ids.update(
                path.name
                for path in self.runs_dir.iterdir()
                if path.is_dir()
                and not path.name.startswith(".")
                and (path / "manifest.json").is_file()
            )
        results = []
        for run_id in run_ids:
            try:
                _, record, manifest = self._read_state(run_id)
                data = self._summary(run_id, record, manifest)
            except (FileNotFoundError, KeyError, TypeError):
                continue
            results.append(data)
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
