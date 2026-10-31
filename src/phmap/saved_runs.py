"""Explicit, hash-verified composition and rendering from immutable saved runs.

Archived commands and scripts are never executed. Only expected cell artifacts
inside the selected run are read; derivatives own independent copies.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, fields, replace
from decimal import Decimal
from pathlib import Path
from threading import Event
from typing import Any, Literal

from PIL import Image

from phmap.backends import (
    CommandRunner,
    PyMOLBackend,
    PyMOLRenderRequest,
    discover_executable,
)
from phmap.compose import RenderedTile, compose_grid, format_ph_labels
from phmap.configuration import WorkflowOptions
from phmap.errors import ConfigurationError
from phmap.inputs import parse_ph_values, validate_protein_names
from phmap.manifest import RunManifest, sha256_file, utc_now
from phmap.models import PyMOLView, validate_protein_id
from phmap.parallel import SINGLE_THREAD_ENV, CellCancelled, check_cancelled, run_cells
from phmap.workflow import RunResult, _failed_stage_dict, _stage_dict, _version_dict
from phmap.workspace import RunWorkspace, safe_path_part

Operation = Literal["compose", "render"]


def options_from_configuration(configuration: dict[str, Any]) -> WorkflowOptions:
    """Restore settings, not executable paths, from older and current manifests."""

    names = {item.name for item in fields(WorkflowOptions)} - {
        "output_dir",
        "runs_dir",
        "pdb2pqr_executable",
        "apbs_executable",
        "pymol_executable",
    }
    values = {key: value for key, value in configuration.get("render", {}).items() if key in names}
    values.update(
        {key: value for key, value in configuration.get("composition", {}).items() if key in names}
    )
    values["force_field"] = configuration.get("scientific", {}).get("force_field", "PARSE")
    values["workers"] = configuration.get("execution", {}).get("workers", 1)
    if "ramp_colors" in values:
        values["ramp_colors"] = tuple(values["ramp_colors"])
    return WorkflowOptions(**values)


@dataclass(frozen=True)
class SavedArtifact:
    path: Path
    sha256: str
    size: int

    def copy_to(self, destination: Path) -> None:
        """Check the bytes actually copied, including changes after preflight."""

        digest = hashlib.sha256()
        size = 0
        destination.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("rb") as source, destination.open("xb") as target:
            while block := source.read(1024 * 1024):
                size += len(block)
                if size > self.size:
                    raise ConfigurationError(f"Saved artifact grew while copying: {self.path.name}")
                target.write(block)
                digest.update(block)
        if size != self.size or digest.hexdigest() != self.sha256:
            raise ConfigurationError(f"Saved artifact changed while copying: {self.path.name}")


class SavedRun:
    def __init__(self, root: Path, *, expected_manifest: str | None = None) -> None:
        self.root = root.expanduser().resolve()
        path = self.root / "manifest.json"
        try:
            if path.is_symlink():
                raise ConfigurationError("Saved manifest must not be a symbolic link")
            data = path.read_bytes()
            self.digest = hashlib.sha256(data).hexdigest()
            if expected_manifest is not None and self.digest != expected_manifest:
                raise ConfigurationError("Source manifest changed; review the plan again")
            self.manifest = json.loads(data)
            if not isinstance(self.manifest, dict) or self.manifest.get("schema_version") != 1:
                raise ConfigurationError("Unsupported saved manifest schema")
            self.configuration = self.manifest["configuration"]
            if self.manifest["status"] not in {"completed", "failed", "cancelled", "interrupted"}:
                raise ConfigurationError(
                    "Wait for the source run to finish before reusing artifacts"
                )
            self.options = options_from_configuration(self.configuration)
            self.tile_options = options_from_configuration(
                {
                    "render": self.configuration.get("render", {}),
                    "scientific": self.configuration.get("scientific", {}),
                }
            )
            self.ph_values = parse_ph_values(self.configuration["ph_values"])
            proteins = self.configuration["proteins"]
            self.proteins = [(validate_protein_id(item["id"]), item["label"]) for item in proteins]
            validate_protein_names(
                len(proteins),
                protein_ids=[p[0] for p in self.proteins],
                labels=[p[1] for p in self.proteins],
            )
            self.view = self.configuration.get("view")
            if self.view is not None:
                self.view = list(PyMOLView(self.view).values)
        except (OSError, KeyError, TypeError, ValueError, AttributeError) as exc:
            raise ConfigurationError(f"Saved run metadata is missing or invalid: {exc}") from exc

    def artifact(self, stage: dict[str, Any], relative: Path) -> SavedArtifact:
        records = [
            item
            for item in stage.get("outputs", [])
            if Path(item["path"].replace("\\", "/")).parts[-len(relative.parts) :] == relative.parts
        ]
        path = (self.root / relative).resolve()
        if (
            len(records) != 1
            or not path.is_relative_to(self.root)
            or not path.is_file()
            or path.stat().st_size == 0
        ):
            raise ConfigurationError(f"Saved artifact is missing or outside its run: {relative}")
        digest = records[0].get("sha256")
        if not isinstance(digest, str) or sha256_file(path) != digest:
            raise ConfigurationError(f"Saved artifact checksum does not match: {relative}")
        size = records[0].get("size")
        if isinstance(size, bool) or not isinstance(size, int) or size != path.stat().st_size:
            raise ConfigurationError(f"Saved artifact size does not match: {relative}")
        return SavedArtifact(path, digest, size)

    def prepare(
        self,
        operation: Operation,
        options: WorkflowOptions | None = None,
        *,
        protein_ids: list[str] | None = None,
        labels: list[str] | None = None,
        ph_values: list[str] | None = None,
        view: PyMOLView | None = None,
        use_saved_view: bool = True,
    ) -> SavedRunWorkflow:
        try:
            return SavedRunWorkflow(
                self,
                operation,
                options or self.options,
                protein_ids=protein_ids,
                labels=labels,
                ph_values=ph_values,
                view=view,
                use_saved_view=use_saved_view,
            )
        except (OSError, KeyError, TypeError, ValueError, AttributeError) as exc:
            raise ConfigurationError(
                f"Saved run artifacts or selection are invalid: {exc}"
            ) from exc


class SavedRunWorkflow:
    """A preflighted explicit stage plan, shared by the CLI and GUI workers."""

    def __init__(
        self,
        source: SavedRun,
        operation: Operation,
        options: WorkflowOptions,
        *,
        protein_ids: list[str] | None,
        labels: list[str] | None,
        ph_values: list[str] | None,
        view: PyMOLView | None,
        use_saved_view: bool,
    ) -> None:
        if operation not in {"compose", "render"}:
            raise ConfigurationError(
                "Choose compose or render; use a template for full calculations"
            )
        self.source, self.operation, self.options = source, operation, options
        source_labels = dict(source.proteins)
        ids = list(source_labels) if protein_ids is None else protein_ids
        if not ids or any(item not in source_labels for item in ids):
            raise ConfigurationError("Select only saved protein IDs; use a template for new inputs")
        names = [source_labels[item] for item in ids] if labels is None else labels
        validate_protein_names(len(ids), protein_ids=ids, labels=names)
        self.proteins = list(zip(ids, names, strict=True))
        selected_ph = source.ph_values if ph_values is None else parse_ph_values(ph_values)
        if not set(selected_ph) <= set(source.ph_values):
            raise ConfigurationError(
                "Select only saved pH values; new values require a full calculation"
            )
        # Keep the exact original decimal spelling used by artifact paths and task IDs.
        self.ph_values = tuple(
            next(old for old in source.ph_values if old == ph) for ph in selected_ph
        )
        format_ph_labels(self.ph_values, options.ph_label_precision)
        self.view = (source.view if use_saved_view else None) if view is None else list(view.values)
        if options.force_field != source.options.force_field:
            raise ConfigurationError("Changing the force field requires a full calculation")
        if operation == "compose":
            allowed = {"dpi", "foreground", "background"}
            before, after = (
                source.tile_options.render_configuration(),
                options.render_configuration(),
            )
            if any(before[key] != value for key, value in after.items() if key not in allowed):
                raise ConfigurationError("Rendering settings changed; choose Re-render instead")
            if self.view != source.view:
                raise ConfigurationError("Changing the camera requires Re-render")
            if (
                source.tile_options.background is not None
                and options.background != source.tile_options.background
            ):
                raise ConfigurationError(
                    "Saved tiles have an opaque background; use Re-render to change it"
                )
        self.cells: list[tuple[str, Decimal, list[tuple[dict[str, Any], list[SavedArtifact]]]]] = []
        self.scientific_metrics: dict[tuple[str, Decimal], dict[str, Any]] = {}
        for protein_id, _ in self.proteins:
            for ph in self.ph_values:
                tasks = [
                    task
                    for task in source.manifest.get("tasks", [])
                    if task.get("protein_id") == protein_id and task.get("ph") == str(ph)
                ]
                if len(tasks) != 1:
                    raise ConfigurationError(f"No unique saved cell for {protein_id}@{ph}")
                metrics = copy.deepcopy(tasks[0].get("scientific_metrics", {}))
                for stage in tasks[0].get("stages", []):
                    if stage.get("status") == "completed" and stage.get("name") in {
                        "pdb2pqr",
                        "apbs",
                    }:
                        metrics.update(stage.get("metrics", {}))
                self.scientific_metrics[protein_id, ph] = metrics
                names = ["pymol"] if operation == "compose" else ["pdb2pqr", "apbs"]
                stages = []
                relative_work = (
                    Path("work") / safe_path_part(protein_id) / f"ph-{safe_path_part(str(ph))}"
                )
                for name in names:
                    matches = [
                        stage
                        for stage in tasks[0].get("stages", [])
                        if stage.get("name") == name and stage.get("status") == "completed"
                    ]
                    if len(matches) != 1:
                        raise ConfigurationError(
                            f"No completed {name} artifact for {protein_id}@{ph}"
                        )
                    relative = (
                        Path("renders") / protein_id / f"ph-{safe_path_part(str(ph))}.png"
                        if name == "pymol"
                        else relative_work / ("model.pqr" if name == "pdb2pqr" else "model.pqr.dx")
                    )
                    artifact = source.artifact(matches[0], relative)
                    if name == "pymol":
                        try:
                            with Image.open(artifact.path) as image:
                                if image.format != "PNG" or image.size != (
                                    options.size,
                                    options.size,
                                ):
                                    raise ConfigurationError(
                                        "Saved tile dimensions do not match the render settings"
                                    )
                                image.verify()
                        except (OSError, ValueError, Image.DecompressionBombError) as exc:
                            raise ConfigurationError(
                                f"Saved tile is unreadable: {relative}"
                            ) from exc
                    stages.append((matches[0], [artifact]))
                self.cells.append((protein_id, ph, stages))

    def plan(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "source_run_id": self.source.root.name,
            "manifest_sha256": self.source.digest,
            "total": len(self.cells),
            "proteins": [{"id": key, "label": label} for key, label in self.proteins],
            "ph_values": [str(ph) for ph in self.ph_values],
            "reuse": ["PNG tiles"]
            if self.operation == "compose"
            else ["PQR structures", "DX potentials"],
            "run": ["composition"] if self.operation == "compose" else ["PyMOL", "composition"],
            "render": self.options.render_configuration(),
            "composition": self.options.composition_configuration(self.ph_values),
            "execution": self.options.execution_configuration(
                len(self.cells),
                parallelizable=self.operation == "render",
            ),
        }

    def run_in_place(self) -> RunResult:
        """Refine one calculation; failed/cancelled work never replaces its result."""
        from phmap.run_updates import RunUpdate, preserve_artifacts

        update = RunUpdate(self.source.root)
        update.begin(self.source.digest)
        try:
            original_options = self.options
            self.options = replace(self.options, output_dir=update.pending)
            try:
                self.run()
            finally:
                self.options = original_options
            preserve_artifacts(self.source, update.pending)
            update.commit()
            root = self.source.root
            workspace = RunWorkspace(
                root,
                root.name,
                root / "inputs",
                root / "work",
                root / "renders",
                root / "logs",
                root / "output",
                root / "manifest.json",
            )
            return RunResult(workspace, root / "output/pHmap.png", root / "manifest.json")
        finally:
            update.discard()

    def run(self) -> RunResult:
        if sha256_file(self.source.root / "manifest.json") != self.source.digest:
            raise ConfigurationError("Source manifest changed; review the plan again")
        options = self.options
        workspace = (
            RunWorkspace.create(options.output_dir, exact=True)
            if options.output_dir
            else RunWorkspace.create(options.runs_dir)
        )
        manifest = RunManifest(workspace.manifest, workspace.run_id)
        config = copy.deepcopy(self.source.configuration)
        by_id = {item["id"]: item for item in config["proteins"]}
        config.update(
            proteins=[{**by_id[key], "label": label} for key, label in self.proteins],
            ph_values=[str(ph) for ph in self.ph_values],
            view=self.view,
            render=(
                copy.deepcopy(self.source.configuration["render"])
                if self.operation == "compose"
                else options.render_configuration()
            ),
            composition=options.composition_configuration(self.ph_values),
            execution=options.execution_configuration(
                len(self.cells), parallelizable=self.operation == "render"
            ),
        )
        if self.ph_values != self.source.ph_values:
            config.update(ph_mode="explicit", ph_range=None)
        manifest.set_configuration(config)
        manifest.data.update(
            based_on={
                "run_id": self.source.root.name,
                "operation": self.operation,
                "manifest_sha256": self.source.digest,
            },
            plan=self.plan(),
            reused_inputs=copy.deepcopy(
                self.source.manifest.get("inputs") or self.source.manifest.get("reused_inputs", [])
            ),
        )
        manifest.set_status("running")
        try:
            for name in ("pdb2pqr", "apbs", "pymol"):
                if name in self.source.manifest.get("tools", {}):
                    manifest.record_tool(
                        name, {**self.source.manifest["tools"][name], "reused": True}
                    )
            executable = None
            if self.operation == "render":
                executable = discover_executable(options.pymol_executable)
                runner = CommandRunner(workspace.logs / "tools", options.timeout)
                manifest.record_tool(
                    "pymol",
                    _version_dict(PyMOLBackend(runner, executable).version(cwd=workspace.root)),
                )
            workers = config["execution"]["effective_workers"]
            manifest.prepare_tasks([(protein, ph) for protein, ph, _ in self.cells])

            def render_cell(
                cell: tuple[str, Decimal, list[tuple[dict[str, Any], list[SavedArtifact]]]],
                stop: Event,
            ) -> RenderedTile:
                check_cancelled(stop)
                protein_id, ph, stages = cell
                task = manifest.start_task(
                    f"{protein_id}@{ph}",
                    protein_id,
                    ph,
                    metrics=self.scientific_metrics[protein_id, ph],
                )
                paths = workspace.task_paths(protein_id, str(ph))
                runner = CommandRunner(
                    paths.pymol_stdout.parent,
                    options.timeout,
                    SINGLE_THREAD_ENV if workers > 1 else None,
                )
                try:
                    for original, artifacts in stages:
                        outputs = []
                        for artifact in artifacts:
                            target = (
                                paths.tile
                                if original["name"] == "pymol"
                                else paths.pqr
                                if original["name"] == "pdb2pqr"
                                else paths.potential
                            )
                            artifact.copy_to(target)
                            outputs.append(
                                {
                                    "path": str(target),
                                    "sha256": artifact.sha256,
                                    "size": target.stat().st_size,
                                }
                            )
                        manifest.record_stage(
                            task,
                            {
                                "name": original["name"],
                                "status": "completed",
                                "reused": True,
                                "source_run_id": self.source.root.name,
                                "reused_from": [
                                    {
                                        "path": str(item.path.relative_to(self.source.root)),
                                        "sha256": item.sha256,
                                    }
                                    for item in artifacts
                                ],
                                "outputs": outputs,
                                "metrics": original.get("metrics", {}),
                                "finished_at": utc_now(),
                            },
                        )
                    if executable is not None:
                        check_cancelled(stop)
                        manifest.start_stage(task, "pymol")
                        rendered = PyMOLBackend(runner, executable).render(
                            PyMOLRenderRequest(
                                pqr_path=paths.pqr,
                                dx_path=paths.potential,
                                png_path=paths.tile,
                                script_path=paths.pymol_script,
                                work_dir=paths.root,
                                options=options.render_options(
                                    view=None if self.view is None else tuple(self.view),
                                    max_threads=1 if workers > 1 else None,
                                ),
                            ),
                            timeout=options.timeout,
                        )
                        manifest.record_stage(
                            task,
                            _stage_dict(
                                "pymol", rendered.command, (paths.tile, paths.pymol_script)
                            ),
                        )
                    manifest.finish_task(task, "completed")
                    return RenderedTile(protein_id, ph, paths.tile)
                except CellCancelled as exc:
                    manifest.finish_task(task, "cancelled", error=str(exc))
                    raise
                except Exception as exc:
                    if stage := task.get("current_stage"):
                        manifest.record_stage(
                            task, _failed_stage_dict(stage, exc, runner, paths.root)
                        )
                    manifest.finish_task(task, "failed", error=str(exc))
                    raise

            tiles = run_cells(self.cells, render_cell, workers=workers)
            figure = compose_grid(
                protein_order=self.proteins,
                ph_order=self.ph_values,
                tiles=tiles,
                output_path=workspace.output / "pHmap.png",
                settings=options.composition_settings(),
            )
            manifest.record_output("figure", figure)
            manifest.set_status("completed")
            return RunResult(workspace, figure, workspace.manifest)
        except BaseException as exc:
            manifest.cancel_unfinished()
            manifest.set_status(
                "cancelled" if isinstance(exc, KeyboardInterrupt) else "failed", error=str(exc)
            )
            raise
