"""Explicit local PDB2PQR → APBS → PyMOL → composition workflow."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from phmap.backends import (
    ApbsBackend,
    ApbsRequest,
    ArtifactError,
    CommandExecutionError,
    CommandResult,
    CommandRunner,
    CommandStartError,
    CommandTimeoutError,
    Pdb2pqrBackend,
    Pdb2pqrRequest,
    PyMOLBackend,
    PyMOLRenderRequest,
    VersionInfo,
    discover_executable,
    pdb2pqr_dx_path,
)
from phmap.compose import RenderedTile, compose_grid
from phmap.configuration import WorkflowOptions as WorkflowOptions
from phmap.errors import ConfigurationError
from phmap.manifest import RunManifest, sha256_file, utc_now
from phmap.models import RunInputs
from phmap.scientific import summarize_opendx, summarize_pqr
from phmap.versions import version_line
from phmap.workspace import RunWorkspace


@dataclass(frozen=True, slots=True)
class RunResult:
    """Primary artifacts returned by a completed workflow."""

    workspace: RunWorkspace
    figure: Path
    manifest: Path


def _version_dict(info: VersionInfo) -> dict[str, Any]:
    return {
        "executable": str(info.executable),
        "argv": list(info.argv),
        "returncode": info.returncode,
        "version": version_line(info.output) or "unknown",
    }


def _command_dict(result: CommandResult) -> dict[str, Any]:
    return {
        "argv": list(result.argv),
        "cwd": str(result.cwd),
        "returncode": result.returncode,
        "duration_seconds": result.duration_seconds,
        "stdout": str(result.stdout_path),
        "stderr": str(result.stderr_path),
    }


def _stage_dict(
    name: str,
    result: CommandResult,
    outputs: tuple[Path, ...],
    *,
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    details = {
        "name": name,
        "status": "completed",
        **_command_dict(result),
        "outputs": [
            {
                "path": str(path),
                "size": path.stat().st_size,
                "sha256": sha256_file(path),
            }
            for path in outputs
        ],
        "finished_at": utc_now(),
    }
    if metrics is not None:
        details["metrics"] = metrics
    return details


def _failed_stage_dict(
    name: str, error: Exception, runner: CommandRunner, cwd: Path,
) -> dict[str, Any]:
    """Retain a stage record even when no completed command result exists."""

    details: dict[str, Any] = {
        "name": name,
        "status": "failed",
        "cwd": str(cwd),
        "stdout": str(runner.log_dir / f"{name}.stdout.log"),
        "stderr": str(runner.log_dir / f"{name}.stderr.log"),
        "outputs": [],
        "error": str(error),
        "error_type": type(error).__name__,
        "finished_at": utc_now(),
    }
    if isinstance(error, CommandExecutionError):
        details.update(_command_dict(error.result))
    elif isinstance(error, CommandTimeoutError):
        details.update(
            timeout_seconds=error.timeout,
            stdout=str(error.stdout_path), stderr=str(error.stderr_path),
        )
    elif isinstance(error, CommandStartError):
        details["executable"] = error.executable
    elif isinstance(error, ArtifactError):
        details["artifact"] = {
            "path": str(error.path), "description": error.description, "reason": error.reason,
        }
    return details


def _configuration(inputs: RunInputs, options: WorkflowOptions) -> dict[str, Any]:
    return {
        "proteins": [
            {
                "id": protein.protein_id,
                "label": protein.label,
                "path": str(protein.path),
                "ligand": None if protein.ligand is None else str(protein.ligand),
            }
            for protein in inputs.proteins
        ],
        "ph_values": [str(ph) for ph in inputs.ph_values],
        "ph_mode": inputs.ph_mode.value,
        "view": None if inputs.view is None else list(inputs.view.values),
        "render": {
            "size": options.size,
            "dpi": options.dpi,
            "potential_level": options.potential_level,
            "ramp_colors": list(options.ramp_colors),
            "background": options.background,
            "foreground": options.foreground,
            "ray_trace": options.ray_trace,
        },
        "scientific": {
            "force_field": options.force_field,
            "titration_state_method": "propka",
            "drop_water": True,
        },
    }


class LocalWorkflow:
    """Run the deterministic local calculation and rendering workflow."""

    def __init__(self, inputs: RunInputs, options: WorkflowOptions | None = None) -> None:
        self.inputs = inputs
        self.options = options or WorkflowOptions()

    def _create_workspace(self) -> RunWorkspace:
        if self.options.output_dir is not None:
            return RunWorkspace.create(self.options.output_dir, exact=True)
        return RunWorkspace.create(self.options.runs_dir)

    def run(self) -> RunResult:
        workspace = self._create_workspace()
        manifest = RunManifest(workspace.manifest, workspace.run_id)
        manifest.set_configuration(_configuration(self.inputs, self.options))
        manifest.set_status("running")

        try:
            executables = {
                "pdb2pqr": discover_executable(self.options.pdb2pqr_executable),
                "apbs": discover_executable(self.options.apbs_executable),
                "pymol": discover_executable(self.options.pymol_executable),
            }
            version_runner = CommandRunner(workspace.logs / "tools")
            version_probe_dir = workspace.logs / "tools" / "probe"
            version_probe_dir.mkdir(parents=True, exist_ok=True)
            version_details = (
                (
                    "pdb2pqr",
                    Pdb2pqrBackend(version_runner, executables["pdb2pqr"]).version(
                        cwd=version_probe_dir
                    ),
                ),
                (
                    "apbs",
                    ApbsBackend(version_runner, executables["apbs"]).version(
                        cwd=version_probe_dir
                    ),
                ),
                (
                    "pymol",
                    PyMOLBackend(version_runner, executables["pymol"]).version(
                        cwd=version_probe_dir
                    ),
                ),
            )
            for name, details in version_details:
                manifest.record_tool(name, _version_dict(details))

            staged_proteins: dict[str, tuple[Path, Path | None]] = {}
            for protein in self.inputs.proteins:
                staged_pdb = workspace.stage_input(
                    protein.path, name=f"{protein.protein_id}.pdb"
                )
                manifest.record_input(
                    f"protein:{protein.protein_id}", protein.path, staged_pdb
                )
                staged_ligand = None
                if protein.ligand is not None:
                    staged_ligand = workspace.stage_input(
                        protein.ligand, name=f"{protein.protein_id}.mol2"
                    )
                    manifest.record_input(
                        f"ligand:{protein.protein_id}", protein.ligand, staged_ligand
                    )
                staged_proteins[protein.protein_id] = (staged_pdb, staged_ligand)

            rendered_tiles: list[RenderedTile] = []
            for protein in self.inputs.proteins:
                staged_pdb, staged_ligand = staged_proteins[protein.protein_id]
                for ph in self.inputs.ph_values:
                    ph_key = str(ph)
                    task_id = f"{protein.protein_id}@{ph_key}"
                    task = manifest.start_task(task_id, protein.protein_id, ph)
                    paths = workspace.task_paths(protein.protein_id, ph_key)
                    runner = CommandRunner(paths.pdb2pqr_stdout.parent, self.options.timeout)
                    try:
                        manifest.start_stage(task, "pdb2pqr")
                        pqr_backend = Pdb2pqrBackend(runner, executables["pdb2pqr"])
                        pqr_artifacts = pqr_backend.run(
                            Pdb2pqrRequest(
                                pdb_path=staged_pdb,
                                pqr_path=paths.pqr,
                                apbs_input_path=paths.apbs_input,
                                ph=ph,
                                force_field=self.options.force_field,
                                ligand_path=staged_ligand,
                                work_dir=paths.root,
                            ),
                            timeout=self.options.timeout,
                        )
                        manifest.record_stage(
                            task,
                            _stage_dict(
                                "pdb2pqr",
                                pqr_artifacts.command,
                                (pqr_artifacts.pqr_path, pqr_artifacts.apbs_input_path),
                                metrics={"pqr": summarize_pqr(pqr_artifacts.pqr_path).as_dict()},
                            ),
                        )

                        expected_dx = pdb2pqr_dx_path(pqr_artifacts.pqr_path)
                        if expected_dx != paths.potential:
                            raise ConfigurationError(
                                "Workspace potential path does not match PDB2PQR output"
                            )
                        apbs_backend = ApbsBackend(runner, executables["apbs"])
                        manifest.start_stage(task, "apbs")
                        apbs_artifacts = apbs_backend.run(
                            ApbsRequest(
                                apbs_input_path=pqr_artifacts.apbs_input_path,
                                dx_path=expected_dx,
                                work_dir=paths.root,
                            ),
                            timeout=self.options.timeout,
                        )
                        manifest.record_stage(
                            task,
                            _stage_dict(
                                "apbs",
                                apbs_artifacts.command,
                                (apbs_artifacts.dx_path,),
                                metrics={
                                    "opendx": summarize_opendx(
                                        apbs_artifacts.dx_path
                                    ).as_dict()
                                },
                            ),
                        )

                        pymol_backend = PyMOLBackend(runner, executables["pymol"])
                        manifest.start_stage(task, "pymol")
                        pymol_artifacts = pymol_backend.render(
                            PyMOLRenderRequest(
                                pqr_path=pqr_artifacts.pqr_path,
                                dx_path=apbs_artifacts.dx_path,
                                png_path=paths.tile,
                                script_path=paths.pymol_script,
                                work_dir=paths.root,
                                options=self.options.render_options(
                                    view=(
                                        None
                                        if self.inputs.view is None
                                        else self.inputs.view.values
                                    ),
                                ),
                            ),
                            timeout=self.options.timeout,
                        )
                        manifest.record_stage(
                            task,
                            _stage_dict(
                                "pymol",
                                pymol_artifacts.command,
                                (pymol_artifacts.png_path, pymol_artifacts.script_path),
                            ),
                        )
                        rendered_tiles.append(
                            RenderedTile(protein.protein_id, ph, pymol_artifacts.png_path)
                        )
                        manifest.finish_task(task, "completed")
                    except Exception as exc:
                        if stage := task.get("current_stage"):
                            manifest.record_stage(
                                task, _failed_stage_dict(stage, exc, runner, paths.root)
                            )
                        manifest.finish_task(task, "failed", error=str(exc))
                        raise

            figure = compose_grid(
                protein_order=[
                    (protein.protein_id, protein.label) for protein in self.inputs.proteins
                ],
                ph_order=self.inputs.ph_values,
                tiles=rendered_tiles,
                output_path=workspace.output / "pHmap.png",
                settings=self.options.composition_settings(),
            )
            manifest.record_output("figure", figure)
            manifest.set_status("completed")
            return RunResult(workspace, figure, workspace.manifest)
        except Exception as exc:
            manifest.set_status("failed", error=str(exc))
            raise
