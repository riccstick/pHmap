"""Explicit local PDB2PQR → APBS → PyMOL → composition workflow."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from phmap.backends import (
    ApbsBackend,
    ApbsRequest,
    CommandExecutionError,
    CommandResult,
    CommandRunner,
    Pdb2pqrBackend,
    Pdb2pqrRequest,
    PyMOLBackend,
    PyMOLRenderOptions,
    PyMOLRenderRequest,
    VersionInfo,
    discover_executable,
    pdb2pqr_dx_path,
)
from phmap.compose import CompositionSettings, RenderedTile, compose_grid
from phmap.errors import ConfigurationError
from phmap.manifest import RunManifest, sha256_file, utc_now
from phmap.models import RunInputs
from phmap.scientific import summarize_opendx, summarize_pqr
from phmap.workspace import RunWorkspace


@dataclass(frozen=True, slots=True)
class WorkflowOptions:
    """Execution and rendering options for a local run."""

    output_dir: Path | None = None
    runs_dir: Path = Path("runs")
    size: int = 500
    dpi: int = 300
    potential_level: float = 5.0
    ramp_colors: tuple[str, str, str] = ("red", "white", "blue")
    background: str | None = None
    foreground: str = "black"
    force_field: str = "PARSE"
    timeout: float = 1800.0
    ray_trace: bool = True
    pdb2pqr_executable: str | Path = "pdb2pqr30"
    apbs_executable: str | Path = "apbs"
    pymol_executable: str | Path = "pymol"

    def __post_init__(self) -> None:
        if isinstance(self.size, bool) or not 64 <= self.size <= 16_384:
            raise ConfigurationError("Image size must be between 64 and 16384 pixels")
        if isinstance(self.dpi, bool) or not 1 <= self.dpi <= 4_800:
            raise ConfigurationError("DPI must be between 1 and 4800")
        if (
            isinstance(self.potential_level, bool)
            or not math.isfinite(self.potential_level)
            or self.potential_level <= 0
        ):
            raise ConfigurationError("Potential level must be finite and positive")
        if (
            isinstance(self.timeout, bool)
            or not math.isfinite(self.timeout)
            or self.timeout <= 0
        ):
            raise ConfigurationError("Backend timeout must be finite and positive")
        if len(self.ramp_colors) != 3:
            raise ConfigurationError("The potential ramp requires exactly three colors")


@dataclass(frozen=True, slots=True)
class RunResult:
    """Primary artifacts returned by a completed workflow."""

    workspace: RunWorkspace
    figure: Path
    manifest: Path


def _version_dict(info: VersionInfo) -> dict[str, Any]:
    lines = [line.strip() for line in info.output.splitlines() if line.strip()]
    version_line = next(
        (
            line
            for line in lines
            if re.search(r"\b(?:PDB2PQR|pdb2pqr|APBS|PyMOL)\b.*\d", line)
        ),
        lines[0] if lines else "unknown",
    )
    return {
        "executable": str(info.executable),
        "argv": list(info.argv),
        "returncode": info.returncode,
        "version": version_line[:500],
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
        "argv": list(result.argv),
        "cwd": str(result.cwd),
        "returncode": result.returncode,
        "duration_seconds": result.duration_seconds,
        "stdout": str(result.stdout_path),
        "stderr": str(result.stderr_path),
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


def _failed_command_dict(error: CommandExecutionError) -> dict[str, Any]:
    result = error.result
    return {
        "name": result.stage,
        "status": "failed",
        "argv": list(result.argv),
        "cwd": str(result.cwd),
        "returncode": result.returncode,
        "duration_seconds": result.duration_seconds,
        "stdout": str(result.stdout_path),
        "stderr": str(result.stderr_path),
        "outputs": [],
        "error": str(error),
        "finished_at": utc_now(),
    }


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
    """Run the first deterministic, local pHmap vertical slice."""

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
                        pymol_artifacts = pymol_backend.render(
                            PyMOLRenderRequest(
                                pqr_path=pqr_artifacts.pqr_path,
                                dx_path=apbs_artifacts.dx_path,
                                png_path=paths.tile,
                                script_path=paths.pymol_script,
                                work_dir=paths.root,
                                options=PyMOLRenderOptions(
                                    width=self.options.size,
                                    height=self.options.size,
                                    dpi=self.options.dpi,
                                    level=self.options.potential_level,
                                    ramp_colors=self.options.ramp_colors,
                                    background=self.options.background or "white",
                                    transparent_background=self.options.background is None,
                                    view=(
                                        None
                                        if self.inputs.view is None
                                        else self.inputs.view.values
                                    ),
                                    ray_trace=self.options.ray_trace,
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
                        if isinstance(exc, CommandExecutionError):
                            manifest.record_stage(task, _failed_command_dict(exc))
                        manifest.finish_task(task, "failed", error=str(exc))
                        raise

            figure = compose_grid(
                protein_order=[
                    (protein.protein_id, protein.label) for protein in self.inputs.proteins
                ],
                ph_order=self.inputs.ph_values,
                tiles=rendered_tiles,
                output_path=workspace.output / "pHmap.png",
                settings=CompositionSettings(
                    background=self.options.background,
                    foreground=self.options.foreground,
                    potential_level=self.options.potential_level,
                    ramp_colors=self.options.ramp_colors,
                ),
            )
            manifest.record_output("figure", figure)
            manifest.set_status("completed")
            return RunResult(workspace, figure, workspace.manifest)
        except Exception as exc:
            manifest.set_status("failed", error=str(exc))
            raise
